"""Tests for the render loop and the on-panel error report."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt
import pytest

from tinydisplay.core import Canvas, Color, Font
from tinydisplay.simulator import (
    DashboardError,
    DashboardLoader,
    NullPreviewWindow,
    SimulatorDriver,
    draw_error,
    run_dashboard,
)

if TYPE_CHECKING:
    from pathlib import Path

ERROR_BACKGROUND = Color.from_hex("#40151a")
ERROR_ACCENT = Color.from_hex("#ff5f56")

WHITE_DASHBOARD = """
from tinydisplay.core import Color

def render(canvas):
    canvas.clear(Color.WHITE)
"""

BROKEN_DASHBOARD = """
def render(canvas):
    raise ValueError('kaboom')
"""


def write_dashboard(path: Path, source: str, *, mtime: int) -> None:
    path.write_text(source, encoding="utf-8")
    os.utime(path, ns=(mtime, mtime))


@pytest.fixture
def dashboard(tmp_path: Path) -> Path:
    path = tmp_path / "board.py"
    write_dashboard(path, WHITE_DASHBOARD, mtime=1_000_000_000_000_000_000)
    return path


class TestDrawError:
    def test_paints_the_error_palette(self) -> None:
        canvas = Canvas(120, 60)
        canvas.clear(Color.WHITE)

        draw_error(canvas, "something went wrong")

        assert canvas.get_pixel(0, 0) == ERROR_ACCENT
        assert canvas.get_pixel(119, 59) == ERROR_BACKGROUND

    def test_writes_visible_text(self) -> None:
        blank = Canvas(120, 60)
        draw_error(blank, "")
        written = Canvas(120, 60)
        draw_error(written, "a readable message")

        assert not np.array_equal(blank.buffer, written.buffer)

    def test_long_message_stays_inside_the_canvas(self) -> None:
        canvas = Canvas(64, 32)
        draw_error(canvas, "word " * 200)

        # Nothing to assert about layout beyond "it returned"; the real check
        # is that a message far larger than the panel neither raises nor
        # overruns, which canvas clipping guarantees.
        assert canvas.size.width == 64

    def test_unbroken_word_wider_than_the_panel_is_split(self) -> None:
        canvas = Canvas(40, 40)
        draw_error(canvas, "x" * 200)
        assert canvas.get_pixel(39, 39) == ERROR_BACKGROUND

    def test_narrow_canvas_does_not_hang(self) -> None:
        """A panel narrower than one glyph must not loop forever."""
        canvas = Canvas(4, 20)
        draw_error(canvas, "overflowing message")
        assert canvas.size.width == 4

    def test_newlines_are_honoured(self) -> None:
        single = Canvas(120, 60)
        draw_error(single, "one line")
        double = Canvas(120, 60)
        draw_error(double, "one line\nsecond line")

        assert not np.array_equal(single.buffer, double.buffer)

    def test_accepts_an_explicit_font(self) -> None:
        canvas = Canvas(120, 60)
        draw_error(canvas, "sized", font=Font.default(8))
        assert canvas.get_pixel(0, 0) == ERROR_ACCENT


class TestRunDashboard:
    async def test_renders_the_requested_number_of_frames(self, dashboard: Path) -> None:
        window = NullPreviewWindow()
        driver = SimulatorDriver(16, 8, window=window, scale=1)

        frames = await run_dashboard(driver, DashboardLoader(dashboard), fps=1000, max_frames=3)

        assert frames == 3
        assert len(window.frames) == 3

    async def test_loads_the_dashboard_itself(self, dashboard: Path) -> None:
        window = NullPreviewWindow()
        driver = SimulatorDriver(8, 8, window=window, scale=1)
        loader = DashboardLoader(dashboard)

        await run_dashboard(driver, loader, fps=1000, max_frames=1)

        assert loader.is_loaded
        frame = window.last_frame
        assert frame is not None
        assert tuple(frame[0, 0]) == (255, 255, 255)

    async def test_disconnects_afterwards(self, dashboard: Path) -> None:
        driver = SimulatorDriver(8, 8, window=NullPreviewWindow(), scale=1)

        await run_dashboard(driver, DashboardLoader(dashboard), fps=1000, max_frames=1)

        assert not driver.is_connected

    async def test_broken_dashboard_is_painted_not_raised(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.py"
        write_dashboard(path, BROKEN_DASHBOARD, mtime=1_000_000_000_000_000_000)
        window = NullPreviewWindow()
        driver = SimulatorDriver(64, 32, window=window, scale=1)

        frames = await run_dashboard(driver, DashboardLoader(path), fps=1000, max_frames=2)

        assert frames == 2
        preview = driver.last_preview
        assert preview is not None
        assert tuple(preview[31, 63]) == ERROR_BACKGROUND.quantized_rgb565().rgb

    async def test_unloadable_dashboard_is_painted_not_raised(self, tmp_path: Path) -> None:
        path = tmp_path / "syntax.py"
        write_dashboard(path, "def render(canvas)\n", mtime=1_000_000_000_000_000_000)
        driver = SimulatorDriver(64, 32, window=NullPreviewWindow(), scale=1)

        frames = await run_dashboard(driver, DashboardLoader(path), fps=1000, max_frames=1)

        assert frames == 1
        preview = driver.last_preview
        assert preview is not None
        assert tuple(preview[31, 63]) == ERROR_BACKGROUND.quantized_rgb565().rgb

    async def test_edit_is_picked_up_mid_run(self, dashboard: Path) -> None:
        """The whole point of hot reload: change the file, see the change."""
        window = NullPreviewWindow()
        driver = SimulatorDriver(8, 8, window=window, scale=1)
        loader = DashboardLoader(dashboard)

        await run_dashboard(driver, loader, fps=1000, max_frames=1)
        first = window.last_frame

        write_dashboard(
            dashboard,
            "from tinydisplay.core import Color\n\ndef render(canvas):\n"
            "    canvas.clear(Color.from_hex('#ff0000'))\n",
            mtime=2_000_000_000_000_000_000,
        )
        await run_dashboard(driver, loader, fps=1000, max_frames=1)
        second = window.last_frame

        assert first is not None
        assert second is not None
        assert tuple(first[0, 0]) == (255, 255, 255)
        assert tuple(second[0, 0]) == (255, 0, 0)

    async def test_recovers_when_a_broken_dashboard_is_fixed(self, tmp_path: Path) -> None:
        path = tmp_path / "board.py"
        write_dashboard(path, BROKEN_DASHBOARD, mtime=1_000_000_000_000_000_000)
        window = NullPreviewWindow()
        driver = SimulatorDriver(64, 32, window=window, scale=1)
        loader = DashboardLoader(path)

        await run_dashboard(driver, loader, fps=1000, max_frames=1)

        write_dashboard(path, WHITE_DASHBOARD, mtime=2_000_000_000_000_000_000)
        await run_dashboard(driver, loader, fps=1000, max_frames=1)

        frame = window.last_frame
        assert frame is not None
        assert tuple(frame[0, 0]) == (255, 255, 255)

    async def test_closed_window_ends_the_loop(self, dashboard: Path) -> None:
        """Closing the window is the simulator's unplug event."""

        class ClosingWindow(NullPreviewWindow):
            def update(self, pixels: npt.NDArray[np.uint8]) -> None:
                super().update(pixels)
                if len(self.frames) >= 2:
                    self.close()

        window = ClosingWindow()
        driver = SimulatorDriver(8, 8, window=window, scale=1)

        frames = await run_dashboard(driver, DashboardLoader(dashboard), fps=1000)

        assert frames == 2

    async def test_window_closed_before_the_first_frame_draws_nothing(
        self, dashboard: Path
    ) -> None:
        class NeverOpenWindow(NullPreviewWindow):
            def open(self) -> None:
                return

        driver = SimulatorDriver(8, 8, window=NeverOpenWindow(), scale=1)

        frames = await run_dashboard(driver, DashboardLoader(dashboard), fps=1000)

        assert frames == 0

    @pytest.mark.parametrize("fps", [0, -1])
    async def test_non_positive_fps_is_rejected(self, dashboard: Path, fps: int) -> None:
        driver = SimulatorDriver(8, 8, window=NullPreviewWindow(), scale=1)
        with pytest.raises(DashboardError, match="fps must be positive"):
            await run_dashboard(driver, DashboardLoader(dashboard), fps=fps, max_frames=1)

    async def test_max_frames_of_zero_draws_nothing(self, dashboard: Path) -> None:
        window = NullPreviewWindow()
        driver = SimulatorDriver(8, 8, window=window, scale=1)

        frames = await run_dashboard(driver, DashboardLoader(dashboard), fps=1000, max_frames=0)

        assert frames == 0
        assert window.frames == ()
