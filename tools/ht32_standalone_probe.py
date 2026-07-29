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
import time
from pathlib import Path

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


def send(
    target: Path,
    packets: list[bytes],
    init_delay: float = DEFAULT_INIT_DELAY,
    chunk_delay: float = 0.0,
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

        for index, packet in enumerate(packets):
            written = os.write(fd, packet)
            if written != len(packet):
                return f"short write on packet {index}: {written} of {len(packet)} bytes"
            # The firmware is documented as wanting brief pauses between
            # transmissions; writing 27 reports flat out may overrun it.
            if chunk_delay > 0:
                time.sleep(chunk_delay)
    except OSError as exc:
        return f"write failed on packet {index}: {exc}"
    finally:
        os.close(fd)

    return None


# -- Sweep ------------------------------------------------------------------

# Each entry is (name, send_orientation, send_heartbeat, seq_first, chunk_delay)
# paired with the colour it paints. Ordered cheapest-hypothesis first: the
# leading suspicion is that the panel needs to be told it is host-driven before
# it will accept a frame.
SWEEP = (
    ("orientation + heartbeat", True, True, False, 0.002, "red"),
    ("orientation only", True, False, False, 0.002, "green"),
    ("heartbeat only", False, True, False, 0.002, "blue"),
    ("no init, paced writes", False, False, False, 0.002, "yellow"),
    ("orientation + heartbeat, seq/phase swapped", True, True, True, 0.002, "cyan"),
    ("seq/phase swapped, no init", False, False, True, 0.002, "magenta"),
    ("orientation + heartbeat, unpaced", True, True, False, 0.0, "orange"),
    ("no init, unpaced (what already failed)", False, False, False, 0.0, "purple"),
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
    for number, (name, orientation, heartbeat, seq_first, chunk_delay, colour) in enumerate(
        SWEEP, start=1
    ):
        frame = build_frame(colour)
        packets = []
        if orientation:
            packets.append(build_orientation())
        if heartbeat:
            packets.append(build_heartbeat())
        builder = build_packet_seq_first if seq_first else build_packet
        packets += [builder(frame, index) for index in range(CHUNK_COUNT)]

        print(f"{number}. {colour:<8} {name}")
        problem = send(target, packets, init_delay, chunk_delay)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pattern",
        choices=("bars", "white", "gradient", "black", *SOLIDS),
        default="bars",
        help="What to draw (default: bars).",
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
    args = parser.parse_args()

    print(f"looking for {VENDOR_ID:04X}:{PRODUCT_ID:04X}")
    nodes = find_nodes()
    for path, interface in nodes:
        marker = " <- display" if interface == LCD_INTERFACE else ""
        print(f"  {path} if{interface}{marker}")

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

    if args.sweep:
        # Sweeping needs a node that accepts writes; find one with the ordinary
        # path first so a sweep is never run against the wrong interface.
        for candidate in targets:
            if send(candidate, packets, args.init_delay, args.chunk_delay) is None:
                return sweep(candidate, args.init_delay, args.settle)
        print("no node accepted a frame, so there is nothing to sweep", file=sys.stderr)
        return 1

    target = None
    for candidate in targets:
        print(f"trying {candidate} (waiting {args.init_delay}s for the panel to wake)")
        problem = send(candidate, packets, args.init_delay, args.chunk_delay)
        if problem is None:
            target = candidate
            break
        print(f"  {problem}")

    if target is None:
        print("", file=sys.stderr)
        print("no node accepted the frame", file=sys.stderr)
        print(
            "ETIMEDOUT on every node usually means the packet framing is wrong "
            "rather than the device being absent -- the panel is there, it just "
            "did not answer.",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
