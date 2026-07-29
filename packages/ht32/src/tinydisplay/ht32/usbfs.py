"""Driving the panel over raw USB, which is the only way that works.

This is the transport hardware bring-up settled on, after ``hidraw`` was shown
not to work at all. The reason is worth stating plainly, because it is not
obvious and it cost several rounds to find:

The panel's HID interface declares **64-byte output reports**. ``hidraw``
applies HID report semantics, so a 4,104-byte frame chunk cannot travel that
path however it is framed -- the kernel accepts the write, and the device never
receives what it expects. Both independent implementations for this hardware
avoid hidraw for exactly this reason: ``node-hid`` is explicitly configured
with ``setDriverType('libusb')``, and ``s1display`` links libusb directly.

libusb detaches the kernel driver and writes to the interface's endpoint, where
the host controller splits the transfer into 64-byte USB packets by itself.
That is what this module does -- but libusb is a wrapper over ``usbfs``, and
usbfs is a device node plus a handful of ioctls, so no library is needed. This
matters more than it sounds: the machines this panel is built into are
appliances with no compiler and no package manager, and a transport that needs
nothing installed is a transport that works there.

Linux only, by construction. :func:`is_usbfs_available` says so before anything
tries.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from tinydisplay.ht32.errors import DeviceNotFoundError, TransportError
from tinydisplay.ht32.protocol import PACKET_SIZE, PRODUCT_ID, VENDOR_ID, device_payload

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

# Linux only, and this module must stay importable everywhere: the package is
# developed and tested on Windows and macOS, and an ImportError here would take
# the whole driver down on machines that merely cannot use this transport.
#
# The ioctl function is bound once as Any rather than the module being used
# directly, because a type checker running on Windows cannot see into a
# platform-gated stub and would reject every call.
try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - exercised by not being Linux
    _fcntl = None  # type: ignore[assignment]

#: ``fcntl.ioctl``, or None where there is no fcntl. Fetched with getattr so
#: that the expression stays valid whichever platform the type checker assumes.
_ioctl: Any = getattr(_fcntl, "ioctl", None)

__all__ = [
    "DEFAULT_INIT_DELAY",
    "UsbDeviceInfo",
    "UsbEndpoint",
    "UsbfsTransport",
    "find_output_endpoint",
    "find_usb_panel",
    "is_usbfs_available",
    "usb_interfaces",
]

USB_DEVICES: Final = Path("/sys/bus/usb/devices")

#: The panel enumerates before it will answer. Shared with the other
#: transports so they cannot disagree about it.
DEFAULT_INIT_DELAY: Final = 1.0

#: bmAttributes low bits. Only these two can carry a frame.
TRANSFER_BULK: Final = 2
TRANSFER_INTERRUPT: Final = 3

# _IOC(dir, type, nr, size), as the kernel encodes ioctl numbers.
_IOC_NONE: Final = 0
_IOC_WRITE: Final = 1
_IOC_READ: Final = 2


def _ioc(direction: int, letter: str, number: int, size: int) -> int:
    """Encode an ioctl request number the way ``linux/ioctl.h`` does."""
    return (direction << 30) | (size << 16) | (ord(letter) << 8) | number


class _BulkTransfer(ctypes.Structure):
    """``struct usbdevfs_bulktransfer``."""

    _fields_ = (
        ("ep", ctypes.c_uint),
        ("len", ctypes.c_uint),
        ("timeout", ctypes.c_uint),
        ("data", ctypes.c_void_p),
    )


class _DevfsIoctl(ctypes.Structure):
    """``struct usbdevfs_ioctl``."""

    _fields_ = (
        ("ifno", ctypes.c_int),
        ("ioctl_code", ctypes.c_int),
        ("data", ctypes.c_void_p),
    )


USBDEVFS_CLAIMINTERFACE: Final = _ioc(_IOC_READ, "U", 15, ctypes.sizeof(ctypes.c_uint))
USBDEVFS_RELEASEINTERFACE: Final = _ioc(_IOC_READ, "U", 16, ctypes.sizeof(ctypes.c_uint))
USBDEVFS_BULK: Final = _ioc(_IOC_READ | _IOC_WRITE, "U", 2, ctypes.sizeof(_BulkTransfer))
USBDEVFS_IOCTL: Final = _ioc(_IOC_READ | _IOC_WRITE, "U", 18, ctypes.sizeof(_DevfsIoctl))
USBDEVFS_DISCONNECT: Final = _ioc(_IOC_NONE, "U", 22, 0)


@dataclass(frozen=True, slots=True)
class UsbEndpoint:
    """One endpoint of one USB interface."""

    address: int
    kind: int
    packet_size: int

    @property
    def is_out(self) -> bool:
        """Whether this endpoint carries data to the device."""
        return not self.address & 0x80

    @property
    def can_carry_frames(self) -> bool:
        """Whether frames can be written to it."""
        return self.is_out and self.kind in (TRANSFER_BULK, TRANSFER_INTERRUPT)


@dataclass(frozen=True, slots=True)
class UsbDeviceInfo:
    """An attached panel, located on the USB bus."""

    bus: int
    device: int
    sysfs: Path

    @property
    def node(self) -> Path:
        """The usbfs node this device is reachable through."""
        return Path(f"/dev/bus/usb/{self.bus:03d}/{self.device:03d}")

    def __str__(self) -> str:
        return f"HT32 panel ({VENDOR_ID:04X}:{PRODUCT_ID:04X}) at {self.node}"


def is_usbfs_available(*, root: Path = USB_DEVICES) -> bool:
    """Whether raw USB access is possible on this system."""
    return sys.platform.startswith("linux") and _ioctl is not None and root.is_dir()


def _read_text(path: Path) -> str:
    """Read a sysfs file, treating any problem as absent."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def find_usb_panel(
    *,
    vendor_id: int = VENDOR_ID,
    product_id: int = PRODUCT_ID,
    root: Path = USB_DEVICES,
) -> UsbDeviceInfo | None:
    """Locate the panel on the USB bus, or ``None`` if it is not attached.

    Absence is not an error here: callers choose a transport based on what is
    present, and raising would make "no panel" harder to handle than it is.
    """
    if not root.is_dir():
        return None
    for entry in sorted(root.iterdir()):
        try:
            vendor = int(_read_text(entry / "idVendor").strip() or "x", 16)
            product = int(_read_text(entry / "idProduct").strip() or "x", 16)
            if (vendor, product) != (vendor_id, product_id):
                continue
            bus = int(_read_text(entry / "busnum").strip())
            number = int(_read_text(entry / "devnum").strip())
        except ValueError:
            continue
        return UsbDeviceInfo(bus=bus, device=number, sysfs=entry)
    return None


