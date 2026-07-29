"""Command-line entry point: ``python -m tinydisplay.simulator dashboard.py``.

Deliberately thin. Everything it does beyond parsing arguments lives in
:mod:`tinydisplay.simulator.runner`, where it can be tested without a display.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import TYPE_CHECKING, Final

from tinydisplay.core import PixelFormat
from tinydisplay.simulator.driver import DEFAULT_SCALE, SimulatorDriver
from tinydisplay.simulator.errors import SimulatorError
from tinydisplay.simulator.reload import DashboardLoader
from tinydisplay.simulator.runner import DEFAULT_FPS, run_dashboard

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["main"]

# The HT32 is the first panel this project targets, so its geometry is the
# default: running the simulator bare should preview the device you own.
DEFAULT_WIDTH: Final = 320
DEFAULT_HEIGHT: Final = 170


def build_parser() -> argparse.ArgumentParser:
    """Construct the argument parser."""
    parser = argparse.ArgumentParser(
        prog="tinydisplay-simulator",
        description="Render a TinyDisplay dashboard to a desktop window.",
        epilog=(
            "The dashboard file must define render(canvas). It is re-executed "
            "whenever it changes on disk, so the window can be left open while "
            "you edit."
        ),
    )
    parser.add_argument("dashboard", help="Path to a Python file defining render(canvas).")
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_WIDTH,
        help=f"Panel width in pixels (default: {DEFAULT_WIDTH}).",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_HEIGHT,
        help=f"Panel height in pixels (default: {DEFAULT_HEIGHT}).",
    )
    parser.add_argument(
        "--scale",
        type=int,
        default=DEFAULT_SCALE,
        help=f"Integer magnification (default: {DEFAULT_SCALE}).",
    )
    parser.add_argument(
        "--fps",
        type=int,
        default=DEFAULT_FPS,
        help=f"Target frame rate (default: {DEFAULT_FPS}).",
    )
    parser.add_argument(
        "--format",
        dest="pixel_format",
        type=PixelFormat,
        choices=list(PixelFormat),
        default=PixelFormat.RGB565_LE,
        help=(
            "Wire format to encode and preview. The 16-bit formats show the "
            "quantised image a real panel displays; rgb888 shows the "
            "unquantised original (default: rgb565_le)."
        ),
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=None,
        help="Stop after this many frames instead of running until closed.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Log every reload and dashboard error.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the simulator. Returns a process exit status."""
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        loader = DashboardLoader(args.dashboard)
        driver = SimulatorDriver(
            args.width,
            args.height,
            pixel_format=args.pixel_format,
            scale=args.scale,
            title=f"TinyDisplay - {loader.path.name}",
        )
    except SimulatorError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 2

    try:
        asyncio.run(run_dashboard(driver, loader, fps=args.fps, max_frames=args.max_frames))
    except SimulatorError as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
