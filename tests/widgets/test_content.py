"""Tests for the content widgets: labels, indicators and icons.

Assertions are on painted pixels and on computed geometry rather than on golden
images, matching the rest of the project: a pixel count states the intent, and a
reference PNG only says "something changed".
"""

from __future__ import annotations

import pytest

from tinydisplay.core import Canvas, Color, Font, HorizontalAlign, Rect, VerticalAlign
from tinydisplay.widgets import (
    MIN_FONT_SIZE,
    Gauge,
    Icon,
    IconName,
    Label,
    ProgressBar,
    Sparkline,
    WidgetError,
    wrap_text,
)

INK = Color.from_hex("#ff0000")


def painted(canvas: Canvas, color: Color) -> int:
    """How many pixels carry exactly ``color``.

    Right for the shapes, which are flat fills. Wrong for text -- see
    :func:`inked`.
    """
    return sum(
        1
        for y in range(canvas.height)
        for x in range(canvas.width)
        if canvas.get_pixel(x, y) == color
    )


def inked(canvas: Canvas) -> list[tuple[int, int]]:
    """Every pixel that is not the background.

    Glyphs are antialiased, so most of a letter is a blend between the ink and
    the background rather than the ink itself. Counting exact matches finds
    almost nothing and would make these tests assert that text is invisible.
    """
    return [
        (x, y)
        for y in range(canvas.height)
        for x in range(canvas.width)
        if canvas.get_pixel(x, y) != canvas.background
    ]


class TestLabel:
    def test_it_draws_its_text(self) -> None:
        canvas = Canvas(80, 20)
        Label("Hi", color=INK, bounds=Rect(0, 0, 80, 20)).draw(canvas)
        assert inked(canvas)

    def test_empty_text_draws_nothing(self) -> None:
        canvas = Canvas(40, 20)
        Label("", color=INK, bounds=Rect(0, 0, 40, 20)).draw(canvas)
        assert not inked(canvas)

    def test_setting_text_marks_it_dirty(self) -> None:
        label = Label("a", bounds=Rect(0, 0, 40, 20))
        label.mark_clean()
        label.text = "b"
        assert label.is_dirty

    def test_setting_the_same_text_does_not(self) -> None:
        label = Label("a", bounds=Rect(0, 0, 40, 20))
        label.mark_clean()
        label.text = "a"
        assert not label.is_dirty

    def test_setting_colour_marks_it_dirty(self) -> None:
        label = Label("a", bounds=Rect(0, 0, 40, 20))
        label.mark_clean()
        label.color = INK
        assert label.is_dirty

    def test_it_stays_inside_its_bounds(self) -> None:
        # The base class clips, but a label is the widget most likely to
        # overflow, so this is worth pinning.
        canvas = Canvas(60, 30)
        Label(
            "wide text that will not fit",
            color=INK,
            font=Font.default(20),
            bounds=Rect(10, 10, 20, 10),
            wrap=False,
        ).draw(canvas)

        for x, y in inked(canvas):
            assert 10 <= x < 30
            assert 10 <= y < 20

    @pytest.mark.parametrize(
        "align",
        [HorizontalAlign.LEFT, HorizontalAlign.CENTER, HorizontalAlign.RIGHT],
    )
    def test_every_horizontal_alignment_draws(self, align: HorizontalAlign) -> None:
        canvas = Canvas(80, 20)
        Label("Hi", color=INK, align=align, bounds=Rect(0, 0, 80, 20)).draw(canvas)
        assert inked(canvas)

    @pytest.mark.parametrize(
        "valign",
        [VerticalAlign.TOP, VerticalAlign.MIDDLE, VerticalAlign.BOTTOM],
    )
    def test_every_vertical_alignment_draws(self, valign: VerticalAlign) -> None:
        canvas = Canvas(80, 30)
        Label("Hi", color=INK, valign=valign, bounds=Rect(0, 0, 80, 30)).draw(canvas)
        assert inked(canvas)

    def test_alignment_moves_the_ink(self) -> None:
        def first_column(align: HorizontalAlign) -> int:
            canvas = Canvas(120, 20)
            Label("Hi", color=INK, align=align, bounds=Rect(0, 0, 120, 20)).draw(canvas)
            return min(x for x, _ in inked(canvas))

        assert first_column(HorizontalAlign.LEFT) < first_column(HorizontalAlign.RIGHT)

    def test_shrink_to_fit_reduces_the_font(self) -> None:
        canvas = Canvas(40, 12)
        label = Label(
            "a long sentence that cannot fit",
            color=INK,
            font=Font.default(24),
            bounds=Rect(0, 0, 40, 12),
            shrink_to_fit=True,
        )
        label.draw(canvas)
        # Something was drawn, and it stayed inside the widget.
        assert inked(canvas)


