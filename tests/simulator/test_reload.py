"""Tests for loading and hot-reloading a dashboard file."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING

import pytest

from tinydisplay.core import Canvas, Color
from tinydisplay.simulator import DashboardError, DashboardLoader

if TYPE_CHECKING:
    from pathlib import Path

WHITE_DASHBOARD = """
from tinydisplay.core import Color

def render(canvas):
    canvas.clear(Color.WHITE)
"""

BLACK_DASHBOARD = """
from tinydisplay.core import Color

def render(canvas):
    canvas.clear(Color.BLACK)
"""


def write_dashboard(path: Path, source: str, *, mtime: int) -> None:
    """Write a dashboard and stamp its mtime.

    The mtime is set explicitly because a test can easily rewrite a file within
    the filesystem's timestamp granularity, which would make a genuine change
    look like no change at all.
    """
    path.write_text(source, encoding="utf-8")
    os.utime(path, ns=(mtime, mtime))


@pytest.fixture
def dashboard(tmp_path: Path) -> Path:
    path = tmp_path / "board.py"
    write_dashboard(path, WHITE_DASHBOARD, mtime=1_000_000_000_000_000_000)
    return path


class TestConstruction:
    def test_missing_file_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(DashboardError, match="dashboard file not found"):
            DashboardLoader(tmp_path / "absent.py")

    def test_directory_is_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(DashboardError, match="dashboard file not found"):
            DashboardLoader(tmp_path)

    def test_path_is_resolved(self, dashboard: Path) -> None:
        loader = DashboardLoader(dashboard)
        assert loader.path.is_absolute()
        assert loader.path.name == "board.py"

    def test_starts_unloaded(self, dashboard: Path) -> None:
        assert not DashboardLoader(dashboard).is_loaded

    def test_repr_reports_state(self, dashboard: Path) -> None:
        loader = DashboardLoader(dashboard)
        assert "unloaded" in repr(loader)
        loader.load()
        assert "loaded" in repr(loader)


class TestLoad:
    def test_loads_and_renders(self, dashboard: Path) -> None:
        loader = DashboardLoader(dashboard)
        loader.load()
        canvas = Canvas(4, 4)
        loader.render(canvas)

        assert loader.is_loaded
        assert canvas.get_pixel(0, 0) == Color.WHITE

    def test_render_before_load_raises(self, dashboard: Path) -> None:
        loader = DashboardLoader(dashboard)
        with pytest.raises(DashboardError, match="no dashboard loaded"):
            loader.render(Canvas(4, 4))

    def test_syntax_error_is_wrapped(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.py"
        write_dashboard(path, "def render(canvas)\n    pass\n", mtime=1_000_000_000_000_000_000)

        with pytest.raises(DashboardError, match="failed to load") as info:
            DashboardLoader(path).load()

        assert isinstance(info.value.__cause__, SyntaxError)

    def test_missing_render_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.py"
        write_dashboard(path, "VALUE = 1\n", mtime=1_000_000_000_000_000_000)

        with pytest.raises(DashboardError, match="defines no callable render"):
            DashboardLoader(path).load()

    def test_non_callable_render_is_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "notafunction.py"
        write_dashboard(path, "render = 42\n", mtime=1_000_000_000_000_000_000)

        with pytest.raises(DashboardError, match="defines no callable render"):
            DashboardLoader(path).load()

    def test_render_exception_is_wrapped(self, tmp_path: Path) -> None:
        path = tmp_path / "raises.py"
        write_dashboard(
            path,
            "def render(canvas):\n    raise ValueError('nope')\n",
            mtime=1_000_000_000_000_000_000,
        )
        loader = DashboardLoader(path)
        loader.load()

        with pytest.raises(DashboardError, match="raised while rendering") as info:
            loader.render(Canvas(4, 4))

        assert isinstance(info.value.__cause__, ValueError)

    def test_failed_load_leaves_no_module_behind(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.py"
        write_dashboard(path, "raise RuntimeError('boom')\n", mtime=1_000_000_000_000_000_000)
        before = set(sys.modules)

        with pytest.raises(DashboardError):
            DashboardLoader(path).load()

        assert set(sys.modules) == before

    def test_unload_clears_the_module(self, dashboard: Path) -> None:
        loader = DashboardLoader(dashboard)
        loader.load()
        before = set(sys.modules)
        loader.unload()

        assert not loader.is_loaded
        assert len(set(sys.modules)) < len(before)

    def test_two_dashboards_sharing_a_name_do_not_collide(self, tmp_path: Path) -> None:
        first_dir = tmp_path / "a"
        second_dir = tmp_path / "b"
        first_dir.mkdir()
        second_dir.mkdir()
        write_dashboard(first_dir / "board.py", WHITE_DASHBOARD, mtime=1_000_000_000_000_000_000)
        write_dashboard(second_dir / "board.py", BLACK_DASHBOARD, mtime=1_000_000_000_000_000_000)

        first = DashboardLoader(first_dir / "board.py")
        second = DashboardLoader(second_dir / "board.py")
        first.load()
        second.load()

        white = Canvas(2, 2)
        black = Canvas(2, 2)
        first.render(white)
        second.render(black)

        assert white.get_pixel(0, 0) == Color.WHITE
        assert black.get_pixel(0, 0) == Color.BLACK


class TestHotReload:
    def test_unloaded_loader_reports_a_change(self, dashboard: Path) -> None:
        assert DashboardLoader(dashboard).has_changed()

    def test_untouched_file_reports_no_change(self, dashboard: Path) -> None:
        loader = DashboardLoader(dashboard)
        loader.load()
        assert not loader.has_changed()
        assert not loader.reload_if_changed()

    def test_edit_is_picked_up(self, dashboard: Path) -> None:
        loader = DashboardLoader(dashboard)
        loader.load()

        write_dashboard(dashboard, BLACK_DASHBOARD, mtime=2_000_000_000_000_000_000)

        assert loader.has_changed()
        assert loader.reload_if_changed()

        canvas = Canvas(4, 4)
        canvas.clear(Color.WHITE)
        loader.render(canvas)
        assert canvas.get_pixel(0, 0) == Color.BLACK

    def test_failed_reload_keeps_the_last_good_dashboard(self, dashboard: Path) -> None:
        """A typo mid-edit must not leave the simulator with nothing to draw."""
        loader = DashboardLoader(dashboard)
        loader.load()

        write_dashboard(dashboard, "def render(canvas)\n", mtime=2_000_000_000_000_000_000)

        with pytest.raises(DashboardError, match="failed to load"):
            loader.reload_if_changed()

        assert loader.is_loaded
        canvas = Canvas(4, 4)
        loader.render(canvas)
        assert canvas.get_pixel(0, 0) == Color.WHITE

    def test_recovery_after_a_failed_reload(self, dashboard: Path) -> None:
        loader = DashboardLoader(dashboard)
        loader.load()

        write_dashboard(dashboard, "def render(canvas)\n", mtime=2_000_000_000_000_000_000)
        with pytest.raises(DashboardError):
            loader.reload_if_changed()

        write_dashboard(dashboard, BLACK_DASHBOARD, mtime=3_000_000_000_000_000_000)
        assert loader.reload_if_changed()

        canvas = Canvas(4, 4)
        loader.render(canvas)
        assert canvas.get_pixel(0, 0) == Color.BLACK

    def test_deleted_file_reports_no_change(self, dashboard: Path) -> None:
        """Editors delete and rewrite; reporting a change would guarantee a failure."""
        loader = DashboardLoader(dashboard)
        loader.load()
        dashboard.unlink()

        assert not loader.has_changed()
        assert not loader.reload_if_changed()

    def test_dashboard_still_renders_after_its_file_is_deleted(self, dashboard: Path) -> None:
        loader = DashboardLoader(dashboard)
        loader.load()
        dashboard.unlink()

        canvas = Canvas(4, 4)
        loader.render(canvas)
        assert canvas.get_pixel(0, 0) == Color.WHITE
