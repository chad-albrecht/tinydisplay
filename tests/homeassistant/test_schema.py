"""Tests for dashboard validation.

The theme running through these is that a dashboard is a hand-written document,
so the parser's job is not only to accept good ones but to reject bad ones
*with the location of the mistake*. Most of the error tests assert on the path
in the message, not just that something was raised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tinydisplay.core import Color, HorizontalAlign, VerticalAlign
from tinydisplay.homeassistant import (
    ColorRef,
    DashboardConfigError,
    DashboardSpec,
    StaticStateSource,
    load_dashboard,
    parse_dashboard,
    parse_dashboard_yaml,
)
from tinydisplay.widgets import MIDNIGHT, PAPER, Align, Axis, IconName

if TYPE_CHECKING:
    from pathlib import Path

MINIMAL = {"root": {"type": "spacer"}}


def parse(root: object, **document: object) -> DashboardSpec:
    """Parse a document with ``root`` filled in, for brevity in the tests."""
    return parse_dashboard({"root": root, **document})


class TestDocument:
    def test_minimal_document(self) -> None:
        spec = parse_dashboard(MINIMAL)
        assert spec.root.kind == "spacer"
        assert spec.entity_ids == frozenset()

    def test_theme_defaults_to_midnight(self) -> None:
        assert parse_dashboard(MINIMAL).theme_name == "midnight"

    def test_theme_is_quantised_for_the_panel(self) -> None:
        # The palette in hand must be the palette on the glass: a contrast
        # ratio measured before quantisation is not the one the panel delivers.
        spec = parse_dashboard({**MINIMAL, "theme": "paper"})
        assert spec.theme == PAPER.quantized()
        assert spec.theme != PAPER

    def test_theme_is_case_insensitive(self) -> None:
        assert parse_dashboard({**MINIMAL, "theme": "MIDNIGHT"}).theme_name == "midnight"

    def test_unknown_theme_lists_the_alternatives(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"theme: unknown theme 'neon'.*midnight"):
            parse_dashboard({**MINIMAL, "theme": "neon"})

    def test_background_defaults_to_the_theme_role(self) -> None:
        spec = parse_dashboard(MINIMAL)
        assert spec.background.resolve(spec.theme) == MIDNIGHT.quantized().background

    def test_background_may_be_a_literal(self) -> None:
        spec = parse_dashboard({**MINIMAL, "background": "#102030"})
        assert spec.background.resolve(spec.theme) == Color.from_hex("#102030")

    def test_background_may_not_depend_on_state(self) -> None:
        # The canvas is cleared to this before anything is drawn, and there is
        # no node here whose entity a state mapping could be keyed on.
        with pytest.raises(DashboardConfigError, match="background: this colour is fixed"):
            parse_dashboard({**MINIMAL, "background": {"on": "danger"}})

    def test_missing_root_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match="needs a 'root' node"):
            parse_dashboard({"theme": "midnight"})

    def test_unknown_top_level_key_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match="colour: unknown key"):
            parse_dashboard({**MINIMAL, "colour": "midnight"})

    def test_non_mapping_document_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match="expected a mapping"):
            parse_dashboard(["not", "a", "dashboard"])

    def test_custom_unavailable_text(self) -> None:
        spec = parse_dashboard({**MINIMAL, "unavailable": "n/a"})
        assert spec.unavailable == "n/a"


class TestYamlLoading:
    def test_parses_yaml_text(self) -> None:
        spec = parse_dashboard_yaml("root:\n  type: spacer\n")
        assert spec.root.kind == "spacer"

    def test_malformed_yaml_is_reported_as_such(self) -> None:
        with pytest.raises(DashboardConfigError, match="not valid YAML"):
            parse_dashboard_yaml("root: [unclosed\n")

    def test_loads_from_a_file(self, tmp_path: Path) -> None:
        path = tmp_path / "dash.yaml"
        path.write_text("root:\n  type: spacer\n", encoding="utf-8")
        assert load_dashboard(path).root.kind == "spacer"

    def test_missing_file_names_the_path(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope.yaml"
        with pytest.raises(DashboardConfigError, match="cannot read dashboard"):
            load_dashboard(missing)

    def test_invalid_file_names_the_path_and_the_key(self, tmp_path: Path) -> None:
        path = tmp_path / "dash.yaml"
        path.write_text("root:\n  type: nonsense\n", encoding="utf-8")
        with pytest.raises(DashboardConfigError, match=r"dash\.yaml: root\.type"):
            load_dashboard(path)


class TestNodeBasics:
    def test_missing_type_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root: needs a 'type'"):
            parse({"text": "hi"})

    def test_unknown_type_lists_the_alternatives(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.type: unknown type 'card'"):
            parse({"type": "card"})

    def test_unknown_key_is_rejected_with_its_path(self) -> None:
        # The whole reason for rejecting rather than ignoring: a misspelled key
        # would otherwise render a perfect-looking panel that ignores it.
        with pytest.raises(DashboardConfigError, match=r"root\.colour: unknown key"):
            parse({"type": "label", "text": "hi", "colour": "accent"})

    def test_nested_errors_carry_the_full_path(self) -> None:
        document = {
            "type": "stack",
            "children": [
                {"type": "spacer"},
                {"type": "spacer"},
                {"type": "gauge", "entity": "sensor.a", "warning_at": 1.5},
            ],
        }
        with pytest.raises(
            DashboardConfigError, match=r"root\.children\[2\]\.warning_at: must be at most 1"
        ):
            parse(document)

    def test_visible_and_name_are_accepted_everywhere(self) -> None:
        spec = parse({"type": "spacer", "visible": False, "name": "gap"})
        assert spec.root.options["visible"] is False
        assert spec.root.options["name"] == "gap"

    def test_visible_must_be_a_boolean(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.visible: expected true or false"):
            parse({"type": "spacer", "visible": "yes"})


class TestLayoutHints:
    def test_defaults(self) -> None:
        node = parse({"type": "spacer"}).root
        assert node.size is None
        assert node.weight == 1.0
        assert node.align is Align.STRETCH
        assert (node.row, node.column, node.row_span, node.column_span) == (0, 0, 1, 1)

    def test_size_and_weight(self) -> None:
        node = parse({"type": "spacer", "size": 20, "weight": 2.5}).root
        assert node.size == 20
        assert node.weight == 2.5

    def test_negative_size_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.size: must be at least 0"):
            parse({"type": "spacer", "size": -1})

    def test_zero_weight_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.weight: must be greater than zero"):
            parse({"type": "spacer", "weight": 0})

    def test_cross_align_is_parsed(self) -> None:
        assert parse({"type": "spacer", "cross_align": "center"}).root.align is Align.CENTER

    def test_unknown_cross_align_is_rejected(self) -> None:
        with pytest.raises(
            DashboardConfigError, match=r"root\.cross_align: unknown value 'middle'"
        ):
            parse({"type": "spacer", "cross_align": "middle"})

    def test_a_label_may_align_its_text_any_of_three_ways(self) -> None:
        # The bug this rename fixes: `align` was read by both the label parser
        # and the layout parser, so only `center` -- the one value both enums
        # happened to share -- was accepted. `align: left` was impossible.
        for value, expected in (
            ("left", HorizontalAlign.LEFT),
            ("center", HorizontalAlign.CENTER),
            ("right", HorizontalAlign.RIGHT),
        ):
            spec = parse({"type": "label", "text": "hi", "align": value})
            assert spec.root.options["align"] is expected

    def test_text_and_layout_alignment_coexist(self) -> None:
        spec = parse({"type": "label", "text": "hi", "align": "right", "cross_align": "end"})
        assert spec.root.options["align"] is HorizontalAlign.RIGHT
        assert spec.root.align is Align.END

    def test_spans_must_be_at_least_one(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.row_span: must be at least 1"):
            parse({"type": "spacer", "row_span": 0})

    def test_cross_size_is_parsed(self) -> None:
        assert parse({"type": "spacer", "cross_size": 12}).root.cross_size == 12

    def test_negative_cross_size_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.cross_size: must be at least 0"):
            parse({"type": "spacer", "cross_size": -1})


class TestPadding:
    def test_a_single_number_pads_every_edge(self) -> None:
        insets = parse({"type": "spacer", "padding": 4}).root.padding
        assert (insets.left, insets.top, insets.right, insets.bottom) == (4, 4, 4, 4)

    def test_a_mapping_pads_named_edges(self) -> None:
        insets = parse({"type": "spacer", "padding": {"left": 2, "bottom": 6}}).root.padding
        assert (insets.left, insets.top, insets.right, insets.bottom) == (2, 0, 0, 6)

    def test_axis_shorthands(self) -> None:
        insets = parse({"type": "spacer", "padding": {"horizontal": 3, "vertical": 1}}).root.padding
        assert (insets.left, insets.top, insets.right, insets.bottom) == (3, 1, 3, 1)

    def test_specific_edges_beat_shorthands(self) -> None:
        node = parse({"type": "spacer", "padding": {"all": 5, "left": 1}}).root
        assert node.padding.left == 1
        assert node.padding.right == 5

    def test_no_padding_is_zero(self) -> None:
        assert parse({"type": "spacer"}).root.padding.is_zero

    def test_negative_padding_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.padding\.left: must be at least 0"):
            parse({"type": "spacer", "padding": {"left": -1}})

    def test_unknown_padding_edge_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.padding\.middle: unknown key"):
            parse({"type": "spacer", "padding": {"middle": 1}})


class TestStack:
    def test_defaults(self) -> None:
        node = parse({"type": "stack"}).root
        assert node.options["axis"] is Axis.VERTICAL
        assert node.options["spacing"] == 0
        assert node.children == ()

    def test_children_are_parsed_in_order(self) -> None:
        node = parse(
            {"type": "stack", "children": [{"type": "spacer"}, {"type": "label", "text": "x"}]}
        ).root
        assert [child.kind for child in node.children] == ["spacer", "label"]

    def test_children_must_be_a_list(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.children: expected a list"):
            parse({"type": "stack", "children": {"type": "spacer"}})

    def test_horizontal_axis(self) -> None:
        node = parse({"type": "stack", "axis": "horizontal"}).root
        assert node.options["axis"] is Axis.HORIZONTAL

    def test_walk_yields_every_descendant(self) -> None:
        node = parse(
            {
                "type": "stack",
                "children": [{"type": "stack", "children": [{"type": "spacer"}]}],
            }
        ).root
        assert [item.kind for item in node.walk()] == ["stack", "stack", "spacer"]


class TestGrid:
    def test_requires_dimensions(self) -> None:
        with pytest.raises(DashboardConfigError, match="a grid needs 'rows'"):
            parse({"type": "grid", "columns": 2})

    def test_dimensions_must_be_positive(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.rows: must be at least 1"):
            parse({"type": "grid", "rows": 0, "columns": 2})

    def test_children_carry_placement(self) -> None:
        node = parse(
            {
                "type": "grid",
                "rows": 2,
                "columns": 2,
                "children": [{"type": "spacer", "row": 1, "column": 1, "column_span": 1}],
            }
        ).root
        assert (node.children[0].row, node.children[0].column) == (1, 1)


class TestLabel:
    def test_requires_text(self) -> None:
        with pytest.raises(DashboardConfigError, match="a label needs 'text'"):
            parse({"type": "label"})

    def test_defaults(self) -> None:
        options = parse({"type": "label", "text": "hi"}).root.options
        assert options["align"] is HorizontalAlign.LEFT
        assert options["valign"] is VerticalAlign.TOP
        assert options["wrap"] is True
        assert options["shrink_to_fit"] is False

    def test_alignment_is_parsed(self) -> None:
        options = parse({"type": "label", "text": "hi", "align": "center", "valign": "middle"})
        assert options.root.options["align"] is HorizontalAlign.CENTER
        assert options.root.options["valign"] is VerticalAlign.MIDDLE

    def test_a_number_may_be_written_unquoted(self) -> None:
        # YAML turns `text: 42` into an int; requiring quotes there would be a
        # papercut in a file that is already quoting things for YAML's reasons.
        spec = parse({"type": "label", "text": 42})
        assert spec.root.options["text"].render(StaticStateSource()) == "42"

    def test_template_errors_carry_the_path(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.text: unknown filter"):
            parse({"type": "label", "text": "{{ sensor.a | shout }}"})

    def test_font_size_must_be_positive(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.font_size: must be at least 1"):
            parse({"type": "label", "text": "hi", "font_size": 0})

    def test_entities_are_collected_from_templates(self) -> None:
        spec = parse({"type": "label", "text": "{{ sensor.a }} {{ sensor.b }}"})
        assert spec.entity_ids == frozenset({"sensor.a", "sensor.b"})


class TestColors:
    def test_theme_roles_are_accepted(self) -> None:
        spec = parse({"type": "label", "text": "hi", "color": "danger"})
        assert spec.root.options["color"].resolve(spec.theme) == MIDNIGHT.quantized().danger

    def test_hex_literals_are_accepted(self) -> None:
        spec = parse({"type": "label", "text": "hi", "color": "#ff0000"})
        assert spec.root.options["color"].resolve(spec.theme) == Color.from_hex("#ff0000")

    def test_unknown_role_lists_the_alternatives(self) -> None:
        message = r"root\.color: unknown colour 'red'.*accent"
        with pytest.raises(DashboardConfigError, match=message):
            parse({"type": "label", "text": "hi", "color": "red"})

    def test_malformed_hex_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match="not a valid hex colour"):
            parse({"type": "label", "text": "hi", "color": "#gggggg"})

    def test_state_mapping_resolves_against_the_entity(self) -> None:
        spec = parse(
            {
                "type": "icon",
                "icon": "circle",
                "entity": "binary_sensor.door",
                "color": {"on": "danger", "off": "success"},
            }
        )
        ref = spec.root.options["color"]
        assert ref.is_dynamic
        open_door = StaticStateSource({"binary_sensor.door": "on"})
        shut_door = StaticStateSource({"binary_sensor.door": "off"})
        assert ref.resolve(spec.theme, open_door) == spec.theme.danger
        assert ref.resolve(spec.theme, shut_door) == spec.theme.success

    def test_state_mapping_falls_back_to_default(self) -> None:
        spec = parse(
            {
                "type": "icon",
                "icon": "circle",
                "entity": "binary_sensor.door",
                "color": {"on": "danger", "default": "muted"},
            }
        )
        ref = spec.root.options["color"]
        # A sensor inventing a state must not stop the render loop.
        assert ref.resolve(spec.theme, StaticStateSource({"binary_sensor.door": "jammed"})) == (
            spec.theme.muted
        )

    def test_state_mapping_without_a_default_falls_back_to_text(self) -> None:
        spec = parse(
            {
                "type": "icon",
                "icon": "circle",
                "entity": "binary_sensor.door",
                "color": {"on": "danger"},
            }
        )
        ref = spec.root.options["color"]
        assert ref.resolve(spec.theme, StaticStateSource()) == spec.theme.text

    def test_an_empty_reference_falls_back_to_text(self) -> None:
        # ColorRef is public, so a caller can build one naming nothing. Falling
        # back to the readable role beats returning None into a draw call.
        spec = parse_dashboard(MINIMAL)
        assert ColorRef().resolve(spec.theme) == spec.theme.text

    def test_state_mapping_needs_an_entity(self) -> None:
        with pytest.raises(DashboardConfigError, match="needs an 'entity'"):
            parse({"type": "label", "text": "hi", "color": {"on": "danger"}})

    def test_constructor_only_colours_reject_state_mappings(self) -> None:
        # A gauge's track cannot be changed after construction, so a state
        # mapping there would silently freeze at whatever it first resolved to.
        with pytest.raises(DashboardConfigError, match="fixed when the widget is built"):
            parse(
                {
                    "type": "gauge",
                    "entity": "sensor.a",
                    "track_color": {"on": "danger"},
                }
            )

    def test_dynamic_colour_contributes_its_entity(self) -> None:
        spec = parse(
            {
                "type": "icon",
                "icon": "circle",
                "entity": "binary_sensor.door",
                "color": {"on": "danger"},
            }
        )
        assert spec.entity_ids == frozenset({"binary_sensor.door"})


class TestValueReferences:
    def test_entity_reference(self) -> None:
        spec = parse({"type": "gauge", "entity": "sensor.a"})
        reference = spec.root.options["value"]
        assert reference.read(StaticStateSource({"sensor.a": "42"})) == 42.0

    def test_attribute_reference(self) -> None:
        spec = parse({"type": "gauge", "entity": "light.a", "attribute": "brightness"})
        states = StaticStateSource()
        states.set("light.a", "on", brightness=128)
        assert spec.root.options["value"].read(states) == 128.0

    def test_template_reference(self) -> None:
        spec = parse({"type": "gauge", "value": "{{ sensor.a | round(0) }}"})
        assert spec.root.options["value"].read(StaticStateSource({"sensor.a": "41.6"})) == 42.0

    def test_unreadable_value_is_none(self) -> None:
        spec = parse({"type": "gauge", "entity": "sensor.a"})
        reference = spec.root.options["value"]
        assert reference.read(StaticStateSource()) is None
        assert reference.read(StaticStateSource({"sensor.a": "on"})) is None

    def test_unreadable_attribute_is_none(self) -> None:
        spec = parse({"type": "gauge", "entity": "light.a", "attribute": "brightness"})
        assert spec.root.options["value"].read(StaticStateSource({"light.a": "on"})) is None

    def test_needs_one_of_entity_or_value(self) -> None:
        with pytest.raises(DashboardConfigError, match="needs an 'entity' to read"):
            parse({"type": "gauge"})

    def test_rejects_both_entity_and_value(self) -> None:
        with pytest.raises(DashboardConfigError, match="not both"):
            parse({"type": "gauge", "entity": "sensor.a", "value": "1"})

    def test_attribute_needs_an_entity(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.attribute"):
            parse({"type": "gauge", "value": "1", "attribute": "brightness"})

    def test_malformed_entity_id_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.entity: 'kitchen' is not a valid"):
            parse({"type": "gauge", "entity": "kitchen"})


class TestNumericWidgets:
    def test_gauge_defaults(self) -> None:
        options = parse({"type": "gauge", "entity": "sensor.a"}).root.options
        assert options["min"] == 0.0
        assert options["max"] == 100.0
        assert options["segments"] == 10
        assert options["gap"] == 2

    def test_range_must_be_ordered(self) -> None:
        with pytest.raises(DashboardConfigError, match="'max' must not be below 'min'"):
            parse({"type": "gauge", "entity": "sensor.a", "min": 10, "max": 5})

    def test_segments_must_be_positive(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.segments: must be at least 1"):
            parse({"type": "gauge", "entity": "sensor.a", "segments": 0})

    def test_warning_at_is_a_fraction(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.warning_at: must be at most 1"):
            parse({"type": "gauge", "entity": "sensor.a", "warning_at": 80})

    def test_progress_defaults(self) -> None:
        options = parse({"type": "progress", "entity": "sensor.a"}).root.options
        assert options["radius"] == 2
        assert options["vertical"] is False

    def test_sparkline_capacity_has_a_floor(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.capacity: must be at least 2"):
            parse({"type": "sparkline", "entity": "sensor.a", "capacity": 1})

    def test_sparkline_range_must_be_ordered(self) -> None:
        with pytest.raises(DashboardConfigError, match="'max' must not be below 'min'"):
            parse({"type": "sparkline", "entity": "sensor.a", "min": 10, "max": 1})

    def test_optional_colours_are_parsed(self) -> None:
        spec = parse(
            {
                "type": "gauge",
                "entity": "sensor.a",
                "track_color": "surface",
                "warning_at": 0.8,
                "warning_color": "warning",
            }
        )
        assert spec.root.options["track_color"].resolve(spec.theme) == spec.theme.surface
        assert spec.root.options["warning_color"].resolve(spec.theme) == spec.theme.warning

    def test_progress_track_colour_is_parsed(self) -> None:
        spec = parse({"type": "progress", "entity": "sensor.a", "track_color": "surface"})
        assert spec.root.options["track_color"].resolve(spec.theme) == spec.theme.surface

    def test_sparkline_fill_colour_is_parsed(self) -> None:
        spec = parse({"type": "sparkline", "entity": "sensor.a", "fill_color": "outline"})
        assert spec.root.options["fill_color"].resolve(spec.theme) == spec.theme.outline

    def test_a_template_value_contributes_its_entities(self) -> None:
        spec = parse({"type": "gauge", "value": "{{ sensor.a }}"})
        assert spec.entity_ids == frozenset({"sensor.a"})

    def test_a_non_numeric_template_reads_as_none(self) -> None:
        spec = parse({"type": "gauge", "value": "{{ sensor.a }}"})
        assert spec.root.options["value"].read(StaticStateSource({"sensor.a": "on"})) is None

    def test_a_range_bound_must_be_a_number(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.max: expected a number"):
            parse({"type": "gauge", "entity": "sensor.a", "max": "lots"})

    def test_a_range_bound_below_its_floor_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.warning_at: must be at least 0"):
            parse({"type": "gauge", "entity": "sensor.a", "warning_at": -0.5})


class TestIconAndImage:
    def test_icon_requires_a_symbol(self) -> None:
        with pytest.raises(DashboardConfigError, match="an icon needs 'icon'"):
            parse({"type": "icon"})

    def test_icon_name_is_parsed(self) -> None:
        assert parse({"type": "icon", "icon": "warning"}).root.options["icon"] is IconName.WARNING

    def test_unknown_icon_lists_the_alternatives(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.icon: unknown value 'rocket'"):
            parse({"type": "icon", "icon": "rocket"})

    def test_image_requires_a_path(self) -> None:
        with pytest.raises(DashboardConfigError, match="an image needs 'path'"):
            parse({"type": "image"})

    def test_image_defaults_to_fitting(self) -> None:
        assert parse({"type": "image", "path": "logo.png"}).root.options["fit"] is True


class TestTypeErrors:
    def test_a_boolean_is_not_a_number(self) -> None:
        # YAML's `yes`/`no` become booleans, and accepting them as 1 and 0
        # would turn a typo into a plausible-looking layout.
        with pytest.raises(DashboardConfigError, match=r"root\.size: expected a whole number"):
            parse({"type": "spacer", "size": True})

    def test_a_float_is_not_a_whole_number(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.spacing: expected a whole number"):
            parse({"type": "stack", "spacing": 1.5})

    def test_a_number_is_not_text(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"root\.type: expected text"):
            parse({"type": 3})

    def test_null_is_described_as_nothing(self) -> None:
        with pytest.raises(DashboardConfigError, match="got nothing"):
            parse({"type": "label", "text": "hi", "color": None})

    def test_non_string_keys_are_rejected(self) -> None:
        # YAML permits `1: value`; the rest of the parser assumes string keys.
        with pytest.raises(DashboardConfigError, match="keys must be strings"):
            parse_dashboard({"root": {"type": "spacer"}, 1: "oops"})

    def test_a_label_may_name_an_entity_for_its_colour(self) -> None:
        spec = parse(
            {
                "type": "label",
                "text": "Door",
                "entity": "binary_sensor.door",
                "color": {"on": "danger", "off": "muted"},
            }
        )
        assert spec.entity_ids == frozenset({"binary_sensor.door"})


class TestScreens:
    """Several screens in one document, cycled on a timer.

    A dashboard written with a bare ``root`` is a dashboard of one screen, and
    keeps working untouched -- there is no reason to make somebody wrap a
    single screen in a list to say what they already said.
    """

    def test_a_bare_root_is_one_screen(self) -> None:
        spec = parse({"type": "label", "text": "hi"})
        assert len(spec.screens) == 1
        assert spec.screens[0].name is None

    def test_root_still_points_at_the_first_screen(self) -> None:
        # Callers written before screens existed keep working.
        spec = parse({"type": "label", "text": "hi"})
        assert spec.root is spec.screens[0].root

    def test_a_screens_list_is_parsed_in_order(self) -> None:
        spec = parse_dashboard(
            {
                "screens": [
                    {"name": "one", "root": {"type": "label", "text": "a"}},
                    {"name": "two", "root": {"type": "label", "text": "b"}},
                ]
            }
        )
        assert [screen.name for screen in spec.screens] == ["one", "two"]

    def test_a_screen_name_is_optional(self) -> None:
        spec = parse_dashboard({"screens": [{"root": {"type": "spacer"}}]})
        assert spec.screens[0].name is None

    def test_entities_are_the_union_across_screens(self) -> None:
        # The loop subscribes once, so a sensor that only appears on screen two
        # still has to wake it -- or that screen shows whatever it said the
        # last time it happened to be up.
        spec = parse_dashboard(
            {
                "screens": [
                    {"root": {"type": "label", "text": "{{ sensor.a }}"}},
                    {"root": {"type": "label", "text": "{{ sensor.b }}"}},
                ]
            }
        )
        assert spec.entity_ids == frozenset({"sensor.a", "sensor.b"})

    def test_rotate_every_is_read(self) -> None:
        spec = parse_dashboard(
            {
                "rotate_every": 12,
                "screens": [
                    {"root": {"type": "spacer"}},
                    {"root": {"type": "spacer"}},
                ],
            }
        )
        assert spec.rotate_every == 12.0
        assert spec.rotates

    def test_rotation_is_dropped_for_a_single_screen(self) -> None:
        # Not an error -- cutting a dashboard down to one screen and leaving
        # the key behind is ordinary -- but rotating through one screen is a
        # repaint on a timer, which is what max_interval is for.
        spec = parse_dashboard({"rotate_every": 5, "root": {"type": "spacer"}})
        assert spec.rotate_every is None
        assert not spec.rotates

    def test_no_rotation_by_default(self) -> None:
        spec = parse_dashboard(
            {"screens": [{"root": {"type": "spacer"}}, {"root": {"type": "spacer"}}]}
        )
        assert spec.rotate_every is None
        assert not spec.rotates

    def test_root_and_screens_together_are_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"either 'root' .* or 'screens'"):
            parse_dashboard({"root": {"type": "spacer"}, "screens": [{"root": {"type": "spacer"}}]})

    def test_neither_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match="needs a 'root' node, or a 'screens' list"):
            parse_dashboard({"theme": "midnight"})

    def test_an_empty_screens_list_is_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match="needs at least one screen"):
            parse_dashboard({"screens": []})

    def test_screens_must_be_a_list(self) -> None:
        with pytest.raises(DashboardConfigError, match="screens: expected a list"):
            parse_dashboard({"screens": {"root": {"type": "spacer"}}})

    def test_a_screen_needs_a_root(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"screens\[0\]: a screen needs a 'root'"):
            parse_dashboard({"screens": [{"name": "nope"}]})

    def test_errors_inside_a_screen_carry_its_index(self) -> None:
        document = {
            "screens": [
                {"root": {"type": "spacer"}},
                {"root": {"type": "gauge", "entity": "sensor.a", "warning_at": 3}},
            ]
        }
        with pytest.raises(
            DashboardConfigError, match=r"screens\[1\]\.root\.warning_at: must be at most 1"
        ):
            parse_dashboard(document)

    def test_unknown_screen_keys_are_rejected(self) -> None:
        with pytest.raises(DashboardConfigError, match=r"screens\[0\]\.titel: unknown key"):
            parse_dashboard({"screens": [{"root": {"type": "spacer"}, "titel": "typo"}]})

    def test_rotate_every_has_a_floor(self) -> None:
        # Faster than the panel can repaint is a mistake, not a preference.
        with pytest.raises(DashboardConfigError, match=r"rotate_every: must be at least 0\.5"):
            parse_dashboard(
                {
                    "rotate_every": 0.1,
                    "screens": [{"root": {"type": "spacer"}}, {"root": {"type": "spacer"}}],
                }
            )
