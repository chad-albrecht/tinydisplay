"""A small set of icons, drawn rather than loaded.

Icons here are vector shapes built from the canvas primitives, not image files.
That is a deliberate trade: a drawn icon scales to whatever box the layout gives
it, recolours with the theme, costs no asset pipeline and adds no dependency,
and a status panel needs a few dozen symbols rather than a library of them.

The cost is that the set is small and everything in it is a shape the available
primitives can make -- lines, rectangles, circles, and triangles and trapezoids
filled by scanline.
Anything needing a genuine arc is absent rather than approximated badly: there
is no crescent moon and no gapped power ring, and the padlock's shackle is
square-topped because that is the honest pixel-art form rather than a stepped
semicircle pretending to be smooth. For a logo or a weather glyph, use
:class:`~tinydisplay.widgets.image.ImageWidget`.

Every icon is drawn inside a square inscribed in the widget's bounds, so a
row of icons in differently-shaped slots still looks like a row of icons.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Final

from tinydisplay.core import Color, Rect, Widget

if TYPE_CHECKING:
    from collections.abc import Callable

    from tinydisplay.core import Canvas

__all__ = ["Icon", "IconName"]


class IconName(StrEnum):
    """The available symbols.

    Grouped by what they are usually for. The values are the names a YAML
    dashboard writes, so they are kebab-case and stable.
    """

    # Shapes and marks
    CIRCLE = "circle"
    DOT = "dot"
    SQUARE = "square"
    CHECK = "check"
    CROSS = "cross"
    WARNING = "warning"
    INFO = "info"
    PLUS = "plus"
    MINUS = "minus"
    ARROW_UP = "arrow-up"
    ARROW_DOWN = "arrow-down"

    # Home and entity domains
    HOME = "home"
    DOOR = "door"
    LOCK = "lock"
    UNLOCK = "unlock"
    LIGHTBULB = "lightbulb"
    PERSON = "person"
    PLUG = "plug"

    # Sensors and weather
    THERMOMETER = "thermometer"
    DROPLET = "droplet"
    SUN = "sun"
    CLOUD = "cloud"
    WIND = "wind"
    FAN = "fan"
    FLAME = "flame"

    # Status and connectivity
    BOLT = "bolt"
    BATTERY = "battery"
    POWER = "power"
    WIFI = "wifi"
    SIGNAL = "signal"
    CLOCK = "clock"
    BELL = "bell"


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


def _taper(
    canvas: Canvas,
    center_x: int,
    top: tuple[int, int],
    base: tuple[int, int],
    color: Color,
) -> None:
    """Fill a symmetric trapezoid by scanlines.

    ``top`` and ``base`` are each ``(y, half_width)``. Shapes that narrow to a
    shoulder rather than to a point -- a bell, a torso -- need this; a triangle
    in their place reads as a cone.
    """
    top_y, top_half = top
    base_y, base_half = base
    height = base_y - top_y
    if height <= 0:
        return
    for offset in range(height + 1):
        half = top_half + round((base_half - top_half) * offset / height)
        canvas.rect(center_x - half, top_y + offset, max(1, half * 2), 1, color)


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


def _draw_info(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    ring = box.width // 2 - 1
    canvas.circle(box.center.x, box.center.y, ring, color, fill=False, thickness=thickness)
    tittle = max(1, box.width // 12)
    canvas.circle(box.center.x, box.y + box.height * 6 // 25, tittle, color, fill=True)
    stem = max(thickness, box.width // 10)
    canvas.rect(
        box.center.x - stem // 2,
        box.y + box.height * 46 // 100,
        stem,
        box.height * 7 // 20,
        color,
    )


def _draw_plus(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:  # noqa: ARG001
    arm = max(1, box.width // 5)
    inset = box.width // 8
    span = box.width - 2 * inset
    canvas.rect(box.x + inset, box.center.y - arm // 2, span, arm, color)
    canvas.rect(box.center.x - arm // 2, box.y + inset, arm, span, color)


def _draw_minus(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:  # noqa: ARG001
    arm = max(1, box.width // 5)
    inset = box.width // 8
    canvas.rect(box.x + inset, box.center.y - arm // 2, box.width - 2 * inset, arm, color)


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


# -- Home and entity domains -----------------------------------------------


def _draw_home(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    eaves = box.y + box.height * 2 // 5
    _triangle(canvas, (box.center.x, box.y + 1), eaves, box.width // 2 - 1, color)
    wall = box.width // 5
    canvas.rect(
        box.x + wall,
        eaves,
        box.width - 2 * wall,
        box.bottom - 1 - eaves,
        color,
        fill=False,
        thickness=thickness,
    )


def _draw_door(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    jamb = box.width // 4
    panel = Rect(
        box.x + jamb,
        box.y + box.height // 8,
        box.width - 2 * jamb,
        box.height - box.height // 8 - 1,
    )
    canvas.rect(panel.x, panel.y, panel.width, panel.height, color, fill=False, thickness=thickness)
    knob = max(1, box.width // 16)
    canvas.circle(panel.right - 1 - max(2, box.width // 8), panel.center.y, knob, color, fill=True)


def _padlock(
    canvas: Canvas,
    box: Rect,
    color: Color,
    thickness: int,
    *,
    shackle_open: bool,
) -> None:
    """Draw a padlock, with the shackle either closed or swung open on the left.

    The shackle is square-topped. With no arc primitive the alternative is a
    stepped semicircle, which at icon sizes reads as a mistake rather than as a
    curve; a squared shackle is a deliberate pixel-art form instead.
    """
    flank = box.width // 5
    body = Rect(
        box.x + flank,
        box.y + box.height // 2,
        box.width - 2 * flank,
        box.height // 2 - 1,
    )
    canvas.rect(body.x, body.y, body.width, body.height, color, fill=False, thickness=thickness)
    canvas.circle(body.center.x, body.center.y, max(1, box.width // 14), color, fill=True)

    top = box.y + box.height // 8
    inset = max(1, box.width // 8)
    left = body.x + inset
    right = body.right - 1 - inset
    canvas.line(left, top, right, top, color, thickness=thickness)
    canvas.line(right, top, right, body.y, color, thickness=thickness)
    open_to = top + (body.y - top) // 2 if shackle_open else body.y
    canvas.line(left, top, left, open_to, color, thickness=thickness)


def _draw_lock(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    _padlock(canvas, box, color, thickness, shackle_open=False)


def _draw_unlock(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    _padlock(canvas, box, color, thickness, shackle_open=True)


def _draw_lightbulb(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    radius = box.width * 3 // 10
    glass_y = box.y + box.height * 2 // 5
    canvas.circle(box.center.x, glass_y, radius, color, fill=False, thickness=thickness)
    neck = max(1, box.width // 3)
    canvas.rect(
        box.center.x - neck // 2,
        glass_y + radius,
        neck,
        box.bottom - 1 - glass_y - radius,
        color,
        fill=False,
        thickness=thickness,
    )


def _draw_person(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:  # noqa: ARG001
    # The head has to clear the shoulders by a couple of pixels. Any larger or
    # any lower and the two merge into one silhouette, which reads as a pawn.
    head = max(1, box.width // 7)
    canvas.circle(box.center.x, box.y + box.height * 22 // 100, head, color, fill=True)
    _taper(
        canvas,
        box.center.x,
        (box.y + box.height // 2, box.width // 8),
        (box.bottom - 2, box.width * 3 // 8),
        color,
    )


def _draw_plug(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:  # noqa: ARG001
    prong = max(1, box.width // 10)
    shell_y = box.y + box.height // 3
    for offset in (-(box.width // 5), box.width // 5):
        canvas.rect(
            box.center.x + offset - prong // 2, box.y + 1, prong, shell_y - box.y - 1, color
        )
    flank = box.width // 5
    canvas.rect(box.x + flank, shell_y, box.width - 2 * flank, box.height // 3, color)
    cord_y = shell_y + box.height // 3
    canvas.rect(box.center.x - prong // 2, cord_y, prong, box.bottom - 1 - cord_y, color)


# -- Sensors and weather ----------------------------------------------------


def _draw_droplet(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:  # noqa: ARG001
    bulb = max(1, box.width // 4)
    bulb_y = box.bottom - 1 - bulb
    canvas.circle(box.center.x, bulb_y, bulb, color, fill=True)
    _triangle(canvas, (box.center.x, box.y + 1), bulb_y, bulb, color)


#: Ray directions for the sun, as tenths of the reach. The diagonals are
#: shortened to 7/10 so every ray ends the same distance from the centre.
_SUN_RAYS: Final = ((10, 0), (-10, 0), (0, 10), (0, -10), (7, 7), (7, -7), (-7, 7), (-7, -7))


def _draw_sun(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    core = max(1, box.width // 5)
    canvas.circle(box.center.x, box.center.y, core, color, fill=True)
    inner = core + max(1, box.width // 10)
    outer = box.width // 2 - 1
    if outer <= inner:
        return
    for dx, dy in _SUN_RAYS:
        canvas.line(
            box.center.x + dx * inner // 10,
            box.center.y + dy * inner // 10,
            box.center.x + dx * outer // 10,
            box.center.y + dy * outer // 10,
            color,
            thickness=thickness,
        )


def _draw_cloud(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:  # noqa: ARG001
    dome = max(1, box.width // 4)
    puff = max(1, box.width // 5)
    shoulder_y = box.y + box.height * 3 // 5
    left_x = box.x + box.width * 3 // 10
    right_x = box.right - 1 - box.width * 3 // 10
    canvas.circle(box.center.x, box.center.y, dome, color, fill=True)
    canvas.circle(left_x, shoulder_y, puff, color, fill=True)
    canvas.circle(right_x, shoulder_y, puff, color, fill=True)
    canvas.rect(left_x, shoulder_y, max(1, right_x - left_x), puff + 1, color)


def _draw_wind(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    left = box.x + box.width // 6
    hook = max(2, box.height // 6)
    top_y = box.y + box.height * 3 // 10
    low_y = box.y + box.height * 7 // 10
    top_end = box.right - 1 - box.width // 5
    low_end = box.right - 1 - box.width // 3
    canvas.line(left, top_y, top_end, top_y, color, thickness=thickness)
    canvas.line(top_end, top_y, top_end, top_y - hook, color, thickness=thickness)
    canvas.line(left, box.center.y, box.right - 2, box.center.y, color, thickness=thickness)
    canvas.line(left, low_y, low_end, low_y, color, thickness=thickness)
    canvas.line(low_end, low_y, low_end, low_y + hook, color, thickness=thickness)


#: Blade directions for the fan, as tenths of the reach: 12, 4 and 8 o'clock.
_FAN_BLADES: Final = ((0, -10), (-9, 5), (9, 5))


def _draw_fan(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    # Reach stops two pixels short of the edge because a thick line is drawn
    # centred on its endpoints and so spreads half its width past them.
    reach = box.width // 2 - 2
    blade = max(thickness + 1, box.width // 5)
    for dx, dy in _FAN_BLADES:
        canvas.line(
            box.center.x,
            box.center.y,
            box.center.x + dx * reach // 10,
            box.center.y + dy * reach // 10,
            color,
            thickness=blade,
        )
    canvas.circle(box.center.x, box.center.y, max(1, box.width // 8), color, fill=True)


def _draw_flame(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:  # noqa: ARG001
    # A round base under two tongues of different height. One tongue drawn
    # straight would be `droplet`, and the tongues without the base would be
    # the symmetric triangle `warning` already owns; it takes both the lean and
    # the second lick before the silhouette reads as fire rather than as either.
    bulb = max(2, box.width * 3 // 10)
    bulb_y = box.bottom - 1 - bulb
    canvas.circle(box.center.x, bulb_y, bulb, color, fill=True)
    _triangle(canvas, (box.center.x + box.width // 5, box.y + 1), bulb_y, bulb, color)
    _triangle(
        canvas,
        (box.x + box.width // 4, box.y + box.height * 2 // 5),
        bulb_y,
        max(1, box.width // 6),
        color,
    )


# -- Status and connectivity ------------------------------------------------


def _draw_power(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    # The IEC symbol's ring is broken at the top. Erasing an arc is not
    # something the primitives can do, so the stem simply overlaps a closed
    # ring -- the same compromise every low-resolution rendering makes.
    ring_y = box.y + box.height * 55 // 100
    canvas.circle(box.center.x, ring_y, box.width * 2 // 5, color, fill=False, thickness=thickness)
    stem = max(1, thickness)
    top = box.y + box.height // 6
    canvas.rect(box.center.x - stem // 2, top, stem, ring_y - top, color)


#: Each wifi chevron as (half-span, peak height), in percent of the box. Three
#: nested chevrons over a dot: the closest a set without arcs gets to the
#: signal fan, and the form everything from phones to routers already uses.
_WIFI_ARCS: Final = ((45, 8), (30, 35), (15, 55))


def _draw_wifi(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    dot = max(1, box.width // 12)
    canvas.circle(box.center.x, box.bottom - 2 - dot, dot, color, fill=True)
    for span_percent, peak_percent in _WIFI_ARCS:
        span = box.width * span_percent // 100
        if span < 1:
            continue
        peak_y = box.y + box.height * peak_percent // 100
        drop_y = peak_y + span // 2
        canvas.line(box.center.x - span, drop_y, box.center.x, peak_y, color, thickness=thickness)
        canvas.line(box.center.x, peak_y, box.center.x + span, drop_y, color, thickness=thickness)


def _draw_signal(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:  # noqa: ARG001
    bars = 4
    bar = max(1, box.width // 6)
    gap = max(1, box.width // 12)
    left = box.center.x - (bars * bar + (bars - 1) * gap) // 2
    for index in range(bars):
        height = (index + 1) * (box.height - 2) // bars
        canvas.rect(left + index * (bar + gap), box.bottom - 1 - height, bar, height, color)


def _draw_clock(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:
    radius = box.width // 2 - 1
    center = box.center
    canvas.circle(center.x, center.y, radius, color, fill=False, thickness=thickness)
    canvas.line(center.x, center.y, center.x, center.y - radius // 2, color, thickness=thickness)
    canvas.line(
        center.x, center.y, center.x + radius * 2 // 3, center.y, color, thickness=thickness
    )


def _draw_bell(canvas: Canvas, box: Rect, color: Color, thickness: int) -> None:  # noqa: ARG001
    nub = max(1, box.width // 10)
    crown_y = box.y + box.height // 6
    canvas.rect(box.center.x - nub // 2, box.y + 1, nub, crown_y - box.y, color)
    rim_y = box.y + box.height * 7 // 10
    # A domed cap over a flaring body. Without the cap the taper alone comes to
    # a shoulder too sharply and the whole thing reads as a fir tree.
    shoulder = max(1, box.width * 3 // 16)
    canvas.circle(box.center.x, crown_y + shoulder, shoulder, color, fill=True)
    _taper(
        canvas, box.center.x, (crown_y + shoulder, shoulder), (rim_y, box.width * 7 // 20), color
    )
    flare = max(1, box.height // 10)
    canvas.rect(box.center.x - box.width * 2 // 5, rim_y, box.width * 4 // 5, flare, color)
    clapper = max(1, box.width // 12)
    canvas.circle(box.center.x, rim_y + flare + clapper, clapper, color, fill=True)


_PAINTERS: dict[IconName, Callable[[Canvas, Rect, Color, int], None]] = {
    IconName.CIRCLE: _draw_circle,
    IconName.DOT: _draw_dot,
    IconName.SQUARE: _draw_square,
    IconName.CHECK: _draw_check,
    IconName.CROSS: _draw_cross,
    IconName.WARNING: _draw_warning,
    IconName.INFO: _draw_info,
    IconName.PLUS: _draw_plus,
    IconName.MINUS: _draw_minus,
    IconName.ARROW_UP: _draw_arrow_up,
    IconName.ARROW_DOWN: _draw_arrow_down,
    IconName.HOME: _draw_home,
    IconName.DOOR: _draw_door,
    IconName.LOCK: _draw_lock,
    IconName.UNLOCK: _draw_unlock,
    IconName.LIGHTBULB: _draw_lightbulb,
    IconName.PERSON: _draw_person,
    IconName.PLUG: _draw_plug,
    IconName.THERMOMETER: _draw_thermometer,
    IconName.DROPLET: _draw_droplet,
    IconName.SUN: _draw_sun,
    IconName.CLOUD: _draw_cloud,
    IconName.WIND: _draw_wind,
    IconName.FAN: _draw_fan,
    IconName.FLAME: _draw_flame,
    IconName.BOLT: _draw_bolt,
    IconName.BATTERY: _draw_battery,
    IconName.POWER: _draw_power,
    IconName.WIFI: _draw_wifi,
    IconName.SIGNAL: _draw_signal,
    IconName.CLOCK: _draw_clock,
    IconName.BELL: _draw_bell,
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

    @name_of_symbol.setter
    def name_of_symbol(self, value: IconName) -> None:
        if value != self._symbol:
            self._symbol = value
            self.mark_dirty()

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
