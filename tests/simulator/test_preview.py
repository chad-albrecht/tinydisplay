"""Tests for frame decoding and scaling.

The decoder is the simulator's claim to honesty: if it disagrees with core's
own RGB565 conversion, the window is lying about what the panel will show. Most
of what follows checks that the two agree.
"""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from tinydisplay.core import Canvas, Color, PixelFormat
from tinydisplay.simulator import (
    MAX_SCALE,
    SimulatorError,
    decode_frame,
    frame_to_canvas,
    scale_nearest,
    validate_scale,
)


class TestDecodeFrame:
    def test_rgb888_round_trips_exactly(self) -> None:
        canvas = Canvas(3, 2)
        canvas.clear(Color.from_hex("#123456"))
        canvas.pixel(1, 1, Color.from_hex("#abcdef"))

        pixels = decode_frame(canvas.to_rgb888(), 3, 2, PixelFormat.RGB888)

        assert pixels.shape == (2, 3, 3)
        assert tuple(pixels[0, 0]) == (0x12, 0x34, 0x56)
        assert tuple(pixels[1, 1]) == (0xAB, 0xCD, 0xEF)

    def test_rgb565_matches_core_quantisation(self) -> None:
        """The decoder must agree with Color.quantized_rgb565, pixel for pixel."""
        samples = ["#000000", "#ffffff", "#ff0000", "#00ff00", "#0000ff", "#123456", "#7f7f7f"]
        canvas = Canvas(len(samples), 1)
        for x, value in enumerate(samples):
            canvas.pixel(x, 0, Color.from_hex(value))

        pixels = decode_frame(canvas.to_rgb565(), len(samples), 1, PixelFormat.RGB565_LE)

        for x, value in enumerate(samples):
            expected = Color.from_hex(value).quantized_rgb565()
            assert tuple(pixels[0, x]) == expected.rgb, f"{value} decoded wrong"

    def test_white_survives_the_round_trip(self) -> None:
        """Bit replication, not zero-fill: white must stay #ffffff."""
        canvas = Canvas(1, 1)
        canvas.clear(Color.WHITE)

        pixels = decode_frame(canvas.to_rgb565(), 1, 1, PixelFormat.RGB565_LE)

        assert tuple(pixels[0, 0]) == (255, 255, 255)

    def test_endianness_is_honoured(self) -> None:
        canvas = Canvas(2, 2)
        canvas.clear(Color.from_hex("#ff8040"))

        little = decode_frame(canvas.to_rgb565(byte_order="little"), 2, 2, PixelFormat.RGB565_LE)
        big = decode_frame(canvas.to_rgb565(byte_order="big"), 2, 2, PixelFormat.RGB565_BE)

        assert np.array_equal(little, big)

    def test_decoding_with_the_wrong_endianness_is_visibly_wrong(self) -> None:
        """The bug the simulator exists to surface must actually be visible."""
        canvas = Canvas(2, 2)
        canvas.clear(Color.from_hex("#ff8040"))

        correct = decode_frame(canvas.to_rgb565(byte_order="little"), 2, 2, PixelFormat.RGB565_LE)
        swapped = decode_frame(canvas.to_rgb565(byte_order="little"), 2, 2, PixelFormat.RGB565_BE)

        assert not np.array_equal(correct, swapped)

    def test_row_major_orientation(self) -> None:
        """A pixel set at (x, y) must decode to [y, x] -- not transposed."""
        canvas = Canvas(4, 3)
        canvas.pixel(3, 0, Color.WHITE)

        pixels = decode_frame(canvas.to_rgb565(), 4, 3, PixelFormat.RGB565_LE)

        assert tuple(pixels[0, 3]) == (255, 255, 255)
        assert tuple(pixels[0, 0]) == (0, 0, 0)

    @pytest.mark.parametrize(
        "pixel_format",
        [PixelFormat.RGB888, PixelFormat.RGB565_LE, PixelFormat.RGB565_BE],
    )
    def test_short_frame_is_rejected(self, pixel_format: PixelFormat) -> None:
        with pytest.raises(SimulatorError, match="expected a"):
            decode_frame(b"\x00" * 3, 4, 4, pixel_format)

    def test_long_frame_is_rejected(self) -> None:
        with pytest.raises(SimulatorError, match="got 100 bytes"):
            decode_frame(b"\x00" * 100, 2, 2, PixelFormat.RGB565_LE)

    def test_quantisation_error_stays_within_the_rgb565_bound(self) -> None:
        """5-bit channels round-trip within 7; the decoder must not exceed it.

        A larger error would mean the bit replication is wrong -- the artefact
        would still look like plausible banding, so only arithmetic catches it.
        """
        rng = np.random.default_rng(seed=20260729)
        source = rng.integers(0, 256, size=(16, 16, 3), dtype=np.uint8)
        canvas = Canvas.from_pil(Image.fromarray(source, mode="RGB"))

        decoded = decode_frame(canvas.to_rgb565(), 16, 16, PixelFormat.RGB565_LE)

        error = np.abs(decoded.astype(int) - source.astype(int))
        assert error.max() <= 7
        # Green has 6 bits, so it must land closer than red and blue.
        assert error[:, :, 1].max() <= 3

    def test_rgb888_round_trip_is_lossless(self) -> None:
        rng = np.random.default_rng(seed=20260729)
        source = rng.integers(0, 256, size=(8, 8, 3), dtype=np.uint8)
        canvas = Canvas.from_pil(Image.fromarray(source, mode="RGB"))

        decoded = decode_frame(canvas.to_rgb888(), 8, 8, PixelFormat.RGB888)

        assert np.array_equal(decoded, source)

    def test_result_does_not_alias_the_input_buffer(self) -> None:
        """Callers reuse frame buffers; the decoder must not hand back a view."""
        frame = bytearray(b"\x11" * 12)
        pixels = decode_frame(bytes(frame), 2, 2, PixelFormat.RGB888)
        frame[0] = 0xFF

        assert pixels[0, 0, 0] == 0x11
        assert pixels.flags.writeable


