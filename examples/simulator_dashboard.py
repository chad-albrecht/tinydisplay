"""A hot-reloadable dashboard for the simulator.

Run it with::

    python -m tinydisplay.simulator examples/simulator_dashboard.py

Then edit this file with the window still open. Every save is picked up on the
next frame, so changing a colour below and hitting save updates the panel
immediately. Introducing a typo is safe: the error is painted onto the panel
and the last working version keeps rendering until the file parses again.

The geometry defaults to the HT32's 320x170. Nothing here assumes that, though
-- ``render`` is handed whatever canvas the driver was configured with, so the
same file previews on any panel size.
"""

from __future__ import annotations

import math
import time

from tinydisplay.core import Canvas, Color, Font, HorizontalAlign, VerticalAlign

BACKGROUND = Color.from_hex("#0d1b2a")
PANEL = Color.from_hex("#1b263b")
ACCENT = Color.from_hex("#00b4d8")
TEXT = Color.from_hex("#e0e1dd")
MUTED = Color.from_hex("#778da9")

MARGIN = 8
CORNER = 6


def render(canvas: Canvas) -> None:
    """Draw one frame. Called by the simulator at the configured frame rate."""
    canvas.clear(BACKGROUND)

    title_font = Font.default(16)
    body_font = Font.default(12)

    canvas.text(
        MARGIN,
        MARGIN,
        "TinyDisplay",
        TEXT,
        font=title_font,
    )
    canvas.text(
        canvas.width - MARGIN,
        MARGIN,
        time.strftime("%H:%M:%S"),
        ACCENT,
        font=title_font,
        align=HorizontalAlign.RIGHT,
    )

    _draw_gradient_strip(canvas)
    _draw_pulse(canvas, body_font)


def _draw_gradient_strip(canvas: Canvas) -> None:
    """A horizontal gradient -- the clearest way to see RGB565 banding.

    On a 24-bit preview this is smooth. Encoded as RGB565, which is what the
    simulator shows by default, it steps visibly. That difference is the whole
    argument for previewing the encoded frame rather than the canvas.
    """
    top = 34
    height = 18
    if canvas.height < top + height + MARGIN:
        return

    for x in range(MARGIN, canvas.width - MARGIN):
        position = (x - MARGIN) / max(1, canvas.width - 2 * MARGIN - 1)
        canvas.rect(x, top, 1, height, ACCENT.lerp(Color.from_hex("#f72585"), position))


def _draw_pulse(canvas: Canvas, font: Font) -> None:
    """A slow sine sweep, so it is obvious the loop is actually running."""
    top = 60
    height = canvas.height - top - MARGIN
    if height <= 0:
        return

    canvas.rounded_rect(
        MARGIN,
        top,
        canvas.width - 2 * MARGIN,
        height,
        PANEL,
        radius=CORNER,
    )

    phase = time.monotonic()
    fraction = (math.sin(phase) + 1) / 2
    bar_width = int((canvas.width - 2 * MARGIN - 2 * CORNER) * fraction)

    if bar_width > 0:
        canvas.rect(
            MARGIN + CORNER,
            top + height - 14,
            bar_width,
            6,
            ACCENT,
        )

    canvas.text(
        canvas.width // 2,
        top + height // 2 - 6,
        f"{fraction * 100:.0f}%",
        TEXT,
        font=font,
        align=HorizontalAlign.CENTER,
        valign=VerticalAlign.MIDDLE,
    )
    canvas.text(
        canvas.width // 2,
        top + 8,
        "edit me and save",
        MUTED,
        font=font,
        align=HorizontalAlign.CENTER,
    )
