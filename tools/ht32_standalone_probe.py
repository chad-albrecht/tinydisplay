#!/usr/bin/env python3
"""Standalone HT32 bring-up probe. Standard library only, no install.

Copy this one file onto a machine with the panel attached and run it::

    python3 ht32_standalone_probe.py            # find the panel, draw bars
    python3 ht32_standalone_probe.py --dry-run  # build packets, write nothing
    python3 ht32_standalone_probe.py --pattern black

It exists because the machines this panel is built into are the worst place to
install anything. On Home Assistant OS there is no compiler and no package
manager on the host, and PyPI publishes no musllinux wheel for hidapi -- so the
"normal" way in is the one that does not work there. This script needs nothing
but a Python interpreter and a ``/dev/hidraw`` node.

It deliberately duplicates the framing in ``tinydisplay.ht32.protocol``. That
duplication is the point -- it is what makes the file self-contained -- and
``tests/ht32/test_standalone_probe.py`` asserts byte-for-byte that the two
agree, so the copy cannot drift from the real implementation without failing
the suite.

Exit status is 0 on success, 1 on a panel or write problem, 2 on bad usage.
"""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import os
import sys
import time
from pathlib import Path

# Linux only, and this file must stay importable everywhere: the tests that
# pin it to the driver run on Windows and macOS too, and a bring-up tool that
# cannot be imported cannot be checked for drift.
try:
    import fcntl
except ImportError:  # pragma: no cover - exercised by not being Linux
    fcntl = None  # type: ignore[assignment]

VENDOR_ID = 0x04D9
PRODUCT_ID = 0xFD01
# Upstream hard-codes interface 1, but real units disagree: an AceMagic S1
# reports interfaces 0 and 2 and no interface 1 at all. So this is a
# preference, not a requirement -- when it is absent, every node the panel
# publishes is tried in turn and whichever accepts a frame is the display.
LCD_INTERFACE = 1

# The panel enumerates before it can be spoken to. Upstream waits a full second
# after opening and before the first command; writing inside that window gets
# no response, which surfaces as ETIMEDOUT rather than as anything descriptive.
DEFAULT_INIT_DELAY = 1.0

PANEL_WIDTH = 320
PANEL_HEIGHT = 170

SIGNATURE = 0x55
COMMAND_CONFIG = 0xA1
COMMAND_PARTIAL = 0xA2
COMMAND_REDRAW = 0xA3
SUB_ORIENTATION = 0xF1
SUB_SET_TIME = 0xF2
ORIENTATION_LANDSCAPE = 0x01
PHASE_START, PHASE_CONTINUE, PHASE_END = 0xF0, 0xF1, 0xF2

REPORT_SIZE = 1
HEADER_SIZE = 8
DATA_SIZE = 4096
PACKET_SIZE = REPORT_SIZE + HEADER_SIZE + DATA_SIZE
DATA_START = REPORT_SIZE + HEADER_SIZE

BYTES_PER_PIXEL = 2
PIXELS_PER_CHUNK = DATA_SIZE // BYTES_PER_PIXEL
FRAME_BYTES = PANEL_WIDTH * PANEL_HEIGHT * BYTES_PER_PIXEL
CHUNK_COUNT = -(-FRAME_BYTES // DATA_SIZE)
FINAL_CHUNK_SIZE = FRAME_BYTES - (CHUNK_COUNT - 1) * DATA_SIZE

HIDRAW_ROOT = Path("/sys/class/hidraw")

# Bar order matters: this is what you compare against the glass.
BARS = (
    ("red", (255, 0, 0)),
    ("green", (0, 255, 0)),
    ("blue", (0, 0, 255)),
    ("white", (255, 255, 255)),
    ("yellow", (255, 255, 0)),
    ("cyan", (0, 255, 255)),
    ("magenta", (255, 0, 255)),
    ("black", (0, 0, 0)),
)

# Sweep variants paint distinct flat colours so that whatever is left on the
# panel at the end names the variant that worked. It is the panel reporting its
# own result, which beats asking somebody to watch eight transfers go by.
SOLIDS = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "yellow": (255, 255, 0),
    "cyan": (0, 255, 255),
    "magenta": (255, 0, 255),
    "orange": (255, 128, 0),
    "purple": (128, 0, 255),
}


# -- Pixel packing ----------------------------------------------------------


def rgb565(red: int, green: int, blue: int) -> int:
    """Pack 8-bit RGB into a 16-bit RGB565 value."""
    return ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)


