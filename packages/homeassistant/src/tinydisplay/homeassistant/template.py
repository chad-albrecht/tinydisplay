"""Putting entity values into text, without depending on Home Assistant.

A dashboard needs to write ``{{ sensor.kitchen }} C`` somewhere. Home Assistant
would do that with Jinja, and using Jinja here would mean this package could
only run inside Home Assistant -- which would put the whole dashboard layer
outside the reach of the test suite and the simulator, and that trade is the
one thing this repository consistently refuses to make.

So this is not Jinja. It is placeholder substitution with a fixed, small set of
filters, and the grammar fits in a paragraph:

.. code-block:: text

    {{ entity_id }}                  the state string
    {{ entity_id.attribute }}        one attribute
    {{ entity_id | round(1) }}       a filter
    {{ entity_id | round(1) | upper }}   several, left to right

Everything outside ``{{ }}`` is literal. There is no control flow, no
arithmetic and no way to call anything, which is a feature rather than a
limitation to be lifted later: a dashboard definition is a document, and the
render loop evaluates it several times a second on an appliance.

Templates are parsed once, when the dashboard is loaded, and rendered many
times. Parsing is strict -- an unknown filter or an unclosed brace raises -- and
rendering is not: an entity that is missing or unavailable becomes
:data:`UNAVAILABLE_TEXT` rather than an exception, because a sensor dropping
out must not take the panel down with it.

Example:
    >>> from tinydisplay.homeassistant import StaticStateSource, Template
    >>> source = StaticStateSource({"sensor.kitchen": "21.53"})
    >>> template = Template.parse("{{ sensor.kitchen | round(1) }} C")
    >>> template.render(source)
    '21.5 C'
    >>> sorted(template.entity_ids)
    ['sensor.kitchen']
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from tinydisplay.homeassistant.errors import TemplateError
from tinydisplay.homeassistant.state import is_entity_id

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from tinydisplay.homeassistant.state import StateSource

__all__ = ["FILTERS", "UNAVAILABLE_TEXT", "Template", "template_entity_ids"]

#: What an unavailable or missing value renders as. Two hyphens rather than an
#: em dash or a symbol: the default font is a bitmap face with a limited
#: repertoire, and a placeholder that renders as a hollow box is worse than the
#: value it replaced.
UNAVAILABLE_TEXT: Final = "--"

_PLACEHOLDER: Final = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_FILTER_CALL: Final = re.compile(r"^([a-z_][a-z0-9_]*)\s*(?:\((.*)\))?$", re.IGNORECASE)
_NUMBER: Final = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)$")


def _as_number(value: Any) -> float | None:
    """``value`` as a float, or ``None`` if it is not numeric."""
    if isinstance(value, bool):
        # bool is an int subclass, and a binary sensor rendering as 1.0 rather
        # than "on" would be a surprise nobody asked for.
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _filter_round(value: Any, args: Sequence[Any]) -> Any:
    """Round to a fixed number of decimals, and format to exactly that many.

    Formatting rather than returning a number is deliberate. A readout that
    shows ``21.5`` and then ``22`` a moment later changes width as it changes
    value, which on a small panel reads as a glitch; ``22.0`` does not.
    """
    number = _as_number(value)
    if number is None:
        return None
    digits = int(args[0]) if args else 0
    if digits <= 0:
        return str(int(round(number, digits)))
    return f"{number:.{digits}f}"


def _filter_int(value: Any, args: Sequence[Any]) -> Any:  # noqa: ARG001
    """Truncate towards zero."""
    number = _as_number(value)
    return None if number is None else str(int(number))


def _filter_float(value: Any, args: Sequence[Any]) -> Any:  # noqa: ARG001
    """Coerce to a float, failing to unavailable if it is not one."""
    number = _as_number(value)
    return None if number is None else str(number)


def _filter_abs(value: Any, args: Sequence[Any]) -> Any:  # noqa: ARG001
    """Magnitude, keeping the value's formatting otherwise."""
    number = _as_number(value)
    return None if number is None else str(abs(number))


def _filter_default(value: Any, args: Sequence[Any]) -> Any:
    """Substitute a value for an unavailable one.

    The only filter that runs on a missing value; every other one passes
    ``None`` straight through, so ``round(1) | default('--')`` does what it
    reads like regardless of which step failed.
    """
    return args[0] if value is None else value


def _text_filter(function: Callable[[str], str]) -> Callable[[Any, Sequence[Any]], Any]:
    """Adapt a string method into a filter that skips missing values."""

    def apply(value: Any, args: Sequence[Any]) -> Any:  # noqa: ARG001
        return None if value is None else function(str(value))

    return apply


