"""Tests for turning a validated dashboard into a live widget tree.

The property under test throughout is that the tree is *built once and
updated*, not rebuilt: widgets keep their identity across updates, only the
bindings that can change produce updaters, and a dashboard with nothing dynamic
produces none at all.
"""

from __future__ import annotations

import itertools
import os
from typing import TYPE_CHECKING

import pytest

from tinydisplay.core import Canvas, Color, Container, Rect, Widget
from tinydisplay.homeassistant import (
    Dashboard,
    DashboardConfigError,
    StaticStateSource,
    build_dashboard,
    parse_dashboard,
)
from tinydisplay.widgets import Gauge, Grid, Icon, Label, Padding, ProgressBar, Sparkline, Stack

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def walk(widget: Widget) -> Iterator[Widget]:
    """Yield ``widget`` and every descendant."""
    yield widget
    if isinstance(widget, Container):
        for child in widget:
            yield from walk(child)


def find[WidgetT: Widget](root: Widget, kind: type[WidgetT]) -> WidgetT:
    """The first widget of ``kind`` in the tree, which is what these tests want."""
    for widget in walk(root):
        if isinstance(widget, kind):
            return widget
    message = f"no {kind.__name__} in the tree"
    raise AssertionError(message)


def build(root: object, **document: object) -> Dashboard:
    return Dashboard.from_document({"root": root, **document})


#: A fixed point in time for hot-reload stamps, so they never depend on the
#: wall clock or on how fast the test ran.
_BASE_NS = 1_700_000_000_000_000_000
_TICKS = itertools.count(1)


class TestUpdaterCount:
    def test_a_constant_dashboard_has_no_updaters(self) -> None:
        dashboard = build({"type": "label", "text": "Kitchen", "color": "text"})
        assert dashboard.is_static

    def test_a_template_produces_one_updater(self) -> None:
        built = build_dashboard(parse_dashboard({"root": {"type": "label", "text": "{{ s.a }}"}}))
        assert len(built.updaters) == 1

    def test_a_static_colour_produces_none(self) -> None:
        built = build_dashboard(
            parse_dashboard({"root": {"type": "label", "text": "hi", "color": "danger"}})
        )
        assert built.updaters == ()

    def test_a_dynamic_colour_produces_one(self) -> None:
        built = build_dashboard(
            parse_dashboard(
                {
                    "root": {
                        "type": "icon",
                        "icon": "circle",
                        "entity": "binary_sensor.a",
                        "color": {"on": "danger", "off": "muted"},
                    }
                }
            )
        )
        assert len(built.updaters) == 1


class TestLabelBinding:
    def test_constant_text_is_rendered_at_build_time(self) -> None:
        dashboard = build({"type": "label", "text": "Kitchen"})
        assert find(dashboard.root, Label).text == "Kitchen"

    def test_template_text_follows_state(self) -> None:
        dashboard = build({"type": "label", "text": "{{ sensor.a | round(1) }} C"})
        label = find(dashboard.root, Label)

        dashboard.update(StaticStateSource({"sensor.a": "21.53"}))
        assert label.text == "21.5 C"

        dashboard.update(StaticStateSource({"sensor.a": "22.07"}))
        assert label.text == "22.1 C"

    def test_the_widget_keeps_its_identity_across_updates(self) -> None:
        # The whole point of building once: a rebuilt tree would drop dirty
        # tracking, cached layout and sparkline history every frame.
        dashboard = build({"type": "label", "text": "{{ sensor.a }}"})
        first = find(dashboard.root, Label)
        dashboard.update(StaticStateSource({"sensor.a": "1"}))
        dashboard.update(StaticStateSource({"sensor.a": "2"}))
        assert find(dashboard.root, Label) is first

    def test_missing_entity_renders_the_placeholder(self) -> None:
        dashboard = build({"type": "label", "text": "{{ sensor.gone }}"}, unavailable="n/a")
        dashboard.update(StaticStateSource())
        assert find(dashboard.root, Label).text == "n/a"

    def test_font_size_is_applied(self) -> None:
        dashboard = build({"type": "label", "text": "hi", "font_size": 24})
        font = find(dashboard.root, Label).font
        assert font is not None
        assert font.size == 24

    def test_no_font_size_leaves_the_default(self) -> None:
        assert find(build({"type": "label", "text": "hi"}).root, Label).font is None


