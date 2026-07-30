"""Tests for the placeholder templating.

Two properties matter here and are tested separately throughout: **parsing is
strict** -- a mistake in the document raises when it is loaded -- and
**rendering is total** -- whatever the sensors are doing, a string comes out.
"""

from __future__ import annotations

import pytest

from tinydisplay.homeassistant import (
    UNAVAILABLE,
    UNAVAILABLE_TEXT,
    StaticStateSource,
    Template,
    TemplateError,
    template_entity_ids,
)


@pytest.fixture
def source() -> StaticStateSource:
    states = StaticStateSource()
    states.set(
        "sensor.kitchen",
        "21.537",
        friendly_name="Kitchen Temperature",
        unit_of_measurement="C",
    )
    states.set("binary_sensor.door", "on")
    states.set("sensor.broken", UNAVAILABLE)
    return states


class TestLiterals:
    def test_text_with_no_placeholders_passes_through(self) -> None:
        template = Template.parse("Kitchen")
        assert template.render(StaticStateSource()) == "Kitchen"

    def test_a_literal_template_is_constant(self) -> None:
        assert Template.parse("Kitchen").is_constant
        assert Template.parse("").is_constant

    def test_a_referencing_template_is_not_constant(self) -> None:
        assert not Template.parse("{{ sensor.a }}").is_constant

    def test_repr_shows_the_source(self) -> None:
        assert repr(Template.parse("hi")) == "Template('hi')"

    def test_source_is_kept(self) -> None:
        assert Template.parse("{{ sensor.a }} C").source == "{{ sensor.a }} C"


class TestReferences:
    def test_state_substitution(self, source: StaticStateSource) -> None:
        assert Template.parse("{{ sensor.kitchen }}").render(source) == "21.537"

    def test_literal_text_around_a_placeholder_is_kept(self, source: StaticStateSource) -> None:
        assert Template.parse("[{{ binary_sensor.door }}]").render(source) == "[on]"

    def test_several_placeholders(self, source: StaticStateSource) -> None:
        template = Template.parse("{{ binary_sensor.door }}/{{ sensor.kitchen }}")
        assert template.render(source) == "on/21.537"

    def test_attribute_reference(self, source: StaticStateSource) -> None:
        template = Template.parse("{{ sensor.kitchen.unit_of_measurement }}")
        assert template.render(source) == "C"

    def test_name_pseudo_attribute(self, source: StaticStateSource) -> None:
        assert Template.parse("{{ sensor.kitchen.name }}").render(source) == "Kitchen Temperature"

    def test_whitespace_inside_braces_is_ignored(self, source: StaticStateSource) -> None:
        assert Template.parse("{{sensor.kitchen}}").render(source) == "21.537"
        assert Template.parse("{{    sensor.kitchen   }}").render(source) == "21.537"

    def test_entity_ids_are_collected(self) -> None:
        template = Template.parse("{{ sensor.a }} {{ light.b.brightness }} {{ sensor.a }}")
        assert template.entity_ids == frozenset({"sensor.a", "light.b"})

    def test_helper_collects_without_keeping_the_template(self) -> None:
        assert template_entity_ids("{{ sensor.a }}") == frozenset({"sensor.a"})


class TestUnavailableValues:
    def test_missing_entity_renders_the_placeholder(self) -> None:
        assert Template.parse("{{ sensor.gone }}").render(StaticStateSource()) == UNAVAILABLE_TEXT

    def test_unavailable_entity_renders_the_placeholder(self, source: StaticStateSource) -> None:
        assert Template.parse("{{ sensor.broken }}").render(source) == UNAVAILABLE_TEXT

    def test_missing_attribute_renders_the_placeholder(self, source: StaticStateSource) -> None:
        assert Template.parse("{{ sensor.kitchen.battery }}").render(source) == UNAVAILABLE_TEXT

    def test_placeholder_is_configurable(self) -> None:
        rendered = Template.parse("{{ sensor.gone }}").render(StaticStateSource(), unavailable="?")
        assert rendered == "?"

    def test_only_the_failing_placeholder_is_replaced(self, source: StaticStateSource) -> None:
        template = Template.parse("{{ sensor.kitchen }}/{{ sensor.gone }}")
        assert template.render(source) == f"21.537/{UNAVAILABLE_TEXT}"


