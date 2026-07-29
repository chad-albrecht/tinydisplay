"""The render loop that ties a driver, a dashboard and hot-reload together.

Kept out of ``__main__`` so it can be tested: given a headless window and a
frame limit, the whole loop runs in-process with no display and no sleeping.

A dashboard that raises does not stop the loop. The exception is painted onto
the panel instead, which is the behaviour that makes the simulator pleasant to
leave open on a second monitor -- a typo shows up as a red screen you can read,
and fixing the file brings the dashboard straight back.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final

from tinydisplay.core import Color, Font, HorizontalAlign, VerticalAlign
from tinydisplay.simulator.errors import DashboardError

if TYPE_CHECKING:
    from tinydisplay.core import Canvas
    from tinydisplay.simulator.driver import SimulatorDriver
    from tinydisplay.simulator.reload import DashboardLoader

__all__ = ["DEFAULT_FPS", "draw_error", "run_dashboard"]

DEFAULT_FPS: Final = 30

_ERROR_BACKGROUND: Final = Color.from_hex("#40151a")
_ERROR_ACCENT: Final = Color.from_hex("#ff5f56")
_ERROR_TEXT: Final = Color.from_hex("#f5e6e6")
_ERROR_MARGIN: Final = 4

_logger = logging.getLogger(__name__)


def draw_error(canvas: Canvas, message: str, *, font: Font | None = None) -> None:
    """Paint an error message onto ``canvas``, wrapped to its width.

    Example:
        >>> from tinydisplay.simulator import draw_error
        >>> canvas = Canvas(120, 60)
        >>> draw_error(canvas, "boom")
        >>> canvas.get_pixel(0, 0) == Color.from_hex("#ff5f56")  # accent bar
        True
        >>> canvas.get_pixel(119, 59) == Color.from_hex("#40151a")  # background
        True
    """
    face = font or Font.default()
    canvas.clear(_ERROR_BACKGROUND)
    canvas.rect(0, 0, canvas.width, 2, _ERROR_ACCENT, fill=True)

    available = canvas.width - 2 * _ERROR_MARGIN
    lines = _wrap(message, face, available)
    # Drop lines that would spill off the bottom rather than drawing a partial
    # glyph row: a truncated message is readable, a clipped one is noise.
    max_lines = max(1, (canvas.height - 2 * _ERROR_MARGIN) // face.line_height)
    if len(lines) > max_lines:
        lines = [*lines[: max_lines - 1], "..."]

    canvas.text(
        _ERROR_MARGIN,
        _ERROR_MARGIN + 2,
        "\n".join(lines),
        _ERROR_TEXT,
        font=face,
        align=HorizontalAlign.LEFT,
        valign=VerticalAlign.TOP,
    )


def _wrap(message: str, font: Font, width: int) -> list[str]:
    """Greedy word wrap. Words wider than ``width`` are broken across lines."""
    if width <= 0:
        return [message]

    lines: list[str] = []
    for paragraph in message.split("\n"):
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}" if current else word
            if font.text_width(candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            # A single word too wide for the panel still has to go somewhere.
            remainder = word
            while font.text_width(remainder) > width and len(remainder) > 1:
                cut = _longest_prefix(remainder, font, width)
                lines.append(remainder[:cut])
                remainder = remainder[cut:]
            current = remainder
        lines.append(current)
    return lines


def _longest_prefix(word: str, font: Font, width: int) -> int:
    """The largest ``n`` where ``word[:n]`` fits in ``width``, at least 1."""
    cut = 1
    while cut < len(word) and font.text_width(word[: cut + 1]) <= width:
        cut += 1
    return cut


async def run_dashboard(
    driver: SimulatorDriver,
    loader: DashboardLoader,
    *,
    fps: int = DEFAULT_FPS,
    max_frames: int | None = None,
) -> int:
    """Render ``loader``'s dashboard through ``driver`` until the window closes.

    Args:
        driver: An unconnected simulator driver. It is connected on entry and
            disconnected on exit, including when the loop raises.
        loader: The dashboard source. Loaded here if it is not already.
        fps: Target frame rate. The loop sleeps for whatever is left of each
            frame's budget, so a slow dashboard simply runs slower.
        max_frames: Stop after this many frames. ``None`` runs until the
            operator closes the window, which is what the CLI wants; a number
            is what a test wants.

    Returns:
        How many frames were drawn.

    Raises:
        SimulatorError: If the window cannot be opened.
    """
    if fps <= 0:
        msg = f"fps must be positive, got {fps}"
        raise DashboardError(msg)

    frame_budget = 1.0 / fps
    frames = 0
    # Only the first occurrence of a repeated error is logged. A dashboard left
    # broken would otherwise write `fps` identical tracebacks every second.
    last_error: str | None = None

    async with driver:
        loop = asyncio.get_running_loop()
        while driver.is_window_open:
            if max_frames is not None and frames >= max_frames:
                break
            started = loop.time()
            canvas = driver.create_canvas()

            try:
                if not loader.is_loaded:
                    loader.load()
                else:
                    loader.reload_if_changed()
                loader.render(canvas)
            except DashboardError as exc:
                message = str(exc)
                if message != last_error:
                    _logger.warning("dashboard error: %s", message)
                    last_error = message
                draw_error(canvas, message)
            else:
                if last_error is not None:
                    _logger.info("dashboard recovered: %s", loader.path.name)
                    last_error = None

            await driver.show(canvas)
            frames += 1

            elapsed = loop.time() - started
            await asyncio.sleep(max(0.0, frame_budget - elapsed))

    return frames