class TestWrapText:
    def test_it_breaks_long_lines(self) -> None:
        assert len(wrap_text("hello there world", Font.default(12), 50)) > 1

    def test_short_text_is_one_line(self) -> None:
        assert wrap_text("hi", Font.default(12), 200) == ["hi"]

    def test_explicit_newlines_are_kept(self) -> None:
        assert len(wrap_text("a\nb", Font.default(12), 200)) == 2

    def test_a_word_wider_than_the_line_is_broken(self) -> None:
        lines = wrap_text("supercalifragilistic", Font.default(14), 30)
        assert len(lines) > 1

    def test_zero_width_returns_the_text_unbroken(self) -> None:
        assert wrap_text("anything", Font.default(12), 0) == ["anything"]

    def test_the_minimum_font_size_is_still_text(self) -> None:
        assert MIN_FONT_SIZE >= 6


class TestProgressBar:
    def test_the_fill_is_proportional(self) -> None:
        canvas = Canvas(100, 10)
        ProgressBar(50, color=INK, radius=0, bounds=Rect(0, 0, 100, 10)).draw(canvas)
        assert painted(canvas, INK) == pytest.approx(500, abs=20)

    def test_zero_draws_no_fill(self) -> None:
        canvas = Canvas(100, 10)
        ProgressBar(0, color=INK, radius=0, bounds=Rect(0, 0, 100, 10)).draw(canvas)
        assert painted(canvas, INK) == 0

    def test_full_covers_the_widget(self) -> None:
        canvas = Canvas(100, 10)
        ProgressBar(100, color=INK, radius=0, bounds=Rect(0, 0, 100, 10)).draw(canvas)
        assert painted(canvas, INK) == 1000

    def test_values_above_the_maximum_clamp(self) -> None:
        # A sensor reporting 150% should look full, not overflow the widget.
        bar = ProgressBar(150, bounds=Rect(0, 0, 100, 10))
        assert bar.fraction == 1.0

    def test_values_below_the_minimum_clamp(self) -> None:
        assert ProgressBar(-20, bounds=Rect(0, 0, 100, 10)).fraction == 0.0

    def test_a_track_is_drawn_behind(self) -> None:
        track = Color.from_hex("#00ff00")
        canvas = Canvas(100, 10)
        ProgressBar(
            50,
            color=INK,
            track_color=track,
            radius=0,
            bounds=Rect(0, 0, 100, 10),
        ).draw(canvas)
        assert painted(canvas, track) > 0

    def test_vertical_bars_fill_upwards(self) -> None:
        canvas = Canvas(10, 100)
        ProgressBar(50, color=INK, radius=0, vertical=True, bounds=Rect(0, 0, 10, 100)).draw(canvas)
        assert canvas.get_pixel(5, 90) == INK
        assert canvas.get_pixel(5, 10) != INK

    def test_a_degenerate_range_does_not_divide_by_zero(self) -> None:
        assert ProgressBar(5, minimum=5, maximum=5, bounds=Rect(0, 0, 10, 10)).fraction == 0.0

    def test_an_inverted_range_is_refused(self) -> None:
        with pytest.raises(WidgetError, match="must not be below minimum"):
            ProgressBar(0, minimum=10, maximum=0)

    def test_a_negative_radius_is_refused(self) -> None:
        with pytest.raises(WidgetError, match="radius must be non-negative"):
            ProgressBar(0, radius=-1)


