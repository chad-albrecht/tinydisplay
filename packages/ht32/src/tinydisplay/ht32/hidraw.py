"""Talking to the panel through Linux ``hidraw``, with no USB library at all.

``/dev/hidrawN`` takes an ordinary ``write()`` whose first byte is the HID
report ID. Our packets already begin with ``0x00``, so a raw write is
byte-for-byte the write hidapi would perform -- and it needs no compiled
extension, no libusb, and no dependencies beyond the standard library.

That matters more than it sounds. The machines this panel is built into tend to
be appliances: a Home Assistant OS box has no compiler, and PyPI ships no
``musllinux`` wheel for hidapi, so the "obvious" transport is the one that is
hardest to install exactly where the panel lives. This one is a file
descriptor.

Discovery works the same way -- sysfs, not enumeration. Every hidraw node
publishes its hardware ID at ``/sys/class/hidraw/hidrawN/device/uevent``::

    HID_ID=0003:000004D9:0000FD01
    HID_NAME=...

so finding the panel is reading text files, and finding *which interface* is
following one symlink to the USB interface's ``bInterfaceNumber``.

Linux only, by construction. :func:`is_hidraw_available` says whether this
transport is usable before anything tries it.
"""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from tinydisplay.ht32.errors import DeviceNotFoundError, TransportError
from tinydisplay.ht32.protocol import LCD_INTERFACE, PACKET_SIZE, PRODUCT_ID, VENDOR_ID

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "HIDRAW_ROOT",
    "HidrawDeviceInfo",
    "HidrawTransport",
    "enumerate_hidraw",
    "find_hidraw_panel",
    "is_hidraw_available",
    "parse_hid_id",
]

#: Where the kernel publishes hidraw devices.
HIDRAW_ROOT: Final = Path("/sys/class/hidraw")

#: ``HID_ID=<bus>:<vendor>:<product>``, all hexadecimal and zero-padded.
_HID_ID_PREFIX: Final = "HID_ID="

#: Reported when the interface number cannot be determined.
UNKNOWN_INTERFACE: Final = -1

#: Binary mode. A no-op on Linux, where this transport actually runs, but
#: without it Windows translates 0x0A in a packet into 0x0D 0x0A and corrupts
#: the frame silently. Cheap insurance, and it keeps the write path honest on
#: the machines the tests run on.
_O_BINARY: Final = getattr(os, "O_BINARY", 0)


@dataclass(frozen=True, slots=True)
class HidrawDeviceInfo:
    """One ``/dev/hidrawN`` node belonging to an HT32 panel."""

    path: Path
    vendor_id: int
    product_id: int
    interface_number: int = UNKNOWN_INTERFACE
    name: str = ""

    @property
    def is_display_interface(self) -> bool:
        """Whether this is the interface that accepts frames."""
        return self.interface_number == LCD_INTERFACE

    def __str__(self) -> str:
        label = self.name or "HT32 panel"
        hardware = f"{self.vendor_id:04X}:{self.product_id:04X}"
        return f"{label} ({hardware} if{self.interface_number}) {self.path}"


def is_hidraw_available(*, root: Path = HIDRAW_ROOT) -> bool:
    """Whether this system exposes hidraw devices at all.

    False on Windows and macOS, and on a Linux container that was not given
    the device nodes.
    """
    return sys.platform.startswith("linux") and root.is_dir()


def parse_hid_id(uevent: str) -> tuple[int, int, int] | None:
    """Pull ``(bus, vendor, product)`` out of a hidraw ``uevent`` file.

    Returns ``None`` when the file carries no usable ``HID_ID`` line, which is
    the honest answer for a malformed or truncated read -- guessing here would
    turn "unreadable" into "wrong device".

    Example:
        >>> from tinydisplay.ht32.hidraw import parse_hid_id
        >>> parse_hid_id("HID_ID=0003:000004D9:0000FD01\\nHID_NAME=Panel\\n")
        (3, 1241, 64769)
    """
    for line in uevent.splitlines():
        if not line.startswith(_HID_ID_PREFIX):
            continue
        fields = line[len(_HID_ID_PREFIX) :].split(":")
        expected_fields = 3
        if len(fields) != expected_fields:
            return None
        try:
            return (int(fields[0], 16), int(fields[1], 16), int(fields[2], 16))
        except ValueError:
            return None
    return None