class TestColorBinding:
    def test_a_role_resolves_against_the_quantised_theme(self) -> None:
        dashboard = build({"type": "label", "text": "hi", "color": "accent"})
        assert find(dashboard.root, Label).color == dashboard.theme.accent

    def test_a_dynamic_colour_follows_state(self) -> None:
        dashboard = build(
            {
                "type": "icon",
                "icon": "circle",
                "entity": "binary_sensor.door",
                "color": {"on": "danger", "off": "success"},
            }
        )
        icon = find(dashboard.root, Icon)

        dashboard.update(StaticStateSource({"binary_sensor.door": "on"}))
        assert icon.color == dashboard.theme.danger

        dashboard.update(StaticStateSource({"binary_sensor.door": "off"}))
        assert icon.color == dashboard.theme.success

    def test_a_gauge_colour_can_be_dynamic(self) -> None:
        dashboard = build(
            {
                "type": "gauge",
                "entity": "sensor.a",
                "color": {"5": "danger", "default": "success"},
            }
        )
        gauge = find(dashboard.root, Gauge)
        dashboard.update(StaticStateSource({"sensor.a": "5"}))
        assert gauge.color == dashboard.theme.danger


class TestNumericBinding:
    def test_a_gauge_follows_its_entity(self) -> None:
        dashboard = build({"type": "gauge", "entity": "sensor.a", "max": 100, "segments": 10})
        gauge = find(dashboard.root, Gauge)
        dashboard.update(StaticStateSource({"sensor.a": "60"}))
        assert gauge.value == 60.0
        assert gauge.lit_segments == 6

    def test_an_unreadable_value_falls_to_the_minimum(self) -> None:
        # An empty gauge rather than a stopped loop: the widget library clamps
        # when drawing, and that rule reaches up to here.
        dashboard = build({"type": "gauge", "entity": "sensor.a", "min": 10, "max": 100})
        gauge = find(dashboard.root, Gauge)
        dashboard.update(StaticStateSource({"sensor.a": "unavailable"}))
        assert gauge.value == 10.0
        assert gauge.lit_segments == 0

    def test_an_out_of_range_value_is_clamped_not_rejected(self) -> None:
        dashboard = build({"type": "gauge", "entity": "sensor.a", "max": 100})
        gauge = find(dashboard.root, Gauge)
        dashboard.update(StaticStateSource({"sensor.a": "150"}))
        assert gauge.fraction == 1.0

    def test_a_progress_bar_follows_its_entity(self) -> None:
        dashboard = build({"type": "progress", "entity": "sensor.a", "max": 50})
        bar = find(dashboard.root, ProgressBar)
        dashboard.update(StaticStateSource({"sensor.a": "25"}))
        assert bar.fraction == pytest.approx(0.5)

    def test_a_template_value_is_read(self) -> None:
        dashboard = build({"type": "gauge", "value": "{{ sensor.a | round(0) }}"})
        gauge = find(dashboard.root, Gauge)
        dashboard.update(StaticStateSource({"sensor.a": "41.6"}))
        assert gauge.value == 42.0

    def test_an_attribute_value_is_read(self) -> None:
        dashboard = build({"type": "gauge", "entity": "light.a", "attribute": "brightness"})
        states = StaticStateSource()
        states.set("light.a", "on", brightness=128)
        dashboard.update(states)
        assert find(dashboard.root, Gauge).value == 128.0


