"""A live clock on an HT32 panel, with the heartbeat the firmware needs.

Run it against real hardware::

    python examples/ht32_clock.py
    python examples/ht32_clock.py --seconds 30      # stop after half a minute
    python examples/ht32_clock.py --dry-run         # no hardware needed

The point of this example is the loop rather than the drawing. A panel that is
sent one frame and then left alone does not simply sit there showing it: after
about a second the firmware decides the host has gone away and paints
"Disconnection, content information display will not be allowed!" across the
screen. So a dashboard has to keep saying hello, and
:func:`tinydisplay.ht32.run_panel` is what does it -- frames and keep-alives
from a single loop, so the two can never race for the USB endpoint.

Everything drawn below is ordinary ``tinydisplay-core``: the same ``render``
function works against the simulator.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import time

from tinydisplay.core import Canvas, Color, Font, HorizontalAlign, VerticalAlign
from tinydisplay.ht32 import HT32Driver, HT32Error, RecordingHidTransport, run_panel

BACKGROUND = Color.from_hex("#0d1b2a")
PANEL = Color.from_hex("#1b263b")
ACCENT = Color.from_hex("#00b4d8")
TEXT = Color.from_hex("#e0e1dd")
MUTED = Color.from_hex("#778da9")

MARGIN = 8
CORNER = 6


def render(canvas: Canvas) -> None:
    """Draw one frame: the time, the date, and a pulse that proves it is live."""
    canvas.clear(BACKGROUND)

    canvas.text(
        canvas.width // 2,
        MARGIN + 4,
        time.strftime("%H:%M:%S"),
        TEXT,
        font=Font.default(40),
        align=HorizontalAlign.CENTER,
    )
    canvas.text(
        canvas.width // 2,
        MARGIN + 54,
        time.strftime("%A %d %B"),
        MUTED,
        font=Font.default(14),
        align=HorizontalAlign.CENTER,
    )

    _draw_pulse(canvas)


def _draw_pulse(canvas: Canvas) -> None:
    """A sweeping bar.

    Worth having on a status panel: a frozen clock and a stopped render loop
    look identical for up to a second, but a stopped sweep is obvious
    immediately.
    """
    height = 18
    top = canvas.height - height - MARGIN
    width = canvas.width - 2 * MARGIN

    canvas.rounded_rect(MARGIN, top, width, height, PANEL, radius=CORNER)

    fraction = (math.sin(time.monotonic() * 2) + 1) / 2
    bar = int((width - 2 * CORNER) * fraction)
    if bar > 0:
        canvas.rect(MARGIN + CORNER, top + height // 2 - 2, bar, 4, ACCENT)

    canvas.text(
        canvas.width - MARGIN - CORNER,
        top + height // 2,
        f"{fraction * 100:3.0f}%",
        MUTED,
        font=Font.default(10),
        align=HorizontalAlign.RIGHT,
        valign=VerticalAlign.MIDDLE,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="Stop after this long. Runs until interrupted by default.",
    )
    parser.add_argument("--fps", type=float, default=5, help="Frame rate (default: 5).")
    parser.add_argument(
        "--portrait",
        action="store_true",
        help="Set portrait orientation instead of landscape.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Drive a recorder instead of a panel.",
    )
    args = parser.parse_args()

    transport = RecordingHidTransport(max_packets=1) if args.dry_run else None
    driver = HT32Driver(transport=transport)

    # max_frames is in frames, but a person thinks in seconds.
    max_frames = None if args.seconds is None else max(1, int(args.seconds * args.fps))

    try:
        frames = await run_panel(
            driver,
            render,
            fps=args.fps,
            max_frames=max_frames,
            landscape=not args.portrait,
        )
    except HT32Error as exc:
        # An absent panel is the ordinary failure, and the message already says
        # what to check.
        print(f"error: {exc}")
        return 1
    except KeyboardInterrupt:
        frames = driver.frame_count

    print(f"drew {frames} frames, sent {driver.heartbeat_count} keep-alives")
    if driver.failure_count:
        print(f"recovered from {driver.failure_count} failed write(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
