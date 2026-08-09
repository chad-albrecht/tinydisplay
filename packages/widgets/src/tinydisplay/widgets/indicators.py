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
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tinydisplay.core import Color, Rect, Widget
from tinydisplay.widgets.errors import WidgetError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tinydisplay.core import Canvas

__all__ = ["Gauge", "ProgressBar", "Sparkline", "Zone"]


def _fraction(value: float, minimum: float, maximum: float) -> float:
    """Where ``value`` sits in the range, clamped to ``0.0`` to ``1.0``."""
    if maximum == minimum:
        return 0.0
    return max(0.0, min(1.0, (value - minimum) / (maximum - minimum)))


def _checked_zones(zones: Sequence[Zone] | None) -> tuple[Zone, ...]:
    """Validate colour bands at construction, where a mistake can still be named.

    Order is the whole meaning of the list -- each zone runs from the previous
    boundary up to its own -- so an out-of-order boundary is not a band that
    draws oddly, it is a band that never draws at all. Same for an open-ended
    zone anywhere but last: everything after it is unreachable.
    """
    if not zones:
        return ()
    ordered = tuple(zones)
    for index, zone in enumerate(ordered):
        if zone.upto is None and index != len(ordered) - 1:
            msg = (
                f"only the last zone may be open-ended, but zone {index} of {len(ordered)} "
                f"has no 'upto'; zones after it could never be reached"
            )
            raise WidgetError(msg)
    boundaries = [zone.upto for zone in ordered if zone.upto is not None]
    for previous, current in itertools.pairwise(boundaries):
        if current <= previous:
            msg = f"zone boundaries must increase, got {previous} then {current}"
            raise WidgetError(msg)
    return ordered


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


@dataclass(frozen=True, slots=True)
class Zone:
    """A band of a gauge's range that lights in its own colour.

    Attributes:
        upto: The top of the band, in the gauge's value units -- so a gauge
            reading 30..90 degrees takes ``65``, not ``0.58``. ``None`` means
            "everything above the band below", which the topmost zone wants:
            it keeps the red end red when the maximum moves.
        color: What segments inside the band draw as when lit.

    Boundaries are value units rather than fractions because the number a
    dashboard author knows is the one the sensor reports. Raising a gauge's
    ``maximum`` should not quietly slide the green zone up with it.

    Example:
        >>> from tinydisplay.core import Color
        >>> from tinydisplay.widgets import Zone
        >>> zones = [Zone(65, Color.GREEN), Zone(78, Color.YELLOW), Zone(None, Color.RED)]
        >>> zones[0].upto
        65
    """

    upto: float | None
    color: Color


class Gauge(_Ranged):
    """A segmented meter.

    Args:
        value: Current value.
        segments: How many blocks to divide the range into.
        color: Colour of lit segments, and of any segment no zone covers.
        track_color: Colour of unlit segments. ``None`` leaves them unpainted.
        gap: Pixels between segments.
        vertical: Fill upwards instead of rightwards.
        thickness: How thick the bar is across its short axis, in pixels,
            centred in the widget's bounds. ``None`` fills the bounds.
        zones: Bands that colour segments by where each one sits in the range,
            lowest first. Mutually exclusive with ``warning_at``.
        warning_at: Fraction above which *every* lit segment uses
            ``warning_color``. The two-state form that predates ``zones``.
        warning_color: The colour to switch to.

    Discrete segments are the reason to prefer this over a bar for anything
    with a "too much" end: a meter that turns amber at 80% is read correctly
    from across a room, where a bar's exact length is not.

    ``zones`` and ``warning_at`` say different things and only one can be
    right at a time, so passing both raises rather than picking:

    - ``warning_at`` colours by the *value*. The whole lit run turns amber
      together, which reads as a state -- this thing is now too hot.
    - ``zones`` colours by *position*. Each segment takes the colour of the
      band it occupies, so a hot gauge is green then amber then red along its
      length, like an LED bargraph. The green zone stays green while the tip
      goes red, which shows headroom as well as level.

    Example:
        >>> from tinydisplay.core import Canvas, Rect
        >>> from tinydisplay.widgets import Gauge
        >>> gauge = Gauge(60, segments=10, bounds=Rect(0, 0, 100, 12))
        >>> gauge.lit_segments
        6
    """

    __slots__ = (
        "_color",
        "_gap",
        "_segments",
        "_thickness",
        "_track_color",
        "_vertical",
        "_warning",
        "_zones",
    )

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
        thickness: int | None = None,
        zones: Sequence[Zone] | None = None,
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
        if thickness is not None and thickness < 1:
            msg = f"thickness must be at least 1 pixel, got {thickness}"
            raise WidgetError(msg)
        if warning_at is not None and not 0.0 <= warning_at <= 1.0:
            msg = f"warning_at is a fraction between 0 and 1, got {warning_at}"
            raise WidgetError(msg)
        if zones and warning_at is not None:
            msg = (
                "a gauge takes 'zones' or 'warning_at', not both: one colours segments by "
                "position and the other recolours all of them by value"
            )
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
        self._thickness = thickness
        self._zones = _checked_zones(zones)
        self._warning = (warning_at, warning_color or Color.from_hex("#ffb703"))

    @property
    def segments(self) -> int:
        """How many blocks the range is divided into."""
        return self._segments

    @property
    def color(self) -> Color:
        """Colour of lit segments no zone covers, and below the warning threshold."""
        return self._color

    @color.setter
    def color(self, value: Color) -> None:
        if value != self._color:
            self._color = value
            self.mark_dirty()

    @property
    def zones(self) -> tuple[Zone, ...]:
        """The colour bands, lowest first. Empty when the gauge has none."""
        return self._zones

    @property
    def thickness(self) -> int | None:
        """How thick the bar draws, or ``None`` to fill the bounds."""
        return self._thickness

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

    def segment_color(self, index: int) -> Color:
        """The colour segment ``index`` lights in.

        Zones are matched on the segment's midpoint, so a boundary that falls
        inside a segment gives that segment to whichever side holds most of
        it, rather than to whichever side the rounding happened to favour.
        """
        if not self._zones:
            return self._warning[1] if self.is_warning else self._color
        midpoint = self._minimum + (index + 0.5) / self._segments * (self._maximum - self._minimum)
        for zone in self._zones:
            if zone.upto is None or midpoint < zone.upto:
                return zone.color
        # Zones that stop short of the maximum leave a tail; the plain colour
        # is a better answer there than refusing to draw.
        return self._color

    def _bar_area(self) -> Rect:
        """The bounds narrowed to ``thickness`` and centred, if one was given."""
        area = self.bounds
        if self._thickness is None:
            return area
        if self._vertical:
            width = min(self._thickness, area.width)
            return Rect(area.x + (area.width - width) // 2, area.y, width, area.height)
        height = min(self._thickness, area.height)
        return Rect(area.x, area.y + (area.height - height) // 2, area.width, height)

    def render(self, canvas: Canvas) -> None:
        """Draw every segment, lit or not."""
        area = self._bar_area()
        if area.is_empty:
            return

        lit = self.lit_segments
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
                canvas.rect(rect.x, rect.y, rect.width, rect.height, self.segment_color(index))
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
