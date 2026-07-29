"""A small set of icons, drawn rather than loaded.

Icons here are vector shapes built from the canvas primitives, not image files.
That is a deliberate trade: a drawn icon scales to whatever box the layout gives
it, recolours with the theme, costs no asset pipeline and adds no dependency,
and a status panel needs about a dozen symbols rather than a library of them.

The cost is that the set is small and everything in it is a shape the available
primitives can make -- lines, rectangles, circles and scanline-filled triangles.
Anything needing arcs is absent rather than approximated badly. For a logo or a
weather glyph, use :class:`~tinydisplay.widgets.image.ImageWidget`.

Every icon is drawn inside a square inscribed in the widget's bounds, so a
row of icons in differently-shaped slots still looks like a row of icons.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from tinydisplay.core import Color, Rect, Widget

if TYPE_CHECKING:
    from collections.abc import Callable

    from tinydisplay.core import Canvas

__all__ = ["Icon", "IconName"]


class IconName(StrEnum):
    """The available symbols."""

    CIRCLE = "circle"
    DOT = "dot"
    SQUARE = "square"
    CHECK = "check"
    CROSS = "cross"
    WARNING = "warning"
    ARROW_UP = "arrow-up"
    ARROW_DOWN = "arrow-down"
    BOLT = "bolt"
    BATTERY = "battery"
    THERMOMETER = "thermometer"


def _square(area: Rect) -> Rect:
    """The largest centred square inside ``area``."""
    side = min(area.width, area.height)
    return Rect(
        area.x + (area.width - side) // 2,
        area.y + (area.height - side) // 2,
        side,
        side,
    )


def _triangle(
    canvas: Canvas,
    apex: tuple[int, int],
    base_y: int,
    half_width: int,
    color: Color,
) -> None:
    """Fill a triangle by scanlines.

    The canvas has no polygon primitive, and a row of one-pixel-tall rectangles
    is both exact and cheap at icon sizes.
    """
    apex_x, apex_y = apex
    height = base_y - apex_y
    if height <= 0:
        return
    for offset in range(height + 1):
        span = round(half_width * offset / height)
        if span <= 0:
            canvas.rect(apex_x, apex_y + offset, 1, 1, color)
        else:
            canvas.rect(apex_x - span, apex_y + offset, span * 2, 1, color)


def _draw_circle(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    radius = min(box.width, box.height) // 2
    canvas.circle(box.center.x, box.center.y, radius, color, fill=False, thickness=thickness)


def _draw_dot(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:  # noqa: ARG001
    radius = max(1, min(box.width, box.height) // 3)
    canvas.circle(box.center.x, box.center.y, radius, color, fill=True)


def _draw_square(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    inner = box.inset(max(1, box.width // 8))
    canvas.rect(inner.x, inner.y, inner.width, inner.height, color, fill=False, thickness=thickness)


def _draw_check(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    left = box.x + box.width // 6
    middle_x = box.x + box.width // 2 - box.width // 12
    right = box.right - box.width // 6
    middle_y = box.y + box.height * 2 // 3
    canvas.line(left, box.y + box.height // 2, middle_x, middle_y, color, thickness=thickness)
    canvas.line(middle_x, middle_y, right, box.y + box.height // 4, color, thickness=thickness)


def _draw_cross(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    inner = box.inset(max(1, box.width // 5))
    canvas.line(inner.x, inner.y, inner.right - 1, inner.bottom - 1, color, thickness=thickness)
    canvas.line(inner.right - 1, inner.y, inner.x, inner.bottom - 1, color, thickness=thickness)


def _draw_warning(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:  # noqa: ARG001
    _triangle(canvas, (box.center.x, box.y + 1), box.bottom - 2, box.width // 2 - 1, color)


def _draw_arrow_up(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    _triangle(canvas, (box.center.x, box.y + 1), box.center.y, box.width // 3, color)
    stem = max(1, thickness)
    canvas.rect(box.center.x - stem // 2, box.center.y, stem, box.height // 2 - 1, color)


def _draw_arrow_down(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    stem = max(1, thickness)
    canvas.rect(box.center.x - stem // 2, box.y + 1, stem, box.height // 2 - 1, color)
    # An inverted triangle, drawn by walking the scanlines the other way.
    half = box.width // 3
    height = box.bottom - 1 - box.center.y
    for offset in range(height + 1):
        span = round(half * (height - offset) / max(1, height))
        canvas.rect(box.center.x - span, box.center.y + offset, max(1, span * 2), 1, color)


def _draw_bolt(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    width = max(1, thickness)
    top = (box.center.x + box.width // 8, box.y + 1)
    waist_left = (box.x + box.width // 3, box.center.y)
    waist_right = (box.center.x + box.width // 6, box.center.y)
    bottom = (box.x + box.width // 3, box.bottom - 2)
    canvas.line(*top, *waist_left, color, thickness=width)
    canvas.line(*waist_left, *waist_right, color, thickness=width)
    canvas.line(*waist_right, *bottom, color, thickness=width)


def _draw_battery(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    body = Rect(box.x, box.y + box.height // 4, box.width - box.width // 8, box.height // 2)
    canvas.rect(body.x, body.y, body.width, body.height, color, fill=False, thickness=thickness)
    cap_height = max(1, body.height // 3)
    canvas.rect(
        body.right,
        body.y + (body.height - cap_height) // 2,
        max(1, box.width // 8),
        cap_height,
        color,
    )


def _draw_thermometer(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    stem_width = max(1, thickness)
    bulb = max(2, box.width // 5)
    canvas.rect(box.center.x - stem_width // 2, box.y + 1, stem_width, box.height - bulb * 2, color)
    canvas.circle(box.center.x, box.bottom - bulb - 1, bulb, color, fill=True)


_PAINTERS: dict[IconName, Callable[[Canvas, Rect, Color, int], None]] = {
    IconName.CIRCLE: _draw_circle,
    IconName.DOT: _draw_dot,
    IconName.SQUARE: _draw_square,
    IconName.CHECK: _draw_check,
    IconName.CROSS: _draw_cross,
    IconName.WARNING: _draw_warning,
    IconName.ARROW_UP: _draw_arrow_up,
    IconName.ARROW_DOWN: _draw_arrow_down,
    IconName.BOLT: _draw_bolt,
    IconName.BATTERY: _draw_battery,
    IconName.THERMOMETER: _draw_thermometer,
}


class Icon(Widget):
    """One symbol, drawn to fill its bounds.

    Args:
        name: Which symbol.
        color: Ink colour.
        thickness: Stroke width for the outlined symbols.

    Example:
        >>> from tinydisplay.core import Canvas, Rect
        >>> from tinydisplay.widgets import Icon, IconName
        >>> icon = Icon(IconName.CHECK, bounds=Rect(0, 0, 16, 16))
        >>> canvas = Canvas(16, 16)
        >>> icon.draw(canvas)
        >>> icon.name_of_symbol
        <IconName.CHECK: 'check'>
    """

    __slots__ = ("_color", "_symbol", "_thickness")

    def __init__(
        self,
        name: IconName,
        *,
        color: Color = Color.WHITE,
        thickness: int = 1,
        bounds: Rect | None = None,
        visible: bool = True,
        widget_name: str | None = None,
    ) -> None:
        super().__init__(bounds, visible=visible, name=widget_name or f"Icon({name})")
        self._symbol = name
        self._color = color
        self._thickness = max(1, thickness)

    @property
    def name_of_symbol(self) -> IconName:
        """Which symbol this draws.

        Named awkwardly because :attr:`~tinydisplay.core.Widget.name` is
        already the widget's debugging identifier.
        """
        return self._symbol

    @property
    def color(self) -> Color:
        """Ink colour."""
        return self._color

    @color.setter
    def color(self, value: Color) -> None:
        if value != self._color:
            self._color = value
            self.mark_dirty()

    def render(self, canvas: Canvas) -> None:
        """Draw the symbol inside the largest centred square."""
        box = _square(self.bounds)
        min_side = 4
        if box.width < min_side:
            return
        _PAINTERS[self._symbol](canvas, box, self._color, self._thickness)
