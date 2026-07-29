"""The render loop for a real panel, heartbeat included.

Kept out of any example so it can be tested: given a recording transport and a
frame limit, the whole loop runs in-process with nothing attached and without
sleeping for any length of time.

The heartbeat is the reason this module exists at all. The panel's firmware
expects a keep-alive roughly once a second, and paints "Disconnection, content
information display will not be allowed!" over the screen when it stops
arriving. A dashboard that draws a frame and then waits ten seconds for the
next one is therefore not a slow dashboard -- it is a broken-looking one, with
the panel's own banner across it. Every caller needs this, so it is written
once here rather than in every caller.

Frames and heartbeats share a single loop and a single writer. The alternative
-- a background heartbeat task -- would need a lock around the transport,
because two coroutines writing 27-packet frames into the same endpoint would
interleave them and paint garbage. One loop that knows both deadlines is
simpler and cannot race.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final

from tinydisplay.ht32.errors import HT32Error

if TYPE_CHECKING:
    from collections.abc import Callable

    from tinydisplay.core import Canvas
    from tinydisplay.ht32.driver import HT32Driver

__all__ = ["DEFAULT_FPS", "DEFAULT_HEARTBEAT_INTERVAL", "run_panel"]

#: Panels are not monitors. One frame is 27 USB transfers of 4 KB, so a modest
#: rate leaves the bus and the firmware room to keep up; the useful information
#: on a status panel changes about this often anyway.
DEFAULT_FPS: Final = 5

#: How often to reassure the firmware that the host is still here.
DEFAULT_HEARTBEAT_INTERVAL: Final = 1.0

_logger = logging.getLogger(__name__)


async def run_panel(
    driver: HT32Driver,
    render: Callable[[Canvas], None],
    *,
    fps: float = DEFAULT_FPS,
    heartbeat_interval: float | None = DEFAULT_HEARTBEAT_INTERVAL,
    max_frames: int | None = None,
    landscape: bool = True,
) -> int:
    """Draw ``render`` to ``driver`` continuously, keeping the panel alive.

    Args:
        driver: An unconnected driver. It is connected on entry and
            disconnected on exit, including when the loop raises.
        render: Called with a fresh canvas for each frame. Exceptions raised
            here are logged and the frame is skipped -- a dashboard bug should
            not take the panel down, and it must not stop the heartbeat.
        fps: Target frame rate. The loop sleeps for whatever is left of each
            frame's budget, so a slow ``render`` simply runs slower.
        heartbeat_interval: Seconds between keep-alives. ``None`` disables
            them, which is only correct when something else is sending them.
        max_frames: Stop after this many *successful* frames. ``None`` runs
            forever, which is what a dashboard wants; a number is what a test
            wants. Note that a ``render`` which never succeeds never reaches
            the limit -- correct for a panel, which should keep heartbeating
            and retrying rather than going dark because of a dashboard bug,
            but worth knowing before passing a limit in a test.
        landscape: Orientation to set once, before the first frame.

    Returns:
        How many frames were drawn.

    Raises:
        HT32Error: If ``fps`` is not positive, or the panel cannot be reached.
    """
    if fps <= 0:
        msg = f"fps must be positive, got {fps}"
        raise HT32Error(msg)
    if heartbeat_interval is not None and heartbeat_interval <= 0:
        msg = f"heartbeat_interval must be positive or None, got {heartbeat_interval}"
        raise HT32Error(msg)

    frame_budget = 1.0 / fps
    frames = 0
    # Only the first of a run of identical errors is logged. A dashboard left
    # broken would otherwise write `fps` tracebacks a second, forever.
    last_error: str | None = None

    async with driver:
        # Heartbeat first, before anything else. The panel starts every session
        # already believing the host is gone -- that is what its disconnection
        # banner is -- so the first keep-alive is not a keep-alive at all, it is
        # the introduction. Scheduling the first one an interval away instead
        # leaves the banner up for that whole interval, and races a watchdog
        # that appears to be about a second long.
        if heartbeat_interval is not None:
            await driver.heartbeat()
        await driver.set_orientation(landscape=landscape)

        loop = asyncio.get_running_loop()
        next_frame = loop.time()
        next_beat = loop.time() + heartbeat_interval if heartbeat_interval else None

        while max_frames is None or frames < max_frames:
            now = loop.time()

            if next_beat is not None and now >= next_beat:
                await driver.heartbeat()
                next_beat = now + (heartbeat_interval or 0)
                continue

            if now >= next_frame:
                canvas = driver.create_canvas()
                try:
                    render(canvas)
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    if message != last_error:
                        _logger.warning("render failed, skipping frame: %s", message)
                        last_error = message
                else:
                    if last_error is not None:
                        _logger.info("render recovered")
                        last_error = None
                    await driver.show(canvas)
                    frames += 1
                next_frame = now + frame_budget
                continue

            # Wake for whichever deadline comes first. Sleeping a whole frame
            # budget would make heartbeats late whenever the frame rate is
            # slower than the keep-alive, which is the normal case.
            deadline = next_frame if next_beat is None else min(next_frame, next_beat)
            await asyncio.sleep(max(0.0, deadline - loop.time()))

    return frames
