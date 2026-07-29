"""Tests for the simulator driver.

These run headless against NullPreviewWindow, which satisfies the same protocol
as the Tk window. The driver cannot tell the difference, which is the point.
"""

from __future__ import annotations

import numpy as np
import pytest

from tinydisplay.core import Canvas, Color, DisplayDriver, DriverError, PixelFormat
from tinydisplay.core.errors import DriverNotConnectedError
from tinydisplay.simulator import (
    DEFAULT_SCALE,
    NullPreviewWindow,
    SimulatorDriver,
    SimulatorError,
)


class TestConstruction:
    def test_is_a_display_driver(self) -> None:
        driver = SimulatorDriver(8, 8, window=NullPreviewWindow())
        assert isinstance(driver, DisplayDriver)

    def test_defaults(self) -> None:
        driver = SimulatorDriver(320, 170, window=NullPreviewWindow())
        assert driver.size.width == 320
        assert driver.size.height == 170
        assert driver.pixel_format is PixelFormat.RGB565_LE
        assert driver.scale == DEFAULT_SCALE
        assert driver.frame_count == 0
        assert driver.last_preview is None

    def test_title_describes_the_panel(self) -> None:
        driver = SimulatorDriver(320, 170, window=NullPreviewWindow())
        assert "320x170" in driver.title
        assert "rgb565_le" in driver.title

    def test_explicit_title_is_kept(self) -> None:
        driver = SimulatorDriver(8, 8, window=NullPreviewWindow(), title="Kitchen")
        assert driver.title == "Kitchen"

    def test_bad_scale_fails_at_construction_not_first_frame(self) -> None:
        with pytest.raises(SimulatorError, match="scale must be in"):
            SimulatorDriver(8, 8, window=NullPreviewWindow(), scale=0)

    def test_inherits_base_class_dimension_validation(self) -> None:
        with pytest.raises(DriverError, match="dimensions must be positive"):
            SimulatorDriver(0, 8, window=NullPreviewWindow())


class TestLifecycle:
    async def test_connect_opens_the_window(self) -> None:
        window = NullPreviewWindow()
        driver = SimulatorDriver(8, 8, window=window)

        assert window.open_count == 0
        await driver.connect()
        assert window.is_open
        assert driver.is_window_open

    async def test_context_manager_opens_and_leaves_a_borrowed_window_alone(self) -> None:
        """A window the caller supplied is the caller's to close."""
        window = NullPreviewWindow()
        async with SimulatorDriver(8, 8, window=window):
            assert window.is_open
        assert window.is_open, "a borrowed window must survive disconnect"

    async def test_connect_is_idempotent(self) -> None:
        window = NullPreviewWindow()
        driver = SimulatorDriver(8, 8, window=window)

        await driver.connect()
        await driver.connect()

        assert window.open_count == 1

    async def test_show_before_connect_raises(self) -> None:
        driver = SimulatorDriver(8, 8, window=NullPreviewWindow())
        with pytest.raises(DriverNotConnectedError):
            await driver.show(driver.create_canvas())

    async def test_wrong_sized_canvas_is_rejected(self) -> None:
        async with SimulatorDriver(8, 8, window=NullPreviewWindow()) as driver:
            with pytest.raises(DriverError, match="expects a 8x8 canvas"):
                await driver.show(Canvas(4, 4))


class TestRendering:
    async def test_frame_reaches_the_window_scaled(self) -> None:
        window = NullPreviewWindow()
        async with SimulatorDriver(8, 4, window=window, scale=3) as driver:
            canvas = driver.create_canvas()
            canvas.clear(Color.WHITE)
            await driver.show(canvas)

        frame = window.last_frame
        assert frame is not None
        assert frame.shape == (12, 24, 3)

    async def test_preview_is_the_quantised_image(self) -> None:
        """What the window shows is what a 16-bit panel would show."""
        window = NullPreviewWindow()
        async with SimulatorDriver(4, 4, window=window) as driver:
            canvas = driver.create_canvas()
            canvas.clear(Color.from_hex("#123456"))
            await driver.show(canvas)

        preview = driver.last_preview
        assert preview is not None
        expected = Color.from_hex("#123456").quantized_rgb565()
        assert tuple(preview[0, 0]) == expected.rgb
        assert tuple(preview[0, 0]) != (0x12, 0x34, 0x56), "should not be the unquantised colour"

    async def test_rgb888_format_previews_without_quantisation(self) -> None:
        window = NullPreviewWindow()
        driver = SimulatorDriver(4, 4, window=window, pixel_format=PixelFormat.RGB888)
        async with driver:
            canvas = driver.create_canvas()
            canvas.clear(Color.from_hex("#123456"))
            await driver.show(canvas)

        preview = driver.last_preview
        assert preview is not None
        assert tuple(preview[0, 0]) == (0x12, 0x34, 0x56)

    async def test_last_preview_is_unscaled(self) -> None:
        window = NullPreviewWindow()
        async with SimulatorDriver(8, 4, window=window, scale=4) as driver:
            await driver.show(driver.create_canvas())

        preview = driver.last_preview
        assert preview is not None
        assert preview.shape == (4, 8, 3)

    async def test_frame_count_tracks_shows(self) -> None:
        async with SimulatorDriver(4, 4, window=NullPreviewWindow()) as driver:
            for _ in range(3):
                await driver.show(driver.create_canvas())
            assert driver.frame_count == 3

    async def test_successive_frames_are_distinct(self) -> None:
        """The recorder must not alias one buffer across every frame."""
        window = NullPreviewWindow()
        async with SimulatorDriver(4, 4, window=window, scale=1) as driver:
            for color in (Color.WHITE, Color.BLACK):
                canvas = driver.create_canvas()
                canvas.clear(color)
                await driver.show(canvas)

        assert len(window.frames) == 2
        assert not np.array_equal(window.frames[0], window.frames[1])

    async def test_scale_of_one_leaves_dimensions_alone(self) -> None:
        window = NullPreviewWindow()
        async with SimulatorDriver(6, 5, window=window, scale=1) as driver:
            await driver.show(driver.create_canvas())

        frame = window.last_frame
        assert frame is not None
        assert frame.shape == (5, 6, 3)
