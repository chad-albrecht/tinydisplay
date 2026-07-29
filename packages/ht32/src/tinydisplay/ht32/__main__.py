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
from tinydisplay.ht32.led import LedController, LedTheme, RecordingLedTransport
from tinydisplay.ht32.patterns import PATTERNS, draw_pattern
from tinydisplay.ht32.protocol import CHUNK_COUNT, PACKET_SIZE, PRODUCT_ID, VENDOR_ID
from tinydisplay.ht32.transport import HidTransport, RecordingHidTransport

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["build_parser", "main"]

DEFAULT_PATTERN = "bars"


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
        "--dry-run",
        action="store_true",
        help="Write to a recorder instead of the panel.",
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

    if not is_hid_available():
        _report("hidapi:  NOT INSTALLED -- install tinydisplay-ht32[hid]")
        return 2
    _report("hidapi:  available")

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


async def run_frame(*, pattern: str, repeat: int, serial: str | None, dry_run: bool) -> int:
    """Draw a pattern on the panel. Returns a process exit status."""
    recorder = RecordingHidTransport(max_packets=CHUNK_COUNT) if dry_run else None
    driver = HT32Driver(transport=recorder, serial_number=serial)

    try:
        async with driver:
            canvas = driver.create_canvas()
            draw_pattern(canvas, pattern)
            for _ in range(max(1, repeat)):
                await driver.show(canvas)
    except HT32Error as exc:
        _report(f"error: {exc}")
        return 1

    if recorder is not None:
        _report(
            f"dry run: {driver.frame_count} frame(s), "
            f"{recorder.write_count} packets of {PACKET_SIZE} bytes, nothing sent"
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
            "  'red' is not red, the RGB565 byte order is wrong."
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
) -> int:
    """Set the LED effect. Returns a process exit status."""
    recorder = RecordingLedTransport() if dry_run else None
    controller = LedController(transport=recorder, port=port)

    try:
        async with controller as leds:
            await leds.set_theme(LedTheme[theme.upper()], intensity=intensity, speed=speed)
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
                )
            )
        return asyncio.run(
            run_led(
                theme=args.theme,
                intensity=args.intensity,
                speed=args.speed,
                port=args.port,
                dry_run=args.dry_run,
            )
        )
    except HT32Error as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
