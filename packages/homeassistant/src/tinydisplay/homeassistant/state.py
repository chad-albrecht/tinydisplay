"""Entity state, and where a dashboard gets it from.

This is the seam between TinyDisplay and Home Assistant, and it is deliberately
tiny: a dashboard needs an entity's state string, its attributes and a name to
show. That is the whole contract, so that is the whole protocol.

Keeping it this small is what lets the rest of the package be tested -- and
previewed in the simulator -- with no Home Assistant anywhere. The integration
supplies a :class:`StateSource` backed by ``hass.states``; a test supplies a
:class:`StaticStateSource` backed by a dict; neither the dashboard builder nor
the render loop can tell the difference. It is the same trick the HT32 driver
plays with :class:`~tinydisplay.ht32.transport.RecordingHidTransport` and the
simulator plays with its preview window.

Example:
    >>> from tinydisplay.homeassistant import EntityState, StaticStateSource
    >>> source = StaticStateSource()
    >>> _ = source.set("sensor.kitchen", "21.5", friendly_name="Kitchen")
    >>> state = source.get("sensor.kitchen")
    >>> state.name, state.numeric
    ('Kitchen', 21.5)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

__all__ = [
    "UNAVAILABLE",
    "UNAVAILABLE_STATES",
    "UNKNOWN",
    "EntityState",
    "StateSource",
    "StaticStateSource",
    "is_entity_id",
    "missing_entities",
    "split_entity_id",
]

#: Home Assistant's two sentinels for "there is no useful value here". They are
#: ordinary state strings as far as the state machine is concerned, which is
#: why every consumer has to know them by name.
UNAVAILABLE: Final = "unavailable"
UNKNOWN: Final = "unknown"

#: Every state string that means "no value", including the empty string a
#: freshly restored entity can briefly carry.
UNAVAILABLE_STATES: Final = frozenset({UNAVAILABLE, UNKNOWN, ""})

#: ``domain.object_id``, both lowercase identifiers. Matches Home Assistant's
#: own rule rather than accepting anything with a dot in it, so a typo in a
#: dashboard is caught when the file is loaded rather than by rendering an
#: empty box forever.
_ENTITY_ID_PATTERN: Final = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")

_EMPTY_ATTRIBUTES: Final[Mapping[str, Any]] = MappingProxyType({})


def is_entity_id(text: str) -> bool:
    """Whether ``text`` is a well-formed ``domain.object_id``.

    Example:
        >>> from tinydisplay.homeassistant import is_entity_id
        >>> is_entity_id("sensor.kitchen_temperature")
        True
        >>> is_entity_id("Sensor.Kitchen")
        False
    """
    return _ENTITY_ID_PATTERN.match(text) is not None


def split_entity_id(entity_id: str) -> tuple[str, str]:
    """Split ``entity_id`` into ``(domain, object_id)``.

    Raises:
        ValueError: If ``entity_id`` is not well-formed.

    Example:
        >>> from tinydisplay.homeassistant import split_entity_id
        >>> split_entity_id("light.desk_lamp")
        ('light', 'desk_lamp')
    """
    if not is_entity_id(entity_id):
        msg = f"not a valid entity id: {entity_id!r}"
        raise ValueError(msg)
    domain, object_id = entity_id.split(".", 1)
    return (domain, object_id)


@dataclass(frozen=True, slots=True)
class EntityState:
    """One entity as a dashboard sees it.

    Attributes:
        entity_id: The ``domain.object_id`` this describes.
        state: The state string, exactly as Home Assistant reports it.
            Numbers arrive as strings here; see :attr:`numeric`.
        attributes: Everything else Home Assistant knows about the entity.

    The state is kept as a string rather than coerced on the way in, because
    Home Assistant's state machine is string-valued and a coercion here would
    have to guess. :attr:`numeric` does the conversion where it can be asked
    for explicitly, and answers ``None`` where it cannot.
    """

    entity_id: str
    state: str = UNKNOWN
    attributes: Mapping[str, Any] = field(default=_EMPTY_ATTRIBUTES)

    @property
    def domain(self) -> str:
        """The part before the dot -- ``sensor``, ``light``, and so on."""
        return self.entity_id.split(".", 1)[0]

    @property
    def object_id(self) -> str:
        """The part after the dot."""
        return self.entity_id.split(".", 1)[-1]

    @property
    def is_available(self) -> bool:
        """Whether the state carries a usable value.

        Example:
            >>> from tinydisplay.homeassistant import EntityState
            >>> EntityState("sensor.a", "21.5").is_available
            True
            >>> EntityState("sensor.a", "unavailable").is_available
            False
        """
        return self.state not in UNAVAILABLE_STATES

    @property
    def numeric(self) -> float | None:
        """The state as a number, or ``None`` if it is not one.

        ``None`` rather than an exception or a zero: a thermostat reporting
        ``unavailable`` is an ordinary Tuesday, and a gauge that reads zero in
        that case is actively misleading.

        Example:
            >>> from tinydisplay.homeassistant import EntityState
            >>> EntityState("sensor.a", "21.5").numeric
            21.5
            >>> EntityState("sensor.a", "unavailable").numeric is None
            True
        """
        try:
            return float(self.state)
        except (TypeError, ValueError):
            return None

    @property
    def name(self) -> str:
        """A human-readable name.

        Prefers the ``friendly_name`` attribute Home Assistant maintains, and
        falls back to a title-cased object id so that a dashboard written
        against an entity with no friendly name still shows something a person
        can read.

        Example:
            >>> from tinydisplay.homeassistant import EntityState
            >>> EntityState("sensor.kitchen_temperature").name
            'Kitchen Temperature'
        """
        friendly = self.attributes.get("friendly_name")
        if isinstance(friendly, str) and friendly:
            return friendly
        return self.object_id.replace("_", " ").title()

    @property
    def unit(self) -> str:
        """The unit of measurement, or an empty string if there is none."""
        unit = self.attributes.get("unit_of_measurement")
        return unit if isinstance(unit, str) else ""

    def attribute(self, name: str) -> Any:
        """One attribute, or ``None`` if the entity does not carry it.

        The three properties Home Assistant exposes as pseudo-attributes in its
        own templates -- ``state``, ``name`` and ``entity_id`` -- resolve here
        too, so a dashboard can write ``{{ sensor.a.name }}`` without knowing
        whether it is reading an attribute or a property.
        """
        if name == "state":
            return self.state
        if name == "name":
            return self.name
        if name == "entity_id":
            return self.entity_id
        return self.attributes.get(name)


@runtime_checkable
class StateSource(Protocol):
    """Where a dashboard reads entity state from.

    One method, because one method is all a dashboard needs. Implementations
    must be safe to call from the event loop and must not block: the
    integration's implementation is a dictionary lookup against
    ``hass.states``, and the render loop calls it once per bound widget per
    frame.
    """

    def get(self, entity_id: str) -> EntityState | None:
        """The current state of ``entity_id``, or ``None`` if it is unknown.

        ``None`` means "Home Assistant has never heard of this entity", which
        is different from an entity that exists and is currently unavailable.
        Dashboards render both as unavailable, but the distinction is what
        lets configuration validation warn about a typo.
        """
        ...


class StaticStateSource:
    """A :class:`StateSource` backed by a dictionary.

    This is the reference implementation of the contract rather than a mock:
    it is what the simulator examples and the test suite drive dashboards
    with, so the whole stack above it runs with no Home Assistant installed.

    Example:
        >>> from tinydisplay.homeassistant import StaticStateSource
        >>> source = StaticStateSource({"sensor.a": "21.5"})
        >>> source.get("sensor.a").numeric
        21.5
        >>> source.get("sensor.missing") is None
        True
    """

    __slots__ = ("_states",)

    def __init__(
        self,
        states: Mapping[str, str | EntityState] | None = None,
    ) -> None:
        self._states: dict[str, EntityState] = {}
        for entity_id, value in (states or {}).items():
            if isinstance(value, EntityState):
                self._states[entity_id] = value
            else:
                self.set(entity_id, value)

    def get(self, entity_id: str) -> EntityState | None:
        """The current state of ``entity_id``, or ``None``."""
        return self._states.get(entity_id)

    def set(
        self,
        entity_id: str,
        state: str,
        **attributes: Any,
    ) -> EntityState:
        """Set an entity's state, replacing any previous value.

        Attributes are passed as keywords for brevity at the call site, which
        is almost always a test or an example:

        Example:
            >>> from tinydisplay.homeassistant import StaticStateSource
            >>> source = StaticStateSource()
            >>> _ = source.set("sensor.a", "21.5", unit_of_measurement="C")
            >>> source.get("sensor.a").unit
            'C'
        """
        entity = EntityState(entity_id, state, MappingProxyType(dict(attributes)))
        self._states[entity_id] = entity
        return entity

    def remove(self, entity_id: str) -> None:
        """Forget an entity, as though Home Assistant had never seen it."""
        self._states.pop(entity_id, None)

    def update(self, states: Mapping[str, str | EntityState]) -> None:
        """Set several entities at once."""
        for entity_id, value in states.items():
            if isinstance(value, EntityState):
                self._states[entity_id] = value
            else:
                self.set(entity_id, value)

    def __contains__(self, entity_id: object) -> bool:
        return entity_id in self._states

    def __iter__(self) -> Iterator[str]:
        return iter(self._states)

    def __len__(self) -> int:
        return len(self._states)

    def __repr__(self) -> str:
        return f"StaticStateSource({len(self._states)} entities)"


def missing_entities(source: StateSource, entity_ids: Iterable[str]) -> list[str]:
    """Which of ``entity_ids`` the source has never heard of, in order.

    Used by configuration validation to turn a typo into a warning at setup
    time rather than a permanently blank readout. Deliberately not used by the
    render loop: an entity can legitimately disappear and come back, and a
    dashboard that refused to draw in the meantime would be worse than one
    showing a placeholder.
    """
    return [entity_id for entity_id in entity_ids if source.get(entity_id) is None]
