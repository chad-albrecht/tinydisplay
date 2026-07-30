"""Dashboard definitions: YAML in, a validated description out.

This module is to the Home Assistant layer what
:mod:`tinydisplay.ht32.protocol` is to the panel driver -- the part that turns
a caller's intent into a checked structure, does no I/O, and can therefore be
tested exhaustively. Nothing here builds a widget or reads an entity; it only
decides whether a document makes sense, and says precisely where it does not.

Two rules shape the validation:

**Unknown keys are errors.** A dashboard is hand-written YAML, and the failure
mode of a permissive parser is a panel that renders perfectly while quietly
ignoring the ``color:`` you misspelled as ``colour:``. Rejecting the key turns
a puzzling afternoon into a message.

**Errors carry a path.** ``root.children[2].warning_at must be between 0 and 1,
got 1.5`` is actionable; "invalid value" is not, and a dashboard is a nested
document where the same key name appears at many depths.

The document itself is Lovelace-flavoured: every node has a ``type``,
containers have ``children``, and the sizing of a child is written on the child
rather than in a wrapper.

.. code-block:: yaml

    theme: midnight
    root:
      type: stack
      axis: vertical
      spacing: 4
      padding: 6
      children:
        - type: label
          size: 20
          text: "{{ sensor.kitchen_temperature.name }}"
          color: muted
          align: center
        - type: label
          text: "{{ sensor.kitchen_temperature | round(1) }} C"
          color: accent
          align: center
          valign: middle
          shrink_to_fit: true
        - type: gauge
          size: 14
          entity: sensor.cpu_percent
          max: 100
          color: success
          warning_at: 0.8
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import yaml

from tinydisplay.core import Color, HorizontalAlign, VerticalAlign
from tinydisplay.homeassistant.errors import DashboardConfigError, TemplateError
from tinydisplay.homeassistant.state import is_entity_id
from tinydisplay.homeassistant.template import UNAVAILABLE_TEXT, Template
from tinydisplay.widgets import THEMES, Align, Axis, IconName, Theme

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from tinydisplay.homeassistant.state import StateSource

__all__ = [
    "NODE_TYPES",
    "THEME_ROLES",
    "ColorRef",
    "DashboardSpec",
    "Insets",
    "NodeSpec",
    "ScreenSpec",
    "ValueRef",
    "load_dashboard",
    "parse_dashboard",
    "parse_dashboard_yaml",
]

#: The colour roles a theme defines, derived from :class:`Theme` rather than
#: listed, so adding a role cannot leave this behind.
THEME_ROLES: Final[frozenset[str]] = frozenset(item.name for item in fields(Theme))

#: Every node type a dashboard may use.
NODE_TYPES: Final[frozenset[str]] = frozenset(
    {"stack", "grid", "label", "gauge", "progress", "sparkline", "icon", "image", "spacer"}
)

_CONTAINER_TYPES: Final[frozenset[str]] = frozenset({"stack", "grid"})

#: Layout hints every node accepts. Read by whichever container holds the node;
#: a ``row`` on a child of a stack is meaningless but harmless, and rejecting
#: it would mean the validator had to know its parent.
#: ``cross_align`` rather than ``align`` because a label already spends
#: ``align`` on its text, and one key parsed by two enums meant only the value
#: they happened to share -- ``center`` -- was accepted on a label at all. It
#: pairs with ``cross_size``, which is the axis it acts on.
_LAYOUT_KEYS: Final[frozenset[str]] = frozenset(
    {"size", "weight", "cross_align", "cross_size", "row", "column", "row_span", "column_span"}
)

#: Keys every node accepts regardless of type.
_COMMON_KEYS: Final[frozenset[str]] = _LAYOUT_KEYS | {"type", "name", "visible", "padding"}

_DEFAULT_THEME: Final = "midnight"
_DEFAULT_SPARKLINE_CAPACITY: Final = 60


# ---------------------------------------------------------------------------
# Error reporting
# ---------------------------------------------------------------------------


def _fail(path: str, message: str) -> DashboardConfigError:
    """Build an error naming the location in the document."""
    return DashboardConfigError(f"{path}: {message}")


def _child_path(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _index_path(path: str, index: int) -> str:
    return f"{path}[{index}]"


# ---------------------------------------------------------------------------
# Resolvable references
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ColorRef:
    """A colour written in a dashboard, resolved against a theme.

    A colour can be named three ways, and all three end up here:

    - a theme role (``accent``), which is the one to prefer, because it follows
      a palette swap and is checked for contrast;
    - a literal (``"#ff5d73"``), for the case a role does not cover;
    - a mapping from entity state to either of the above, which is how a
      dashboard says "green when the door is shut, red when it is open".

    Attributes:
        role: The theme role to use, if this is a role reference.
        literal: The fixed colour, if this is a literal.
        states: State-to-reference mapping, if this is state-dependent.
        entity_id: The entity whose state selects from ``states``.
    """

    role: str | None = None
    literal: Color | None = None
    states: Mapping[str, ColorRef] = field(default_factory=dict)
    entity_id: str | None = None

    @property
    def entity_ids(self) -> frozenset[str]:
        """Every entity this reference reads, which is at most one."""
        return frozenset({self.entity_id}) if self.entity_id else frozenset()

    @property
    def is_dynamic(self) -> bool:
        """Whether the resolved colour depends on entity state."""
        return bool(self.states)

    def resolve(self, theme: Theme, source: StateSource | None = None) -> Color:
        """The colour to draw with right now.

        Never raises: a state with no mapping falls back to the ``default``
        key, and then to the theme's ``text`` role. A render loop is the wrong
        place to discover that a sensor invented a state nobody listed.
        """
        if self.states:
            entity = source.get(self.entity_id or "") if source is not None else None
            key = entity.state if entity is not None else "default"
            chosen = self.states.get(key) or self.states.get("default")
            return chosen.resolve(theme, source) if chosen is not None else theme.text
        if self.literal is not None:
            return self.literal
        if self.role is not None:
            resolved: Color = getattr(theme, self.role)
            return resolved
        return theme.text


@dataclass(frozen=True, slots=True)
class ValueRef:
    """Where a numeric widget reads its number from.

    Either an entity -- optionally one of its attributes -- or a template whose
    rendered text is parsed as a number. Exactly one, decided at parse time, so
    the render loop never has to ask.

    Attributes:
        entity_id: The entity to read, if this is an entity reference.
        attribute: An attribute of that entity instead of its state.
        template: The template to render and parse, if this is a template
            reference.
    """

    entity_id: str | None = None
    attribute: str | None = None
    template: Template | None = None

    @property
    def entity_ids(self) -> frozenset[str]:
        """Every entity this reference reads."""
        if self.template is not None:
            return self.template.entity_ids
        return frozenset({self.entity_id}) if self.entity_id else frozenset()

    def read(self, source: StateSource) -> float | None:
        """The current number, or ``None`` if there is not one.

        ``None`` rather than zero, so a widget can tell "the sensor is down"
        apart from "the sensor reads zero" and draw them differently.
        """
        if self.template is not None:
            text = self.template.render(source, unavailable="")
            try:
                return float(text.strip())
            except ValueError:
                return None

        entity = source.get(self.entity_id or "")
        if entity is None:
            return None
        if self.attribute is not None:
            raw = entity.attribute(self.attribute)
            try:
                return float(raw)
            except (TypeError, ValueError):
                return None
        return entity.numeric


@dataclass(frozen=True, slots=True)
class Insets:
    """Padding around a node, in pixels."""

    left: int = 0
    top: int = 0
    right: int = 0
    bottom: int = 0

    @property
    def is_zero(self) -> bool:
        """Whether this adds no padding at all."""
        return not (self.left or self.top or self.right or self.bottom)


# ---------------------------------------------------------------------------
# The parsed document
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """One node of a dashboard, validated but not yet built.

    Attributes:
        kind: The node's ``type`` from the document. Named ``kind`` because
            ``type`` is a builtin and reads badly as an attribute.
        options: Type-specific settings, already validated and converted --
            templates parsed, colours resolved to :class:`ColorRef`, enums to
            their enum members.
        children: Child nodes, empty for everything but containers.
        padding: Insets to apply around this node.
        path: Where this node sits in the document, for error messages.
    """

    kind: str
    options: Mapping[str, Any] = field(default_factory=dict)
    children: tuple[NodeSpec, ...] = ()
    padding: Insets = Insets()
    path: str = "root"

    # -- Layout hints, read by whichever container holds this node ----------
    size: int | None = None
    weight: float = 1.0
    align: Align = Align.STRETCH
    cross_size: int | None = None
    row: int = 0
    column: int = 0
    row_span: int = 1
    column_span: int = 1

    def walk(self) -> Iterator[NodeSpec]:
        """Yield this node and every descendant, depth first."""
        yield self
        for child in self.children:
            yield from child.walk()

    @property
    def entity_ids(self) -> frozenset[str]:
        """Every entity this node and its descendants read."""
        found: set[str] = set()
        for node in self.walk():
            for value in node.options.values():
                found |= _referenced_entities(value)
        return frozenset(found)


def _referenced_entities(value: Any) -> frozenset[str]:
    """Entity ids reachable from one validated option value."""
    if isinstance(value, Template | ColorRef | ValueRef):
        return value.entity_ids
    if isinstance(value, str) and is_entity_id(value):
        return frozenset({value})
    return frozenset()


@dataclass(frozen=True, slots=True)
class ScreenSpec:
    """One screen of a dashboard.

    A dashboard with a bare ``root`` has exactly one of these, unnamed. The
    name is only ever shown in logs and diagnostics -- a panel this size has no
    room to label itself, and a screen that spent pixels saying which screen it
    was would be a poor trade.
    """

    root: NodeSpec
    name: str | None = None

    @property
    def entity_ids(self) -> frozenset[str]:
        """Every entity this screen reads."""
        return self.root.entity_ids


@dataclass(frozen=True, slots=True)
class DashboardSpec:
    """A whole dashboard definition.

    Attributes:
        screens: The screens, in the order they are shown. Always at least one.
        theme: The palette, already quantised for the target panel.
        theme_name: The name it was chosen by, for logs and diagnostics.
        background: What to clear the canvas to before drawing.
        unavailable: Placeholder text for values that cannot be read.
        rotate_every: Seconds between screens, or ``None`` to hold the first
            one. Ignored when there is only one screen, because rotating
            through a single screen is just a repaint on a timer -- which
            ``max_interval`` already does, and better.
    """

    screens: tuple[ScreenSpec, ...]
    theme: Theme
    theme_name: str
    background: ColorRef
    unavailable: str = UNAVAILABLE_TEXT
    rotate_every: float | None = None

    @property
    def root(self) -> NodeSpec:
        """The first screen's root, for callers that predate screens."""
        return self.screens[0].root

    @property
    def entity_ids(self) -> frozenset[str]:
        """Every entity this dashboard reads, across every screen.

        The union rather than the visible screen's, because the render loop
        subscribes to this once: a sensor that only appears on screen three
        still has to wake the loop, or that screen would show whatever it said
        the last time it happened to be on display.
        """
        found = self.background.entity_ids
        for screen in self.screens:
            found |= screen.entity_ids
        return found

    @property
    def rotates(self) -> bool:
        """Whether this dashboard cycles through more than one screen."""
        return self.rotate_every is not None and len(self.screens) > 1


