"""Tests for :mod:`tinydisplay.core.color`."""

from __future__ import annotations

import pytest

from tinydisplay.core import Color


class TestConstruction:
    def test_defaults_to_opaque(self) -> None:
        assert Color(1, 2, 3).a == 255

    @pytest.mark.parametrize(
        "channels",
        [(-1, 0, 0), (256, 0, 0), (0, -1, 0), (0, 0, 256), (0, 0, 0, 256)],
    )
    def test_rejects_out_of_range_channels(self, channels: tuple[int, ...]) -> None:
        with pytest.raises(ValueError, match=r"must be in 0\.\.255"):
            Color(*channels)

    def test_is_hashable_and_immutable(self) -> None:
        assert len({Color(1, 2, 3), Color(1, 2, 3)}) == 1
        with pytest.raises(AttributeError):
            Color(1, 2, 3).r = 9  # type: ignore[misc]


class TestHex:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("#ff0000", Color(255, 0, 0)),
            ("ff0000", Color(255, 0, 0)),
            ("#336699", Color(0x33, 0x66, 0x99)),
            ("#f00", Color(255, 0, 0)),
            ("#abc", Color(0xAA, 0xBB, 0xCC)),
            ("#11223344", Color(0x11, 0x22, 0x33, 0x44)),
        ],
    )
    def test_parses_supported_forms(self, text: str, expected: Color) -> None:
        assert Color.from_hex(text) == expected

    @pytest.mark.parametrize("text", ["#ff", "#fffff", "nothex", "", "#gggggg"])
    def test_rejects_malformed_input(self, text: str) -> None:
        with pytest.raises(ValueError, match="hex colour"):
            Color.from_hex(text)

    def test_round_trips(self) -> None:
        assert Color.from_hex(Color(18, 52, 86).to_hex()) == Color(18, 52, 86)

    def test_alpha_is_opt_in(self) -> None:
        translucent = Color(1, 2, 3, 4)
        assert translucent.to_hex() == "#010203"
        assert translucent.to_hex(include_alpha=True) == "#01020304"

    def test_str_shows_alpha_only_when_translucent(self) -> None:
        assert str(Color(255, 0, 0)) == "#ff0000"
        assert str(Color(255, 0, 0, 128)) == "#ff000080"


class TestRgb565:
    @pytest.mark.parametrize(
        ("color", "packed"),
        [
            (Color.BLACK, 0x0000),
            (Color.WHITE, 0xFFFF),
            (Color.RED, 0xF800),
            (Color.GREEN, 0x07E0),
            (Color.BLUE, 0x001F),
        ],
    )
    def test_packs_primaries(self, color: Color, packed: int) -> None:
        assert color.to_rgb565() == packed

    @pytest.mark.parametrize("color", [Color.BLACK, Color.WHITE, Color.RED, Color.BLUE])
    def test_primaries_survive_a_round_trip(self, color: Color) -> None:
        # Bit replication (not zero-fill) is what makes this hold for white.
        assert Color.from_rgb565(color.to_rgb565()) == color

    def test_quantisation_is_idempotent(self) -> None:
        once = Color(137, 200, 41).quantized_rgb565()
        assert once.quantized_rgb565() == once

    def test_quantisation_preserves_alpha(self) -> None:
        assert Color(137, 200, 41, 77).quantized_rgb565().a == 77

    @pytest.mark.parametrize("value", [-1, 0x10000])
    def test_rejects_out_of_range_words(self, value: int) -> None:
        with pytest.raises(ValueError, match=r"0\.\.65535"):
            Color.from_rgb565(value)


class TestBlending:
    def test_opaque_source_replaces_destination(self) -> None:
        assert Color.RED.blend_over(Color.BLUE) == Color.RED

    def test_transparent_source_is_a_no_op(self) -> None:
        assert Color.TRANSPARENT.blend_over(Color.BLUE) == Color.BLUE

    def test_half_alpha_lands_midway(self) -> None:
        blended = Color(255, 255, 255, 128).blend_over(Color.BLACK)
        assert blended.rgb == (128, 128, 128)
        assert blended.is_opaque

    def test_result_is_opaque_over_an_opaque_background(self) -> None:
        assert Color(1, 2, 3, 10).blend_over(Color.WHITE).is_opaque

    def test_blending_two_transparent_colours_stays_transparent(self) -> None:
        assert Color.TRANSPARENT.blend_over(Color.TRANSPARENT) == Color(0, 0, 0, 0)


class TestDerivations:
    def test_with_alpha_clamps(self) -> None:
        assert Color.RED.with_alpha(999).a == 255
        assert Color.RED.with_alpha(-5).a == 0

    @pytest.mark.parametrize(("t", "expected"), [(0.0, 0), (0.5, 128), (1.0, 255)])
    def test_lerp_interpolates(self, t: float, expected: int) -> None:
        assert Color.BLACK.lerp(Color.WHITE, t).r == expected

    @pytest.mark.parametrize("t", [-1.0, 2.0])
    def test_lerp_clamps_out_of_range_factors(self, t: float) -> None:
        result = Color.BLACK.lerp(Color.WHITE, t)
        assert result in (Color.BLACK, Color.WHITE)

    def test_luminance_bounds(self) -> None:
        assert Color.BLACK.relative_luminance() == pytest.approx(0.0)
        assert Color.WHITE.relative_luminance() == pytest.approx(1.0)

    def test_contrast_ratio_is_symmetric_and_maximal(self) -> None:
        assert Color.BLACK.contrast_ratio(Color.WHITE) == pytest.approx(21.0)
        assert Color.WHITE.contrast_ratio(Color.BLACK) == pytest.approx(21.0)
        assert Color.RED.contrast_ratio(Color.RED) == pytest.approx(1.0)


def test_named_constants_are_colours() -> None:
    assert Color.WHITE.rgb == (255, 255, 255)
    assert Color.TRANSPARENT.is_transparent
    assert Color.BLACK.is_opaque
