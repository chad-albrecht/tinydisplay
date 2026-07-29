"""A dashboard built from the widget library.

Run it in the simulator, editing while it is open::

    python -m tinydisplay.simulator examples/widget_dashboard.py

Or send it to a real panel::

    python examples/ht32_widget_dashboard.py

The point is that the layout is *described* rather than computed. There is no
arithmetic here positioning anything: a vertical stack splits the panel into a
header, a body and a footer; the body is a horizontal stack; the tiles are a
grid. Change the header's height and everything below moves.

The theme is quantised up front, so every colour in this file is a colour the
panel can actually produce. Ask for ``#4cc9f0`` on a 16-bit panel and you get
``#4ac8f0``; quantising means the value the widgets hold is the value on the
glass, and a contrast check means something.
"""

from __future__ import annotations

import math
import time

from tinydisplay.core import Canvas, HorizontalAlign, Rect, VerticalAlign
from tinydisplay.widgets import (
    MIDNIGHT,
    Axis,
    Gauge,
    Grid,
    Icon,
    IconName,
    Label,
    Padding,
    ProgressBar,
    Slot,
    Sparkline,
    Stack,
)

THEME = MIDNIGHT.quantized()

HEADER_HEIGHT = 34
FOOTER_HEIGHT = 26
GUTTER = 6

# The widgets that change are built once and kept, so a frame is a few
# attribute writes rather than a rebuilt tree.
_clock = Label(
    "",
    color=THEME.text,
    align=HorizontalAlign.RIGHT,
    valign=VerticalAlign.MIDDLE,
)
_cpu = Gauge(
    0,
    segments=12,
    color=THEME.accent,
    track_color=THEME.surface,
    warning_at=0.8,
    warning_color=THEME.warning,
)
_memory = ProgressBar(0, color=THEME.success, track_color=THEME.surface, radius=2)
_history = Sparkline([], color=THEME.accent, capacity=48)
_status = Label(
    "all systems nominal",
    color=THEME.muted,
    valign=VerticalAlign.MIDDLE,
)

_root: Stack | None = None


def build() -> Stack:
    """Assemble the widget tree once."""
    header = Stack(
        Axis.HORIZONTAL,
        slots=[
            Slot(
                Icon(IconName.BOLT, color=THEME.accent, thickness=2),
                size=HEADER_HEIGHT,
            ),
            Slot(
                Label(
                    "TinyDisplay",
                    color=THEME.text,
                    valign=VerticalAlign.MIDDLE,
                )
            ),
            Slot(_clock, size=110),
        ],
        spacing=GUTTER,
    )

    tiles = Grid(2, 1, spacing=GUTTER)
    tiles.place(_labelled("CPU", _cpu), row=0, column=0)
    tiles.place(_labelled("MEM", _memory), row=1, column=0)

    body = Stack(
        Axis.HORIZONTAL,
        slots=[Slot(tiles, weight=1), Slot(_framed(_history), weight=1)],
        spacing=GUTTER,
    )

    footer = Stack(
        Axis.HORIZONTAL,
        slots=[
            Slot(Icon(IconName.CHECK, color=THEME.success), size=FOOTER_HEIGHT),
            Slot(_status),
        ],
        spacing=GUTTER,
    )

    return Stack(
        Axis.VERTICAL,
        slots=[
            Slot(header, size=HEADER_HEIGHT),
            Slot(body),
            Slot(footer, size=FOOTER_HEIGHT),
        ],
        spacing=GUTTER,
    )


def _labelled(caption: str, indicator: Gauge | ProgressBar) -> Stack:
    """A caption above an indicator."""
    # In a vertical stack `size` is the height; the width comes from the
    # default STRETCH alignment, which is what an indicator wants.
    return Stack(
        Axis.VERTICAL,
        slots=[
            Slot(Label(caption, color=THEME.muted), size=12),
            Slot(indicator, size=14),
        ],
        spacing=2,
    )


def _framed(inner: Sparkline) -> Padding:
    """A sparkline inset from the edge of its tile."""
    return Padding(inner, all=2)


def render(canvas: Canvas) -> None:
    """Draw one frame. Called by the simulator and by the panel runner."""
    global _root  # noqa: PLW0603 - the tree is built once and reused
    if _root is None:
        _root = build()

    canvas.clear(THEME.background)

    now = time.monotonic()
    _clock.text = time.strftime("%H:%M:%S")
    _cpu.value = 50 + 45 * math.sin(now / 3)
    _memory.value = 40 + 30 * math.sin(now / 7 + 1)
    _history.push(_cpu.value)
    _status.text = "cpu high" if _cpu.is_warning else "all systems nominal"
    _status.color = THEME.warning if _cpu.is_warning else THEME.muted

    _root.bounds = Rect(
        GUTTER,
        GUTTER,
        max(0, canvas.width - 2 * GUTTER),
        max(0, canvas.height - 2 * GUTTER),
    )
    _root.draw(canvas)