class TestSparklineBinding:
    def test_samples_are_recorded_as_the_value_changes(self) -> None:
        dashboard = build({"type": "sparkline", "entity": "sensor.a"})
        spark = find(dashboard.root, Sparkline)

        for reading in ("1", "2", "3"):
            dashboard.update(StaticStateSource({"sensor.a": reading}))

        assert list(spark.values) == [1.0, 2.0, 3.0]

    def test_repeated_values_are_not_resampled(self) -> None:
        # The loop repaints for reasons unrelated to this entity; sampling on
        # every repaint would let a neighbouring widget stretch this history.
        dashboard = build({"type": "sparkline", "entity": "sensor.a"})
        spark = find(dashboard.root, Sparkline)

        for _ in range(5):
            dashboard.update(StaticStateSource({"sensor.a": "7"}))

        assert list(spark.values) == [7.0]

    def test_unreadable_values_are_not_recorded(self) -> None:
        dashboard = build({"type": "sparkline", "entity": "sensor.a"})
        spark = find(dashboard.root, Sparkline)
        dashboard.update(StaticStateSource({"sensor.a": "unavailable"}))
        dashboard.update(StaticStateSource())
        assert list(spark.values) == []

    def test_capacity_drops_the_oldest(self) -> None:
        dashboard = build({"type": "sparkline", "entity": "sensor.a", "capacity": 3})
        spark = find(dashboard.root, Sparkline)
        for reading in range(5):
            dashboard.update(StaticStateSource({"sensor.a": str(reading)}))
        assert list(spark.values) == [2.0, 3.0, 4.0]


class TestLayout:
    def test_stack_slots_take_their_sizes(self) -> None:
        dashboard = build(
            {
                "type": "stack",
                "axis": "vertical",
                "children": [
                    {"type": "spacer", "size": 20},
                    {"type": "spacer"},
                ],
            }
        )
        stack = find(dashboard.root, Stack)
        stack.bounds = Rect(0, 0, 100, 60)
        stack.layout()
        assert [child.bounds.height for child in stack] == [20, 40]

    def test_grid_children_are_placed(self) -> None:
        dashboard = build(
            {
                "type": "grid",
                "rows": 2,
                "columns": 2,
                "children": [{"type": "spacer", "row": 1, "column": 1}],
            }
        )
        grid = find(dashboard.root, Grid)
        grid.bounds = Rect(0, 0, 100, 100)
        grid.layout()
        assert next(iter(grid)).bounds == Rect(50, 50, 50, 50)

    def test_padding_wraps_the_node(self) -> None:
        dashboard = build({"type": "label", "text": "hi", "padding": 4})
        assert isinstance(dashboard.root, Padding)
        dashboard.root.bounds = Rect(0, 0, 40, 20)
        dashboard.root.layout()
        assert dashboard.root.child.bounds == Rect(4, 4, 32, 12)

    def test_no_padding_leaves_the_node_unwrapped(self) -> None:
        assert isinstance(build({"type": "label", "text": "hi"}).root, Label)

    def test_padding_applies_inside_a_stack_slot(self) -> None:
        dashboard = build(
            {
                "type": "stack",
                "children": [{"type": "label", "text": "hi", "padding": 2, "size": 20}],
            }
        )
        stack = find(dashboard.root, Stack)
        assert isinstance(stack.slots[0].widget, Padding)
        assert stack.slots[0].size == 20


class TestImage:
    @pytest.fixture
    def logo(self, tmp_path: Path) -> Path:
        path = tmp_path / "logo.png"
        source = Canvas(8, 8)
        source.clear(Color.from_hex("#ff0000"))
        source.save(path)
        return path

    def test_an_image_node_draws_its_file(self, logo: Path) -> None:
        dashboard = build({"type": "image", "path": str(logo)}, background="#000000")
        canvas = Canvas(16, 16)
        dashboard.render(canvas, StaticStateSource())
        # `fit` defaults on, so the 8x8 source fills the 16x16 canvas.
        assert canvas.get_pixel(0, 0) == Color.from_hex("#ff0000")
        assert canvas.get_pixel(15, 15) == Color.from_hex("#ff0000")

    def test_fit_off_draws_at_the_source_size(self, logo: Path) -> None:
        dashboard = build(
            {"type": "image", "path": str(logo), "fit": False},
            background="#000000",
        )
        canvas = Canvas(16, 16)
        dashboard.render(canvas, StaticStateSource())
        assert canvas.get_pixel(0, 0) == Color.from_hex("#ff0000")
        assert canvas.get_pixel(15, 15) == Color.from_hex("#000000")

    def test_an_image_needs_no_updater(self, logo: Path) -> None:
        assert build({"type": "image", "path": str(logo)}).is_static