# ---------------------------------------------------------------------------
# Primitive validators
# ---------------------------------------------------------------------------


def _require_mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise _fail(path, f"expected a mapping, got {_describe(value)}")
    for key in value:
        if not isinstance(key, str):
            raise _fail(path, f"keys must be strings, got {_describe(key)}")
    return value


def _describe(value: Any) -> str:
    """A short, readable description of an unexpected value."""
    if value is None:
        return "nothing"
    return f"{type(value).__name__} ({value!r})"


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise _fail(path, f"expected text, got {_describe(value)}")
    return value


def _boolean(value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(path, f"expected true or false, got {_describe(value)}")
    return value


def _integer(value: Any, path: str, *, minimum: int | None = None) -> int:
    """Validate a whole number.

    No upper bound, because nothing in the document has one: sizes, spacings
    and spans are bounded by the panel rather than by the schema. Fractions are
    the only bounded values, and those go through :func:`_number`.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise _fail(path, f"expected a whole number, got {_describe(value)}")
    if minimum is not None and value < minimum:
        raise _fail(path, f"must be at least {minimum}, got {value}")
    return value


def _number(
    value: Any,
    path: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _fail(path, f"expected a number, got {_describe(value)}")
    if minimum is not None and value < minimum:
        raise _fail(path, f"must be at least {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise _fail(path, f"must be at most {maximum}, got {value}")
    return float(value)


def _entity_id(value: Any, path: str) -> str:
    text = _string(value, path)
    if not is_entity_id(text):
        raise _fail(path, f"{text!r} is not a valid entity id (expected domain.object_id)")
    return text


def _template(value: Any, path: str) -> Template:
    # Numbers are accepted so that `text: 42` does not need quoting in YAML;
    # anything else is a mistake worth naming.
    if isinstance(value, int | float) and not isinstance(value, bool):
        return Template.parse(str(value))
    text = _string(value, path)
    try:
        return Template.parse(text)
    except TemplateError as exc:
        raise _fail(path, str(exc)) from exc


def _choice[T](value: Any, path: str, options: Mapping[str, T]) -> T:
    text = _string(value, path)
    chosen = options.get(text.lower())
    if chosen is None:
        known = ", ".join(sorted(options))
        raise _fail(path, f"unknown value {text!r}; expected one of {known}")
    return chosen


_AXES: Final = {item.value: item for item in Axis}
_ALIGNS: Final = {item.value: item for item in Align}
_ICONS: Final = {item.value: item for item in IconName}
_H_ALIGNS: Final = {item.value: item for item in HorizontalAlign}
_V_ALIGNS: Final = {item.value: item for item in VerticalAlign}


def _color(
    value: Any,
    path: str,
    *,
    entity_id: str | None = None,
    static: bool = False,
) -> ColorRef:
    """Parse a colour: a theme role, a hex literal, or a state mapping.

    ``static`` marks the colours the widget library only accepts at
    construction -- a gauge's track, a sparkline's line. A state mapping there
    could never be reapplied, so it is rejected rather than silently frozen at
    whatever the first state happened to be.
    """
    if isinstance(value, dict):
        mapping = _require_mapping(value, path)
        if static:
            raise _fail(
                path,
                "this colour is fixed when the widget is built, so it cannot depend on "
                "entity state; use a single role or hex value",
            )
        if entity_id is None:
            raise _fail(
                path,
                "a state-dependent colour needs an 'entity' on the same node to select from",
            )
        states = {
            str(state): _color(nested, _child_path(path, str(state)))
            for state, nested in mapping.items()
        }
        return ColorRef(states=states, entity_id=entity_id)

    text = _string(value, path)
    if text in THEME_ROLES:
        return ColorRef(role=text)
    if text.startswith("#"):
        try:
            return ColorRef(literal=Color.from_hex(text))
        except ValueError as exc:
            raise _fail(path, f"{text!r} is not a valid hex colour") from exc

    known = ", ".join(sorted(THEME_ROLES))
    raise _fail(path, f"unknown colour {text!r}; expected a hex value or one of {known}")


def _value_ref(options: Mapping[str, Any], path: str) -> ValueRef:
    """Parse the ``entity``/``attribute``/``value`` trio the numeric widgets share."""
    has_entity = "entity" in options
    has_value = "value" in options

    if has_entity and has_value:
        raise _fail(path, "give either 'entity' or 'value', not both")
    if not has_entity and not has_value:
        raise _fail(path, "needs an 'entity' to read, or a 'value' template")

    if has_value:
        if "attribute" in options:
            raise _fail(
                _child_path(path, "attribute"),
                "'attribute' reads from an 'entity'; a 'value' template already says what to read",
            )
        return ValueRef(template=_template(options["value"], _child_path(path, "value")))

    entity_id = _entity_id(options["entity"], _child_path(path, "entity"))
    attribute = (
        _string(options["attribute"], _child_path(path, "attribute"))
        if "attribute" in options
        else None
    )
    return ValueRef(entity_id=entity_id, attribute=attribute)


def _insets(value: Any, path: str) -> Insets:
    """Parse ``padding``: one number, or a mapping of edges."""
    if isinstance(value, int) and not isinstance(value, bool):
        amount = _integer(value, path, minimum=0)
        return Insets(amount, amount, amount, amount)

    mapping = _require_mapping(value, path)
    allowed = {"all", "horizontal", "vertical", "left", "top", "right", "bottom"}
    _reject_unknown(mapping, allowed, path)

    def edge(*names: str) -> int:
        for name in names:
            if name in mapping:
                return _integer(mapping[name], _child_path(path, name), minimum=0)
        return 0

    return Insets(
        left=edge("left", "horizontal", "all"),
        top=edge("top", "vertical", "all"),
        right=edge("right", "horizontal", "all"),
        bottom=edge("bottom", "vertical", "all"),
    )


def _reject_unknown(
    mapping: Mapping[str, Any],
    allowed: frozenset[str] | set[str],
    path: str,
) -> None:
    """Raise on the first key that is not recognised.

    Sorted so the message is stable, and listing the alternatives because the
    overwhelmingly common cause is a near miss.
    """
    unknown = sorted(set(mapping) - set(allowed))
    if not unknown:
        return
    known = ", ".join(sorted(allowed))
    raise _fail(_child_path(path, unknown[0]), f"unknown key; this node accepts {known}")


# ---------------------------------------------------------------------------
# Node parsing
# ---------------------------------------------------------------------------


def _parse_node(value: Any, path: str) -> NodeSpec:
    """Validate one node and everything under it."""
    mapping = _require_mapping(value, path)
    if "type" not in mapping:
        raise _fail(path, f"needs a 'type'; expected one of {', '.join(sorted(NODE_TYPES))}")

    kind = _string(mapping["type"], _child_path(path, "type")).lower()
    if kind not in NODE_TYPES:
        known = ", ".join(sorted(NODE_TYPES))
        raise _fail(_child_path(path, "type"), f"unknown type {kind!r}; expected one of {known}")

    parser = _NODE_PARSERS[kind]
    _reject_unknown(mapping, _COMMON_KEYS | parser.keys, path)

    options = parser.parse(mapping, path)
    if "visible" in mapping:
        options = {**options, "visible": _boolean(mapping["visible"], _child_path(path, "visible"))}
    if "name" in mapping:
        options = {**options, "name": _string(mapping["name"], _child_path(path, "name"))}

    children = _parse_children(mapping, path) if kind in _CONTAINER_TYPES else ()

    return NodeSpec(
        kind=kind,
        options=options,
        children=children,
        padding=(
            _insets(mapping["padding"], _child_path(path, "padding"))
            if "padding" in mapping
            else Insets()
        ),
        path=path,
        **_layout_hints(mapping, path),
    )


def _parse_children(mapping: Mapping[str, Any], path: str) -> tuple[NodeSpec, ...]:
    """Validate a container's ``children`` list."""
    raw = mapping.get("children", [])
    if not isinstance(raw, list):
        raise _fail(_child_path(path, "children"), f"expected a list, got {_describe(raw)}")
    children_path = _child_path(path, "children")
    return tuple(
        _parse_node(child, _index_path(children_path, index)) for index, child in enumerate(raw)
    )


def _layout_hints(mapping: Mapping[str, Any], path: str) -> dict[str, Any]:
    """Pull the sizing and placement keys every node may carry."""
    hints: dict[str, Any] = {}
    if "size" in mapping:
        hints["size"] = _integer(mapping["size"], _child_path(path, "size"), minimum=0)
    if "weight" in mapping:
        hints["weight"] = _number(mapping["weight"], _child_path(path, "weight"), minimum=0)
        if hints["weight"] <= 0:
            raise _fail(_child_path(path, "weight"), "must be greater than zero")
    if "cross_align" in mapping:
        hints["align"] = _choice(mapping["cross_align"], _child_path(path, "cross_align"), _ALIGNS)
    if "cross_size" in mapping:
        hints["cross_size"] = _integer(
            mapping["cross_size"], _child_path(path, "cross_size"), minimum=0
        )
    for key in ("row", "column"):
        if key in mapping:
            hints[key] = _integer(mapping[key], _child_path(path, key), minimum=0)
    for key in ("row_span", "column_span"):
        if key in mapping:
            hints[key] = _integer(mapping[key], _child_path(path, key), minimum=1)
    return hints


@dataclass(frozen=True, slots=True)
class _NodeParser:
    """The keys one node type accepts, and how to validate them."""

    keys: frozenset[str]
    parse: Callable[[Mapping[str, Any], str], dict[str, Any]]


def _parse_stack(mapping: Mapping[str, Any], path: str) -> dict[str, Any]:
    return {
        "axis": (
            _choice(mapping["axis"], _child_path(path, "axis"), _AXES)
            if "axis" in mapping
            else Axis.VERTICAL
        ),
        "spacing": (
            _integer(mapping["spacing"], _child_path(path, "spacing"), minimum=0)
            if "spacing" in mapping
            else 0
        ),
    }


def _parse_grid(mapping: Mapping[str, Any], path: str) -> dict[str, Any]:
    for key in ("rows", "columns"):
        if key not in mapping:
            raise _fail(path, f"a grid needs '{key}'")
    return {
        "rows": _integer(mapping["rows"], _child_path(path, "rows"), minimum=1),
        "columns": _integer(mapping["columns"], _child_path(path, "columns"), minimum=1),
        "spacing": (
            _integer(mapping["spacing"], _child_path(path, "spacing"), minimum=0)
            if "spacing" in mapping
            else 0
        ),
    }


def _parse_label(mapping: Mapping[str, Any], path: str) -> dict[str, Any]:
    if "text" not in mapping:
        raise _fail(path, "a label needs 'text'")
    entity_id = (
        _entity_id(mapping["entity"], _child_path(path, "entity")) if "entity" in mapping else None
    )
    options: dict[str, Any] = {
        "text": _template(mapping["text"], _child_path(path, "text")),
        "color": (
            _color(mapping["color"], _child_path(path, "color"), entity_id=entity_id)
            if "color" in mapping
            else ColorRef(role="text")
        ),
        "align": (
            _choice(mapping["align"], _child_path(path, "align"), _H_ALIGNS)
            if "align" in mapping
            else HorizontalAlign.LEFT
        ),
        "valign": (
            _choice(mapping["valign"], _child_path(path, "valign"), _V_ALIGNS)
            if "valign" in mapping
            else VerticalAlign.TOP
        ),
        "wrap": _boolean(mapping["wrap"], _child_path(path, "wrap")) if "wrap" in mapping else True,
        "shrink_to_fit": (
            _boolean(mapping["shrink_to_fit"], _child_path(path, "shrink_to_fit"))
            if "shrink_to_fit" in mapping
            else False
        ),
    }
    if "font_size" in mapping:
        options["font_size"] = _integer(
            mapping["font_size"], _child_path(path, "font_size"), minimum=1
        )
    if entity_id is not None:
        options["entity"] = entity_id
    return options


def _parse_ranged(mapping: Mapping[str, Any], path: str) -> dict[str, Any]:
    """The ``value``/``min``/``max`` trio shared by the numeric widgets."""
    minimum = _number(mapping["min"], _child_path(path, "min")) if "min" in mapping else 0.0
    maximum = _number(mapping["max"], _child_path(path, "max")) if "max" in mapping else 100.0
    if maximum < minimum:
        raise _fail(path, f"'max' must not be below 'min', got {minimum}..{maximum}")
    return {"value": _value_ref(mapping, path), "min": minimum, "max": maximum}


def _parse_gauge(mapping: Mapping[str, Any], path: str) -> dict[str, Any]:
    entity_id = mapping.get("entity") if isinstance(mapping.get("entity"), str) else None
    options = _parse_ranged(mapping, path)
    options.update(
        {
            "segments": (
                _integer(mapping["segments"], _child_path(path, "segments"), minimum=1)
                if "segments" in mapping
                else 10
            ),
            "color": (
                _color(mapping["color"], _child_path(path, "color"), entity_id=entity_id)
                if "color" in mapping
                else ColorRef(role="accent")
            ),
            "gap": (
                _integer(mapping["gap"], _child_path(path, "gap"), minimum=0)
                if "gap" in mapping
                else 2
            ),
            "vertical": (
                _boolean(mapping["vertical"], _child_path(path, "vertical"))
                if "vertical" in mapping
                else False
            ),
        }
    )
    if "track_color" in mapping:
        options["track_color"] = _color(
            mapping["track_color"], _child_path(path, "track_color"), static=True
        )
    if "warning_at" in mapping:
        options["warning_at"] = _number(
            mapping["warning_at"], _child_path(path, "warning_at"), minimum=0.0, maximum=1.0
        )
    if "warning_color" in mapping:
        options["warning_color"] = _color(
            mapping["warning_color"], _child_path(path, "warning_color"), static=True
        )
    return options


def _parse_progress(mapping: Mapping[str, Any], path: str) -> dict[str, Any]:
    entity_id = mapping.get("entity") if isinstance(mapping.get("entity"), str) else None
    options = _parse_ranged(mapping, path)
    options.update(
        {
            "color": (
                _color(mapping["color"], _child_path(path, "color"), entity_id=entity_id)
                if "color" in mapping
                else ColorRef(role="accent")
            ),
            "radius": (
                _integer(mapping["radius"], _child_path(path, "radius"), minimum=0)
                if "radius" in mapping
                else 2
            ),
            "vertical": (
                _boolean(mapping["vertical"], _child_path(path, "vertical"))
                if "vertical" in mapping
                else False
            ),
        }
    )
    if "track_color" in mapping:
        options["track_color"] = _color(
            mapping["track_color"], _child_path(path, "track_color"), static=True
        )
    return options


def _parse_sparkline(mapping: Mapping[str, Any], path: str) -> dict[str, Any]:
    options: dict[str, Any] = {
        "value": _value_ref(mapping, path),
        "color": (
            _color(mapping["color"], _child_path(path, "color"), static=True)
            if "color" in mapping
            else ColorRef(role="accent")
        ),
        "capacity": (
            _integer(mapping["capacity"], _child_path(path, "capacity"), minimum=2)
            if "capacity" in mapping
            else _DEFAULT_SPARKLINE_CAPACITY
        ),
    }
    if "fill_color" in mapping:
        options["fill_color"] = _color(
            mapping["fill_color"], _child_path(path, "fill_color"), static=True
        )
    if "min" in mapping:
        options["min"] = _number(mapping["min"], _child_path(path, "min"))
    if "max" in mapping:
        options["max"] = _number(mapping["max"], _child_path(path, "max"))
    if "min" in options and "max" in options and options["max"] < options["min"]:
        raise _fail(path, "'max' must not be below 'min'")
    return options


def _parse_icon(mapping: Mapping[str, Any], path: str) -> dict[str, Any]:
    if "icon" not in mapping:
        known = ", ".join(sorted(_ICONS))
        raise _fail(path, f"an icon needs 'icon'; available icons are {known}")
    entity_id = (
        _entity_id(mapping["entity"], _child_path(path, "entity")) if "entity" in mapping else None
    )
    options: dict[str, Any] = {
        "icon": _choice(mapping["icon"], _child_path(path, "icon"), _ICONS),
        "color": (
            _color(mapping["color"], _child_path(path, "color"), entity_id=entity_id)
            if "color" in mapping
            else ColorRef(role="text")
        ),
        "thickness": (
            _integer(mapping["thickness"], _child_path(path, "thickness"), minimum=1)
            if "thickness" in mapping
            else 1
        ),
    }
    if entity_id is not None:
        options["entity"] = entity_id
    return options


def _parse_image(mapping: Mapping[str, Any], path: str) -> dict[str, Any]:
    if "path" not in mapping:
        raise _fail(path, "an image needs 'path'")
    return {
        "path": _string(mapping["path"], _child_path(path, "path")),
        "fit": _boolean(mapping["fit"], _child_path(path, "fit")) if "fit" in mapping else True,
    }


def _parse_spacer(mapping: Mapping[str, Any], path: str) -> dict[str, Any]:  # noqa: ARG001
    """A spacer has nothing to configure; it exists to occupy a slot."""
    return {}


_NODE_PARSERS: Final[dict[str, _NodeParser]] = {
    "stack": _NodeParser(frozenset({"axis", "spacing", "children"}), _parse_stack),
    "grid": _NodeParser(frozenset({"rows", "columns", "spacing", "children"}), _parse_grid),
    "label": _NodeParser(
        frozenset(
            {"text", "color", "align", "valign", "wrap", "shrink_to_fit", "font_size", "entity"}
        ),
        _parse_label,
    ),
    "gauge": _NodeParser(
        frozenset(
            {
                "entity",
                "attribute",
                "value",
                "min",
                "max",
                "segments",
                "color",
                "track_color",
                "gap",
                "vertical",
                "warning_at",
                "warning_color",
            }
        ),
        _parse_gauge,
    ),
    "progress": _NodeParser(
        frozenset(
            {
                "entity",
                "attribute",
                "value",
                "min",
                "max",
                "color",
                "track_color",
                "radius",
                "vertical",
            }
        ),
        _parse_progress,
    ),
    "sparkline": _NodeParser(
        frozenset(
            {"entity", "attribute", "value", "color", "fill_color", "min", "max", "capacity"}
        ),
        _parse_sparkline,
    ),
    "icon": _NodeParser(frozenset({"icon", "color", "thickness", "entity"}), _parse_icon),
    "image": _NodeParser(frozenset({"path", "fit"}), _parse_image),
    "spacer": _NodeParser(frozenset(), _parse_spacer),
}


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

_DOCUMENT_KEYS: Final[frozenset[str]] = frozenset(
    {"theme", "background", "root", "screens", "rotate_every", "unavailable", "pixel_format"}
)

#: Paths for document-level errors. Every other path is derived from a parent
#: key; these are at the top and so name themselves.
_DOCUMENT_PATH: Final = "dashboard"
_THEME_PATH: Final = "theme"
_BACKGROUND_PATH: Final = "background"

_SCREENS_PATH: Final = "screens"
_ROTATE_PATH: Final = "rotate_every"

#: Keys a screen inside ``screens`` accepts.
_SCREEN_KEYS: Final[frozenset[str]] = frozenset({"root", "name"})


def _parse_screens(mapping: Mapping[str, Any]) -> tuple[ScreenSpec, ...]:
    """Read either a bare ``root`` or a list of ``screens``.

    Both spellings are supported and exactly one must be used. ``root`` came
    first and every dashboard written so far uses it, so it keeps working
    unchanged and simply means a dashboard of one screen -- there is no reason
    to make somebody wrap a single screen in a list to say what they already
    said.
    """
    has_root = "root" in mapping
    has_screens = "screens" in mapping

    if has_root and has_screens:
        raise _fail(_DOCUMENT_PATH, "use either 'root' for one screen or 'screens' for several")
    if not has_root and not has_screens:
        raise _fail(_DOCUMENT_PATH, "needs a 'root' node, or a 'screens' list")

    if has_root:
        return (ScreenSpec(root=_parse_node(mapping["root"], "root")),)

    raw = mapping["screens"]
    if not isinstance(raw, list):
        raise _fail(_SCREENS_PATH, f"expected a list, got {_describe(raw)}")
    if not raw:
        raise _fail(_SCREENS_PATH, "needs at least one screen")

    return tuple(
        _parse_screen(screen, _index_path(_SCREENS_PATH, index)) for index, screen in enumerate(raw)
    )


def _parse_screen(value: Any, path: str) -> ScreenSpec:
    """Validate one entry of a ``screens`` list."""
    mapping = _require_mapping(value, path)
    _reject_unknown(mapping, _SCREEN_KEYS, path)

    if "root" not in mapping:
        raise _fail(path, "a screen needs a 'root' node")

    return ScreenSpec(
        root=_parse_node(mapping["root"], _child_path(path, "root")),
        name=_string(mapping["name"], _child_path(path, "name")) if "name" in mapping else None,
    )


def parse_dashboard(document: Any) -> DashboardSpec:
    """Validate an already-loaded dashboard document.

    Args:
        document: The mapping a YAML loader produced.

    Raises:
        DashboardConfigError: If the document is not a valid dashboard. The
            message names the offending key's path within the document.

    Example:
        >>> from tinydisplay.homeassistant import parse_dashboard
        >>> spec = parse_dashboard(
        ...     {
        ...         "theme": "midnight",
        ...         "root": {"type": "label", "text": "{{ sensor.a }}"},
        ...     }
        ... )
        >>> sorted(spec.entity_ids)
        ['sensor.a']
    """
    mapping = _require_mapping(document, _DOCUMENT_PATH)
    _reject_unknown(mapping, _DOCUMENT_KEYS, "")
    screens = _parse_screens(mapping)

    theme_name = (
        _string(mapping["theme"], _THEME_PATH).lower() if "theme" in mapping else _DEFAULT_THEME
    )
    base = THEMES.get(theme_name)
    if base is None:
        known = ", ".join(sorted(THEMES))
        raise _fail(_THEME_PATH, f"unknown theme {theme_name!r}; expected one of {known}")

    # Quantised here rather than at draw time: the panel's colour depth is a
    # property of the dashboard's target, and a contrast ratio measured on the
    # unquantised palette is not the ratio the hardware delivers.
    theme = base.quantized()

    # Static, like the widget colours that are fixed at construction: the
    # canvas is cleared to this before anything is drawn, and there is no node
    # here whose entity a state mapping could be keyed on.
    background = (
        _color(mapping["background"], _BACKGROUND_PATH, static=True)
        if "background" in mapping
        else ColorRef(role="background")
    )

    unavailable = (
        _string(mapping["unavailable"], "unavailable")
        if "unavailable" in mapping
        else UNAVAILABLE_TEXT
    )

    rotate_every = (
        _number(mapping[_ROTATE_PATH], _ROTATE_PATH, minimum=0.5)
        if _ROTATE_PATH in mapping
        else None
    )
    if rotate_every is not None and len(screens) == 1:
        # Not an error -- a dashboard being cut down to one screen while the
        # key is left behind is ordinary -- but worth being explicit that it
        # does nothing, since `max_interval` is what repaints a still screen.
        rotate_every = None

    return DashboardSpec(
        screens=screens,
        theme=theme,
        theme_name=theme_name,
        background=background,
        unavailable=unavailable,
        rotate_every=rotate_every,
    )


def parse_dashboard_yaml(text: str) -> DashboardSpec:
    """Parse and validate a dashboard from YAML text.

    Raises:
        DashboardConfigError: If the YAML is malformed or the document is not a
            valid dashboard.

    Example:
        >>> from tinydisplay.homeassistant import parse_dashboard_yaml
        >>> spec = parse_dashboard_yaml("root:\\n  type: spacer\\n")
        >>> spec.root.kind
        'spacer'
    """
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        msg = f"dashboard is not valid YAML: {exc}"
        raise DashboardConfigError(msg) from exc
    return parse_dashboard(document)


def load_dashboard(path: Path | str) -> DashboardSpec:
    """Read and validate a dashboard from a file.

    Raises:
        DashboardConfigError: If the file cannot be read, is not valid YAML, or
            is not a valid dashboard. The path is included in the message,
            because the caller is usually a config flow reporting to someone
            who typed it.
    """
    location = Path(path)
    try:
        text = location.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"cannot read dashboard {location}: {exc}"
        raise DashboardConfigError(msg) from exc

    try:
        return parse_dashboard_yaml(text)
    except DashboardConfigError as exc:
        msg = f"{location}: {exc}"
        raise DashboardConfigError(msg) from exc
