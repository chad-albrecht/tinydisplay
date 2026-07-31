"""Tests for the command-line entry point.

The CLI opens a real window, so these tests exercise argument parsing and the
failure paths rather than a full run.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from tinydisplay.core import PixelFormat
from tinydisplay.simulator.__main__ import DEFAULT_HEIGHT, DEFAULT_WIDTH, build_parser, main

if TYPE_CHECKING:
    from pathlib import Path

WHITE_DASHBOARD = """
from tinydisplay.core import Color

def render(canvas):
    canvas.clear(Color.WHITE)
"""


@pytest.fixture
def dashboard(tmp_path: Path) -> Path:
    path = tmp_path / "board.py"
    path.write_text(WHITE_DASHBOARD, encoding="utf-8")
    os.utime(path, ns=(1_000_000_000_000_000_000, 1_000_000_000_000_000_000))
    return path


class TestParser:
    def test_defaults_match_the_ht32(self) -> None:
        """Running bare should preview the panel this project targets first."""
        args = build_parser().parse_args(["board.py"])

        assert args.width == DEFAULT_WIDTH == 320
        assert args.height == DEFAULT_HEIGHT == 170
        assert args.pixel_format is PixelFormat.RGB565_LE
        assert args.max_frames is None
        assert not args.verbose

    def test_geometry_overrides(self) -> None:
        args = build_parser().parse_args(
            ["board.py", "--width", "64", "--height", "32", "--scale", "5"]
        )

        assert args.width == 64
        assert args.height == 32
        assert args.scale == 5

    def test_pixel_format_is_parsed_as_an_enum(self) -> None:
        args = build_parser().parse_args(["board.py", "--format", "rgb888"])
        assert args.pixel_format is PixelFormat.RGB888

    def test_unknown_pixel_format_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["board.py", "--format", "cmyk"])

    def test_dashboard_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])


class TestMain:
    def test_missing_dashboard_exits_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        status = main([str(tmp_path / "absent.py")])

        assert status == 2
        assert "dashboard file not found" in capsys.readouterr().err

    def test_bad_scale_exits_two(self, dashboard: Path, capsys: pytest.CaptureFixture[str]) -> None:
        status = main([str(dashboard), "--scale", "0"])

        assert status == 2
        assert "scale must be in" in capsys.readouterr().err

    def test_unopenable_window_exits_one(
        self, dashboard: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No display is a run failure, not a usage error.

        Skipped where tkinter itself is absent, which is the uv-managed Python
        on a Linux runner: reaching a TclError needs Tk present and a display
        missing. macOS and Windows in the matrix cover it.
        """
        tk = pytest.importorskip("tkinter")

        def explode(*_args: object, **_kwargs: object) -> None:
            msg = "no display name and no $DISPLAY environment variable"
            raise tk.TclError(msg)

        monkeypatch.setattr(tk, "Tk", explode)

        status = main([str(dashboard), "--width", "8", "--height", "8", "--max-frames", "1"])

        assert status == 1
        assert "could not open a preview window" in capsys.readouterr().err