def build_frame(pattern: str) -> bytes:
    """Build one full frame, RGB565 big-endian, in transmission order."""
    frame = bytearray(FRAME_BYTES)

    for y in range(PANEL_HEIGHT):
        for x in range(PANEL_WIDTH):
            if pattern == "bars":
                colour = BARS[min(x * len(BARS) // PANEL_WIDTH, len(BARS) - 1)][1]
            elif pattern == "white":
                colour = (255, 255, 255)
            elif pattern == "gradient":
                level = x * 255 // (PANEL_WIDTH - 1)
                colour = (level, level, level)
            elif pattern in SOLIDS:
                colour = SOLIDS[pattern]
            else:  # black
                colour = (0, 0, 0)

            value = rgb565(*colour)
            offset = (y * PANEL_WIDTH + x) * BYTES_PER_PIXEL
            # Big-endian: most significant byte first.
            frame[offset] = (value >> 8) & 0xFF
            frame[offset + 1] = value & 0xFF

    return bytes(frame)


def chunk_phase(index: int) -> int:
    """Which redraw phase the chunk at ``index`` carries."""
    if index == 0:
        return PHASE_START
    if index == CHUNK_COUNT - 1:
        return PHASE_END
    return PHASE_CONTINUE


def build_packet(frame: bytes, index: int) -> bytes:
    """Build the redraw packet carrying chunk ``index``.

    The offset field counts *pixels*, not bytes: it is 16 bits wide, and the
    frame is 108,800 bytes (which does not fit) but 54,400 pixels (which does).
    Only the high byte of the chunk size is written, because pixel data starts
    at byte 9 and would overwrite the low byte anyway -- harmless, since both
    legal chunk sizes are exact multiples of 256.
    """
    size = DATA_SIZE if index < CHUNK_COUNT - 1 else FINAL_CHUNK_SIZE
    pixel_offset = index * PIXELS_PER_CHUNK

    packet = bytearray(PACKET_SIZE)
    packet[1] = SIGNATURE
    packet[2] = COMMAND_REDRAW
    packet[3] = chunk_phase(index)
    packet[4] = index + 1
    packet[5] = 0x00
    packet[6] = (pixel_offset >> 8) & 0xFF
    packet[7] = pixel_offset & 0xFF
    packet[8] = (size >> 8) & 0xFF

    start = index * DATA_SIZE
    packet[DATA_START : DATA_START + size] = frame[start : start + size]
    return bytes(packet)


def build_packets(frame: bytes) -> list[bytes]:
    """Every packet needed to paint ``frame``, in transmission order."""
    return [build_packet(frame, index) for index in range(CHUNK_COUNT)]


def build_packet_seq_first(frame: bytes, index: int) -> bytes:
    """A redraw packet with the sequence and phase bytes swapped.

    Sources disagree about which of bytes 2 and 3 (device-side) holds the
    phase and which holds the sequence counter. This is the other reading, so
    the panel can settle it instead of a document.
    """
    packet = bytearray(build_packet(frame, index))
    packet[3], packet[4] = packet[4], packet[3]
    return bytes(packet)


def build_command(command: int, sub_command: int, params: bytes = b"") -> bytes:
    """Build a non-redraw command packet.

    Byte 0 is the HID report ID, which the kernel strips before the report
    reaches the device -- so the signature the firmware sees is at its byte 0,
    as documented.
    """
    packet = bytearray(PACKET_SIZE)
    packet[1] = SIGNATURE
    packet[2] = command
    packet[3] = sub_command
    packet[4 : 4 + len(params)] = params
    return bytes(packet)


def build_orientation(landscape: bool = True) -> bytes:
    """Tell the panel which way up it is. Documented as 0x55 A1 F1 01."""
    return build_command(
        COMMAND_CONFIG,
        SUB_ORIENTATION,
        bytes([ORIENTATION_LANDSCAPE if landscape else 0x02]),
    )


def build_heartbeat() -> bytes:
    """The keep-alive the firmware expects roughly every second.

    Documented as a set-time command carrying the wall clock. The panel is
    believed to want this to stay in host-driven mode, which is the leading
    explanation for a panel that accepts frames and draws none.
    """
    now = time.localtime()
    return build_command(
        COMMAND_CONFIG,
        SUB_SET_TIME,
        bytes([now.tm_hour, now.tm_min, now.tm_sec]),
    )


# -- Finding the panel ------------------------------------------------------


def read_text(path: Path) -> str:
    """Read a sysfs file, treating any problem as absent."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def hid_id(device_dir: Path) -> tuple[int, int] | None:
    """The ``(vendor, product)`` a hidraw node reports, if any."""
    for line in read_text(device_dir / "uevent").splitlines():
        if line.startswith("HID_ID="):
            fields = line[len("HID_ID=") :].split(":")
            expected = 3
            if len(fields) != expected:
                return None
            try:
                return (int(fields[1], 16), int(fields[2], 16))
            except ValueError:
                return None
    return None


def interface_number(device_dir: Path) -> int:
    """The USB interface number, or -1 when the kernel does not say."""
    try:
        return int(read_text(device_dir / ".." / "bInterfaceNumber").strip())
    except ValueError:
        return -1


def find_nodes() -> list[tuple[Path, int]]:
    """Every hidraw node belonging to the panel, with its interface number."""
    if not HIDRAW_ROOT.is_dir():
        return []

    found = []
    for entry in sorted(HIDRAW_ROOT.iterdir()):
        device_dir = entry / "device"
        if hid_id(device_dir) == (VENDOR_ID, PRODUCT_ID):
            found.append((Path("/dev") / entry.name, interface_number(device_dir)))
    return found


def choose_node(nodes: list[tuple[Path, int]]) -> Path | None:
    """Prefer the display interface, settle for the first."""
    if not nodes:
        return None
    for path, interface in nodes:
        if interface == LCD_INTERFACE:
            return path
    return nodes[0][0]


def candidate_order(nodes: list[tuple[Path, int]]) -> list[Path]:
    """Every node worth trying, best first.

    Upstream's interface number does not survive contact with real hardware --
    an AceMagic S1 publishes interfaces 0 and 2 -- so rather than guess, try
    the preferred interface first and then everything else. Whichever accepts
    a full frame is the display, and that is a fact about the device rather
    than an assumption about it.
    """
    preferred = choose_node(nodes)
    ordered = [preferred] if preferred is not None else []
    ordered += [path for path, _ in nodes if path != preferred]
    return ordered


# -- Writing ----------------------------------------------------------------


HID_REPORT_BYTES = 64


def frame_whole(packet: bytes) -> list[bytes]:
    """One write of the full 4105-byte buffer, report ID included.

    What every attempt so far has used. The kernel accepts it, and the panel
    ignores it.
    """
    return [packet]


def frame_without_report_id(packet: bytes) -> list[bytes]:
    """One write of 4104 bytes, with the report-ID byte removed.

    hidraw is documented to strip a leading zero report ID before the report
    reaches the device. If it does not -- or if this device numbers its
    reports differently than assumed -- then every packet we have sent arrived
    with its signature one byte late, which the firmware would reject exactly
    as quietly as we have observed.
    """
    return [packet[1:]]


def frame_reports(packet: bytes) -> list[bytes]:
    """The declared shape: 64-byte reports, each with a zero report ID.

    The descriptor says this interface speaks 64 bytes at a time. A 4104-byte
    write is not something it ever claimed to accept.
    """
    payload = packet[1:]
    reports = []
    for start in range(0, len(payload), HID_REPORT_BYTES):
        chunk = payload[start : start + HID_REPORT_BYTES]
        reports.append(bytes([0x00]) + chunk.ljust(HID_REPORT_BYTES, b"\x00"))
    return reports


def frame_reports_bare(packet: bytes) -> list[bytes]:
    """64-byte reports with no report-ID byte at all."""
    payload = packet[1:]
    return [
        payload[start : start + HID_REPORT_BYTES].ljust(HID_REPORT_BYTES, b"\x00")
        for start in range(0, len(payload), HID_REPORT_BYTES)
    ]


FRAMINGS = {
    "whole": frame_whole,
    "no-report-id": frame_without_report_id,
    "reports": frame_reports,
    "reports-bare": frame_reports_bare,
}


def send(
    target: Path,
    packets: list[bytes],
    init_delay: float = DEFAULT_INIT_DELAY,
    chunk_delay: float = 0.0,
    framing: str = "whole",
) -> str | None:
    """Write every packet to ``target``.

    Returns ``None`` on success, or a description of what went wrong. A string
    rather than an exit status because the caller may try another node.

    A short write means the report was rejected, which would leave the panel
    holding a truncated frame -- worth reporting rather than continuing and
    blaming the picture.
    """
    try:
        fd = os.open(target, os.O_WRONLY)
    except OSError as exc:
        return f"could not open {target}: {exc}"

    index = 0
    try:
        # Anything written before the panel has initialised is accepted by the
        # kernel and ignored by the device, or times out waiting for a reply.
        if init_delay > 0:
            time.sleep(init_delay)

        split = FRAMINGS[framing]
        for index, packet in enumerate(packets):
            for report in split(packet):
                written = os.write(fd, report)
                if written != len(report):
                    return f"short write on packet {index}: {written} of {len(report)} bytes"
            # The firmware is documented as wanting brief pauses between
            # transmissions; writing flat out may overrun it.
            if chunk_delay > 0:
                time.sleep(chunk_delay)
    except OSError as exc:
        return f"write failed on packet {index}: {exc}"
    finally:
        os.close(fd)

    return None


# -- HID report descriptors -------------------------------------------------
#
# The decisive diagnostic, and the one to reach for first when a device accepts
# writes and does nothing. A HID device *declares* its reports: how many, what
# ID each carries, and exactly how many bytes each holds. The kernel enforces
# those sizes. So an interface with no OUTPUT report will swallow anything sent
# to it and act on none of it, and an interface whose OUTPUT report is a
# different size than we send will reject or truncate the write.
#
# Parsing the descriptor turns "which interface is the display?" from a guess
# into a lookup: the display is the one declaring an output report big enough
# to carry a 4096-byte chunk.

# Item prefix: bTag in the top four bits, bType in bits 2-3, bSize in bits 0-1.
_ITEM_SIZES = (0, 1, 2, 4)

_TAG_REPORT_SIZE = 0x74
_TAG_REPORT_ID = 0x84
_TAG_REPORT_COUNT = 0x94
_TAG_OUTPUT = 0x90
_TAG_INPUT = 0x80
_TAG_FEATURE = 0xB0

# Long items carry no report geometry, so they are skipped wholesale.
_LONG_ITEM_PREFIX = 0xFE


def parse_report_descriptor(data: bytes) -> dict[str, list[tuple[int, int]]]:
    """Extract report sizes from a HID report descriptor.

    Returns ``{"output": [(report_id, size_in_bytes), ...], "input": [...],
    "feature": [...]}``. Sizes are the payload the device expects, excluding
    the report-ID byte that hidraw prepends.

    This is a deliberately partial parser: it tracks only the global items that
    determine report sizes and ignores usages, collections and everything else,
    because the only question being asked is how big a report may be.
    """
    reports: dict[str, list[tuple[int, int]]] = {"input": [], "output": [], "feature": []}
    report_size = 0
    report_count = 0
    report_id = 0
    bits: dict[tuple[str, int], int] = {}

    index = 0
    while index < len(data):
        prefix = data[index]
        index += 1
        if prefix == _LONG_ITEM_PREFIX:
            if index >= len(data):
                break
            index += 2 + data[index]
            continue

        length = _ITEM_SIZES[prefix & 0x03]
        value = int.from_bytes(data[index : index + length], "little")
        index += length
        tag = prefix & 0xFC

        if tag == _TAG_REPORT_SIZE:
            report_size = value
        elif tag == _TAG_REPORT_COUNT:
            report_count = value
        elif tag == _TAG_REPORT_ID:
            report_id = value
        elif tag in (_TAG_INPUT, _TAG_OUTPUT, _TAG_FEATURE):
            kind = {_TAG_INPUT: "input", _TAG_OUTPUT: "output", _TAG_FEATURE: "feature"}[tag]
            key = (kind, report_id)
            bits[key] = bits.get(key, 0) + report_size * report_count

    for (kind, ident), total_bits in bits.items():
        reports[kind].append((ident, (total_bits + 7) // 8))
    for entries in reports.values():
        entries.sort()
    return reports


def output_report_size(path: Path) -> int:
    """The largest output report a node declares, or 0 if it declares none."""
    sysfs = HIDRAW_ROOT / path.name / "device" / "report_descriptor"
    try:
        reports = parse_report_descriptor(sysfs.read_bytes())
    except OSError:
        return 0
    return max((size for _, size in reports["output"]), default=0)


def writable_node(nodes: list[tuple[Path, int]]) -> Path | None:
    """The node that can actually receive a frame.

    Picking by descriptor rather than by "the write returned success" is the
    lesson of this bring-up: an input-only interface accepts writes and does
    nothing with them, which looks like a protocol bug for as long as you let
    it.
    """
    writable = [(path, output_report_size(path)) for path, _ in nodes]
    writable = [(path, size) for path, size in writable if size > 0]
    if not writable:
        return None
    # Prefer the biggest output report: if one interface can take a whole
    # chunk and another only a few bytes, the big one is the display.
    return max(writable, key=lambda entry: entry[1])[0]


def describe_nodes() -> int:
    """Print what each hidraw node declares. Returns a process exit status."""
    nodes = find_nodes()
    if not nodes:
        print("no HT32 panel nodes found", file=sys.stderr)
        return 1

    print("")
    print("HID report descriptors -- the display is the interface declaring an")
    print(f"output report large enough for a {DATA_SIZE}-byte chunk:")
    print("")

    for path, interface in nodes:
        sysfs = HIDRAW_ROOT / path.name / "device" / "report_descriptor"
        try:
            data = sysfs.read_bytes()
        except OSError as exc:
            print(f"{path} if{interface}: could not read descriptor: {exc}")
            continue

        reports = parse_report_descriptor(data)
        print(f"{path} if{interface}  ({len(data)}-byte descriptor)")
        for kind in ("output", "input", "feature"):
            if not reports[kind]:
                print(f"    {kind:<8} none")
                continue
            for ident, size in reports[kind]:
                verdict = ""
                if kind == "output" and size >= DATA_SIZE:
                    verdict = "  <- big enough to be the display"
                print(f"    {kind:<8} report id {ident:<3} {size} bytes{verdict}")
        print("")

    print("If no interface declares a large output report, this panel is not")
    print("driven by HID output reports at all -- the transfers must go out some")
    print("other way, and writing to /dev/hidraw will never work.")
    return 0


# -- Raw USB via usbfs ------------------------------------------------------
#
# The panel is not driven through hidraw. Both working implementations for this
# hardware use libusb -- tjaworski's node-hid is explicitly configured with
# setDriverType('libusb'), and rojkov's s1display depends on libusb directly.
# That is not a stylistic choice: hidraw applies HID report semantics, and this
# interface declares 64-byte reports, so a 4104-byte frame chunk cannot travel
# that path however it is framed. libusb detaches the kernel driver and pushes
# the whole transfer at the interrupt endpoint, where the host controller
# splits it into 64-byte USB packets by itself.
#
# libusb is not installable on the machines this panel lives in, but it is not
# needed: it is a wrapper over usbfs, and usbfs is a device node plus a handful
# of ioctls. Everything below is standard library.

# _IOC(dir, type, nr, size) as the kernel encodes it on x86 and arm.
_IOC_NONE, _IOC_WRITE, _IOC_READ = 0, 1, 2


def _ioc(direction: int, letter: str, number: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(letter) << 8) | number


class _BulkTransfer(ctypes.Structure):
    _fields_ = (
        ("ep", ctypes.c_uint),
        ("len", ctypes.c_uint),
        ("timeout", ctypes.c_uint),
        ("data", ctypes.c_void_p),
    )


class _DevfsIoctl(ctypes.Structure):
    _fields_ = (
        ("ifno", ctypes.c_int),
        ("ioctl_code", ctypes.c_int),
        ("data", ctypes.c_void_p),
    )


USBDEVFS_CLAIMINTERFACE = _ioc(_IOC_READ, "U", 15, ctypes.sizeof(ctypes.c_uint))
USBDEVFS_RELEASEINTERFACE = _ioc(_IOC_READ, "U", 16, ctypes.sizeof(ctypes.c_uint))
USBDEVFS_BULK = _ioc(_IOC_READ | _IOC_WRITE, "U", 2, ctypes.sizeof(_BulkTransfer))
USBDEVFS_IOCTL = _ioc(_IOC_READ | _IOC_WRITE, "U", 18, ctypes.sizeof(_DevfsIoctl))
USBDEVFS_DISCONNECT = _ioc(_IOC_NONE, "U", 22, 0)

USB_DEVICES = Path("/sys/bus/usb/devices")

#: bmAttributes low bits: 2 is bulk, 3 is interrupt. Either can carry frames.
_TRANSFER_BULK, _TRANSFER_INTERRUPT = 2, 3


def find_usb_device() -> tuple[int, int, Path] | None:
    """Locate the panel on the USB bus.

    Returns ``(busnum, devnum, sysfs_path)``, read from sysfs rather than
    guessed, or ``None`` when the panel is not attached.
    """
    if not USB_DEVICES.is_dir():
        return None
    for entry in sorted(USB_DEVICES.iterdir()):
        try:
            vendor = int(read_text(entry / "idVendor").strip() or "0", 16)
            product = int(read_text(entry / "idProduct").strip() or "0", 16)
        except ValueError:
            continue
        if (vendor, product) != (VENDOR_ID, PRODUCT_ID):
            continue
        try:
            bus = int(read_text(entry / "busnum").strip())
            device = int(read_text(entry / "devnum").strip())
        except ValueError:
            continue
        return (bus, device, entry)
    return None


def usb_interfaces(sysfs: Path) -> list[dict[str, object]]:
    """Describe every interface the panel publishes, with its endpoints.

    This is what the hidraw view could not show: interfaces that are not HID
    have no /dev/hidraw node at all, so they were invisible to every earlier
    diagnostic while being the most likely home of the display.
    """
    interfaces = []
    for entry in sorted(sysfs.iterdir()):
        if not entry.is_dir() or not entry.name.startswith(f"{sysfs.name}:"):
            continue
        try:
            number = int(read_text(entry / "bInterfaceNumber").strip(), 16)
        except ValueError:
            continue

        endpoints = []
        for ep_dir in sorted(entry.glob("ep_*")):
            try:
                address = int(read_text(ep_dir / "bEndpointAddress").strip(), 16)
                attributes = int(read_text(ep_dir / "bmAttributes").strip(), 16)
                packet_size = int(read_text(ep_dir / "wMaxPacketSize").strip(), 16)
            except ValueError:
                continue
            endpoints.append(
                {
                    "address": address,
                    "kind": attributes & 0x03,
                    "packet_size": packet_size,
                    "direction": "in" if address & 0x80 else "out",
                }
            )

        driver = ""
        driver_link = entry / "driver"
        if driver_link.is_symlink():
            driver = Path(driver_link.readlink()).name

        interfaces.append(
            {
                "number": number,
                "class": read_text(entry / "bInterfaceClass").strip(),
                "driver": driver,
                "endpoints": endpoints,
            }
        )
    return interfaces


def find_output_endpoint(interfaces: list[dict[str, object]]) -> tuple[int, int] | None:
    """Pick the interface and endpoint that can carry frames.

    Returns ``(interface_number, endpoint_address)``. Prefers an interrupt or
    bulk OUT endpoint; among candidates, the largest packet size wins, since a
    display endpoint moves more data than a keypad's.
    """
    best: tuple[int, int, int] | None = None
    for interface in interfaces:
        endpoints = interface["endpoints"]
        assert isinstance(endpoints, list)
        for endpoint in endpoints:
            if endpoint["direction"] != "out":
                continue
            if endpoint["kind"] not in (_TRANSFER_BULK, _TRANSFER_INTERRUPT):
                continue
            size = int(endpoint["packet_size"])
            if best is None or size > best[2]:
                best = (int(interface["number"]), int(endpoint["address"]), size)
    if best is None:
        return None
    return (best[0], best[1])


class UsbfsDevice:
    """A panel opened through usbfs, the way libusb would open it."""

    def __init__(self, bus: int, device: int, interface: int, endpoint: int) -> None:
        self.path = Path(f"/dev/bus/usb/{bus:03d}/{device:03d}")
        self.interface = interface
        self.endpoint = endpoint
        self._fd: int | None = None

    def open(self) -> None:
        """Open the device node, detach the kernel driver and claim the interface.

        Detaching is the step hidraw cannot offer. While usbhid owns the
        interface, every transfer is subject to HID report rules; once it is
        detached, the endpoint takes whatever we send it.
        """
        if fcntl is None:
            msg = "usbfs is Linux-only"
            raise OSError(msg)
        self._fd = os.open(self.path, os.O_RDWR)

        # Ask the kernel to let go. Harmless if nothing was bound, which is why
        # the failure is ignored rather than reported.
        disconnect = _DevfsIoctl(self.interface, USBDEVFS_DISCONNECT, None)
        with contextlib.suppress(OSError):
            fcntl.ioctl(self._fd, USBDEVFS_IOCTL, disconnect, True)

        claim = ctypes.c_uint(self.interface)
        fcntl.ioctl(self._fd, USBDEVFS_CLAIMINTERFACE, claim, True)

    def write(self, payload: bytes, timeout_ms: int = 2000) -> int:
        """Send one transfer to the OUT endpoint. Returns bytes accepted."""
        if self._fd is None:
            msg = "device is not open"
            raise OSError(msg)
        buffer = ctypes.create_string_buffer(payload, len(payload))
        transfer = _BulkTransfer(
            ep=self.endpoint,
            len=len(payload),
            timeout=timeout_ms,
            data=ctypes.cast(buffer, ctypes.c_void_p),
        )
        return fcntl.ioctl(self._fd, USBDEVFS_BULK, transfer, True)

    def close(self) -> None:
        """Release the interface and close the node."""
        if self._fd is None:
            return
        release = ctypes.c_uint(self.interface)
        with contextlib.suppress(OSError):
            fcntl.ioctl(self._fd, USBDEVFS_RELEASEINTERFACE, release, True)
        os.close(self._fd)
        self._fd = None


def send_usbfs(packets: list[bytes], init_delay: float, chunk_delay: float) -> str | None:
    """Write every packet over usbfs. Returns None on success, else a reason."""
    located = find_usb_device()
    if located is None:
        return "panel not found on the USB bus"
    bus, devnum, sysfs = located

    target = find_output_endpoint(usb_interfaces(sysfs))
    if target is None:
        return "no interface publishes an OUT endpoint"
    interface, endpoint = target

    device = UsbfsDevice(bus, devnum, interface, endpoint)
    try:
        device.open()
    except OSError as exc:
        return f"could not claim interface {interface}: {exc}"

    try:
        if init_delay > 0:
            time.sleep(init_delay)
        for index, packet in enumerate(packets):
            # The report-ID byte is a hidraw convention. On the wire the
            # device expects the signature first, so it is dropped here.
            payload = packet[1:]
            written = device.write(payload)
            if written != len(payload):
                return f"short transfer on packet {index}: {written} of {len(payload)}"
            if chunk_delay > 0:
                time.sleep(chunk_delay)
    except OSError as exc:
        return f"transfer failed: {exc}"
    finally:
        device.close()

    return None


def describe_usb() -> int:
    """Print the panel's USB topology. Returns a process exit status."""
    located = find_usb_device()
    if located is None:
        print("panel not found on the USB bus", file=sys.stderr)
        return 1
    bus, devnum, sysfs = located

    print("")
    print(f"USB device {VENDOR_ID:04X}:{PRODUCT_ID:04X} at {sysfs.name}")
    print(f"  node: /dev/bus/usb/{bus:03d}/{devnum:03d}")
    print("")

    interfaces = usb_interfaces(sysfs)
    kinds = {
        0: "control",
        1: "isochronous",
        _TRANSFER_BULK: "bulk",
        _TRANSFER_INTERRUPT: "interrupt",
    }
    for interface in interfaces:
        driver = interface["driver"] or "(none)"
        print(f"  interface {interface['number']}  class {interface['class']}  driver {driver}")
        endpoints = interface["endpoints"]
        assert isinstance(endpoints, list)
        if not endpoints:
            print("      no endpoints")
        for endpoint in endpoints:
            kind = kinds.get(int(endpoint["kind"]), "?")
            print(
                f"      ep 0x{int(endpoint['address']):02x} {endpoint['direction']:<3} "
                f"{kind:<9} {endpoint['packet_size']} bytes"
            )
        print("")

    target = find_output_endpoint(interfaces)
    if target is None:
        print("No OUT endpoint anywhere. This panel cannot be written to over USB")
        print("in any obvious way, which would be a genuinely surprising result.")
        return 1

    interface, endpoint = target
    print(f"frames should go to interface {interface}, endpoint 0x{endpoint:02x}")
    print("run with --transport usbfs to try exactly that")
    return 0


# -- Sweep ------------------------------------------------------------------

# Each entry is (name, framing, send_init, seq_first, chunk_delay, colour).
#
# The command-sequence question is closed: all eight sequences were accepted and
# none drew. The descriptor then showed why nothing could have worked -- this
# interface declares 64-byte output reports, and every attempt so far has been a
# single 4104-byte write. So these vary the *transport framing* instead, which is
# the layer the evidence now points at.
SWEEP = (
    ("64-byte reports, zero report id", "reports", True, False, 0.002, "red"),
    ("64-byte reports, no report id", "reports-bare", True, False, 0.002, "green"),
    ("one 4104-byte write, report id removed", "no-report-id", True, False, 0.002, "blue"),
    ("64-byte reports, no init commands", "reports", False, False, 0.002, "yellow"),
    ("64-byte reports, seq/phase swapped", "reports", True, True, 0.002, "cyan"),
    ("64-byte reports, unpaced", "reports", True, False, 0.0, "magenta"),
    ("4104-byte write, no init", "no-report-id", False, False, 0.002, "orange"),
    ("one 4105-byte write (what already failed)", "whole", True, False, 0.002, "purple"),
)


def sweep(target: Path, init_delay: float, settle: float) -> int:
    """Try every plausible command sequence, one per colour.

    Nothing here is a guess about what is *correct* -- it is eight readings of
    ambiguous documentation, offered to the only authority that can settle it.
    Whatever colour the panel is showing at the end identifies a sequence that
    works; the mapping is printed so the answer needs no interpretation.
    """
    print("")
    print(f"sweeping {len(SWEEP)} command sequences on {target}")
    print("each paints a different flat colour -- note the colour left on the panel")
    print("")

    accepted = []
    for number, (name, framing, init, seq_first, chunk_delay, colour) in enumerate(SWEEP, start=1):
        frame = build_frame(colour)
        packets = []
        if init:
            packets.append(build_orientation())
            packets.append(build_heartbeat())
        builder = build_packet_seq_first if seq_first else build_packet
        packets += [builder(frame, index) for index in range(CHUNK_COUNT)]

        print(f"{number}. {colour:<8} {name}")
        problem = send(target, packets, init_delay, chunk_delay, framing)
        if problem is not None:
            print(f"     rejected: {problem}")
        else:
            accepted.append((number, colour, name))
        # Let the panel settle, and leave the colour visible long enough that a
        # brief flash is not mistaken for nothing happening.
        time.sleep(settle)

    print("")
    if not accepted:
        print("every sequence was rejected at the transport level")
        return 1

    print("all of these were accepted without error:")
    for number, colour, name in accepted:
        print(f"  {number}. {colour:<8} {name}")
    print("")
    print("Look at the panel now. Whatever colour it shows is the LAST sequence")
    print("that actually drew. If it is still unchanged, none of them worked and")
    print("the fault is in the packet contents rather than the sequence.")
    return 0


# -- Entry point ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pattern",
        choices=("bars", "white", "gradient", "black", *SOLIDS),
        default="bars",
        help="What to draw (default: bars).",
    )
    parser.add_argument(
        "--transport",
        choices=("usbfs", "hidraw"),
        default="usbfs",
        help=(
            "How to reach the panel. 'usbfs' claims the interface and writes "
            "to its endpoint, which is what libusb does and what the working "
            "implementations for this hardware use (default). 'hidraw' is the "
            "kernel HID path, which this panel does not answer on."
        ),
    )
    parser.add_argument(
        "--usb",
        action="store_true",
        help="Print the panel's USB interfaces and endpoints, and exit.",
    )
    parser.add_argument(
        "--descriptors",
        action="store_true",
        help=(
            "Print what each interface declares and exit. Run this when the "
            "panel accepts writes but does not change."
        ),
    )
    parser.add_argument(
        "--sweep",
        action="store_true",
        help=(
            "Try every plausible command sequence, each in a different colour. "
            "Use this when the panel accepts frames but does not change."
        ),
    )
    parser.add_argument(
        "--settle",
        type=float,
        default=2.0,
        help="Seconds to leave each sweep colour on screen (default: 2.0).",
    )
    parser.add_argument(
        "--chunk-delay",
        type=float,
        default=0.002,
        help="Seconds between chunk writes (default: 0.002).",
    )
    parser.add_argument(
        "--framing",
        choices=sorted(FRAMINGS),
        default="reports",
        help=(
            "How each packet reaches the device. 'reports' matches what the "
            "interface declares -- 64-byte reports (default)."
        ),
    )
    parser.add_argument(
        "--init",
        choices=("full", "orientation", "heartbeat", "none"),
        default="full",
        help=(
            "Which commands to send before the frame (default: full, meaning "
            "orientation then heartbeat)."
        ),
    )
    parser.add_argument(
        "--node",
        default=None,
        help="Write to this /dev/hidrawN node instead of trying each in turn.",
    )
    parser.add_argument(
        "--init-delay",
        type=float,
        default=DEFAULT_INIT_DELAY,
        help=(
            f"Seconds to wait after opening before writing (default: "
            f"{DEFAULT_INIT_DELAY}). The panel ignores anything sent before it "
            "has initialised."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the packets and report, without writing.",
    )
    return parser


def diagnostics_only(args: argparse.Namespace) -> int | None:
    """Run a read-only diagnostic if one was asked for, else return None."""
    if args.usb:
        return describe_usb()
    if args.descriptors:
        return describe_nodes()
    return None


def main() -> int:
    args = build_parser().parse_args()

    print(f"looking for {VENDOR_ID:04X}:{PRODUCT_ID:04X}")
    nodes = find_nodes()
    for path, interface in nodes:
        marker = " <- display" if interface == LCD_INTERFACE else ""
        print(f"  {path} if{interface}{marker}")

    diagnostic = diagnostics_only(args)
    if diagnostic is not None:
        return diagnostic

    targets = [Path(args.node)] if args.node is not None else candidate_order(nodes)

    print(f"building a {args.pattern} frame ({FRAME_BYTES} bytes)")
    frame = build_frame(args.pattern)
    packets: list[bytes] = []
    if args.init in ("full", "orientation"):
        packets.append(build_orientation())
    if args.init in ("full", "heartbeat"):
        packets.append(build_heartbeat())
    packets += build_packets(frame)
    print(f"{len(packets)} packets of {PACKET_SIZE} bytes (init: {args.init})")

    if args.dry_run:
        print(f"dry run: would try {', '.join(str(path) for path in targets) or '(nothing)'}")
        return 0

    if not targets:
        print("no HT32 panel found on this machine", file=sys.stderr)
        print(
            "check that the panel is attached, that this container was given "
            "the device nodes, and that you can read /dev/hidraw*",
            file=sys.stderr,
        )
        return 1

    if args.transport == "usbfs":
        return run_usbfs(args, packets)

    if args.sweep:
        return run_sweep(args, nodes)

    return run_once(args, targets, packets)


def run_sweep(args: argparse.Namespace, nodes: list[tuple[Path, int]]) -> int:
    """Sweep framings on the node that can actually receive them."""
    # Choose by what the interface declares, not by whether a write returned
    # success: an input-only interface accepts writes and cannot possibly be
    # the display, which is the trap that cost two earlier rounds.
    target = writable_node(nodes)
    if target is None:
        print("no interface declares an output report; nothing to sweep", file=sys.stderr)
        return 1
    return sweep(target, args.init_delay, args.settle)


def run_usbfs(args: argparse.Namespace, packets: list[bytes]) -> int:
    """Send one frame over usbfs. Returns a process exit status."""
    print("sending over usbfs (claiming the interface, as libusb does)")
    problem = send_usbfs(packets, args.init_delay, args.chunk_delay)
    if problem is not None:
        print(f"  {problem}", file=sys.stderr)
        print("", file=sys.stderr)
        print("Permission denied here means the container cannot write to", file=sys.stderr)
        print("/dev/bus/usb -- Protection Mode off, or a privileged add-on.", file=sys.stderr)
        return 1

    print(f"wrote {len(packets)} packets over usbfs")
    if args.pattern == "bars":
        print("")
        print("Now look at the panel. Left to right, the bars should be:")
        print("  " + ", ".join(name for name, _ in BARS))
    return 0


def run_once(
    args: argparse.Namespace,
    targets: list[Path],
    packets: list[bytes],
) -> int:
    """Send one frame to the first node that accepts it."""
    target = None
    for candidate in targets:
        print(f"trying {candidate} (waiting {args.init_delay}s for the panel to wake)")
        problem = send(candidate, packets, args.init_delay, args.chunk_delay, args.framing)
        if problem is None:
            target = candidate
            break
        print(f"  {problem}")

    if target is None:
        print("", file=sys.stderr)
        print("no node accepted the frame", file=sys.stderr)
        print(
            "ETIMEDOUT on every node usually means the report size is wrong for "
            "that interface -- run with --descriptors to see what each one "
            "actually declares.",
            file=sys.stderr,
        )
        return 1

    print(f"wrote {len(packets)} packets to {target}")
    if args.pattern == "bars":
        print("")
        print("Now look at the panel. Left to right, the bars should be:")
        print("  " + ", ".join(name for name, _ in BARS))
        print("")
        print("If the leftmost bar is not RED, the byte order is wrong.")
        print("If the panel is unchanged, run with --descriptors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
