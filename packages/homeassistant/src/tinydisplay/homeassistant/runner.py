"""A render loop driven by state changes rather than by a clock.

The simulator repaints at a fixed rate because a developer is watching a file
and wants edits to appear. A panel on a shelf is the opposite case: the picture
is identical between sensor updates, and repainting it thirty times a second
costs 27 USB transfers each time to produce the same pixels.

So this loop waits. It sleeps on an :class:`asyncio.Event` that the caller sets
when a subscribed entity changes, and repaints when it wakes. Two bounds keep
that honest:

- ``min_interval`` is a floor between repaints. A light group turning on emits
  a dozen state changes in a few milliseconds, and coalescing them into one
  frame is the difference between a smooth update and a visibly stuttering one.
- ``max_interval`` is a ceiling. Something is always drawn eventually, which
  covers the case where the panel has quietly lost the frame -- and means a
  dashboard whose entities are all unavailable still shows a live picture
  rather than a stale one.

**Keep-alives are a parameter, not a feature of the loop.** The HT32's firmware
paints a disconnection banner over the screen when it stops hearing from the
host, so its driver has a ``heartbeat`` method that has to be called about once
a second. This package must not import a driver -- that would invert the
dependency stack, and the whole point of the layering is that a second panel
should not require touching anything up here. So the caller passes the
coroutine to call, and this loop schedules it against the same deadlines as the
frames. A driver that needs no keep-alive passes nothing.

Frames and keep-alives share one loop and one writer for the same reason the
HT32's own runner does: two coroutines writing multi-packet frames into the
same endpoint would interleave them and paint garbage, and a lock around the
transport is a worse answer than not needing one.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final

from tinydisplay.homeassistant.errors import HomeAssistantError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tinydisplay.core import Canvas, DisplayDriver
    from tinydisplay.homeassistant.dashboard import Dashboard
    from tinydisplay.homeassistant.state import StateSource

__all__ = [
    "DEFAULT_KEEPALIVE_INTERVAL",
    "DEFAULT_MAX_INTERVAL",
    "DEFAULT_MIN_INTERVAL",
    "run_dashboard",
]

#: Floor between repaints, in seconds. Five frames a second is far more than a
#: status panel needs and still fast enough that a button press feels immediate.
DEFAULT_MIN_INTERVAL: Final = 0.2

#: Ceiling between repaints, in seconds. Long enough to be genuinely idle,
#: short enough that a panel which dropped a frame recovers without anyone
#: noticing.
DEFAULT_MAX_INTERVAL: Final = 30.0

#: How often to call ``keepalive`` when one is supplied. Matches what the HT32
#: firmware expects; a driver needing a different cadence passes its own.
DEFAULT_KEEPALIVE_INTERVAL: Final = 1.0

_logger = logging.getLogger(__name__)


async def run_dashboard(
    driver: DisplayDriver,
    dashboard: Dashboard,
    source: StateSource,
    *,
    changed: asyncio.Event | None = None,
    min_interval: float = DEFAULT_MIN_INTERVAL,
    max_interval: float | None = DEFAULT_MAX_INTERVAL,
    keepalive: Callable[[], Awaitable[None]] | None = None,
    keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL,
    on_connect: Callable[[], Awaitable[None]] | None = None,
    on_frame: Callable[[Canvas], None] | None = None,
    max_frames: int | None = None,
) -> int:
    """Draw ``dashboard`` through ``driver``, repainting when state changes.

    Args:
        driver: An unconnected driver. It is connected on entry and
            disconnected on exit, including when the loop raises or is
            cancelled.
        dashboard: What to draw.
        source: Where entity state is read from, consulted at repaint time.
        changed: Set by the caller when a subscribed entity changes. The loop
            clears it after each repaint. Defaults to a fresh event, which
            makes the loop purely periodic -- useful for a static dashboard.
        min_interval: Seconds to wait after a repaint before another one,
            however many changes arrive in between.
        max_interval: Repaint at least this often even with nothing changing.
            ``None`` repaints only on change, which is correct for a panel
            that holds its frame reliably and wrong for one that does not.
        keepalive: Called every ``keepalive_interval`` seconds. Pass a driver's
            heartbeat coroutine for panels that need one, and nothing for
            panels that do not.
        keepalive_interval: Seconds between keep-alives.
        on_connect: Called once after the driver connects and before the first
            frame. This is where per-panel setup goes -- the HT32 sets its
            orientation here -- so that the loop stays driver-agnostic without
            pretending panels have nothing to configure.
        on_frame: Called with each canvas *after* it reaches the panel, so a
            caller can show the same picture somewhere else. Called after
            rather than before because a frame the driver rejected is not one
            the panel showed, and a preview claiming otherwise would be worse
            than no preview. Exceptions raised here are not caught: this is
            caller-supplied bookkeeping, not dashboard code, and swallowing its
            failures would hide a bug in something that should be trivial.
        max_frames: Stop after this many frames. ``None`` runs until cancelled,
            which is what an integration wants; a number is what a test wants.

    Returns:
        How many frames were drawn.

    Raises:
        HomeAssistantError: If the intervals are not positive.
    """
    _validate_intervals(min_interval, max_interval, keepalive_interval)

    signal = changed if changed is not None else asyncio.Event()
    frames = 0
    # Only the first of a run of identical errors is logged. A dashboard
    # referencing a file that has gone missing would otherwise fill Home
    # Assistant's log at the repaint rate, forever.
    last_error: str | None = None

    async with driver:
        loop = asyncio.get_running_loop()

        # Before the first frame, not after. A panel whose firmware paints a
        # disconnection banner starts every session with that banner up, so the
        # first keep-alive is an introduction rather than a reassurance --
        # scheduling it an interval away leaves the banner over the first frame.
        if keepalive is not None:
            await keepalive()
        if on_connect is not None:
            await on_connect()
        next_beat = loop.time() + keepalive_interval if keepalive is not None else None

        # The first frame is drawn immediately: an integration that has just
        # started should put something on the panel now, not at the first
        # deadline.
        last_frame = loop.time() - max(min_interval, 1.0)
        signal.set()

        while max_frames is None or frames < max_frames:
            now = loop.time()

            if next_beat is not None and now >= next_beat and keepalive is not None:
                await keepalive()
                next_beat = loop.time() + keepalive_interval
                continue

            due = _next_repaint(
                last_frame,
                pending=signal.is_set(),
                min_interval=min_interval,
                max_interval=max_interval,
            )

            if due is not None and now >= due:
                signal.clear()
                last_frame = now
                try:
                    canvas = driver.create_canvas()
                    dashboard.render(canvas, source)
                # A dashboard bug must not take the panel down, and must not
                # stop the keep-alive: the frame is skipped, the loop is not.
                except Exception as exc:
                    message = f"{type(exc).__name__}: {exc}"
                    if message != last_error:
                        _logger.warning("dashboard render failed, skipping frame: %s", message)
                        last_error = message
                else:
                    if last_error is not None:
                        _logger.info("dashboard render recovered")
                        last_error = None
                    await driver.show(canvas)
                    frames += 1
                    if on_frame is not None:
                        on_frame(canvas)
                continue

            await _wait(signal, _earliest(due, next_beat), loop)

    return frames


def _validate_intervals(
    min_interval: float,
    max_interval: float | None,
    keepalive_interval: float,
) -> None:
    """Reject intervals that would make the loop spin or never fire."""
    if min_interval < 0:
        msg = f"min_interval must not be negative, got {min_interval}"
        raise HomeAssistantError(msg)
    if max_interval is not None and max_interval <= 0:
        msg = f"max_interval must be positive or None, got {max_interval}"
        raise HomeAssistantError(msg)
    if max_interval is not None and max_interval < min_interval:
        msg = f"max_interval {max_interval} is below min_interval {min_interval}"
        raise HomeAssistantError(msg)
    if keepalive_interval <= 0:
        msg = f"keepalive_interval must be positive, got {keepalive_interval}"
        raise HomeAssistantError(msg)


def _next_repaint(
    last_frame: float,
    *,
    pending: bool,
    min_interval: float,
    max_interval: float | None,
) -> float | None:
    """When the next repaint is due, or ``None`` if nothing is scheduled.

    A pending change is held until ``min_interval`` has passed since the last
    frame; with nothing pending, the periodic ceiling applies. ``None`` means
    the loop should sleep until something happens, which is only reachable when
    ``max_interval`` is disabled.
    """
    if pending:
        return last_frame + min_interval
    if max_interval is not None:
        return last_frame + max_interval
    return None


def _earliest(*deadlines: float | None) -> float | None:
    """The soonest of several optional deadlines."""
    present = [deadline for deadline in deadlines if deadline is not None]
    return min(present) if present else None


async def _wait(
    signal: asyncio.Event,
    deadline: float | None,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Sleep until ``signal`` is set or ``deadline`` passes, whichever is first.

    With no deadline this waits indefinitely, which is the correct behaviour
    for a dashboard that repaints only on change: the loop should consume no
    CPU at all while the house is quiet.
    """
    if deadline is None:
        await signal.wait()
        return

    timeout = max(0.0, deadline - loop.time())
    if signal.is_set():
        # A change is already pending and the deadline is the rate limit
        # holding it back. Waiting on the event here would return instantly and
        # spin the loop until the limit expired; sleeping is the whole point.
        await asyncio.sleep(timeout)
        return

    try:
        await asyncio.wait_for(signal.wait(), timeout)
    except TimeoutError:
        # The deadline arrived first. An ordinary outcome, not a failure: it is
        # how the periodic repaint and the keep-alive fire.
        return
