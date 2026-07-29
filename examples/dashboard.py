"""A small dashboard, rendered at HT32 panel resolution and pushed to a driver.

This example exercises all three layers of the engine:

* custom :class:`~tinydisplay.core.widget.Widget` subclasses paint themselves,
* a :class:`~tinydisplay.core.widget.Container` composes and clips them,
* a :class:`~tinydisplay.core.driver.DisplayDriver` encodes the finished frame.

The driver here is :class:`~tinydisplay.core.driver.MemoryDriver`, so the whole
thing runs with no hardware attached. Swapping in the HT32 driver later changes
only the two lines that construct it.

Run it with::

    python examples/dashboard.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from tinydisplay.core import (
    Canvas,
    Color,
    Container,
    Font,
    HorizontalAlign,
    MemoryDriver,
    PixelFormat,
    Rect,
    VerticalAlign,
    Widget,
)

# The HT32 panel is 320x170, RGB565, over USB HID.
PANEL_WIDTH = 320
PANEL_HEIGHT = 170
OUTPUT = Path("dashboard.png")

BACKGROUND = Color.from_hex("#101418")
CARD = Color.from_hex("#1b2229")
ACCENT = Color.from_hex("#38bdf8")
MUTED = Color.from_hex("#7c8a99")


class Card(Widget):
    """A rounded panel that acts as a background for other widgets."""

    __slots__ = ("_fill", "_radius")

    def __init__(self, bounds: Rect, *, fill: Color = CARD, radius: int = 8) -> None:
        super().__init__(bounds)
        self._fill = fill
        self._radius = radius

    def render(self, canvas: Canvas) -> None:
        b = self.bounds
        canvas.rounded_rect(b.x, b.y, b.width, b.height, self._fill, radius=self._radius)


PADDING = 12
GAP = 6


class Readout(Widget):
    """A label above a large value, the classic dashboard tile.

    The value is positioned from the label's measured line height rather than
    a hard-coded offset, so changing either font size cannot make the two
    collide.
    """

    __slots__ = ("_label", "_label_font", "_value", "_value_font")

    def __init__(self, bounds: Rect, label: str, value: str, *, value_size: int = 28) -> None:
        super().__init__(bounds)
        self._label = label
        self._value = value
        self._label_font = Font.default(11)
        self._value_font = Font.default(value_size)

    @property
    def value(self) -> str:
        """The large text shown beneath the label."""
        return self._value

    @value.setter
    def value(self, new_value: str) -> None:
        if new_value != self._value:
            self._value = new_value
            self.mark_dirty()

    def render(self, canvas: Canvas) -> None:
        b = self.bounds
        label_top = b.y + PADDING - 2
        canvas.text(b.x + PADDING, label_top, self._label, MUTED, font=self._label_font)
        canvas.text(
            b.x + PADDING,
            label_top + self._label_font.line_height + GAP,
            self._value,
            Color.WHITE,
            font=self._value_font,
        )


class StatLine(Widget):
    """A label on the left and a value on the right, sharing one row.

    Useful where a full :class:`Readout` would not fit -- above a gauge, for
    instance. It shows off right-alignment: the value is anchored to the right
    edge with no manual text measurement.
    """

    __slots__ = ("_font", "_label", "_value")

    def __init__(self, bounds: Rect, label: str, value: str) -> None:
        super().__init__(bounds)
        self._label = label
        self._value = value
        self._font = Font.default(12)

    def render(self, canvas: Canvas) -> None:
        b = self.bounds
        canvas.text(b.x, b.y, self._label, MUTED, font=self._font)
        canvas.text(
            b.right,
            b.y,
            self._value,
            Color.WHITE,
            font=self._font,
            align=HorizontalAlign.RIGHT,
        )


class Gauge(Widget):
    """A horizontal bar showing a fraction between 0.0 and 1.0."""

    __slots__ = ("_fraction",)

    def __init__(self, bounds: Rect, fraction: float) -> None:
        super().__init__(bounds)
        self._fraction = _clamp_fraction(fraction)

    @property
    def fraction(self) -> float:
        """How full the bar is, from ``0.0`` to ``1.0``."""
        return self._fraction

    @fraction.setter
    def fraction(self, value: float) -> None:
        clamped = _clamp_fraction(value)
        if clamped != self._fraction:
            self._fraction = clamped
            self.mark_dirty()

    def render(self, canvas: Canvas) -> None:
        b = self.bounds
        canvas.rounded_rect(b.x, b.y, b.width, b.height, Color.from_hex("#2b3541"), radius=4)
        filled = round(b.width * self._fraction)
        if filled > 0:
            canvas.rounded_rect(b.x, b.y, filled, b.height, ACCENT, radius=4)


def _clamp_fraction(value: float) -> float:
    """Clamp a gauge fraction into ``0.0..1.0``."""
    return max(0.0, min(1.0, value))


def build_dashboard() -> Container:
    """Compose the widget tree for the panel."""
    root = Container(Rect(0, 0, PANEL_WIDTH, PANEL_HEIGHT), name="root")

    root.add(Card(Rect(8, 8, 148, 76)))
    root.add(Readout(Rect(8, 8, 148, 76), "LIVING ROOM", "21.4 C"))

    root.add(Card(Rect(164, 8, 148, 76)))
    root.add(Readout(Rect(164, 8, 148, 76), "HUMIDITY", "48 %"))

    root.add(Card(Rect(8, 92, 304, 52)))
    root.add(StatLine(Rect(20, 100, 280, 16), "BATTERY", "72 %"))
    root.add(Gauge(Rect(20, 122, 280, 12), 0.72))

    return root


def render(root: Container) -> Canvas:
    """Paint the widget tree onto a fresh canvas."""
    canvas = Canvas(PANEL_WIDTH, PANEL_HEIGHT, background=BACKGROUND)
    canvas.clear()
    root.draw(canvas)

    footer = Font.default(10)
    canvas.text(
        PANEL_WIDTH // 2,
        PANEL_HEIGHT - 4,
        "tinydisplay",
        MUTED,
        font=footer,
        align=HorizontalAlign.CENTER,
        valign=VerticalAlign.BOTTOM,
    )
    return canvas


async def main() -> None:
    """Render the dashboard, push it to a driver, and save a preview."""
    root = build_dashboard()
    canvas = render(root)

    async with MemoryDriver(
        PANEL_WIDTH,
        PANEL_HEIGHT,
        pixel_format=PixelFormat.RGB565_LE,
        name="ht32-stand-in",
    ) as driver:
        await driver.show(canvas)
        frame = driver.last_frame
        assert frame is not None
        print(f"pushed {len(frame)} bytes ({driver.pixel_format})")

    canvas.save(OUTPUT)
    print(f"wrote {OUTPUT.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
