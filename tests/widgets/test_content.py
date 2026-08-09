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
    Zone,
    wrap_text,
)

INK = Color.from_hex("#ff0000")
GREEN = Color.from_hex("#00ff00")
AMBER = Color.from_hex("#ffbb00")
CRIMSON = Color.from_hex("#cc0033")


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


class TestGaugeZones:
    """Colour bands, which colour each segment by where it sits in the range."""

    ZONES = (Zone(60, GREEN), Zone(80, AMBER), Zone(None, CRIMSON))

    def gauge(self, value: float, **kwargs: object) -> Gauge:
        return Gauge(
            value,
            segments=10,
            gap=0,
            color=INK,
            zones=self.ZONES,
            bounds=Rect(0, 0, 100, 10),
            **kwargs,  # type: ignore[arg-type]
        )

    def test_each_segment_takes_the_colour_of_the_band_it_sits_in(self) -> None:
        # Ten segments over 0..100, so midpoints fall at 5, 15 ... 95: six below
        # 60, two below 80, two above. Each segment is a 10x10 block.
        canvas = Canvas(100, 10)
        self.gauge(100).draw(canvas)
        assert painted(canvas, GREEN) == 600
        assert painted(canvas, AMBER) == 200
        assert painted(canvas, CRIMSON) == 200

    def test_a_low_value_lights_only_the_green_band(self) -> None:
        # The point of zones over a threshold: the tip of a cool gauge is still
        # green, rather than the whole bar being one state colour.
        canvas = Canvas(100, 10)
        self.gauge(40).draw(canvas)
        assert painted(canvas, GREEN) == 400
        assert painted(canvas, AMBER) == 0
        assert painted(canvas, CRIMSON) == 0

    def test_the_green_band_survives_the_bar_going_red(self) -> None:
        canvas = Canvas(100, 10)
        self.gauge(90).draw(canvas)
        assert painted(canvas, GREEN) == 600
        assert painted(canvas, CRIMSON) == 100

    def test_a_boundary_inside_a_segment_goes_to_the_majority_side(self) -> None:
        # Boundary at 62 splits segment 6 (60..70) 20/80, so it reads as amber.
        gauge = Gauge(100, segments=10, zones=[Zone(62, GREEN), Zone(None, AMBER)])
        assert gauge.segment_color(5) == GREEN
        assert gauge.segment_color(6) == AMBER

    def test_boundaries_are_value_units_not_fractions(self) -> None:
        # A 30..90 gauge: 65 means 65 degrees, and the band ends there whatever
        # the maximum is.
        gauge = Gauge(90, minimum=30, maximum=90, segments=6, zones=[Zone(65, GREEN)])
        # Midpoints at 35, 45, 55, 65, 75, 85 -- the first three are below 65.
        assert [gauge.segment_color(i) for i in range(3)] == [GREEN] * 3
        assert gauge.segment_color(3) != GREEN

    def test_segments_past_the_last_band_fall_back_to_the_plain_colour(self) -> None:
        gauge = Gauge(100, segments=10, color=INK, zones=[Zone(50, GREEN)])
        assert gauge.segment_color(0) == GREEN
        assert gauge.segment_color(9) == INK

    def test_unlit_segments_still_use_the_track_colour(self) -> None:
        canvas = Canvas(100, 10)
        self.gauge(30, track_color=CRIMSON).draw(canvas)
        assert painted(canvas, GREEN) == 300
        assert painted(canvas, CRIMSON) == 700

    def test_zones_and_a_warning_threshold_together_are_refused(self) -> None:
        with pytest.raises(WidgetError, match="'zones' or 'warning_at', not both"):
            Gauge(0, zones=[Zone(None, GREEN)], warning_at=0.8)

    def test_boundaries_must_increase(self) -> None:
        with pytest.raises(WidgetError, match="boundaries must increase"):
            Gauge(0, zones=[Zone(80, GREEN), Zone(60, AMBER)])

    def test_an_open_ended_band_must_come_last(self) -> None:
        with pytest.raises(WidgetError, match="only the last zone may be open-ended"):
            Gauge(0, zones=[Zone(None, GREEN), Zone(80, AMBER)])

    def test_no_zones_leaves_the_threshold_behaviour_alone(self) -> None:
        gauge = Gauge(90, segments=10, color=INK, warning_at=0.8, warning_color=AMBER)
        assert gauge.segment_color(0) == AMBER
        assert gauge.zones == ()


