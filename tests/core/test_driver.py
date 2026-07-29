"""Tests for :mod:`tinydisplay.core.driver`."""

from __future__ import annotations

import pytest

from tinydisplay.core import (
    Canvas,
    Color,
    DriverError,
    DriverNotConnectedError,
    MemoryDriver,
    PixelFormat,
    Size,
)


class ExplodingDriver(MemoryDriver):
    """A driver whose transport fails to close, for lifecycle assertions."""

    async def _disconnect(self) -> None:
        msg = "transport died"
        raise OSError(msg)


class TestPixelFormat:
    @pytest.mark.parametrize(
        ("pixel_format", "expected"),
        [
            (PixelFormat.RGB888, 3),
            (PixelFormat.RGB565_LE, 2),
            (PixelFormat.RGB565_BE, 2),
        ],
    )
    def test_bytes_per_pixel(self, pixel_format: PixelFormat, expected: int) -> None:
        assert pixel_format.bytes_per_pixel == expected


class TestConstruction:
    def test_exposes_its_geometry(self) -> None:
        driver = MemoryDriver(320, 170)
        assert driver.size == Size(320, 170)
        assert (driver.width, driver.height) == (320, 170)

    def test_defaults_to_little_endian_rgb565(self) -> None:
        assert MemoryDriver(8, 8).pixel_format is PixelFormat.RGB565_LE

    @pytest.mark.parametrize("dimensions", [(0, 10), (10, -1)])
    def test_rejects_non_positive_dimensions(self, dimensions: tuple[int, int]) -> None:
        with pytest.raises(DriverError, match="must be positive"):
            MemoryDriver(*dimensions)

    def test_rejects_a_non_positive_frame_limit(self) -> None:
        with pytest.raises(DriverError, match="max_frames"):
            MemoryDriver(8, 8, max_frames=0)

    def test_frame_size_matches_the_pixel_format(self) -> None:
        assert MemoryDriver(10, 10, pixel_format=PixelFormat.RGB565_LE).frame_size == 200
        assert MemoryDriver(10, 10, pixel_format=PixelFormat.RGB888).frame_size == 300

    def test_repr_reports_connection_state(self) -> None:
        assert "disconnected" in repr(MemoryDriver(8, 8))


class TestCanvasHelpers:
    def test_create_canvas_matches_the_display(self) -> None:
        driver = MemoryDriver(320, 170)
        assert driver.create_canvas().size == driver.size

    def test_create_canvas_honours_the_background(self) -> None:
        canvas = MemoryDriver(8, 8).create_canvas(background=Color.RED)
        assert canvas.get_pixel(0, 0) == Color.RED


class TestEncoding:
    def test_rgb565_little_endian(self) -> None:
        driver = MemoryDriver(1, 1, pixel_format=PixelFormat.RGB565_LE)
        canvas = driver.create_canvas(background=Color.RED)
        assert driver.encode(canvas) == bytes([0x00, 0xF8])

    def test_rgb565_big_endian(self) -> None:
        driver = MemoryDriver(1, 1, pixel_format=PixelFormat.RGB565_BE)
        canvas = driver.create_canvas(background=Color.RED)
        assert driver.encode(canvas) == bytes([0xF8, 0x00])

    def test_rgb888(self) -> None:
        driver = MemoryDriver(1, 1, pixel_format=PixelFormat.RGB888)
        canvas = driver.create_canvas(background=Color.RED)
        assert driver.encode(canvas) == bytes([0xFF, 0x00, 0x00])

    def test_encoded_frames_match_the_advertised_size(self) -> None:
        driver = MemoryDriver(320, 170)
        assert len(driver.encode(driver.create_canvas())) == driver.frame_size

    def test_rejects_a_mismatched_canvas(self) -> None:
        driver = MemoryDriver(320, 170)
        with pytest.raises(DriverError, match="expects a 320x170 canvas"):
            driver.encode(Canvas(240, 240))


class TestLifecycle:
    async def test_connect_and_disconnect(self) -> None:
        # Sampled into locals rather than asserted inline: repeated asserts on
        # the same property let mypy narrow it to a constant and declare the
        # rest of the test unreachable.
        driver = MemoryDriver(8, 8)
        before = driver.is_connected
        await driver.connect()
        during = driver.is_connected
        await driver.disconnect()
        after = driver.is_connected
        assert (before, during, after) == (False, True, False)

    async def test_connect_is_idempotent(self) -> None:
        driver = MemoryDriver(8, 8)
        await driver.connect()
        await driver.connect()
        assert driver.connect_count == 1

    async def test_disconnect_on_a_closed_driver_is_a_no_op(self) -> None:
        await MemoryDriver(8, 8).disconnect()

    async def test_context_manager_opens_and_closes(self) -> None:
        driver = MemoryDriver(8, 8)
        async with driver as opened:
            assert opened is driver
            assert driver.is_connected
        assert not driver.is_connected

    async def test_a_failing_close_still_marks_the_driver_disconnected(self) -> None:
        driver = ExplodingDriver(8, 8)
        await driver.connect()
        with pytest.raises(OSError, match="transport died"):
            await driver.disconnect()
        assert not driver.is_connected
        # The driver must remain reusable rather than wedged.
        await driver.connect()
        assert driver.is_connected


class TestShow:
    async def test_records_a_frame(self) -> None:
        async with MemoryDriver(8, 8) as driver:
            canvas = driver.create_canvas(background=Color.WHITE)
            await driver.show(canvas)
            assert len(driver.frames) == 1
            assert driver.last_frame == canvas.to_rgb565()

    async def test_requires_a_connection(self) -> None:
        driver = MemoryDriver(8, 8)
        with pytest.raises(DriverNotConnectedError, match="not connected"):
            await driver.show(driver.create_canvas())

    async def test_rejects_a_mismatched_canvas(self) -> None:
        async with MemoryDriver(8, 8) as driver:
            with pytest.raises(DriverError, match="expects a 8x8 canvas"):
                await driver.show(Canvas(16, 16))

    async def test_no_frame_before_the_first_show(self) -> None:
        assert MemoryDriver(8, 8).last_frame is None

    async def test_frames_accumulate_in_order(self) -> None:
        async with MemoryDriver(1, 1) as driver:
            for color in (Color.RED, Color.GREEN, Color.BLUE):
                await driver.show(driver.create_canvas(background=color))
            assert len(driver.frames) == 3
            assert driver.frames[0] == Canvas(1, 1, background=Color.RED).to_rgb565()

    async def test_max_frames_keeps_only_the_newest(self) -> None:
        async with MemoryDriver(1, 1, max_frames=2) as driver:
            for color in (Color.RED, Color.GREEN, Color.BLUE):
                await driver.show(driver.create_canvas(background=color))
            assert len(driver.frames) == 2
            assert driver.last_frame == Canvas(1, 1, background=Color.BLUE).to_rgb565()

    async def test_reset_discards_frames(self) -> None:
        async with MemoryDriver(8, 8) as driver:
            await driver.show(driver.create_canvas())
            driver.reset()
            assert driver.frames == ()
