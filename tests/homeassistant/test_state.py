"""Tests for the entity-state model and the state source protocol."""

from __future__ import annotations

import pytest

from tinydisplay.homeassistant import (
    UNAVAILABLE,
    UNKNOWN,
    EntityState,
    StateSource,
    StaticStateSource,
    is_entity_id,
    missing_entities,
    split_entity_id,
)


class TestEntityIdParsing:
    @pytest.mark.parametrize(
        "entity_id",
        ["sensor.kitchen", "binary_sensor.front_door", "light.a1", "_private.thing"],
    )
    def test_accepts_well_formed_ids(self, entity_id: str) -> None:
        assert is_entity_id(entity_id)

    @pytest.mark.parametrize(
        "entity_id",
        [
            "Sensor.Kitchen",  # Home Assistant ids are lowercase
            "sensor",  # no object id
            "sensor.",  # empty object id
            ".kitchen",  # empty domain
            "sensor.kitchen.temperature",  # that is an attribute reference
            "sensor kitchen",
            "",
            "1sensor.kitchen",  # domains do not start with a digit
        ],
    )
    def test_rejects_malformed_ids(self, entity_id: str) -> None:
        assert not is_entity_id(entity_id)

    def test_split_returns_both_halves(self) -> None:
        assert split_entity_id("light.desk_lamp") == ("light", "desk_lamp")

    def test_split_rejects_malformed(self) -> None:
        with pytest.raises(ValueError, match="not a valid entity id"):
            split_entity_id("nope")


class TestEntityState:
    def test_domain_and_object_id(self) -> None:
        state = EntityState("binary_sensor.front_door", "on")
        assert state.domain == "binary_sensor"
        assert state.object_id == "front_door"

    def test_numeric_parses_the_state_string(self) -> None:
        assert EntityState("sensor.a", "21.5").numeric == 21.5
        assert EntityState("sensor.a", "-3").numeric == -3.0

    def test_numeric_is_none_for_non_numbers(self) -> None:
        # None rather than zero: a gauge reading zero because the sensor is
        # down is worse than one that can tell the difference.
        assert EntityState("sensor.a", "on").numeric is None
        assert EntityState("sensor.a", UNAVAILABLE).numeric is None

    @pytest.mark.parametrize("state", [UNAVAILABLE, UNKNOWN, ""])
    def test_sentinel_states_are_unavailable(self, state: str) -> None:
        assert not EntityState("sensor.a", state).is_available

    def test_ordinary_states_are_available(self) -> None:
        assert EntityState("sensor.a", "21.5").is_available
        # "off" is a value, not an absence.
        assert EntityState("binary_sensor.a", "off").is_available

    def test_name_prefers_the_friendly_name(self) -> None:
        state = EntityState("sensor.kitchen_temp", "1", {"friendly_name": "Kitchen"})
        assert state.name == "Kitchen"

    def test_name_falls_back_to_a_readable_object_id(self) -> None:
        assert EntityState("sensor.kitchen_temperature").name == "Kitchen Temperature"

    def test_empty_friendly_name_falls_back(self) -> None:
        state = EntityState("sensor.kitchen_temp", "1", {"friendly_name": ""})
        assert state.name == "Kitchen Temp"

    def test_unit_reads_the_attribute(self) -> None:
        state = EntityState("sensor.a", "21", {"unit_of_measurement": "C"})
        assert state.unit == "C"
        assert EntityState("sensor.a", "21").unit == ""

    def test_attribute_exposes_pseudo_attributes(self) -> None:
        state = EntityState("sensor.a", "21", {"friendly_name": "A"})
        assert state.attribute("state") == "21"
        assert state.attribute("name") == "A"
        assert state.attribute("entity_id") == "sensor.a"

    def test_attribute_returns_none_when_absent(self) -> None:
        assert EntityState("sensor.a", "21").attribute("battery") is None

    def test_is_frozen(self) -> None:
        state = EntityState("sensor.a", "21")
        with pytest.raises(AttributeError):
            state.state = "22"  # type: ignore[misc]


class TestStaticStateSource:
    def test_satisfies_the_protocol(self) -> None:
        # The protocol is runtime-checkable so that this assertion is possible;
        # it is the contract the integration's adapter also has to meet.
        assert isinstance(StaticStateSource(), StateSource)

    def test_constructed_from_plain_strings(self) -> None:
        source = StaticStateSource({"sensor.a": "21.5"})
        entity = source.get("sensor.a")
        assert entity is not None
        assert entity.numeric == 21.5

    def test_constructed_from_entity_states(self) -> None:
        source = StaticStateSource({"sensor.a": EntityState("sensor.a", "9")})
        assert source.get("sensor.a").state == "9"  # type: ignore[union-attr]

    def test_unknown_entity_is_none(self) -> None:
        # Distinct from an entity that exists and is unavailable: only this one
        # means "you probably made a typo".
        assert StaticStateSource().get("sensor.missing") is None

    def test_set_attaches_attributes(self) -> None:
        source = StaticStateSource()
        source.set("sensor.a", "21", unit_of_measurement="C", friendly_name="A")
        entity = source.get("sensor.a")
        assert entity is not None
        assert entity.unit == "C"
        assert entity.name == "A"

    def test_set_replaces_previous_attributes(self) -> None:
        source = StaticStateSource()
        source.set("sensor.a", "21", unit_of_measurement="C")
        source.set("sensor.a", "22")
        assert source.get("sensor.a").unit == ""  # type: ignore[union-attr]

    def test_remove_forgets_the_entity(self) -> None:
        source = StaticStateSource({"sensor.a": "1"})
        source.remove("sensor.a")
        assert source.get("sensor.a") is None

    def test_remove_is_forgiving(self) -> None:
        StaticStateSource().remove("sensor.never_existed")

    def test_update_sets_several(self) -> None:
        source = StaticStateSource()
        source.update({"sensor.a": "1", "sensor.b": EntityState("sensor.b", "2")})
        assert len(source) == 2
        assert "sensor.a" in source
        assert sorted(source) == ["sensor.a", "sensor.b"]

    def test_repr_reports_the_size(self) -> None:
        assert "2 entities" in repr(StaticStateSource({"sensor.a": "1", "sensor.b": "2"}))


class TestMissingEntities:
    def test_reports_only_the_unknown_ones(self) -> None:
        source = StaticStateSource({"sensor.a": "1"})
        assert missing_entities(source, ["sensor.a", "sensor.b", "sensor.c"]) == [
            "sensor.b",
            "sensor.c",
        ]

    def test_an_unavailable_entity_is_not_missing(self) -> None:
        # It exists; it is simply not reporting. Only a typo produces a None.
        source = StaticStateSource({"sensor.a": UNAVAILABLE})
        assert missing_entities(source, ["sensor.a"]) == []