def _read_text(path: Path) -> str:
    """Read a sysfs file, treating any I/O problem as absent."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _interface_number(device_dir: Path) -> int:
    """Read the USB interface number for a hidraw node.

    ``<hidraw>/device`` is the HID device, whose *parent* is the USB interface
    carrying ``bInterfaceNumber``. Absent on non-USB HID devices, and on some
    container filesystems, so a failure here is not fatal -- it just means
    interface preference falls back to ordering.
    """
    raw = _read_text(device_dir / ".." / "bInterfaceNumber").strip()
    try:
        return int(raw)
    except ValueError:
        return UNKNOWN_INTERFACE


def _device_name(device_dir: Path) -> str:
    """The human-readable HID name, if the kernel reports one."""
    for line in _read_text(device_dir / "uevent").splitlines():
        if line.startswith("HID_NAME="):
            return line.removeprefix("HID_NAME=").strip()
    return ""


def enumerate_hidraw(
    *,
    vendor_id: int = VENDOR_ID,
    product_id: int = PRODUCT_ID,
    root: Path = HIDRAW_ROOT,
) -> tuple[HidrawDeviceInfo, ...]:
    """List hidraw nodes belonging to matching panels.

    Returns an empty tuple when nothing matches, including on systems with no
    hidraw at all. That is a normal state rather than an error, so that callers
    can fall back to another transport without catching anything.
    """
    if not root.is_dir():
        return ()

    found: list[HidrawDeviceInfo] = []
    for entry in sorted(root.iterdir()):
        device_dir = entry / "device"
        hid_id = parse_hid_id(_read_text(device_dir / "uevent"))
        if hid_id is None:
            continue
        _, vendor, product = hid_id
        if (vendor, product) != (vendor_id, product_id):
            continue
        found.append(
            HidrawDeviceInfo(
                path=Path("/dev") / entry.name,
                vendor_id=vendor,
                product_id=product,
                interface_number=_interface_number(device_dir),
                name=_device_name(device_dir),
            )
        )

    return tuple(found)


def select_display_node(devices: Sequence[HidrawDeviceInfo]) -> HidrawDeviceInfo | None:
    """Pick the node that accepts frames, or fall back to the first.

    The fallback covers kernels and containers that do not expose
    ``bInterfaceNumber``: preferring the documented interface and settling for
    what exists beats refusing to try.
    """
    if not devices:
        return None
    for device in devices:
        if device.is_display_interface:
            return device
    return devices[0]


def find_hidraw_panel(
    *,
    vendor_id: int = VENDOR_ID,
    product_id: int = PRODUCT_ID,
    root: Path = HIDRAW_ROOT,
) -> HidrawDeviceInfo:
    """Find the hidraw node to write frames to.

    Raises:
        DeviceNotFoundError: If no matching node exists.
    """
    selected = select_display_node(
        enumerate_hidraw(vendor_id=vendor_id, product_id=product_id, root=root)
    )
    if selected is None:
        detail = (
            "no hidraw devices are exposed here"
            if not root.is_dir()
            else f"no hidraw node reports {vendor_id:04X}:{product_id:04X}"
        )
        msg = (
            f"no HT32 panel found: {detail}; check that the panel is attached, that "
            "this container was given the device nodes, and on Linux that a udev rule "
            "grants access to them"
        )
        raise DeviceNotFoundError(msg)
    return selected


class HidrawTransport:
    """Writes packets straight to a ``/dev/hidrawN`` node.

    Args:
        path: The node to write to. Defaults to discovering one.
        device: A node already found by :func:`find_hidraw_panel`.

    Satisfies the same protocol as
    :class:`~tinydisplay.ht32.transport.HidTransport`, so the driver cannot
    tell which one it is holding.
    """

    def __init__(
        self,
        *,
        path: Path | str | None = None,
        device: HidrawDeviceInfo | None = None,
    ) -> None:
        if device is not None:
            self._path: Path | None = device.path
        elif path is not None:
            self._path = Path(path)
        else:
            self._path = None
        self._device = device
        self._fd: int | None = None

    @property
    def is_open(self) -> bool:
        """Whether the device node is currently open."""
        return self._fd is not None

    @property
    def path(self) -> Path | None:
        """The node this transport writes to, once known."""
        return self._path

    def open(self) -> None:
        """Open the device node for writing.

        Raises:
            DeviceNotFoundError: If no node can be found or opened. On Linux a
                refusal here is almost always permissions rather than absence.
        """
        if self._fd is not None:
            return

        path = self._path if self._path is not None else find_hidraw_panel().path
        try:
            self._fd = os.open(path, os.O_WRONLY | _O_BINARY)
        except PermissionError as exc:
            msg = (
                f"permission denied opening {path}; hidraw nodes are root-only by "
                "default -- add a udev rule, or run with the privileges to write them"
            )
            raise DeviceNotFoundError(msg) from exc
        except OSError as exc:
            msg = f"could not open {path}: {exc}"
            raise DeviceNotFoundError(msg) from exc

        self._path = Path(path)

    def write(self, packet: bytes) -> None:
        """Write one packet.

        The first byte is the HID report ID, which is what the kernel expects
        from a hidraw write and what our packets already carry.

        Raises:
            TransportError: If the transport is closed, the packet is the wrong
                size, or the write fails or is truncated.
        """
        if self._fd is None:
            msg = "transport is not open; call open() first"
            raise TransportError(msg)
        if len(packet) != PACKET_SIZE:
            msg = f"HT32 packets are {PACKET_SIZE} bytes, got {len(packet)}"
            raise TransportError(msg)

        try:
            written = os.write(self._fd, packet)
        except OSError as exc:
            self.close()
            msg = f"write to {self._path} failed: {exc}"
            raise TransportError(msg) from exc

        if written != len(packet):
            # A short write on a HID node means the report was rejected; the
            # panel would receive a truncated packet and paint nothing.
            self.close()
            msg = f"short write to {self._path}: {written} of {len(packet)} bytes"
            raise TransportError(msg)

    def close(self) -> None:
        """Close the device node if it is open."""
        fd, self._fd = self._fd, None
        if fd is None:
            return
        # Already gone is the expected case: nothing left to release, and
        # refusing to close would strand the object in an unusable state.
        with contextlib.suppress(OSError):
            os.close(fd)