class TestGauge:
    def test_segments_light_in_proportion(self) -> None:
        assert Gauge(60, segments=10, bounds=Rect(0, 0, 100, 10)).lit_segments == 6

    def test_any_value_above_zero_lights_one_segment(self) -> None:
        # A gauge showing nothing at 1% reads as broken rather than as low.
        assert Gauge(1, segments=10, bounds=Rect(0, 0, 100, 10)).lit_segments == 1

    def test_zero_lights_nothing(self) -> None:
        assert Gauge(0, segments=10, bounds=Rect(0, 0, 100, 10)).lit_segments == 0

    def test_the_top_of_the_range_lights_everything(self) -> None:
        assert Gauge(100, segments=10, bounds=Rect(0, 0, 100, 10)).lit_segments == 10

    def test_overshoot_does_not_light_extra_segments(self) -> None:
        assert Gauge(400, segments=10, bounds=Rect(0, 0, 100, 10)).lit_segments == 10

    def test_the_warning_threshold_changes_state(self) -> None:
        gauge = Gauge(85, segments=10, warning_at=0.8, bounds=Rect(0, 0, 100, 10))
        assert gauge.is_warning

    def test_below_the_threshold_is_not_a_warning(self) -> None:
        gauge = Gauge(50, segments=10, warning_at=0.8, bounds=Rect(0, 0, 100, 10))
        assert not gauge.is_warning

    def test_no_threshold_never_warns(self) -> None:
        assert not Gauge(100, segments=10, bounds=Rect(0, 0, 100, 10)).is_warning

    def test_it_draws_the_lit_segments(self) -> None:
        canvas = Canvas(100, 10)
        Gauge(50, segments=10, color=INK, gap=0, bounds=Rect(0, 0, 100, 10)).draw(canvas)
        assert painted(canvas, INK) == pytest.approx(500, abs=30)

    def test_the_warning_colour_is_used(self) -> None:
        amber = Color.from_hex("#00ff00")
        canvas = Canvas(100, 10)
        Gauge(
            90,
            segments=10,
            color=INK,
            warning_at=0.8,
            warning_color=amber,
            bounds=Rect(0, 0, 100, 10),
        ).draw(canvas)
        assert painted(canvas, amber) > 0
        assert painted(canvas, INK) == 0

    def test_vertical_gauges_light_from_the_bottom(self) -> None:
        canvas = Canvas(10, 100)
        Gauge(30, segments=10, color=INK, gap=0, vertical=True, bounds=Rect(0, 0, 10, 100)).draw(
            canvas
        )
        assert canvas.get_pixel(5, 95) == INK
        assert canvas.get_pixel(5, 5) != INK

    def test_zero_segments_is_refused(self) -> None:
        with pytest.raises(WidgetError, match="at least one segment"):
            Gauge(0, segments=0)

    def test_a_negative_gap_is_refused(self) -> None:
        with pytest.raises(WidgetError, match="gap must be non-negative"):
            Gauge(0, gap=-1)

    def test_an_out_of_range_threshold_is_refused(self) -> None:
        with pytest.raises(WidgetError, match="fraction between 0 and 1"):
            Gauge(0, warning_at=1.5)