class TestFilters:
    def test_round_formats_to_the_requested_precision(self, source: StaticStateSource) -> None:
        assert Template.parse("{{ sensor.kitchen | round(1) }}").render(source) == "21.5"
        assert Template.parse("{{ sensor.kitchen | round(2) }}").render(source) == "21.54"

    def test_round_pads_to_a_stable_width(self) -> None:
        # A readout that alternates between "21" and "21.5" changes width as it
        # changes value, which on a small panel reads as a glitch.
        states = StaticStateSource({"sensor.a": "21"})
        assert Template.parse("{{ sensor.a | round(1) }}").render(states) == "21.0"

    def test_round_with_no_argument_gives_a_whole_number(self, source: StaticStateSource) -> None:
        assert Template.parse("{{ sensor.kitchen | round }}").render(source) == "22"

    def test_int_truncates(self, source: StaticStateSource) -> None:
        assert Template.parse("{{ sensor.kitchen | int }}").render(source) == "21"

    def test_float_coerces(self) -> None:
        states = StaticStateSource({"sensor.a": "3"})
        assert Template.parse("{{ sensor.a | float }}").render(states) == "3.0"

    def test_abs_takes_the_magnitude(self) -> None:
        states = StaticStateSource({"sensor.a": "-4.5"})
        assert Template.parse("{{ sensor.a | abs }}").render(states) == "4.5"

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("{{ binary_sensor.door | upper }}", "ON"),
            ("{{ binary_sensor.door | title }}", "On"),
            ("{{ binary_sensor.door | capitalize }}", "On"),
        ],
    )
    def test_text_filters(self, source: StaticStateSource, expression: str, expected: str) -> None:
        assert Template.parse(expression).render(source) == expected

    def test_lower_folds_case(self) -> None:
        states = StaticStateSource({"sensor.a": "HELLO"})
        assert Template.parse("{{ sensor.a | lower }}").render(states) == "hello"

    def test_filters_chain_left_to_right(self, source: StaticStateSource) -> None:
        template = Template.parse("{{ sensor.kitchen | round(1) | upper }}")
        assert template.render(source) == "21.5"

    def test_default_substitutes_for_a_missing_value(self) -> None:
        template = Template.parse("{{ sensor.gone | default(n/a) }}")
        assert template.render(StaticStateSource()) == "n/a"

    def test_default_accepts_a_quoted_argument(self) -> None:
        template = Template.parse("{{ sensor.gone | default('not here') }}")
        assert template.render(StaticStateSource()) == "not here"

    def test_default_leaves_a_present_value_alone(self, source: StaticStateSource) -> None:
        template = Template.parse("{{ sensor.kitchen | default(n/a) }}")
        assert template.render(source) == "21.537"

    def test_default_rescues_a_failed_conversion(self, source: StaticStateSource) -> None:
        # The value exists but is not a number, so round() yields nothing and
        # default() catches it. This is why default is the one filter that runs
        # on a missing value.
        template = Template.parse("{{ binary_sensor.door | round(1) | default(--) }}")
        assert template.render(source) == "--"

    def test_numeric_filters_skip_non_numeric_values(self, source: StaticStateSource) -> None:
        assert Template.parse("{{ binary_sensor.door | int }}").render(source) == UNAVAILABLE_TEXT

    def test_a_value_that_goes_missing_stays_missing(self, source: StaticStateSource) -> None:
        # round() cannot use "on", so it yields nothing, and upper() passes the
        # nothing straight through rather than stringifying it. Only default()
        # catches a missing value -- which is what makes it useful at the end
        # of a chain regardless of which step failed.
        template = Template.parse("{{ binary_sensor.door | round(1) | upper }}")
        assert template.render(source) == UNAVAILABLE_TEXT

    def test_a_numeric_attribute_is_used_directly(self) -> None:
        states = StaticStateSource()
        states.set("light.a", "on", brightness=128)
        assert Template.parse("{{ light.a.brightness | round(1) }}").render(states) == "128.0"

    def test_a_float_attribute_is_used_directly(self) -> None:
        states = StaticStateSource()
        states.set("sensor.a", "ok", drift=1.25)
        assert Template.parse("{{ sensor.a.drift | round(1) }}").render(states) == "1.2"

    def test_a_structured_attribute_is_not_a_number(self) -> None:
        states = StaticStateSource()
        states.set("weather.a", "sunny", forecast=[{"temp": 20}])
        assert Template.parse("{{ weather.a.forecast | round(1) }}").render(states) == "--"

    def test_a_boolean_attribute_is_not_a_number(self) -> None:
        states = StaticStateSource()
        states.set("light.a", "on", is_dimmable=True)
        # bool is an int subclass; rendering "1.0" for True would be a surprise.
        assert Template.parse("{{ light.a.is_dimmable | round(1) }}").render(states) == "--"


