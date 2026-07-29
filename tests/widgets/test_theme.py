"""Tests for theming.

The interesting assertions here are about quantisation. A palette picked on a
24-bit monitor is not the palette a 16-bit panel shows, and a contrast ratio
checked against the colours a designer chose is a ratio the hardware may not
actually deliver. These tests check the built-in themes as the panel renders
them, which is the only version that matters.
"""

from __future__ import annotations

import pytest

from tinydisplay.core import Color, PixelFormat
from tinydisplay.widgets import HIGH_CONTRAST, MIDNIGHT, MIN_TEXT_CONTRAST, PAPER, THEMES, Theme


class TestQuantization:
    def test_rgb888_is_unchanged(self) -> None:
        assert MIDNIGHT.quantized(PixelFormat.RGB888) == MIDNIGHT

    def test_rgb565_rounds_every_role(self) -> None:
        shown = MIDNIGHT.quantized(PixelFormat.RGB565_LE)
        for (role, original), (_, rounded) in zip(MIDNIGHT.colors(), shown.colors(), strict=True):
            assert rounded == original.quantized_rgb565(), role

    def test_byte_order_does_not_change_the_colours(self) -> None:
        # Endianness is a wire-format concern; it cannot change which colours
        # the panel is capable of.
        assert MIDNIGHT.quantized(PixelFormat.RGB565_LE) == MIDNIGHT.quantized(
            PixelFormat.RGB565_BE
        )

    def test_quantizing_twice_changes_nothing_further(self) -> None:
        once = MIDNIGHT.quantized()
        assert once.quantized() == once

    def test_the_default_target_is_sixteen_bit(self) -> None:
        # Most small panels are, so the default should not flatter the design.
        assert MIDNIGHT.quantized() == MIDNIGHT.quantized(PixelFormat.RGB565_LE)


class TestLegibility:
    @pytest.mark.parametrize("name", sorted(THEMES))
    def test_built_in_themes_are_legible_as_designed(self, name: str) -> None:
        assert THEMES[name].is_legible()

    @pytest.mark.parametrize("name", sorted(THEMES))
    def test_built_in_themes_stay_legible_on_a_16_bit_panel(self, name: str) -> None:
        # The assertion this module exists for: rounding must not push any
        # text role below the threshold it was chosen to clear.
        assert THEMES[name].quantized().is_legible()

    def test_high_contrast_beats_the_others(self) -> None:
        assert HIGH_CONTRAST.worst_text_contrast() > MIDNIGHT.worst_text_contrast()

    def test_contrast_is_measured_against_the_background_by_default(self) -> None:
        assert MIDNIGHT.contrast("text") == MIDNIGHT.text.contrast_ratio(MIDNIGHT.background)

    def test_contrast_can_target_another_role(self) -> None:
        assert MIDNIGHT.contrast("text", against="surface") == MIDNIGHT.text.contrast_ratio(
            MIDNIGHT.surface
        )

    def test_an_unknown_role_raises(self) -> None:
        with pytest.raises(AttributeError):
            MIDNIGHT.contrast("chartreuse")

    def test_worst_contrast_is_the_minimum_over_text_roles(self) -> None:
        worst = MIDNIGHT.worst_text_contrast()
        assert worst == min(
            MIDNIGHT.contrast(role)
            for role in ("text", "muted", "accent", "success", "warning", "danger")
        )

    def test_an_illegible_theme_is_reported_as_such(self) -> None:
        grey = Color.from_hex("#808080")
        washed = Theme(
            background=grey,
            surface=grey,
            text=Color.from_hex("#8a8a8a"),
            muted=grey,
            accent=grey,
            success=grey,
            warning=grey,
            danger=grey,
            outline=grey,
        )
        assert not washed.is_legible()


class TestRegistry:
    def test_every_named_theme_resolves(self) -> None:
        assert set(THEMES) == {"midnight", "paper", "high-contrast"}

    def test_names_map_to_the_exported_objects(self) -> None:
        assert THEMES["midnight"] is MIDNIGHT
        assert THEMES["paper"] is PAPER
        assert THEMES["high-contrast"] is HIGH_CONTRAST


class TestRoles:
    def test_colors_yields_every_role(self) -> None:
        roles = [role for role, _ in MIDNIGHT.colors()]
        assert roles == [
            "background",
            "surface",
            "text",
            "muted",
            "accent",
            "success",
            "warning",
            "danger",
            "outline",
        ]

    def test_a_theme_is_hashable_and_immutable(self) -> None:
        assert {MIDNIGHT, MIDNIGHT} == {MIDNIGHT}
        with pytest.raises(AttributeError):
            MIDNIGHT.text = Color.WHITE  # type: ignore[misc]

    def test_the_minimum_is_the_wcag_large_text_threshold(self) -> None:
        assert MIN_TEXT_CONTRAST == 3.0
