"""Tests for :mod:`tinydisplay.core.font`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tinydisplay.core import Font, FontError, HorizontalAlign, VerticalAlign

if TYPE_CHECKING:
    from pathlib import Path


class TestDefaultFont:
    def test_loads_at_the_requested_size(self) -> None:
        font = Font.default(16)
        assert font.size == 16
        assert font.name == "default"

    def test_has_sane_metrics(self) -> None:
        font = Font.default(16)
        assert font.ascent > 0
        assert font.descent >= 0
        assert font.line_height >= font.ascent

    def test_is_cached(self) -> None:
        assert Font.default(14).pil_font is Font.default(14).pil_font

    @pytest.mark.parametrize("size", [0, -1])
    def test_rejects_non_positive_sizes(self, size: int) -> None:
        with pytest.raises(ValueError, match="size must be positive"):
            Font.default(size)

    def test_rejects_non_positive_line_spacing(self) -> None:
        with pytest.raises(ValueError, match="line spacing must be positive"):
            Font.default(12, line_spacing=0)

    def test_repr_is_informative(self) -> None:
        assert repr(Font.default(12)) == "Font(name='default', size=12)"


class TestMeasurement:
    def test_empty_string_has_no_width_but_one_line_of_height(self) -> None:
        font = Font.default(12)
        size = font.measure("")
        assert size.width == 0
        assert size.height == font.line_height

    def test_wider_strings_measure_wider(self) -> None:
        font = Font.default(12)
        assert font.measure("iiii").width < font.measure("WWWW").width

    def test_text_width_ignores_newlines(self) -> None:
        font = Font.default(12)
        assert font.text_width("AB") > 0

    def test_multiline_height_scales_with_line_count(self) -> None:
        font = Font.default(12)
        assert font.measure("A\nB\nC").height == font.line_height * 3

    def test_multiline_width_is_the_widest_line(self) -> None:
        font = Font.default(12)
        assert font.measure("W\nWWWW").width == font.measure("WWWW").width

    def test_line_spacing_stretches_the_line_height(self) -> None:
        tight = Font.default(12)
        loose = Font.default(12, line_spacing=2.0)
        assert loose.line_height > tight.line_height

    def test_larger_sizes_measure_larger(self) -> None:
        assert Font.default(24).measure("Hello").width > Font.default(8).measure("Hello").width


class TestLoading:
    def test_missing_file_raises_font_error(self, tmp_path: Path) -> None:
        with pytest.raises(FontError, match="could not load font"):
            Font.load(tmp_path / "missing.ttf", 12)

    def test_non_font_file_raises_font_error(self, tmp_path: Path) -> None:
        path = tmp_path / "fake.ttf"
        path.write_bytes(b"definitely not a font")
        with pytest.raises(FontError, match="could not load font"):
            Font.load(path, 12)


class TestAlignmentEnums:
    def test_horizontal_members(self) -> None:
        assert {a.value for a in HorizontalAlign} == {"left", "center", "right"}

    def test_vertical_members(self) -> None:
        assert {a.value for a in VerticalAlign} == {"top", "middle", "bottom", "baseline"}

    def test_are_strings(self) -> None:
        assert isinstance(HorizontalAlign.LEFT, str)
        assert f"{HorizontalAlign.LEFT}" == "left"
