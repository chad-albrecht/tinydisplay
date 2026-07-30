"""Command-line bring-up tool: ``python -m tinydisplay.ht32 probe``.

Deliberately thin -- everything below argument parsing lives in the package,
where it is testable without a panel.

This exists for one situation: a headless machine with the panel soldered into
it, where the only way to find out whether the driver works is to try. The
subcommands escalate:

- ``probe`` touches nothing. It reports what the USB bus says.
- ``frame`` draws a test pattern chosen to make specific failures obvious.
- ``led`` drives the separate CH340 bridge.

Every subcommand takes ``--dry-run``, which swaps the transport for a recorder
and reports what would have been written. That is not a simulation of the
protocol; it is the protocol, with the write removed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import TYPE_CHECKING

from tinydisplay.ht32.device import (
    HT32DeviceInfo,
    enumerate_panels,
    is_hid_available,
    select_display_interface,
)
from tinydisplay.ht32.driver import HT32Driver
from tinydisplay.ht32.errors import HT32Error
from tinydisplay.ht32.hidraw import (
    HidrawDeviceInfo,
    HidrawTransport,
    enumerate_hidraw,
    is_hidraw_available,
    select_display_node,
)
from tinydisplay.ht32.led import (
    DEFAULT_HOLD_HZ,
    HELD_COLOURS,
    LedController,
    LedTheme,
    RecordingLedTransport,
)
from tinydisplay.ht32.patterns import PATTERNS, draw_pattern
from tinydisplay.ht32.protocol import CHUNK_COUNT, PACKET_SIZE, PRODUCT_ID, VENDOR_ID
from tinydisplay.ht32.transport import HidTransport, RecordingHidTransport

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["build_parser", "main"]

DEFAULT_PATTERN = "bars"

#: How long ``frame`` holds its pattern on screen, sending keep-alives. Long
#: enough to walk round the machine and look at it; short enough that the
#: command still ends on its own.
DEFAULT_HOLD_SECONDS = 20


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="tinydisplay-ht32",
        description="Bring-up and diagnostics for the HT32 panel.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe = subparsers.add_parser(
        "probe",
        help="Report what the USB bus says about attached panels.",
        description=(
            "Enumerates HID interfaces without opening anything. Run this "
            "first: if the panel is not listed here, nothing else will work."
        ),
    )
    probe.add_argument(
        "--open",
        action="store_true",
        help="Also try to open the panel, which is where permissions fail.",
    )

    frame = subparsers.add_parser(
        "frame",
        help="Draw a test pattern on the panel.",
        description=(
            "Patterns are chosen so that failures look specific: 'bars' "
            "catches byte order, 'gradient' catches stride, 'chunks' catches "
            "framing."
        ),
    )
    frame.add_argument(
        "--pattern",
        choices=sorted(PATTERNS),
        default=DEFAULT_PATTERN,
        help=f"Which pattern to draw (default: {DEFAULT_PATTERN}).",
    )
    frame.add_argument(
        "--repeat",
        type=int,
        default=1,
        help="Send the frame this many times, to check the panel keeps accepting.",
    )
    frame.add_argument("--serial", default=None, help="Restrict to a panel with this serial.")
    frame.add_argument(
        "--hidraw",
        default=None,
        metavar="PATH",
        help=(
            "Write straight to this /dev/hidrawN node, skipping discovery and "
            "needing no USB library. Useful when the panel is found but not opened."
        ),
    )
    frame.add_argument(
        "--dry-run",
        action="store_true",
        help="Write to a recorder instead of the panel.",
    )
    frame.add_argument(
        "--hold",
        type=int,
        default=DEFAULT_HOLD_SECONDS,
        help=(
            "Seconds to hold the frame on screen, sending keep-alives. "
            "Without these the panel paints its disconnection banner over the "
            f"pattern about a second after we stop (default: {DEFAULT_HOLD_SECONDS})."
        ),
    )

    led = subparsers.add_parser(
        "led",
        help="Set the LED strip's effect over the CH340 bridge.",
    )
    led.add_argument(
        "--theme",
        choices=[theme.name.lower() for theme in LedTheme],
        default="rainbow",
        help="Lighting effect (default: rainbow).",
    )
    led.add_argument("--intensity", type=int, default=3, help="Brightness, 1-5 (default: 3).")
    led.add_argument("--speed", type=int, default=3, help="Effect speed, 1-5 (default: 3).")
    led.add_argument("--port", default=None, help="Serial port; defaults to discovery.")
    led.add_argument(
        "--hold",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help=(
            "Hold the effect at its first frame for this long, which is how a "
            "solid colour is made: 'colors' holds red and 'rainbow' holds "
            "blue-purple. Saturates the serial link while it runs, and the "
            "strip animates again the moment it stops."
        ),
    )
    led.add_argument(
        "--dry-run",
        action="store_true",
        help="Write to a recorder instead of the bridge.",
    )
    return parser


def _report(line: str) -> None:
    """Write one line of human-facing output."""
    print(line)  # noqa: T201


def run_probe(*, try_open: bool) -> int:
    """Report what is on the bus. Returns a process exit status."""
    _report(f"looking for {VENDOR_ID:04X}:{PRODUCT_ID:04X}")

    # hidraw first: it needs no USB library, so on Linux it is both the more
    # likely route to work and the cheaper one to check.
    hidraw_nodes = enumerate_hidraw() if is_hidraw_available() else ()
    if hidraw_nodes:
        _report(f"hidraw:  {len(hidraw_nodes)} node(s)")
        for node in hidraw_nodes:
            marker = " <- display" if node.is_display_interface else ""
            _report(f"  {node.path} if{node.interface_number}{marker}")
    elif is_hidraw_available():
        _report("hidraw:  available, but no node reports this hardware ID")
    else:
        _report("hidraw:  not available (not Linux, or no device nodes here)")

    if not is_hid_available():
        _report("hidapi:  NOT INSTALLED -- install tinydisplay-ht32[hid]")
        if not hidraw_nodes:
            return 2
        # hidraw alone is enough to drive the panel, so this is not fatal.
        return _probe_hidraw(hidraw_nodes, try_open=try_open)

    _report("hidapi:  available")
    return _probe_hidapi(try_open=try_open)


def _probe_hidapi(*, try_open: bool) -> int:
    """Report what hidapi enumeration finds."""
    try:
        devices = enumerate_panels()
    except HT32Error as exc:
        _report(f"error: {exc}")
        return 2

    if not devices:
        _report("panel:   NOT FOUND")
        _report("")
        _report("Nothing matching the panel's hardware ID is on the bus. Check that")
        _report("it is attached, and on Linux that a udev rule grants access to the")
        _report("hidraw node -- an unreadable device does not appear here at all.")
        return 1

    _report(f"panel:   {len(devices)} interface(s)")
    for device in devices:
        marker = " <- display" if device.is_display_interface else ""
        serial = f" serial={device.serial_number}" if device.serial_number else ""
        _report(f"  if{device.interface_number:<3} {device.path!r}{serial}{marker}")

    selected = select_display_interface(devices)
    if selected is None:  # pragma: no cover - unreachable while devices is non-empty
        return 1
    _report(f"chosen:  interface {selected.interface_number}")

    if not try_open:
        _report("")
        _report("Re-run with --open to check permissions as well.")
        return 0

    return _try_open(selected)


def _probe_hidraw(nodes: Sequence[HidrawDeviceInfo], *, try_open: bool) -> int:
    """Report on the hidraw route when hidapi is unavailable."""
    chosen = select_display_node(nodes)
    if chosen is None:  # pragma: no cover - unreachable while nodes is non-empty
        return 1
    _report(f"chosen:  {chosen.path} (hidraw needs no USB library)")

    if not try_open:
        _report("")
        _report("Re-run with --open to check permissions as well.")
        return 0

    transport = HidrawTransport(device=chosen)
    try:
        transport.open()
    except HT32Error as exc:
        _report(f"open:    FAILED -- {exc}")
        return 1
    transport.close()
    _report("open:    ok")
    return 0


def _try_open(device: HT32DeviceInfo) -> int:
    """Open the chosen interface, which is where permissions fail."""
    transport = HidTransport(device=device)
    try:
        transport.open()
    except HT32Error as exc:
        _report(f"open:    FAILED -- {exc}")
        return 1
    transport.close()
    _report("open:    ok")
    return 0


async def run_frame(
    *,
    pattern: str,
    repeat: int,
    serial: str | None,
    dry_run: bool,
    hidraw: str | None = None,
    hold: int = DEFAULT_HOLD_SECONDS,
) -> int:
    """Draw a pattern on the panel. Returns a process exit status."""
    transport: RecordingHidTransport | HidrawTransport | None
    if dry_run:
        transport = RecordingHidTransport(max_packets=CHUNK_COUNT)
    elif hidraw is not None:
        transport = HidrawTransport(path=hidraw)
    else:
        transport = None

    recorder = transport if isinstance(transport, RecordingHidTransport) else None
    driver = HT32Driver(transport=transport, serial_number=serial)

    try:
        async with driver:
            # The panel starts every session believing the host is gone -- that
            # is what its disconnection banner is -- and paints that banner
            # back over the frame about a second after the last keep-alive.
            # A diagnostic that draws once and exits therefore shows a defaced
            # screen on perfectly working hardware, which is exactly the sort
            # of false negative a bring-up tool must not produce. So: introduce
            # ourselves first, then hold the frame with keep-alives long enough
            # to be looked at.
            await driver.heartbeat()
            canvas = driver.create_canvas()
            draw_pattern(canvas, pattern)
            for _ in range(max(1, repeat)):
                await driver.show(canvas)
            # Nothing reaches a panel in a dry run, so there is nothing to hold
            # on screen and no reason to make the caller wait for it.
            for _ in range(0 if dry_run else max(0, hold)):
                await asyncio.sleep(1.0)
                await driver.heartbeat()
    except HT32Error as exc:
        _report(f"error: {exc}")
        return 1

    if recorder is not None:
        # Frame packets and keep-alives are counted apart, because "28 packets"
        # for a 27-chunk frame reads like an arithmetic error otherwise.
        frame_packets = recorder.write_count - driver.heartbeat_count
        _report(
            f"dry run: {driver.frame_count} frame(s), "
            f"{frame_packets} packets of {PACKET_SIZE} bytes, "
            f"plus {driver.heartbeat_count} keep-alive(s), nothing sent"
        )
    else:
        _report(f"sent {driver.frame_count} frame(s) of pattern {pattern!r}")
        _report(f"retries: {driver.failure_count} failed write(s)")
        _report("")
        _report("Now look at the panel. What you should see:")
        _report(_expectation(pattern))
    return 0


def _expectation(pattern: str) -> str:
    """What a correct panel shows for ``pattern``."""
    return {
        "bars": (
            "  Eight vertical bars, each matching its label. If the bar labelled\n"
            "  'red' is not red, the RGB565 byte order is wrong.\n"
            "  This pattern cannot tell you the image is upside down -- use\n"
            "  'corners' for that."
        ),
        "corners": (
            "  Four coloured corners and a line of text. Turn your head until the\n"
            "  words read normally, then find the red block:\n"
            "    top left     -- correct\n"
            "    bottom right -- rotated half a turn\n"
            "    bottom left  -- rows reversed\n"
            "    top right    -- columns reversed"
        ),
        "gradient": (
            "  Two smooth ramps, cyan-to-pink on top and black-to-white below.\n"
            "  A diagonal shear means the row stride is wrong."
        ),
        "chunks": (
            f"  {CHUNK_COUNT} alternating horizontal bands, one per HID packet.\n"
            "  A band in the wrong place identifies the packet that went astray."
        ),
        "solid": "  A flat white screen.",
        "black": "  A dark screen -- which is also what a panel that ignored us shows.",
    }.get(pattern, "  The pattern you asked for.")


async def run_led(
    *,
    theme: str,
    intensity: int,
    speed: int,
    port: str | None,
    dry_run: bool,
    hold: float = 0.0,
) -> int:
    """Set the LED effect. Returns a process exit status."""
    recorder = RecordingLedTransport() if dry_run else None
    controller = LedController(transport=recorder, port=port)
    chosen = LedTheme[theme.upper()]

    try:
        async with controller as leds:
            if hold > 0:
                writes = await leds.hold_theme(
                    chosen,
                    intensity=intensity,
                    speed=speed,
                    max_writes=max(1, round(hold * DEFAULT_HOLD_HZ)),
                )
                _report(f"held {theme} for {hold:g}s ({writes} restarts)")
                held = HELD_COLOURS.get(chosen)
                if held is None:
                    _report(f"  note: {theme} has no steady first frame; expect animation")
                else:
                    _report(f"  the strip should have shown a steady {held}")
                return 0
            await leds.set_theme(chosen, intensity=intensity, speed=speed)
    except HT32Error as exc:
        _report(f"error: {exc}")
        return 1

    if recorder is not None and recorder.last_packet is not None:
        packet = " ".join(f"{byte:02x}" for byte in recorder.last_packet)
        _report(f"dry run: would send {packet}")
    else:
        _report(f"set LEDs to {theme} (intensity {intensity}, speed {speed})")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the bring-up tool. Returns a process exit status."""
    args = build_parser().parse_args(argv)

    try:
        if args.command == "probe":
            return run_probe(try_open=args.open)
        if args.command == "frame":
            return asyncio.run(
                run_frame(
                    pattern=args.pattern,
                    repeat=args.repeat,
                    serial=args.serial,
                    dry_run=args.dry_run,
                    hidraw=args.hidraw,
                    hold=args.hold,
                )
            )
        return asyncio.run(
            run_led(
                theme=args.theme,
                intensity=args.intensity,
                speed=args.speed,
                port=args.port,
                dry_run=args.dry_run,
                hold=args.hold,
            )
        )
    except HT32Error as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
