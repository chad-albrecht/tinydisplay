"""Push frames to an HT32 panel.

Run it against real hardware::

    python examples/ht32_panel.py

Or with nothing attached, which writes to a recorder and prints what would
have gone out on the wire::

    python examples/ht32_panel.py --dry-run

The dry run is not a simulation of the protocol -- it is the protocol, with the
USB write replaced. The packets counted below are byte-for-byte the ones a
panel would receive, which is the whole point of putting the transport behind
an interface.

The same ``render`` function drives the simulator, because both are ordinary
``DisplayDriver`` implementations::

    python -m tinydisplay.simulator examples/simulator_dashboard.py
"""

from __future__ import annotations

import argparse
import asyncio

from tinydisplay.core import Canvas, Color, Font, HorizontalAlign
from tinydisplay.ht32 import (
    CHUNK_COUNT,
    PANEL_HEIGHT,
    PANEL_WIDTH,
    HT32Driver,
    HT32Error,
    RecordingHidTransport,
)

BACKGROUND = Color.from_hex("#0d1b2a")
ACCENT = Color.from_hex("#00b4d8")
TEXT = Color.from_hex("#e0e1dd")

MARGIN = 8


def render(canvas: Canvas) -> None:
    """Draw one frame."""
    canvas.clear(BACKGROUND)

    canvas.text(MARGIN, MARGIN, "TinyDisplay", TEXT, font=Font.default(18))
    canvas.text(
        canvas.width - MARGIN,
        MARGIN,
        f"{canvas.width}x{canvas.height}",
        ACCENT,
        font=Font.default(12),
        align=HorizontalAlign.RIGHT,
    )

    # A gradient makes RGB565 quantisation visible on the panel itself, which
    # is the quickest way to confirm the byte order is right: get it wrong and
    # this reads as noise rather than a smooth sweep.
    top = 48
    height = canvas.height - top - MARGIN
    for x in range(MARGIN, canvas.width - MARGIN):
        position = (x - MARGIN) / max(1, canvas.width - 2 * MARGIN - 1)
        canvas.rect(x, top, 1, height, ACCENT.lerp(Color.from_hex("#f72585"), position))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="write to a recorder instead of a panel",
    )
    args = parser.parse_args()

    transport = RecordingHidTransport() if args.dry_run else None
    driver = HT32Driver(transport=transport)

    try:
        async with driver:
            canvas = driver.create_canvas()
            render(canvas)
            await driver.show(canvas)
    except HT32Error as exc:
        # An absent panel is the normal failure here, and the message already
        # explains what to check, so there is nothing to add to it.
        print(f"error: {exc}")
        return 1

    if transport is not None:
        written = sum(len(packet) for packet in transport.packets)
        print(f"dry run: {len(transport.packets)} packets ({written} bytes) not sent")
        print(f"a {PANEL_WIDTH}x{PANEL_HEIGHT} frame is {CHUNK_COUNT} packets")
    else:
        print(f"sent {driver.frame_count} frame to the panel")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
