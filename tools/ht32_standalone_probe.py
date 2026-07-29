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
import os
import sys
from pathlib import Path

VENDOR_ID = 0x04D9
PRODUCT_ID = 0xFD01
LCD_INTERFACE = 1

PANEL_WIDTH = 320
PANEL_HEIGHT = 170

SIGNATURE = 0x55
COMMAND_REDRAW = 0xA3
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


# -- Writing ----------------------------------------------------------------


def send(target: Path, packets: list[bytes]) -> int:
    """Write every packet to ``target``. Returns a process exit status.

    A short write means the report was rejected, which would leave the panel
    holding a truncated frame -- worth failing loudly rather than continuing
    and blaming the picture.
    """
    try:
        fd = os.open(target, os.O_WRONLY)
    except OSError as exc:
        print(f"could not open {target}: {exc}", file=sys.stderr)
        return 1

    try:
        for index, packet in enumerate(packets):
            written = os.write(fd, packet)
            if written != len(packet):
                print(f"short write on packet {index}: {written} bytes", file=sys.stderr)
                return 1
    except OSError as exc:
        print(f"write failed: {exc}", file=sys.stderr)
        return 1
    finally:
        os.close(fd)

    return 0


# -- Entry point ------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pattern",
        choices=("bars", "white", "gradient", "black"),
        default="bars",
        help="What to draw (default: bars).",
    )
    parser.add_argument("--node", default=None, help="Write to this /dev/hidrawN node.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build the packets and report, without writing.",
    )
    args = parser.parse_args()

    print(f"looking for {VENDOR_ID:04X}:{PRODUCT_ID:04X}")
    nodes = find_nodes()
    for path, interface in nodes:
        marker = " <- display" if interface == LCD_INTERFACE else ""
        print(f"  {path} if{interface}{marker}")

    if args.node is not None:
        target = Path(args.node)
    else:
        target = choose_node(nodes)
        if target is None and not args.dry_run:
            print("no HT32 panel found on this machine", file=sys.stderr)
            print(
                "check that the panel is attached, that this container was given "
                "the device nodes, and that you can read /dev/hidraw*",
                file=sys.stderr,
            )
            return 1

    print(f"building a {args.pattern} frame ({FRAME_BYTES} bytes)")
    packets = build_packets(build_frame(args.pattern))
    print(f"{len(packets)} packets of {PACKET_SIZE} bytes")

    if args.dry_run:
        print("dry run: nothing written")
        return 0

    if target is None:
        print("no HT32 panel found on this machine", file=sys.stderr)
        return 1

    status = send(target, packets)
    if status != 0:
        return status

    print(f"wrote {len(packets)} packets to {target}")
    if args.pattern == "bars":
        print("")
        print("Now look at the panel. Left to right, the bars should be:")
        print("  " + ", ".join(name for name, _ in BARS))
        print("")
        print("If the leftmost bar is not RED, the byte order is wrong.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