class TestDashboardRendering:
    def test_render_clears_to_the_background(self) -> None:
        dashboard = build({"type": "spacer"}, background="#123456")
        canvas = Canvas(32, 16)
        dashboard.render(canvas, StaticStateSource())
        assert canvas.get_pixel(0, 0) == Color.from_hex("#123456")
        assert canvas.get_pixel(31, 15) == Color.from_hex("#123456")

    def test_render_fills_the_canvas_it_is_given(self) -> None:
        # A dashboard is not tied to a panel size: the same definition is
        # previewed in the simulator and drawn on hardware.
        dashboard = build({"type": "spacer"})
        for width, height in ((320, 170), (64, 64)):
            canvas = Canvas(width, height)
            dashboard.draw(canvas)
            assert dashboard.root.bounds == Rect(0, 0, width, height)

    def test_a_bound_widget_actually_paints(self) -> None:
        dashboard = build(
            {"type": "progress", "entity": "sensor.a", "max": 100, "color": "#ff0000", "radius": 0},
            background="#000000",
        )
        canvas = Canvas(100, 10)
        dashboard.render(canvas, StaticStateSource({"sensor.a": "50"}))
        assert canvas.get_pixel(10, 5) == Color.from_hex("#ff0000")
        assert canvas.get_pixel(90, 5) == Color.from_hex("#000000")

    def test_render_is_update_then_draw(self) -> None:
        dashboard = build({"type": "label", "text": "{{ sensor.a }}"})
        canvas = Canvas(64, 16)
        dashboard.render(canvas, StaticStateSource({"sensor.a": "hello"}))
        assert find(dashboard.root, Label).text == "hello"

    def test_repeated_draws_are_stable(self) -> None:
        dashboard = build({"type": "label", "text": "hi", "color": "text"})
        first, second = Canvas(64, 16), Canvas(64, 16)
        dashboard.draw(first)
        dashboard.draw(second)
        assert first.to_rgb888() == second.to_rgb888()


class TestDashboardFacade:
    def test_entity_ids_are_reported(self) -> None:
        dashboard = build(
            {
                "type": "stack",
                "children": [
                    {"type": "label", "text": "{{ sensor.a }}"},
                    {"type": "gauge", "entity": "sensor.b"},
                ],
            }
        )
        assert dashboard.entity_ids == frozenset({"sensor.a", "sensor.b"})

    def test_theme_is_quantised(self) -> None:
        dashboard = build({"type": "spacer"}, theme="paper")
        assert dashboard.theme.background == dashboard.theme.background.quantized_rgb565()

    def test_from_yaml(self) -> None:
        dashboard = Dashboard.from_yaml("root:\n  type: label\n  text: hello\n")
        assert find(dashboard.root, Label).text == "hello"

    def test_load_records_the_source_path(self, tmp_path: Path) -> None:
        path = tmp_path / "dash.yaml"
        path.write_text("root:\n  type: spacer\n", encoding="utf-8")
        dashboard = Dashboard.load(path)
        assert dashboard.source_path == path

    def test_spec_is_reachable(self) -> None:
        dashboard = build({"type": "spacer"}, theme="high-contrast")
        assert dashboard.spec.theme_name == "high-contrast"
        assert dashboard.spec.root.kind == "spacer"

    def test_no_source_path_for_an_inline_dashboard(self) -> None:
        assert build({"type": "spacer"}).source_path is None

    def test_repr_names_the_theme_and_entity_count(self) -> None:
        dashboard = build({"type": "label", "text": "{{ sensor.a }}"})
        assert repr(dashboard) == "Dashboard(midnight, 1 entities)"