@dataclass(frozen=True, slots=True)
class _FilterSpec:
    """A filter's implementation and how many arguments it takes."""

    apply: Callable[[Any, Sequence[Any]], Any]
    min_args: int = 0
    max_args: int = 0
    handles_missing: bool = False


#: Every filter a dashboard may use. Deliberately closed: an unknown filter is
#: a typo, and rendering it as empty text would hide the mistake behind a
#: plausible-looking panel.
FILTERS: Final[dict[str, _FilterSpec]] = {
    "round": _FilterSpec(_filter_round, min_args=0, max_args=1),
    "int": _FilterSpec(_filter_int),
    "float": _FilterSpec(_filter_float),
    "abs": _FilterSpec(_filter_abs),
    "upper": _FilterSpec(_text_filter(str.upper)),
    "lower": _FilterSpec(_text_filter(str.lower)),
    "title": _FilterSpec(_text_filter(str.title)),
    "capitalize": _FilterSpec(_text_filter(str.capitalize)),
    "strip": _FilterSpec(_text_filter(str.strip)),
    "default": _FilterSpec(_filter_default, min_args=1, max_args=1, handles_missing=True),
}


@dataclass(frozen=True, slots=True)
class _Filter:
    """One parsed filter application."""

    name: str
    args: tuple[Any, ...]

    def apply(self, value: Any) -> Any:
        spec = FILTERS[self.name]
        if value is None and not spec.handles_missing:
            return None
        return spec.apply(value, self.args)


@dataclass(frozen=True, slots=True)
class _Expression:
    """One parsed ``{{ ... }}``."""

    entity_id: str
    attribute: str | None
    filters: tuple[_Filter, ...]

    def evaluate(self, source: StateSource) -> Any:
        entity = source.get(self.entity_id)
        value: Any
        if entity is None:
            value = None
        elif self.attribute is not None:
            value = entity.attribute(self.attribute)
        elif entity.is_available:
            value = entity.state
        else:
            value = None

        for step in self.filters:
            value = step.apply(value)
        return value


def _parse_argument(text: str, source: str) -> Any:
    """Parse one filter argument: a quoted string, a number, or a bare word."""
    argument = text.strip()
    if not argument:
        msg = f"empty filter argument in {source!r}"
        raise TemplateError(msg)

    # A quoted argument needs both quotes, so a lone `'` is a bare word rather
    # than an unterminated string.
    quoted_minimum = 2
    if len(argument) >= quoted_minimum and argument[0] == argument[-1] and argument[0] in "'\"":
        return argument[1:-1]
    if _NUMBER.match(argument):
        return float(argument) if "." in argument else int(argument)
    # A bare word is a string. Quoting every default value would be noise in a
    # YAML file that is already quoting things for YAML's own reasons.
    return argument


def _split_arguments(text: str) -> list[str]:
    """Split a filter's argument list on commas, respecting quotes."""
    arguments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    for character in text:
        if quote is not None:
            current.append(character)
            if character == quote:
                quote = None
        elif character in "'\"":
            quote = character
            current.append(character)
        elif character == ",":
            arguments.append("".join(current))
            current = []
        else:
            current.append(character)
    arguments.append("".join(current))
    return arguments


def _parse_filter(text: str, source: str) -> _Filter:
    """Parse ``name`` or ``name(args)`` into a filter, validating arity."""
    match = _FILTER_CALL.match(text.strip())
    if match is None:
        msg = f"malformed filter {text.strip()!r} in {source!r}"
        raise TemplateError(msg)

    name = match.group(1).lower()
    spec = FILTERS.get(name)
    if spec is None:
        known = ", ".join(sorted(FILTERS))
        msg = f"unknown filter {name!r} in {source!r}; available filters are {known}"
        raise TemplateError(msg)

    raw = match.group(2)
    args = (
        tuple(_parse_argument(part, source) for part in _split_arguments(raw))
        if raw is not None and raw.strip()
        else ()
    )
    if not spec.min_args <= len(args) <= spec.max_args:
        msg = (
            f"filter {name!r} in {source!r} takes between {spec.min_args} and "
            f"{spec.max_args} argument(s), got {len(args)}"
        )
        raise TemplateError(msg)
    return _Filter(name, args)