class TestFrameToCanvas:
    def test_produces_the_quantised_canvas(self) -> None:
        source = Canvas(4, 4)
        source.clear(Color.from_hex("#123456"))

        shown = frame_to_canvas(source.to_rgb565(), 4, 4, PixelFormat.RGB565_LE)

        assert shown.size == source.size
        assert shown.get_pixel(0, 0) == Color.from_hex("#123456").quantized_rgb565()

    def test_rgb888_is_identical_to_the_source(self) -> None:
        source = Canvas(5, 3)
        source.clear(Color.from_hex("#204060"))
        source.pixel(4, 2, Color.WHITE)

        shown = frame_to_canvas(source.to_rgb888(), 5, 3, PixelFormat.RGB888)

        assert np.array_equal(shown.buffer, source.buffer)


class TestScaleNearest:
    def test_scale_of_one_is_a_passthrough(self) -> None:
        pixels = np.zeros((2, 2, 3), dtype=np.uint8)
        assert scale_nearest(pixels, 1) is pixels

    def test_dimensions_multiply(self) -> None:
        pixels = np.zeros((5, 3, 3), dtype=np.uint8)
        scaled = scale_nearest(pixels, 4)
        assert scaled.shape == (20, 12, 3)

    def test_each_pixel_becomes_a_block(self) -> None:
        pixels = np.zeros((1, 2, 3), dtype=np.uint8)
        pixels[0, 0] = (255, 0, 0)
        pixels[0, 1] = (0, 0, 255)

        scaled = scale_nearest(pixels, 3)

        assert scaled.shape == (3, 6, 3)
        assert np.array_equal(scaled[:, :3], np.broadcast_to((255, 0, 0), (3, 3, 3)))
        assert np.array_equal(scaled[:, 3:], np.broadcast_to((0, 0, 255), (3, 3, 3)))

    def test_no_interpolation_is_introduced(self) -> None:
        """Nearest neighbour must not invent intermediate colours."""
        pixels = np.zeros((1, 2, 3), dtype=np.uint8)
        pixels[0, 1] = (255, 255, 255)

        scaled = scale_nearest(pixels, 8)

        assert set(np.unique(scaled).tolist()) == {0, 255}

    @pytest.mark.parametrize("scale", [0, -1, MAX_SCALE + 1])
    def test_out_of_range_scale_is_rejected(self, scale: int) -> None:
        pixels = np.zeros((1, 1, 3), dtype=np.uint8)
        with pytest.raises(SimulatorError, match="scale must be in"):
            scale_nearest(pixels, scale)


class TestValidateScale:
    def test_returns_the_scale(self) -> None:
        assert validate_scale(4) == 4

    @pytest.mark.parametrize("scale", [1, MAX_SCALE])
    def test_boundaries_are_allowed(self, scale: int) -> None:
        assert validate_scale(scale) == scale

    def test_zero_is_rejected(self) -> None:
        with pytest.raises(SimulatorError, match=r"scale must be in 1\.\.32"):
            validate_scale(0)
