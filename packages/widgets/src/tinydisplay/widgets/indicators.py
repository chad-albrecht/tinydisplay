"""Widgets that show a number without making you read one.

The three here answer different questions, which is why they are three widgets
and not one with a mode flag:

- :class:`ProgressBar` -- how far along is this? A continuous fill.
- :class:`Gauge` -- how full is this, roughly? Discrete segments, because a
  segmented meter is readable at a glance and at a distance in a way a smooth
  bar is not, and because 32 levels of red make a smooth gradient band anyway.
- :class:`Sparkline` -- what has this been doing? A shape over time.

All three clamp rather than reject. A sensor that reports 105% or briefly
returns nonsense should make the panel look odd for a frame, not stop the
render loop; construction-time mistakes still raise.
"""

from __future__ import annotations

import itertools
import math
from typing import TYPE_CHECKING

from tinydisplay.core import Color, Rect, Widget
from tinydisplay.widgets.errors import WidgetError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tinydisplay.core import Canvas

__all__ = ["Gauge", "ProgressBar", "Sparkline"]


def _fraction(value: float, minimum: float, maximum: float) -> float:
    """Where ``value`` sits in the range, clamped to ``0.0`` to ``1.0``."""
    if maximum == minimum:
        return 0.0
    return max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))


class _Ranged(Widget):
    """Shared range handling for the value-showing widgets."""

    __slots__ = ("_maximum", "_minimum", "_value")

    def __init__(
        self,
        value: float = 0.0,
        *,
        minimum: float = 0.0,
        maximum: float = 100.0,
        bounds: Rect | None = None,
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        if maximum < minimum:
            msg = f"maximum must not be below minimum, got {minimum}..{maximum}"
            raise WidgetError(msg)
        super().__init__(bounds, visible=visible, name=name)
        self._minimum = minimum
        self._maximum = maximum
        self._value = value

    @property
    def value(self) -> float:
        """The current value, as set. Not clamped -- see :attr:`fraction`."""
        return self._value

    @value.setter
    def value(self, new_value: float) -> None:
        if new_value != self._value:
            self._value = new_value
            self.mark_dirty()

    @property
    def minimum(self) -> float:
        """The bottom of the range."""
        return self._minimum

    @property
    def maximum(self) -> float:
        """The top of the range."""
        return self._maximum

    @property
    def fraction(self) -> float:
        """The value as ``0.0`` to ``1.0``, clamped into range."""
        return _fraction(self._value, self._minimum, self._maximum)


class ProgressBar(_Ranged):
    """A continuous bar that fills left to right, or bottom to top.

    Args:
        value: Current value.
        minimum: Bottom of the range.
        maximum: Top of the range.
        color: The fill.
        track_color: The unfilled remainder. ``None`` draws nothing behind.
        radius: Corner rounding. Zero draws square ends.
        vertical: Fill upwards instead of rightwards.

    Example:
        >>> from tinydisplay.core import Canvas, Color, Rect
        >>> from tinydisplay.widgets import ProgressBar
        >>> bar = ProgressBar(50, bounds=Rect(0, 0, 100, 10), radius=0)
        >>> canvas = Canvas(100, 10)
        >>> bar.draw(canvas)
        >>> canvas.get_pixel(10, 5) == bar.color
        True
        >>> canvas.get_pixel(90, 5) == bar.color
        False
    """

    __slots__ = ("_color", "_radius", "_track_color", "_vertical")

    def __init__(
        self,
        value: float = 0.0,
        *,
        minimum: float = 0.0,
        maximum: float = 100.0,
        color: Color = Color.WHITE,
        track_color: Color | None = None,
        radius: int = 2,
        vertical: bool = False,
        bounds: Rect | None = None,
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        if radius < 0:
            msg = f"radius must be non-negative, got {radius}"
            raise WidgetError(msg)
        super().__init__(
            value,
            minimum=minimum,
            maximum=maximum,
            bounds=bounds,
            visible=visible,
            name=name,
        )
        self._color = color
        self._track_color = track_color
        self._radius = radius
        self._vertical = vertical

    @property
    def color(self) -> Color:
        """The fill colour."""
        return self._color

    @color.setter
    def color(self, value: Color) -> None:
        if value != self._color:
            self._color = value
            self.mark_dirty()

    @property
    def track_color(self) -> Color | None:
        """The unfilled remainder's colour, if any."""
        return self._track_color

    def render(self, canvas: Canvas) -> None:
        """Draw the track, then the fill."""
        area = self.bounds
        if self._track_color is not None:
            self._draw_rect(canvas, area, self._track_color)

        filled = self.fraction
        if filled <= 0:
            return

        if self._vertical:
            height = max(1, round(area.height * filled))
            fill = Rect(area.x, area.bottom - height, area.width, height)
        else:
            width = max(1, round(area.width * filled))
            fill = Rect(area.x, area.y, width, area.height)
        self._draw_rect(canvas, fill, self._color)

    def _draw_rect(self, canvas: Canvas, area: Rect, color: Color) -> None:
        """Draw one rectangle, rounded if the radius fits inside it."""
        if area.is_empty:
            return
        # A radius larger than half the shorter side draws as a lozenge or
        # fails outright, so fall back to square corners rather than surprise
        # the caller with a different shape at small sizes.
        if self._radius > 0 and min(area.width, area.height) > self._radius * 2:
            canvas.rounded_rect(area.x, area.y, area.width, area.height, color, radius=self._radius)
        else:
            canvas.rect(area.x, area.y, area.width, area.height, color)


class Gauge(_Ranged):
    """A segmented meter.

    Args:
        value: Current value.
        segments: How many blocks to divide the range into.
        color: Colour of lit segments.
        track_color: Colour of unlit segments. ``None`` leaves them unpainted.
        gap: Pixels between segments.
        vertical: Fill upwards instead of rightwards.
        warning_at: Fraction above which lit segments use ``warning_color``.
        warning_color: The colour to switch to.

    The threshold colouring is the reason to prefer this over a bar for
    anything with a "too much" end: a gauge that turns amber at 80% is read
    correctly from across a room, where a bar's exact length is not.

    Example:
        >>> from tinydisplay.core import Canvas, Rect
        >>> from tinydisplay.widgets import Gauge
        >>> gauge = Gauge(60, segments=10, bounds=Rect(0, 0, 100, 12))
        >>> gauge.lit_segments
        6
    """

    __slots__ = ("_color", "_gap", "_segments", "_track_color", "_vertical", "_warning")

    def __init__(
        self,
        value: float = 0.0,
        *,
        minimum: float = 0.0,
        maximum: float = 100.0,
        segments: int = 10,
        color: Color = Color.WHITE,
        track_color: Color | None = None,
        gap: int = 2,
        vertical: bool = False,
        warning_at: float | None = None,
        warning_color: Color | None = None,
        bounds: Rect | None = None,
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        if segments < 1:
            msg = f"a gauge needs at least one segment, got {segments}"
            raise WidgetError(msg)
        if gap < 0:
            msg = f"gap must be non-negative, got {gap}"
            raise WidgetError(msg)
        if warning_at is not None and not 0.0 <= warning_at <= 1.0:
            msg = f"warning_at is a fraction between 0 and 1, got {warning_at}"
            raise WidgetError(msg)
        super().__init__(
            value,
            minimum=minimum,
            maximum=maximum,
            bounds=bounds,
            visible=visible,
            name=name,
        )
        self._segments = segments
        self._color = color
        self._track_color = track_color
        self._gap = gap
        self._vertical = vertical
        self._warning = (warning_at, warning_color or Color.from_hex("#ffb703"))

    @property
    def segments(self) -> int:
        """How many blocks the range is divided into."""
        return self._segments

    @property
    def lit_segments(self) -> int:
        """How many blocks the current value lights.

        Rounds up, so any value above the minimum lights at least one block --
        a gauge showing nothing at 1% reads as broken rather than as low.
        """
        filled = self.fraction
        if filled <= 0:
            return 0
        return max(1, min(self._segments, math.ceil(filled * self._segments)))

    @property
    def is_warning(self) -> bool:
        """Whether the value has passed the warning threshold."""
        threshold = self._warning[0]
        return threshold is not None and self.fraction >= threshold

    def render(self, canvas: Canvas) -> None:
        """Draw every segment, lit or not."""
        area = self.bounds
        if area.is_empty:
            return

        lit = self.lit_segments
        color = self._warning[1] if self.is_warning else self._color
        total = area.height if self._vertical else area.width
        inner = max(0, total - self._gap * (self._segments - 1))

        for index in range(self._segments):
            start = (inner * index) // self._segments + self._gap * index
            end = (inner * (index + 1)) // self._segments + self._gap * index
            extent = max(1, end - start)

            # Vertical gauges fill upwards, so the first lit segment is at the
            # bottom of the widget rather than the top.
            is_lit = index < lit
            if self._vertical:
                top = area.bottom - start - extent
                rect = Rect(area.x, top, area.width, extent)
            else:
                rect = Rect(area.x + start, area.y, extent, area.height)

            if is_lit:
                canvas.rect(rect.x, rect.y, rect.width, rect.height, color)
            elif self._track_color is not None:
                canvas.rect(rect.x, rect.y, rect.width, rect.height, self._track_color)


class Sparkline(Widget):
    """A line showing how a value has moved.

    Args:
        values: The series, oldest first.
        color: Line colour.
        fill_color: Optional colour under the line.
        minimum: Bottom of the scale. ``None`` scales to the data.
        maximum: Top of the scale. ``None`` scales to the data.
        capacity: Keep at most this many points, dropping the oldest.

    Auto-scaling is the default because a sparkline's job is showing *shape*,
    and a fixed scale flattens an interesting wiggle into a straight line. Pass
    an explicit range when the absolute level matters more than the movement.

    Example:
        >>> from tinydisplay.core import Rect
        >>> from tinydisplay.widgets import Sparkline
        >>> spark = Sparkline([1, 5, 2, 8], bounds=Rect(0, 0, 40, 20))
        >>> spark.push(3)
        >>> len(spark.values)
        5
    """

    __slots__ = ("_capacity", "_color", "_fill_color", "_maximum", "_minimum", "_values")

    def __init__(
        self,
        values: Sequence[float] = (),
        *,
        color: Color = Color.WHITE,
        fill_color: Color | None = None,
        minimum: float | None = None,
        maximum: float | None = None,
        capacity: int | None = None,
        bounds: Rect | None = None,
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        if capacity is not None and capacity < 1:
            msg = f"capacity must be at least 1, got {capacity}"
            raise WidgetError(msg)
        super().__init__(bounds, visible=visible, name=name)
        self._values = list(values)
        self._color = color
        self._fill_color = fill_color
        self._minimum = minimum
        self._maximum = maximum
        self._capacity = capacity
        self._trim()

    @property
    def values(self) -> Sequence[float]:
        """The series, oldest first."""
        return tuple(self._values)

    def push(self, value: float) -> None:
        """Append a sample, dropping the oldest if at capacity."""
        self._values.append(value)
        self._trim()
        self.mark_dirty()

    def clear(self) -> None:
        """Discard every sample."""
        if self._values:
            self._values.clear()
            self.mark_dirty()

    def _trim(self) -> None:
        """Drop samples beyond the capacity."""
        if self._capacity is not None and len(self._values) > self._capacity:
            del self._values[: len(self._values) - self._capacity]

    def scale(self) -> tuple[float, float]:
        """The ``(minimum, maximum)`` the line is drawn against.

        A flat series is given a small artificial range, so that a constant
        value draws as a line through the middle rather than collapsing onto
        an edge or dividing by zero.
        """
        low = self._minimum if self._minimum is not None else min(self._values, default=0.0)
        high = self._maximum if self._maximum is not None else max(self._values, default=1.0)
        if high <= low:
            return (low - 0.5, low + 0.5)
        return (low, high)

    def render(self, canvas: Canvas) -> None:
        """Draw the series as a polyline."""
        area = self.bounds
        min_points = 2
        if area.is_empty or len(self._values) < min_points:
            return

        low, high = self.scale()
        span = high - low
        step = (area.width - 1) / (len(self._values) - 1)

        points = [
            (
                area.x + round(index * step),
                area.bottom - 1 - round((value - low) / span * (area.height - 1)),
            )
            for index, value in enumerate(self._values)
        ]
        points = [(x, max(area.y, min(area.bottom - 1, y))) for x, y in points]

        if self._fill_color is not None:
            for x, y in points:
                canvas.rect(x, y, 1, area.bottom - y, self._fill_color)

        for (x0, y0), (x1, y1) in itertools.pairwise(points):
            canvas.line(x0, y0, x1, y1, self._color)