def _parse_expression(body: str, source: str) -> _Expression:
    """Parse the inside of a ``{{ ... }}``."""
    parts = body.split("|")
    reference = parts[0].strip()
    if not reference:
        msg = f"empty placeholder in {source!r}"
        raise TemplateError(msg)

    segments = reference.split(".")
    min_segments = 2
    if len(segments) < min_segments:
        msg = (
            f"{reference!r} in {source!r} is not an entity reference; "
            f"expected domain.object_id, optionally followed by .attribute"
        )
        raise TemplateError(msg)

    entity_id = ".".join(segments[:2])
    if not is_entity_id(entity_id):
        msg = f"{entity_id!r} in {source!r} is not a valid entity id"
        raise TemplateError(msg)

    # Anything past the entity id is one attribute name. Nested attributes are
    # not supported: the attributes a panel shows are scalars, and traversal
    # syntax would invite the whole expression language this module exists to
    # avoid.
    max_segments = 3
    if len(segments) > max_segments:
        msg = (
            f"{reference!r} in {source!r} has too many parts; "
            f"expected at most domain.object_id.attribute"
        )
        raise TemplateError(msg)
    attribute = segments[2] if len(segments) == max_segments else None

    filters = tuple(_parse_filter(part, source) for part in parts[1:])
    return _Expression(entity_id, attribute, filters)


class Template:
    """A parsed piece of dashboard text.

    Args:
        segments: Literal strings and expressions, in document order.
        source: The original text, kept for error messages and :meth:`__repr__`.

    Build one with :meth:`parse` rather than calling the constructor.
    """

    __slots__ = ("_entity_ids", "_segments", "_source")

    def __init__(self, segments: Sequence[str | _Expression], source: str) -> None:
        self._segments = tuple(segments)
        self._source = source
        self._entity_ids = frozenset(
            segment.entity_id for segment in self._segments if isinstance(segment, _Expression)
        )

    @classmethod
    def parse(cls, text: str) -> Template:
        """Parse ``text`` into a template.

        Raises:
            TemplateError: If a placeholder is malformed, references something
                that is not an entity id, or uses an unknown filter.

        Example:
            >>> from tinydisplay.homeassistant import Template
            >>> Template.parse("{{ sensor.a }}").is_constant
            False
            >>> Template.parse("Kitchen").is_constant
            True
        """
        segments: list[str | _Expression] = []
        position = 0
        for match in _PLACEHOLDER.finditer(text):
            if match.start() > position:
                segments.append(text[position : match.start()])
            segments.append(_parse_expression(match.group(1), text))
            position = match.end()
        if position < len(text):
            segments.append(text[position:])

        _reject_stray_braces(text)
        return cls(segments, text)

    @property
    def source(self) -> str:
        """The text this was parsed from."""
        return self._source

    @property
    def entity_ids(self) -> frozenset[str]:
        """Every entity this template reads.

        This is what makes the render loop change-driven: the dashboard
        subscribes to exactly these, and a state change outside the set cannot
        require a repaint.
        """
        return self._entity_ids

    @property
    def is_constant(self) -> bool:
        """Whether this template renders the same text forever."""
        return not self._entity_ids

    def render(self, source: StateSource, *, unavailable: str = UNAVAILABLE_TEXT) -> str:
        """Render against ``source``.

        Never raises. An entity that is missing, unavailable or carrying a
        value a filter cannot use renders as ``unavailable``.

        Example:
            >>> from tinydisplay.homeassistant import StaticStateSource, Template
            >>> template = Template.parse("{{ sensor.a }} / {{ sensor.b }}")
            >>> template.render(StaticStateSource({"sensor.a": "1"}))
            '1 / --'
        """
        parts: list[str] = []
        for segment in self._segments:
            if isinstance(segment, str):
                parts.append(segment)
                continue
            value = segment.evaluate(source)
            parts.append(unavailable if value is None else str(value))
        return "".join(parts)

    def __repr__(self) -> str:
        return f"Template({self._source!r})"


def _reject_stray_braces(text: str) -> None:
    """Raise if the text contains a brace pair that did not parse as a placeholder.

    Catches the common typo of a missing closing brace, which would otherwise
    render the rest of the line as literal text -- a dashboard that silently
    shows ``{{ sensor.kitchen`` instead of a temperature.
    """
    literal = _PLACEHOLDER.sub("", text)
    if "{{" in literal or "}}" in literal:
        msg = f"unbalanced {{{{ }}}} in {text!r}"
        raise TemplateError(msg)


def template_entity_ids(text: str) -> frozenset[str]:
    """Every entity id referenced by ``text``.

    A convenience for callers that need the dependencies of a string without
    keeping the parsed template.

    Raises:
        TemplateError: If the text does not parse.

    Example:
        >>> from tinydisplay.homeassistant import template_entity_ids
        >>> sorted(template_entity_ids("{{ sensor.a }} and {{ light.b.brightness }}"))
        ['light.b', 'sensor.a']
    """
    return Template.parse(text).entity_ids