def usb_interfaces(sysfs: Path) -> dict[int, list[UsbEndpoint]]:
    """Map each interface number to its endpoints.

    Read from sysfs rather than by opening the device, so it is safe to call
    while something else owns the interface -- and so it sees interfaces that
    are not HID at all, which no hidraw diagnostic can.
    """
    interfaces: dict[int, list[UsbEndpoint]] = {}
    if not sysfs.is_dir():
        return interfaces

    for entry in sorted(sysfs.iterdir()):
        # An interface is any child that declares an interface number. The
        # kernel names these "<device>:<config>.<interface>", but matching on
        # content rather than on a naming convention is both simpler and
        # harder to get subtly wrong.
        if not entry.is_dir():
            continue
        try:
            number = int(_read_text(entry / "bInterfaceNumber").strip(), 16)
        except ValueError:
            continue

        endpoints = []
        for ep_dir in sorted(entry.glob("ep_*")):
            try:
                endpoints.append(
                    UsbEndpoint(
                        address=int(_read_text(ep_dir / "bEndpointAddress").strip(), 16),
                        kind=int(_read_text(ep_dir / "bmAttributes").strip(), 16) & 0x03,
                        packet_size=int(_read_text(ep_dir / "wMaxPacketSize").strip(), 16),
                    )
                )
            except ValueError:
                continue
        interfaces[number] = endpoints
    return interfaces


def find_output_endpoint(interfaces: dict[int, list[UsbEndpoint]]) -> tuple[int, int] | None:
    """Choose the interface and endpoint that can carry frames.

    Returns ``(interface_number, endpoint_address)``. Among candidates the
    largest packet size wins: a display endpoint moves more data per transfer
    than a keypad's, and choosing by capability beats hard-coding an interface
    number that real hardware disagrees with.
    """
    best: tuple[int, int, int] | None = None
    for number, endpoints in sorted(interfaces.items()):
        for endpoint in endpoints:
            if not endpoint.can_carry_frames:
                continue
            if best is None or endpoint.packet_size > best[2]:
                best = (number, endpoint.address, endpoint.packet_size)
    return None if best is None else (best[0], best[1])


