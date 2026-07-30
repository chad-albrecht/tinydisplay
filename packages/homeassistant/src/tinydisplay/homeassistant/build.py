"""Turning a validated dashboard description into a live widget tree.

The tree is built once and then *updated*, rather than rebuilt every frame.
That is not a micro-optimisation: layout containers cache what they last laid
out into, dirty tracking propagates from the widget that changed, and a
sparkline is only a sparkline because it remembers what it was shown before.
Rebuilding would throw all three away every frame.

What comes out is a :class:`BuiltDashboard`: the root widget, plus a flat tuple
of *updaters* -- small closures that each know one widget, one reference into
the document, and how to push the current value into the former from the
latter. A dashboard with no templates and no state-dependent colours produces
zero updaters, and its render loop does no work between repaints at all.

The updaters are flat rather than a parallel tree because nothing needs the
shape: applying them in any order gives the same result, and a flat tuple is
the cheapest thing to walk several times a second.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from tinydisplay.core import Font, Widget
from tinydisplay.homeassistant.state import StaticStateSource
from tinydisplay.widgets import (
    Gauge,
    Grid,
    Icon,
    ImageWidget,
    Label,
    Padding,
    ProgressBar,
    Slot,
    Spacer,
    Sparkline,
    Stack,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tinydisplay.core import Color
    from tinydisplay.homeassistant.schema import ColorRef, DashboardSpec, NodeSpec, ValueRef
    from tinydisplay.homeassistant.state import StateSource
    from tinydisplay.widgets import Theme

__all__ = ["BuiltDashboard", "Updater", "build_dashboard", "build_node"]

#: Pushes the current state of the world into one widget.
type Updater = Callable[[StateSource], None]


@dataclass(frozen=True, slots=True)
class BuiltDashboard:
    """A widget tree and everything needed to keep it current.

    Attributes:
        root: The top-level widget. Its bounds are assigned by the caller,
            because only the caller knows how big the panel is.
        updaters: Applied in :meth:`update` to bring the tree in line with the
            current entity state.
    """

    root: Widget
    updaters: tuple[Updater, ...]

    def update(self, source: StateSource) -> None:
        """Push the current entity state into every bound widget.

        Never raises on account of the data: every updater reads through a
        reference that renders missing values as placeholders. A dashboard
        going quiet is a panel showing dashes, not a stopped render loop.
        """
        for updater in self.updaters:
            updater(source)


# ---------------------------------------------------------------------------
# Reference plumbing
# ---------------------------------------------------------------------------


def _bind_color(
    ref: ColorRef,
    theme: Theme,
    apply: Callable[[Color], None],
    updaters: list[Updater],
) -> None:
    """Apply a colour now, and arrange to reapply it when it can change.

    A static colour costs one call at build time and nothing thereafter, which
    is the case almost every colour in a dashboard is.
    """
    apply(ref.resolve(theme, None))
    if ref.is_dynamic:
        updaters.append(lambda source: apply(ref.resolve(theme, source)))


def _numeric_updater(
    ref: ValueRef,
    apply: Callable[[float], None],
    *,
    missing: float,
) -> Updater:
    """Read a number each update, substituting ``missing`` when there is none.

    The widgets take a float and clamp it, so an unreadable sensor draws as an
    empty gauge rather than stopping the frame. It is the widget library's
    "clamp when drawing, raise when constructing" rule reaching one layer up.
    """

    def update(source: StateSource) -> None:
        value = ref.read(source)
        apply(missing if value is None else value)

    return update


# ---------------------------------------------------------------------------
# Node builders
# ---------------------------------------------------------------------------


def _build_label(node: NodeSpec, theme: Theme, unavailable: str, updaters: list[Updater]) -> Widget:
    options = node.options
    font_size = options.get("font_size")
    label = Label(
        "",
        font=Font.default(font_size) if font_size is not None else None,
        align=options["align"],
        valign=options["valign"],
        wrap=options["wrap"],
        shrink_to_fit=options["shrink_to_fit"],
        visible=options.get("visible", True),
        name=options.get("name"),
    )

    template = options["text"]
    if template.is_constant:
        label.text = template.render(_NO_STATE, unavailable=unavailable)
    else:
        updaters.append(
            lambda source: setattr(label, "text", template.render(source, unavailable=unavailable))
        )

    _bind_color(options["color"], theme, lambda color: setattr(label, "color", color), updaters)
    return label


def _build_gauge(node: NodeSpec, theme: Theme, updaters: list[Updater]) -> Widget:
    options = node.options
    warning_color = options.get("warning_color")
    gauge = Gauge(
        options["min"],
        minimum=options["min"],
        maximum=options["max"],
        segments=options["segments"],
        gap=options["gap"],
        vertical=options["vertical"],
        warning_at=options.get("warning_at"),
        warning_color=warning_color.resolve(theme, None) if warning_color is not None else None,
        track_color=_optional_color(options.get("track_color"), theme),
        visible=options.get("visible", True),
        name=options.get("name"),
    )
    _bind_color(options["color"], theme, lambda color: setattr(gauge, "color", color), updaters)
    updaters.append(
        _numeric_updater(
            options["value"],
            lambda value: setattr(gauge, "value", value),
            missing=options["min"],
        )
    )
    return gauge


def _build_progress(node: NodeSpec, theme: Theme, updaters: list[Updater]) -> Widget:
    options = node.options
    bar = ProgressBar(
        options["min"],
        minimum=options["min"],
        maximum=options["max"],
        radius=options["radius"],
        vertical=options["vertical"],
        track_color=_optional_color(options.get("track_color"), theme),
        visible=options.get("visible", True),
        name=options.get("name"),
    )
    _bind_color(options["color"], theme, lambda color: setattr(bar, "color", color), updaters)
    updaters.append(
        _numeric_updater(
            options["value"],
            lambda value: setattr(bar, "value", value),
            missing=options["min"],
        )
    )
    return bar


def _build_sparkline(node: NodeSpec, theme: Theme, updaters: list[Updater]) -> Widget:
    options = node.options
    # A sparkline's colours are fixed at construction -- the widget exposes no
    # setters for them -- which is why the schema rejects a state-dependent
    # colour here rather than accepting one and quietly ignoring it.
    spark = Sparkline(
        color=options["color"].resolve(theme, None),
        fill_color=_optional_color(options.get("fill_color"), theme),
        minimum=options.get("min"),
        maximum=options.get("max"),
        capacity=options["capacity"],
        visible=options.get("visible", True),
        name=options.get("name"),
    )

    reference = options["value"]
    # A sample is recorded when the value *changes*, not on every repaint. The
    # loop above this one repaints for reasons unrelated to this entity -- a
    # neighbouring sensor moved, the periodic refresh came round -- and
    # sampling on repaint would let an unrelated widget stretch this one's
    # history. Real time-series history belongs to Home Assistant's recorder,
    # which is a larger feature than this widget.
    previous: list[float | None] = [None]

    def update(source: StateSource) -> None:
        value = reference.read(source)
        if value is not None and value != previous[0]:
            previous[0] = value
            spark.push(value)

    updaters.append(update)
    return spark


def _build_icon(node: NodeSpec, theme: Theme, updaters: list[Updater]) -> Widget:
    options = node.options
    icon = Icon(
        options["icon"],
        thickness=options["thickness"],
        visible=options.get("visible", True),
        widget_name=options.get("name"),
    )
    _bind_color(options["color"], theme, lambda color: setattr(icon, "color", color), updaters)
    return icon


def _build_image(node: NodeSpec) -> Widget:
    options = node.options
    return ImageWidget(
        options["path"],
        fit=options["fit"],
        visible=options.get("visible", True),
        name=options.get("name"),
    )


def _build_spacer(node: NodeSpec) -> Widget:
    options = node.options
    return Spacer(visible=options.get("visible", True), name=options.get("name"))


def _build_stack(
    node: NodeSpec,
    theme: Theme,
    unavailable: str,
    updaters: list[Updater],
) -> Widget:
    options = node.options
    slots = [
        Slot(
            build_node(child, theme, unavailable, updaters),
            size=child.size,
            weight=child.weight,
            align=child.align,
            cross_size=child.cross_size,
        )
        for child in node.children
    ]
    return Stack(
        options["axis"],
        slots=slots,
        spacing=options["spacing"],
        visible=options.get("visible", True),
        name=options.get("name"),
    )


def _build_grid(
    node: NodeSpec,
    theme: Theme,
    unavailable: str,
    updaters: list[Updater],
) -> Widget:
    options = node.options
    grid = Grid(
        options["rows"],
        options["columns"],
        spacing=options["spacing"],
        visible=options.get("visible", True),
        name=options.get("name"),
    )
    for child in node.children:
        grid.place(
            build_node(child, theme, unavailable, updaters),
            row=child.row,
            column=child.column,
            row_span=child.row_span,
            column_span=child.column_span,
        )
    return grid


def _optional_color(ref: ColorRef | None, theme: Theme) -> Color | None:
    """Resolve a colour that may be absent, statically.

    Track and fill colours are constructor-only on the widgets that take them,
    so a state-dependent one could not be reapplied anyway; the schema is what
    makes them static, and this is where that shows up.
    """
    return None if ref is None else ref.resolve(theme, None)


def build_node(
    node: NodeSpec,
    theme: Theme,
    unavailable: str,
    updaters: list[Updater],
) -> Widget:
    """Build one node and its descendants, appending updaters as it goes.

    Args:
        node: The validated node to build.
        theme: The palette colour roles resolve against.
        unavailable: Placeholder text for values that cannot be read.
        updaters: Accumulator. Appended to rather than returned, so that the
            recursion does not have to merge lists at every level.

    Returns:
        The widget, already wrapped in its padding if it asked for any.
    """
    widget: Widget
    match node.kind:
        case "label":
            widget = _build_label(node, theme, unavailable, updaters)
        case "stack":
            widget = _build_stack(node, theme, unavailable, updaters)
        case "grid":
            widget = _build_grid(node, theme, unavailable, updaters)
        case "gauge":
            widget = _build_gauge(node, theme, updaters)
        case "progress":
            widget = _build_progress(node, theme, updaters)
        case "sparkline":
            widget = _build_sparkline(node, theme, updaters)
        case "icon":
            widget = _build_icon(node, theme, updaters)
        case "image":
            widget = _build_image(node)
        case _:
            widget = _build_spacer(node)

    if not node.padding.is_zero:
        insets = node.padding
        widget = Padding(
            widget,
            left=insets.left,
            top=insets.top,
            right=insets.right,
            bottom=insets.bottom,
        )
    return widget


def build_dashboard(spec: DashboardSpec) -> BuiltDashboard:
    """Build the widget tree a dashboard describes.

    The tree has no bounds yet -- assigning those needs a canvas, and a
    dashboard is deliberately not tied to one panel size.

    Example:
        >>> from tinydisplay.homeassistant import build_dashboard, parse_dashboard
        >>> spec = parse_dashboard({"root": {"type": "label", "text": "{{ sensor.a }}"}})
        >>> built = build_dashboard(spec)
        >>> len(built.updaters)
        1
    """
    updaters: list[Updater] = []
    root = build_node(spec.root, spec.theme, spec.unavailable, updaters)
    return BuiltDashboard(root=root, updaters=tuple(updaters))


#: An empty state source, used to render constant templates at build time. A
#: template with no entity references never consults it, so what it holds does
#: not matter -- but passing ``None`` would mean the renderer had to cope with
#: a source that is not there, for the sake of one call site.
_NO_STATE: Final = StaticStateSource()