class TestParseErrors:
    def test_a_filter_that_is_not_a_name_is_rejected(self) -> None:
        with pytest.raises(TemplateError, match="malformed filter"):
            Template.parse("{{ sensor.a | 3 }}")

    def test_unknown_filter_is_rejected(self) -> None:
        with pytest.raises(TemplateError, match="unknown filter 'shout'"):
            Template.parse("{{ sensor.a | shout }}")

    def test_the_error_lists_the_available_filters(self) -> None:
        with pytest.raises(TemplateError, match="round"):
            Template.parse("{{ sensor.a | nope }}")

    def test_too_many_filter_arguments(self) -> None:
        with pytest.raises(TemplateError, match="takes between"):
            Template.parse("{{ sensor.a | round(1, 2) }}")

    def test_missing_required_filter_argument(self) -> None:
        with pytest.raises(TemplateError, match="takes between"):
            Template.parse("{{ sensor.a | default }}")

    def test_unclosed_brace_is_rejected(self) -> None:
        # Left alone this renders as literal text and looks like the template
        # was ignored, which is a maddening thing to debug.
        with pytest.raises(TemplateError, match="unbalanced"):
            Template.parse("{{ sensor.a }")

    def test_stray_closing_brace_is_rejected(self) -> None:
        with pytest.raises(TemplateError, match="unbalanced"):
            Template.parse("sensor.a }}")

    def test_empty_placeholder_is_rejected(self) -> None:
        with pytest.raises(TemplateError, match="empty placeholder"):
            Template.parse("{{ }}")

    def test_bare_word_is_not_an_entity_reference(self) -> None:
        with pytest.raises(TemplateError, match="not an entity reference"):
            Template.parse("{{ kitchen }}")

    def test_malformed_entity_id_is_rejected(self) -> None:
        with pytest.raises(TemplateError, match="not a valid entity id"):
            Template.parse("{{ Sensor.Kitchen }}")

    def test_nested_attribute_paths_are_rejected(self) -> None:
        with pytest.raises(TemplateError, match="too many parts"):
            Template.parse("{{ sensor.a.forecast.temperature }}")

    def test_empty_parentheses_report_the_arity(self) -> None:
        with pytest.raises(TemplateError, match="takes between 1 and 1"):
            Template.parse("{{ sensor.a | default() }}")

    def test_empty_filter_argument_is_rejected(self) -> None:
        # A trailing comma leaves a genuinely empty argument, which is a
        # different mistake from writing no arguments at all.
        with pytest.raises(TemplateError, match="empty filter argument"):
            Template.parse("{{ sensor.a | default(1,) }}")

    def test_replace_tidies_underscored_states(self) -> None:
        # Home Assistant's state strings are full of underscores, and a panel
        # showing "Above_Horizon" reads as a bug rather than a state.
        states = StaticStateSource({"sun.sun": "above_horizon"})
        template = Template.parse("{{ sun.sun | replace(_, ' ') | title }}")
        assert template.render(states) == "Above Horizon"

    def test_replace_needs_both_arguments(self) -> None:
        with pytest.raises(TemplateError, match="takes between 2 and 2"):
            Template.parse("{{ sensor.a | replace(_) }}")

    def test_replace_leaves_a_missing_value_missing(self) -> None:
        rendered = Template.parse("{{ sensor.gone | replace(a, b) }}").render(StaticStateSource())
        assert rendered == UNAVAILABLE_TEXT
