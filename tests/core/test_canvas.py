"""Tests for :mod:`tinydisplay.core.canvas`."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest
from PIL import Image

from tinydisplay.core import (
    MAX_DIMENSION,
    Canvas,
    CanvasError,
    Color,
    HorizontalAlign,
    ImageError,
    Rect,
    Size,
    VerticalAlign,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def canvas() -> Canvas:
    """A small black canvas, cleared and ready to draw on."""
    return Canvas(16, 16)


def painted_pixel_count(canvas: Canvas, background: Color = Color.BLACK) -> int:
    """How many pixels differ from ``background``."""
    return int(np.count_nonzero(np.any(canvas.buffer != background.rgb, axis=-1)))


class TestConstruction:
    def test_dimensions_and_bounds(self) -> None:
        c = Canvas(320, 170)
        assert (c.width, c.height) == (320, 170)
        assert c.size == Size(320, 170)
        assert c.bounds == Rect(0, 0, 320, 170)

    def test_starts_filled_with_the_background(self) -> None:
        c = Canvas(4, 4, background=Color.RED)
        assert c.get_pixel(0, 0) == Color.RED
        assert c.background == Color.RED

    @pytest.mark.parametrize("dimensions", [(0, 10), (10, 0), (-1, 10)])
    def test_rejects_non_positive_dimensions(self, dimensions: tuple[int, int]) -> None:
        with pytest.raises(CanvasError, match="must be positive"):
            Canvas(*dimensions)

    def test_rejects_absurd_dimensions(self) -> None:
        with pytest.raises(CanvasError, match="must not exceed"):
            Canvas(MAX_DIMENSION + 1, 10)

    def test_repr_names_the_size(self) -> None:
        assert repr(Canvas(8, 4)) == "Canvas(8x4)"


class TestBuffer:
    def test_shape_and_dtype(self, canvas: Canvas) -> None:
        assert canvas.buffer.shape == (16, 16, 3)
        assert canvas.buffer.dtype == np.uint8

    def test_view_is_read_only(self, canvas: Canvas) -> None:
        with pytest.raises(ValueError, match="read-only"):
            canvas.buffer[0, 0] = 5

    def test_view_reflects_later_drawing(self, canvas: Canvas) -> None:
        view = canvas.buffer
        canvas.pixel(0, 0, Color.WHITE)
        assert tuple(view[0, 0]) == (255, 255, 255)


class TestPixels:
    def test_set_and_read(self, canvas: Canvas) -> None:
        canvas.pixel(3, 4, Color.RED)
        assert canvas.get_pixel(3, 4) == Color.RED

    @pytest.mark.parametrize("point", [(-1, 0), (0, -1), (16, 0), (0, 16)])
    def test_reads_outside_the_canvas_raise(self, canvas: Canvas, point: tuple[int, int]) -> None:
        with pytest.raises(IndexError, match="outside"):
            canvas.get_pixel(*point)

    @pytest.mark.parametrize("point", [(-1, 0), (0, -1), (16, 0), (999, 999)])
    def test_writes_outside_the_canvas_are_silently_clipped(
        self, canvas: Canvas, point: tuple[int, int]
    ) -> None:
        canvas.pixel(*point, Color.WHITE)
        assert painted_pixel_count(canvas) == 0

    def test_transparent_writes_do_nothing(self, canvas: Canvas) -> None:
        canvas.pixel(0, 0, Color.TRANSPARENT)
        assert canvas.get_pixel(0, 0) == Color.BLACK

    def test_alpha_is_blended_against_existing_content(self, canvas: Canvas) -> None:
        canvas.pixel(0, 0, Color(255, 255, 255, 128))
        assert canvas.get_pixel(0, 0) == Color(128, 128, 128)


class TestClear:
    def test_clear_uses_the_background_by_default(self) -> None:
        c = Canvas(4, 4, background=Color.BLUE)
        c.rect(0, 0, 4, 4, Color.RED)
        c.clear()
        assert c.get_pixel(2, 2) == Color.BLUE

    def test_clear_accepts_an_explicit_colour(self, canvas: Canvas) -> None:
        canvas.clear(Color.GREEN)
        assert painted_pixel_count(canvas) == 16 * 16

    def test_clear_ignores_the_clip_region(self, canvas: Canvas) -> None:
        with canvas.clip(Rect(0, 0, 2, 2)):
            canvas.clear(Color.WHITE)
        assert canvas.get_pixel(15, 15) == Color.WHITE

    def test_translucent_clear_blends(self, canvas: Canvas) -> None:
        canvas.clear(Color(255, 255, 255, 128))
        assert canvas.get_pixel(0, 0) == Color(128, 128, 128)


class TestRectangles:
    def test_filled_rect_covers_exactly_its_bounds(self, canvas: Canvas) -> None:
        canvas.rect(2, 3, 4, 5, Color.WHITE)
        assert canvas.get_pixel(2, 3) == Color.WHITE
        assert canvas.get_pixel(5, 7) == Color.WHITE
        assert canvas.get_pixel(6, 8) == Color.BLACK
        assert painted_pixel_count(canvas) == 4 * 5

    def test_outline_stays_inside_the_bounds(self, canvas: Canvas) -> None:
        canvas.rect(0, 0, 6, 6, Color.WHITE, fill=False, thickness=1)
        assert canvas.get_pixel(0, 0) == Color.WHITE
        assert canvas.get_pixel(5, 5) == Color.WHITE
        assert canvas.get_pixel(1, 1) == Color.BLACK
        assert painted_pixel_count(canvas) == 6 * 6 - 4 * 4

    def test_thick_outline_does_not_double_blend_corners(self, canvas: Canvas) -> None:
        canvas.rect(0, 0, 10, 10, Color(255, 255, 255, 128), fill=False, thickness=2)
        # Every painted pixel should have been blended exactly once.
        assert canvas.get_pixel(0, 0) == Color(128, 128, 128)
        assert canvas.get_pixel(1, 1) == Color(128, 128, 128)

    def test_outline_thicker_than_the_shape_fills_it(self, canvas: Canvas) -> None:
        canvas.rect(0, 0, 4, 4, Color.WHITE, fill=False, thickness=10)
        assert painted_pixel_count(canvas) == 16

    @pytest.mark.parametrize("size", [(0, 5), (5, 0), (-3, 5)])
    def test_degenerate_rects_draw_nothing(self, canvas: Canvas, size: tuple[int, int]) -> None:
        canvas.rect(0, 0, *size, Color.WHITE)
        assert painted_pixel_count(canvas) == 0

    def test_rounded_rect_clips_its_corners(self, canvas: Canvas) -> None:
        canvas.rounded_rect(0, 0, 16, 16, Color.WHITE, radius=6)
        assert canvas.get_pixel(8, 8) == Color.WHITE
        assert canvas.get_pixel(0, 0) == Color.BLACK

    def test_zero_radius_falls_back_to_a_plain_rect(self, canvas: Canvas) -> None:
        canvas.rounded_rect(0, 0, 8, 8, Color.WHITE, radius=0)
        assert canvas.get_pixel(0, 0) == Color.WHITE
        assert painted_pixel_count(canvas) == 64


class TestLines:
    def test_vertical_line(self, canvas: Canvas) -> None:
        canvas.line(4, 0, 4, 15, Color.WHITE)
        assert painted_pixel_count(canvas) == 16
        assert all(canvas.get_pixel(4, y) == Color.WHITE for y in range(16))

    def test_horizontal_line(self, canvas: Canvas) -> None:
        canvas.line(0, 7, 15, 7, Color.WHITE)
        assert painted_pixel_count(canvas) == 16

    def test_diagonal_line_hits_both_endpoints(self, canvas: Canvas) -> None:
        canvas.line(0, 0, 15, 15, Color.WHITE)
        assert canvas.get_pixel(0, 0) == Color.WHITE
        assert canvas.get_pixel(15, 15) == Color.WHITE
        assert painted_pixel_count(canvas) == 16

    def test_single_point_line(self, canvas: Canvas) -> None:
        canvas.line(5, 5, 5, 5, Color.WHITE)
        assert painted_pixel_count(canvas) == 1

    def test_line_is_clipped_not_wrapped(self, canvas: Canvas) -> None:
        canvas.line(-50, 8, 50, 8, Color.WHITE)
        assert painted_pixel_count(canvas) == 16

    def test_thick_line_paints_more_than_a_thin_one(self, canvas: Canvas) -> None:
        canvas.line(0, 8, 15, 8, Color.WHITE, thickness=3)
        assert painted_pixel_count(canvas) > 16

    def test_zero_thickness_draws_nothing(self, canvas: Canvas) -> None:
        canvas.line(0, 0, 15, 15, Color.WHITE, thickness=0)
        assert painted_pixel_count(canvas) == 0


class TestCircles:
    def test_filled_circle_covers_its_centre_but_not_the_corners(self, canvas: Canvas) -> None:
        canvas.circle(8, 8, 5, Color.WHITE)
        assert canvas.get_pixel(8, 8) == Color.WHITE
        assert canvas.get_pixel(0, 0) == Color.BLACK

    def test_outline_leaves_the_centre_empty(self, canvas: Canvas) -> None:
        canvas.circle(8, 8, 5, Color.WHITE, fill=False)
        assert canvas.get_pixel(8, 8) == Color.BLACK
        assert painted_pixel_count(canvas) > 0

    def test_non_positive_radius_draws_nothing(self, canvas: Canvas) -> None:
        canvas.circle(8, 8, 0, Color.WHITE)
        assert painted_pixel_count(canvas) == 0


class TestClipping:
    def test_clip_restricts_drawing(self, canvas: Canvas) -> None:
        with canvas.clip(Rect(0, 0, 4, 4)):
            canvas.rect(0, 0, 16, 16, Color.WHITE)
        assert painted_pixel_count(canvas) == 16

    def test_clip_is_restored_afterwards(self, canvas: Canvas) -> None:
        with canvas.clip(Rect(0, 0, 4, 4)):
            pass
        assert canvas.clip_rect == canvas.bounds

    def test_clips_nest_by_intersection(self, canvas: Canvas) -> None:
        with canvas.clip(Rect(0, 0, 8, 8)), canvas.clip(Rect(4, 4, 8, 8)) as inner:
            assert inner == Rect(4, 4, 4, 4)
            canvas.rect(0, 0, 16, 16, Color.WHITE)
        assert painted_pixel_count(canvas) == 16

    def test_disjoint_clip_yields_an_empty_region(self, canvas: Canvas) -> None:
        with canvas.clip(Rect(0, 0, 4, 4)), canvas.clip(Rect(100, 100, 4, 4)) as inner:
            assert inner.is_empty
            canvas.rect(0, 0, 16, 16, Color.WHITE)
        assert painted_pixel_count(canvas) == 0

    def test_clip_is_restored_when_the_block_raises(self, canvas: Canvas) -> None:
        with pytest.raises(RuntimeError), canvas.clip(Rect(0, 0, 4, 4)):
            raise RuntimeError
        assert canvas.clip_rect == canvas.bounds


class TestText:
    def test_returns_a_non_empty_ink_box_and_paints(self, canvas: Canvas) -> None:
        box = canvas.text(1, 1, "Hi", Color.WHITE)
        assert not box.is_empty
        assert painted_pixel_count(canvas) > 0

    def test_empty_string_paints_nothing(self, canvas: Canvas) -> None:
        box = canvas.text(1, 1, "", Color.WHITE)
        assert box == Rect(1, 1, 0, 0)
        assert painted_pixel_count(canvas) == 0

    def test_alignment_moves_the_ink(self) -> None:
        left = Canvas(64, 32)
        centre = Canvas(64, 32)
        left.text(32, 8, "Hello", Color.WHITE)
        centre.text(32, 8, "Hello", Color.WHITE, align=HorizontalAlign.CENTER)
        assert not np.array_equal(left.buffer, centre.buffer)

    def test_vertical_alignment_moves_the_ink(self) -> None:
        top = Canvas(64, 32)
        middle = Canvas(64, 32)
        top.text(4, 16, "Hello", Color.WHITE)
        middle.text(4, 16, "Hello", Color.WHITE, valign=VerticalAlign.MIDDLE)
        assert not np.array_equal(top.buffer, middle.buffer)

    def test_multiline_text_is_taller_than_one_line(self) -> None:
        canvas = Canvas(64, 64)
        single = canvas.text(0, 0, "A", Color.WHITE)
        canvas.clear()
        double = canvas.text(0, 0, "A\nA", Color.WHITE)
        assert double.height > single.height

    def test_text_is_clipped_to_the_canvas(self) -> None:
        canvas = Canvas(16, 16)
        canvas.text(-100, -100, "Hello", Color.WHITE)
        assert painted_pixel_count(canvas) == 0

    def test_whitespace_only_text_reports_a_layout_box(self, canvas: Canvas) -> None:
        box = canvas.text(2, 3, "   ", Color.WHITE)
        assert box.height > 0
        assert painted_pixel_count(canvas) == 0


class TestImages:
    def test_draws_a_pillow_image(self, canvas: Canvas) -> None:
        source = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
        box = canvas.image(2, 2, source)
        assert box == Rect(2, 2, 4, 4)
        assert canvas.get_pixel(2, 2) == Color.RED
        assert canvas.get_pixel(6, 6) == Color.BLACK

    def test_honours_the_source_alpha(self, canvas: Canvas) -> None:
        source = Image.new("RGBA", (4, 4), (255, 255, 255, 128))
        canvas.image(0, 0, source)
        assert canvas.get_pixel(0, 0) == Color(128, 128, 128)

    def test_opacity_scales_the_alpha(self, canvas: Canvas) -> None:
        source = Image.new("RGBA", (4, 4), (255, 255, 255, 255))
        canvas.image(0, 0, source, opacity=128)
        assert canvas.get_pixel(0, 0) == Color(128, 128, 128)

    def test_rejects_out_of_range_opacity(self, canvas: Canvas) -> None:
        with pytest.raises(ValueError, match="opacity"):
            canvas.image(0, 0, Image.new("RGBA", (2, 2)), opacity=300)

    def test_resizes_when_a_size_is_given(self, canvas: Canvas) -> None:
        source = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
        box = canvas.image(0, 0, source, size=Size(8, 8))
        assert box == Rect(0, 0, 8, 8)
        assert canvas.get_pixel(7, 7) == Color.RED

    def test_empty_target_size_draws_nothing(self, canvas: Canvas) -> None:
        canvas.image(0, 0, Image.new("RGBA", (2, 2), (255, 0, 0, 255)), size=Size(0, 8))
        assert painted_pixel_count(canvas) == 0

    def test_draws_another_canvas(self, canvas: Canvas) -> None:
        source = Canvas(4, 4, background=Color.GREEN)
        canvas.image(1, 1, source)
        assert canvas.get_pixel(1, 1) == Color.GREEN

    def test_loads_from_disk(self, canvas: Canvas, tmp_path: Path) -> None:
        path = tmp_path / "swatch.png"
        Image.new("RGBA", (4, 4), (0, 0, 255, 255)).save(path)
        canvas.image(0, 0, path)
        assert canvas.get_pixel(0, 0) == Color.BLUE

    def test_missing_file_raises_image_error(self, canvas: Canvas, tmp_path: Path) -> None:
        with pytest.raises(ImageError, match="not found"):
            canvas.image(0, 0, tmp_path / "nope.png")

    def test_undecodable_file_raises_image_error(self, canvas: Canvas, tmp_path: Path) -> None:
        path = tmp_path / "junk.png"
        path.write_bytes(b"not an image")
        with pytest.raises(ImageError, match="could not decode"):
            canvas.image(0, 0, path)


class TestBlit:
    def test_copies_pixels_at_an_offset(self, canvas: Canvas) -> None:
        source = Canvas(4, 4, background=Color.WHITE)
        canvas.blit(source, 2, 2)
        assert canvas.get_pixel(2, 2) == Color.WHITE
        assert canvas.get_pixel(5, 5) == Color.WHITE
        assert canvas.get_pixel(1, 1) == Color.BLACK
        assert painted_pixel_count(canvas) == 16

    def test_is_clipped_at_the_edges(self, canvas: Canvas) -> None:
        source = Canvas(8, 8, background=Color.WHITE)
        canvas.blit(source, 12, 12)
        assert painted_pixel_count(canvas) == 16

    def test_fully_offscreen_blit_is_a_no_op(self, canvas: Canvas) -> None:
        canvas.blit(Canvas(4, 4, background=Color.WHITE), 100, 100)
        assert painted_pixel_count(canvas) == 0

    def test_respects_the_clip_region(self, canvas: Canvas) -> None:
        source = Canvas(8, 8, background=Color.WHITE)
        with canvas.clip(Rect(0, 0, 2, 2)):
            canvas.blit(source, 0, 0)
        assert painted_pixel_count(canvas) == 4


class TestCopy:
    def test_copy_is_independent(self, canvas: Canvas) -> None:
        canvas.rect(0, 0, 4, 4, Color.WHITE)
        clone = canvas.copy()
        clone.clear(Color.RED)
        assert canvas.get_pixel(0, 0) == Color.WHITE
        assert clone.get_pixel(0, 0) == Color.RED

    def test_copy_preserves_the_background(self) -> None:
        original = Canvas(4, 4, background=Color.BLUE)
        assert original.copy().background == Color.BLUE


class TestExport:
    def test_rgb888_length_and_content(self) -> None:
        c = Canvas(4, 2, background=Color.WHITE)
        data = c.to_rgb888()
        assert len(data) == 4 * 2 * 3
        assert set(data) == {255}

    def test_rgb565_length(self) -> None:
        assert len(Canvas(320, 170).to_rgb565()) == 320 * 170 * 2

    def test_rgb565_little_endian_byte_order(self) -> None:
        data = Canvas(1, 1, background=Color.RED).to_rgb565()
        assert data == bytes([0x00, 0xF8])

    def test_rgb565_big_endian_byte_order(self) -> None:
        data = Canvas(1, 1, background=Color.RED).to_rgb565(byte_order="big")
        assert data == bytes([0xF8, 0x00])

    def test_png_bytes_have_the_png_signature(self, canvas: Canvas) -> None:
        assert canvas.to_png_bytes().startswith(b"\x89PNG\r\n\x1a\n")

    def test_save_writes_a_readable_png(self, canvas: Canvas, tmp_path: Path) -> None:
        canvas.rect(0, 0, 8, 8, Color.RED)
        path = canvas.save(tmp_path / "preview.png")
        assert path.exists()
        with Image.open(path) as reopened:
            assert reopened.size == (16, 16)
            assert reopened.convert("RGB").getpixel((0, 0)) == (255, 0, 0)

    def test_save_creates_missing_parent_directories(self, canvas: Canvas, tmp_path: Path) -> None:
        path = canvas.save(tmp_path / "nested" / "deep" / "out.png")
        assert path.exists()

    def test_save_rejects_an_unknown_format(self, canvas: Canvas, tmp_path: Path) -> None:
        with pytest.raises(ImageError, match="could not save"):
            canvas.save(tmp_path / "out.bogus")

    def test_to_pil_is_a_detached_copy(self, canvas: Canvas) -> None:
        image = canvas.to_pil()
        canvas.clear(Color.RED)
        assert image.getpixel((0, 0)) == (0, 0, 0)

    def test_from_pil_round_trips(self) -> None:
        source = Image.new("RGB", (5, 3), (10, 20, 30))
        canvas = Canvas.from_pil(source)
        assert canvas.size == Size(5, 3)
        assert canvas.get_pixel(0, 0) == Color(10, 20, 30)

    def test_from_pil_flattens_alpha_onto_the_background(self) -> None:
        source = Image.new("RGBA", (2, 2), (255, 255, 255, 0))
        canvas = Canvas.from_pil(source, background=Color.BLUE)
        assert canvas.get_pixel(0, 0) == Color.BLUE