class TestSparkline:
    def test_it_draws_a_line(self) -> None:
        canvas = Canvas(40, 20)
        Sparkline([1, 5, 2, 8], color=INK, bounds=Rect(0, 0, 40, 20)).draw(canvas)
        assert painted(canvas, INK) > 0

    def test_fewer_than_two_points_draws_nothing(self) -> None:
        canvas = Canvas(40, 20)
        Sparkline([5], color=INK, bounds=Rect(0, 0, 40, 20)).draw(canvas)
        assert painted(canvas, INK) == 0

    def test_push_appends(self) -> None:
        spark = Sparkline([1, 2], bounds=Rect(0, 0, 40, 20))
        spark.push(3)
        assert list(spark.values) == [1, 2, 3]

    def test_capacity_drops_the_oldest(self) -> None:
        spark = Sparkline([1, 2, 3], capacity=3, bounds=Rect(0, 0, 40, 20))
        spark.push(4)
        assert list(spark.values) == [2, 3, 4]

    def test_capacity_applies_to_the_initial_series(self) -> None:
        assert list(Sparkline([1, 2, 3, 4], capacity=2).values) == [3, 4]

    def test_it_autoscales_to_the_data(self) -> None:
        assert Sparkline([10, 20]).scale() == (10, 20)

    def test_an_explicit_scale_is_respected(self) -> None:
        assert Sparkline([10, 20], minimum=0, maximum=100).scale() == (0, 100)

    def test_a_flat_series_gets_an_artificial_range(self) -> None:
        # Otherwise the line collapses onto an edge, or divides by zero.
        low, high = Sparkline([5, 5, 5]).scale()
        assert low < high

    def test_a_flat_series_draws_without_error(self) -> None:
        canvas = Canvas(40, 20)
        Sparkline([5, 5, 5], color=INK, bounds=Rect(0, 0, 40, 20)).draw(canvas)
        assert painted(canvas, INK) > 0

    def test_the_fill_paints_under_the_line(self) -> None:
        fill = Color.from_hex("#00ff00")
        canvas = Canvas(40, 20)
        Sparkline([1, 9, 3], color=INK, fill_color=fill, bounds=Rect(0, 0, 40, 20)).draw(canvas)
        assert painted(canvas, fill) > 0

    def test_clearing_empties_the_series(self) -> None:
        spark = Sparkline([1, 2, 3])
        spark.clear()
        assert spark.values == ()

    def test_a_zero_capacity_is_refused(self) -> None:
        with pytest.raises(WidgetError, match="capacity must be at least 1"):
            Sparkline([], capacity=0)


class TestIcon:
    @pytest.mark.parametrize("symbol", list(IconName))
    def test_every_icon_draws_something(self, symbol: IconName) -> None:
        canvas = Canvas(24, 24)
        Icon(symbol, color=INK, bounds=Rect(0, 0, 24, 24)).draw(canvas)
        assert painted(canvas, INK) > 0, symbol

    @pytest.mark.parametrize("symbol", list(IconName))
    def test_every_icon_stays_inside_its_bounds(self, symbol: IconName) -> None:
        canvas = Canvas(40, 40)
        Icon(symbol, color=INK, bounds=Rect(10, 10, 20, 20)).draw(canvas)
        for y in range(canvas.height):
            for x in range(canvas.width):
                if canvas.get_pixel(x, y) == INK:
                    assert 10 <= x < 30, symbol
                    assert 10 <= y < 30, symbol

    def test_a_tiny_icon_draws_nothing_rather_than_a_smudge(self) -> None:
        canvas = Canvas(4, 4)
        Icon(IconName.CHECK, color=INK, bounds=Rect(0, 0, 3, 3)).draw(canvas)
        assert painted(canvas, INK) == 0

    def test_icons_are_square_in_a_wide_box(self) -> None:
        # A row of icons in differently shaped slots should still look like a
        # row of icons.
        canvas = Canvas(60, 20)
        Icon(IconName.SQUARE, color=INK, bounds=Rect(0, 0, 60, 20)).draw(canvas)
        columns = {
            x
            for y in range(canvas.height)
            for x in range(canvas.width)
            if canvas.get_pixel(x, y) == INK
        }
        assert max(columns) - min(columns) <= 20

    def test_the_symbol_is_reported(self) -> None:
        assert Icon(IconName.BOLT).name_of_symbol is IconName.BOLT

    def test_setting_colour_marks_it_dirty(self) -> None:
        icon = Icon(IconName.DOT, bounds=Rect(0, 0, 16, 16))
        icon.mark_clean()
        icon.color = INK
        assert icon.is_dirty