class TestHotReload:
    """Re-reading a dashboard whose file has changed, in place.

    In place matters: a running render loop holds this object, and handing back
    a new one would mean finding every holder. Rebuilding inside lets an edit
    reach the panel without restarting anything.
    """

    def write(self, path: Path, text: str) -> None:
        """Write the file with a stamp that is always newer than the last.

        Two edits inside one filesystem timestamp tick are indistinguishable,
        which is a real thing to do while getting a layout right. Stamping from
        a counter rather than the clock keeps these tests about the reload
        logic instead of about timer resolution -- and an earlier version that
        nudged the clock forward by a millisecond made the *second* write land
        before the first, which is exactly the kind of flake this avoids.
        """
        path.write_text(text, encoding="utf-8")
        stamp = _BASE_NS + next(_TICKS) * 1_000_000_000
        os.utime(path, ns=(stamp, stamp))

    def test_an_unchanged_file_is_not_reread(self, tmp_path: Path) -> None:
        path = tmp_path / "d.yaml"
        self.write(path, "root:\n  type: label\n  text: one\n")
        dashboard = Dashboard.load(path)

        assert dashboard.reload_if_changed() is False
        assert find(dashboard.root, Label).text == "one"

    def test_a_changed_file_is_picked_up(self, tmp_path: Path) -> None:
        path = tmp_path / "d.yaml"
        self.write(path, "root:\n  type: label\n  text: one\n")
        dashboard = Dashboard.load(path)

        self.write(path, "root:\n  type: label\n  text: two\n")
        assert dashboard.reload_if_changed() is True
        assert find(dashboard.root, Label).text == "two"

    def test_the_object_survives_the_reload(self, tmp_path: Path) -> None:
        # The whole point: whoever is holding this keeps holding it.
        path = tmp_path / "d.yaml"
        self.write(path, "root:\n  type: label\n  text: one\n")
        dashboard = Dashboard.load(path)

        self.write(path, "root:\n  type: label\n  text: two\n")
        dashboard.reload_if_changed()
        assert dashboard.source_path == path

    def test_new_entities_are_reported(self, tmp_path: Path) -> None:
        # A caller subscribing to entity_ids has to know they moved.
        path = tmp_path / "d.yaml"
        self.write(path, 'root:\n  type: label\n  text: "{{ sensor.a }}"\n')
        dashboard = Dashboard.load(path)
        assert dashboard.entity_ids == {"sensor.a"}

        self.write(path, 'root:\n  type: label\n  text: "{{ sensor.b }}"\n')
        dashboard.reload_if_changed()
        assert dashboard.entity_ids == {"sensor.b"}

    def test_a_broken_edit_leaves_the_last_good_one_running(self, tmp_path: Path) -> None:
        # An edit that does not parse must not blank the panel.
        path = tmp_path / "d.yaml"
        self.write(path, "root:\n  type: label\n  text: one\n")
        dashboard = Dashboard.load(path)

        self.write(path, "root:\n  type: nonsense\n")
        with pytest.raises(DashboardConfigError):
            dashboard.reload_if_changed()

        assert find(dashboard.root, Label).text == "one"

    def test_a_broken_edit_is_retried_after_it_is_fixed(self, tmp_path: Path) -> None:
        # The stamp is only advanced on success, so a fixed file is noticed.
        path = tmp_path / "d.yaml"
        self.write(path, "root:\n  type: label\n  text: one\n")
        dashboard = Dashboard.load(path)

        self.write(path, "root:\n  type: nonsense\n")
        with pytest.raises(DashboardConfigError):
            dashboard.reload_if_changed()

        self.write(path, "root:\n  type: label\n  text: three\n")
        assert dashboard.reload_if_changed() is True
        assert find(dashboard.root, Label).text == "three"

    def test_an_inline_dashboard_never_reloads(self) -> None:
        dashboard = Dashboard.from_yaml("root:\n  type: label\n  text: one\n")
        assert dashboard.source_path is None
        assert dashboard.reload_if_changed() is False

    def test_a_deleted_file_is_not_an_error(self, tmp_path: Path) -> None:
        # Editors write by rename, so the file can briefly not exist. Keeping
        # the last good dashboard beats blanking the panel over a save.
        path = tmp_path / "d.yaml"
        self.write(path, "root:\n  type: label\n  text: one\n")
        dashboard = Dashboard.load(path)

        path.unlink()
        assert dashboard.reload_if_changed() is False
        assert find(dashboard.root, Label).text == "one"