class UsbfsTransport:
    """Writes packets to the panel's USB endpoint, as libusb would.

    Args:
        device: A panel already located. Defaults to discovering one.
        interface: Which interface to claim. Defaults to the one publishing the
            most capable OUT endpoint.
        endpoint: Which endpoint to write to. Defaults as above.
        init_delay: Seconds to wait after claiming before writing.
        timeout_ms: Per-transfer timeout.

    Satisfies the same protocol as the other transports, so the driver cannot
    tell which one it holds.
    """

    def __init__(
        self,
        *,
        device: UsbDeviceInfo | None = None,
        interface: int | None = None,
        endpoint: int | None = None,
        init_delay: float = DEFAULT_INIT_DELAY,
        timeout_ms: int = 2000,
    ) -> None:
        self._device = device
        self._interface = interface
        self._endpoint = endpoint
        self._init_delay = init_delay
        self._timeout_ms = timeout_ms
        self._fd: int | None = None

    @property
    def is_open(self) -> bool:
        """Whether the interface is currently claimed."""
        return self._fd is not None

    @property
    def device(self) -> UsbDeviceInfo | None:
        """The panel this transport talks to, once known."""
        return self._device

    @property
    def interface(self) -> int | None:
        """The claimed interface number, once known."""
        return self._interface

    @property
    def endpoint(self) -> int | None:
        """The endpoint frames are written to, once known."""
        return self._endpoint

    def open(self) -> None:
        """Claim the interface, detaching whatever kernel driver holds it.

        Raises:
            DeviceNotFoundError: If no panel is attached, no suitable endpoint
                exists, or the interface cannot be claimed -- which on Linux
                usually means the process cannot write to ``/dev/bus/usb``.
        """
        if self._fd is not None:
            return
        if _ioctl is None or not sys.platform.startswith("linux"):
            msg = "raw USB access through usbfs is available on Linux only"
            raise DeviceNotFoundError(msg)

        device = self._device or find_usb_panel()
        if device is None:
            msg = (
                f"no HT32 panel found at {VENDOR_ID:04X}:{PRODUCT_ID:04X} on the USB bus; "
                "check that it is attached and that this container can see /sys/bus/usb"
            )
            raise DeviceNotFoundError(msg)

        if self._interface is None or self._endpoint is None:
            target = find_output_endpoint(usb_interfaces(device.sysfs))
            if target is None:
                msg = f"{device} publishes no endpoint that can carry frames"
                raise DeviceNotFoundError(msg)
            self._interface, self._endpoint = target

        try:
            fd = os.open(device.node, os.O_RDWR)
        except OSError as exc:
            msg = (
                f"could not open {device.node}: {exc}; raw USB needs write access to "
                "/dev/bus/usb, which usually means root or a udev rule"
            )
            raise DeviceNotFoundError(msg) from exc

        # Detach the kernel driver. This is the step hidraw cannot offer, and
        # the reason this transport works where that one cannot: while usbhid
        # owns the interface, every transfer is subject to HID report sizes.
        disconnect = _DevfsIoctl(self._interface, USBDEVFS_DISCONNECT, None)
        with contextlib.suppress(OSError):
            _ioctl(fd, USBDEVFS_IOCTL, disconnect, True)

        try:
            claim = ctypes.c_uint(self._interface)
            _ioctl(fd, USBDEVFS_CLAIMINTERFACE, claim, True)
        except OSError as exc:
            os.close(fd)
            msg = f"could not claim interface {self._interface} of {device}: {exc}"
            raise DeviceNotFoundError(msg) from exc

        self._fd = fd
        self._device = device

        if self._init_delay > 0:
            time.sleep(self._init_delay)

    def write(self, packet: bytes) -> None:
        """Write one packet to the endpoint.

        The leading HID report-ID byte is removed first: it is a convention of
        the hidraw API, and the device expects its signature in byte 0.

        Raises:
            TransportError: If the transport is closed, the packet is the wrong
                size, or the transfer fails or is truncated.
        """
        if self._fd is None or _ioctl is None:
            msg = "transport is not open; call open() first"
            raise TransportError(msg)
        if len(packet) != PACKET_SIZE:
            msg = f"HT32 packets are {PACKET_SIZE} bytes, got {len(packet)}"
            raise TransportError(msg)

        payload = device_payload(packet)
        buffer = ctypes.create_string_buffer(payload, len(payload))
        transfer = _BulkTransfer(
            ep=self._endpoint or 0,
            len=len(payload),
            timeout=self._timeout_ms,
            data=ctypes.cast(buffer, ctypes.c_void_p),
        )

        try:
            written = _ioctl(self._fd, USBDEVFS_BULK, transfer, True)
        except OSError as exc:
            self.close()
            msg = f"USB transfer failed: {exc}"
            raise TransportError(msg) from exc

        if written != len(payload):
            self.close()
            msg = f"short USB transfer: {written} of {len(payload)} bytes"
            raise TransportError(msg)

    def write_all(self, packets: Iterable[bytes]) -> None:
        """Write several packets in order."""
        for packet in packets:
            self.write(packet)

    def close(self) -> None:
        """Release the interface and close the node."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        if _ioctl is not None and self._interface is not None:
            release = ctypes.c_uint(self._interface)
            with contextlib.suppress(OSError):
                _ioctl(fd, USBDEVFS_RELEASEINTERFACE, release, True)
        with contextlib.suppress(OSError):
            os.close(fd)


def describe_panel(device: UsbDeviceInfo) -> Sequence[str]:
    """Lines describing a panel's interfaces, for diagnostics and logs."""
    lines = [str(device)]
    for number, endpoints in sorted(usb_interfaces(device.sysfs).items()):
        lines.append(f"  interface {number}")
        for endpoint in endpoints:
            direction = "out" if endpoint.is_out else "in"
            lines.append(
                f"    ep 0x{endpoint.address:02x} {direction} "
                f"kind {endpoint.kind} {endpoint.packet_size} bytes"
            )
    return lines