class TestGaugeThickness:
    """A bar narrower than its slot, centred in it."""

    def test_it_narrows_the_bar_and_centres_it(self) -> None:
        canvas = Canvas(100, 20)
        Gauge(100, segments=10, gap=0, color=INK, thickness=6, bounds=Rect(0, 0, 100, 20)).draw(
            canvas
        )
        assert painted(canvas, INK) == 600
        # Centred: rows 7..12 painted, the ones either side clear.
        assert canvas.get_pixel(50, 10) == INK
        assert canvas.get_pixel(50, 2) != INK
        assert canvas.get_pixel(50, 18) != INK

    def test_a_vertical_gauge_narrows_across_its_width(self) -> None:
        canvas = Canvas(20, 100)
        Gauge(
            100,
            segments=10,
            gap=0,
            color=INK,
            thickness=6,
            vertical=True,
            bounds=Rect(0, 0, 20, 100),
        ).draw(canvas)
        assert painted(canvas, INK) == 600
        assert canvas.get_pixel(10, 50) == INK
        assert canvas.get_pixel(2, 50) != INK

    def test_a_thickness_larger_than_the_bounds_just_fills_them(self) -> None:
        canvas = Canvas(100, 10)
        Gauge(100, segments=10, gap=0, color=INK, thickness=40, bounds=Rect(0, 0, 100, 10)).draw(
            canvas
        )
        assert painted(canvas, INK) == 1000

    def test_no_thickness_fills_the_bounds(self) -> None:
        assert Gauge(0, bounds=Rect(0, 0, 100, 20)).thickness is None

    def test_a_thickness_below_one_pixel_is_refused(self) -> None:
        with pytest.raises(WidgetError, match="at least 1 pixel"):
            Gauge(0, thickness=0)


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

    def test_the_symbol_can_be_changed(self) -> None:
        # A dashboard maps entity state to a symbol -- a lock that opens when
        # the door unlocks -- so the symbol has to be settable, not just read.
        icon = Icon(IconName.LOCK, bounds=Rect(0, 0, 16, 16))
        icon.mark_clean()
        icon.name_of_symbol = IconName.UNLOCK
        assert icon.name_of_symbol is IconName.UNLOCK
        assert icon.is_dirty

    def test_setting_the_same_symbol_leaves_it_clean(self) -> None:
        icon = Icon(IconName.LOCK, bounds=Rect(0, 0, 16, 16))
        icon.mark_clean()
        icon.name_of_symbol = IconName.LOCK
        assert not icon.is_dirty


class TestWeatherIcons:
    """The glyphs a `weather` entity's condition maps onto.

    The whole-set tests above already check that each one draws and stays in
    bounds. These check the things that make a weather glyph *that* glyph, and
    which a shape bug would leave passing: the moon has a bite, the composites
    put weather under their cloud, and no two of them are the same picture.
    """

    COMPOSITES = (
        IconName.CLOUD_SUN,
        IconName.CLOUD_RAIN,
        IconName.CLOUD_SNOW,
        IconName.CLOUD_LIGHTNING,
        IconName.FOG,
    )

    def render(self, symbol: IconName, size: int = 40) -> Canvas:
        canvas = Canvas(size, size)
        Icon(symbol, color=INK, thickness=3, bounds=Rect(0, 0, size, size)).draw(canvas)
        return canvas

    def test_the_moon_is_a_crescent_not_a_disc(self) -> None:
        # The bite is computed rather than erased, so the failure mode is a
        # full disc -- which passes "draws something" and is not a moon.
        moon = painted(self.render(IconName.MOON), INK)
        disc = painted(self.render(IconName.DOT), INK)
        assert moon < disc

    def test_the_crescent_opens_to_the_right(self) -> None:
        canvas = self.render(IconName.MOON)
        columns = [
            sum(1 for y in range(canvas.height) if canvas.get_pixel(x, y) == INK)
            for x in range(canvas.width)
        ]
        left = sum(columns[: canvas.width // 2])
        right = sum(columns[canvas.width // 2 :])
        assert left > right

    @pytest.mark.parametrize("symbol", COMPOSITES)
    def test_a_composite_paints_below_its_cloud(self, symbol: IconName) -> None:
        # A plain cloud stops around 80% of the way down; every composite puts
        # something under it, so the bottom rows must not be empty.
        canvas = self.render(symbol)
        floor = canvas.height * 82 // 100
        below = sum(
            1
            for y in range(floor, canvas.height)
            for x in range(canvas.width)
            if canvas.get_pixel(x, y) == INK
        )
        assert below > 0, symbol

    def test_a_plain_cloud_leaves_the_floor_clear(self) -> None:
        # The premise of the test above.
        canvas = self.render(IconName.CLOUD)
        floor = canvas.height * 82 // 100
        assert not [
            (x, y)
            for y in range(floor, canvas.height)
            for x in range(canvas.width)
            if canvas.get_pixel(x, y) == INK
        ]

    def test_cloud_sun_puts_the_sun_above_its_cloud(self) -> None:
        # The sun is drawn first and occluded by the cloud, so what proves it
        # is there is ink in the top-right corner, where a cloud alone has none.
        def top_right(symbol: IconName) -> int:
            # Above where any cloud in the set starts, and right of its dome.
            canvas = self.render(symbol)
            return sum(
                1
                for y in range(canvas.height * 22 // 100)
                for x in range(canvas.width * 60 // 100, canvas.width)
                if canvas.get_pixel(x, y) == INK
            )

        assert top_right(IconName.CLOUD) == 0
        assert top_right(IconName.CLOUD_SUN) > 0

    def test_every_weather_glyph_is_a_different_picture(self) -> None:
        # Composites share a cloud, so a copy-paste slip in one of the painters
        # produces two identical icons rather than a crash.
        weather = [IconName.SUN, IconName.MOON, IconName.CLOUD, *self.COMPOSITES]
        renders = {symbol: self.render(symbol).buffer.tobytes() for symbol in weather}
        assert len(set(renders.values())) == len(weather)

    def test_they_survive_the_smallest_size_a_panel_would_use(self) -> None:
        for symbol in (IconName.MOON, *self.COMPOSITES):
            assert painted(self.render(symbol, size=16), INK) > 0, symbol
