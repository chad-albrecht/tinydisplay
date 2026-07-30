"""The glue between Home Assistant and the TinyDisplay rendering stack.

Everything in this module is adapter code, and that is the point. The decisions
-- what a dashboard means, which entities it reads, when to repaint, what to
draw when a sensor drops out -- all live in ``tinydisplay.homeassistant``,
where they are covered by the test suite. What is left here is the part that
genuinely needs Home Assistant running: reading ``hass.states``, subscribing to
changes, and owning a task.

If this module starts making decisions, they belong one layer down.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from tinydisplay.core import Canvas, DisplayDriver, MemoryDriver
from tinydisplay.homeassistant import (
    Dashboard,
    DashboardConfigError,
    EntityState,
    run_dashboard,
)

from .const import (
    CONF_LANDSCAPE,
    CONF_MAX_INTERVAL,
    CONF_MIN_INTERVAL,
    DEFAULT_LANDSCAPE,
    DEFAULT_MAX_INTERVAL,
    DEFAULT_MIN_INTERVAL,
    DRIVER_HT32,
    MEMORY_PANEL_HEIGHT,
    MEMORY_PANEL_WIDTH,
    RELOAD_POLL_SECONDS,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from datetime import datetime

    from homeassistant.core import Event, HomeAssistant

_LOGGER = logging.getLogger(__name__)


class HassStateSource:
    """A :class:`~tinydisplay.homeassistant.StateSource` over ``hass.states``.

    A straight translation with no caching. ``hass.states.get`` is a dictionary
    lookup against the state machine, and a cache here would only add a way for
    the panel to show something Home Assistant no longer believes.
    """

    __slots__ = ("_hass",)

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def get(self, entity_id: str) -> EntityState | None:
        """The current state of ``entity_id``, or ``None`` if it does not exist."""
        state = self._hass.states.get(entity_id)
        if state is None:
            return None
        return EntityState(entity_id, state.state, state.attributes)


def create_driver(
    driver: str,
    *,
    serial_number: str | None = None,
) -> DisplayDriver:
    """Build the display driver named by a config entry.

    Blocking: selecting the HT32 transport looks at the USB bus. Call it from
    an executor, not the event loop.

    Raises:
        ValueError: If the driver name is not one this integration knows.
    """
    if driver == DRIVER_HT32:
        # Imported here rather than at module scope so that a Home Assistant
        # instance configured for the memory driver -- or one where the HT32
        # extra failed to install -- still loads this integration.
        from tinydisplay.ht32 import HT32Driver  # noqa: PLC0415

        return HT32Driver(serial_number=serial_number)

    return MemoryDriver(
        MEMORY_PANEL_WIDTH,
        MEMORY_PANEL_HEIGHT,
        name="TinyDisplay preview",
        # A render loop left running for a week must not accumulate a week of
        # frames; only the most recent one is ever of interest.
        max_frames=1,
    )


def keepalive_for(driver: DisplayDriver) -> Callable[[], Awaitable[None]] | None:
    """The keep-alive coroutine a driver needs, if it needs one.

    The HT32's firmware paints a disconnection banner over the screen when the
    host stops checking in. Detected by capability rather than by driver name,
    so a future panel with the same requirement needs no change here.
    """
    heartbeat = getattr(driver, "heartbeat", None)
    return heartbeat if callable(heartbeat) else None


def on_connect_for(
    driver: DisplayDriver,
    *,
    landscape: bool,
) -> Callable[[], Awaitable[None]] | None:
    """Per-panel setup to run once after connecting, if the driver has any."""
    set_orientation = getattr(driver, "set_orientation", None)
    if not callable(set_orientation):
        return None

    async def configure() -> None:
        await set_orientation(landscape=landscape)

    return configure


@dataclass(slots=True)
class TinyDisplayRuntime:
    """One panel: its dashboard, its driver, and the task drawing to it.

    Attributes:
        dashboard: What is being drawn.
        driver: Where it is being drawn.
        source: Where entity state is read from.
        options: The config entry's options, read for intervals and orientation.
    """

    dashboard: Dashboard
    driver: DisplayDriver
    source: HassStateSource
    options: Mapping[str, Any] = field(default_factory=dict)

    _changed: asyncio.Event = field(default_factory=asyncio.Event, init=False)
    _task: asyncio.Task[int] | None = field(default=None, init=False)
    _unsubscribe: Callable[[], None] | None = field(default=None, init=False)
    _last_canvas: Canvas | None = field(default=None, init=False)
    _last_frame_at: datetime | None = field(default=None, init=False)
    _stop_watching: Callable[[], None] | None = field(default=None, init=False)
    _last_reload_error: str | None = field(default=None, init=False)
    # Kept so the reload check can schedule its own executor work. Home
    # Assistant hands it to async_start rather than to the constructor, which
    # is why it is set there instead of being a field with a default.
    _hass: HomeAssistant | None = field(default=None, init=False)

    @property
    def is_running(self) -> bool:
        """Whether the render task is alive."""
        return self._task is not None and not self._task.done()

    @property
    def last_frame_at(self) -> datetime | None:
        """When the most recent frame reached the panel."""
        return self._last_frame_at

    @property
    def driver_version(self) -> str:
        """The panel's geometry, shown as the device's firmware line.

        Not a version of anything, strictly -- the panel reports none -- but
        `320x170 rgb565_be` is the fact someone opening the device page
        actually wants, and the field is otherwise empty.
        """
        driver = self.driver
        return f"{driver.width}x{driver.height} {driver.pixel_format.value}"

    def encode_last_frame(self) -> bytes | None:
        """The last frame as PNG bytes, or ``None`` before the first one.

        Blocking: PNG compression is real work. Encoding on demand rather than
        per frame means a panel nobody is watching pays nothing.
        """
        canvas = self._last_canvas
        return None if canvas is None else canvas.to_png_bytes()

    @callback
    def _note_frame(self, canvas: Canvas) -> None:
        """Remember the frame the panel was just given."""
        self._last_canvas = canvas
        self._last_frame_at = dt_util.utcnow()

    async def async_start(self, hass: HomeAssistant) -> None:
        """Subscribe to the dashboard's entities and start rendering."""
        self._hass = hass
        self._subscribe(hass)
        self._stop_watching = async_track_time_interval(
            hass,
            self._check_for_edits,
            timedelta(seconds=RELOAD_POLL_SECONDS),
        )
        self._task = hass.async_create_background_task(
            self._render_forever(),
            name=f"tinydisplay render {self.driver.name}",
        )

    def _subscribe(self, hass: HomeAssistant) -> None:
        """Watch exactly the entities the current dashboard reads.

        Called again after a hot reload, because an edited dashboard may name a
        different set -- and a subscription list left behind would mean the
        panel stopped following the very sensor that was just added.
        """
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

        entity_ids = sorted(self.dashboard.entity_ids)
        if entity_ids:
            self._unsubscribe = async_track_state_change_event(
                hass, entity_ids, self._handle_state_change
            )
        else:
            # Legal, and worth saying out loud: a dashboard of fixed text will
            # only ever be repainted by the periodic refresh, and someone
            # wondering why their panel is not updating should find this line.
            _LOGGER.info(
                "dashboard %s references no entities; it will repaint only periodically",
                self.dashboard.source_path or "<inline>",
            )

    async def _check_for_edits(self, now: datetime) -> None:
        """Re-read the dashboard if its file has changed, and repaint.

        This is what makes editing pleasant: save in the file editor and the
        panel follows, with no reload, no restart and no service call. It is
        the same bargain the simulator strikes with its hot reload, including
        that an edit which does not parse is logged and ignored rather than
        blanking the screen.
        """
        del now
        try:
            if self._hass is None:
                return
            changed = await self._hass.async_add_executor_job(self.dashboard.reload_if_changed)
        except DashboardConfigError as exc:
            message = str(exc)
            if message != self._last_reload_error:
                _LOGGER.warning("dashboard edit ignored, keeping the last good one: %s", message)
                self._last_reload_error = message
            return

        if self._last_reload_error is not None:
            _LOGGER.info("dashboard parses again: %s", self.dashboard.source_path)
            self._last_reload_error = None
        if changed:
            _LOGGER.info("reloaded %s", self.dashboard.source_path)
            self._subscribe(self._hass)
            self._changed.set()

    @callback
    def _handle_state_change(self, event: Event[Any]) -> None:
        """Wake the render loop. Called for every subscribed entity."""
        del event
        self._changed.set()

    async def _render_forever(self) -> None:
        """Run the render loop until the task is cancelled."""
        try:
            await run_dashboard(
                self.driver,
                self.dashboard,
                self.source,
                changed=self._changed,
                min_interval=float(self.options.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL)),
                max_interval=float(self.options.get(CONF_MAX_INTERVAL, DEFAULT_MAX_INTERVAL)),
                keepalive=keepalive_for(self.driver),
                on_connect=on_connect_for(
                    self.driver,
                    landscape=bool(self.options.get(CONF_LANDSCAPE, DEFAULT_LANDSCAPE)),
                ),
                on_frame=self._note_frame,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            # The loop already swallows dashboard errors; reaching here means
            # the panel itself failed. Log it with a traceback and stop -- Home
            # Assistant's reload is the recovery path, and a task that
            # relaunched itself would hide a dead panel behind a busy log.
            _LOGGER.exception("TinyDisplay render loop stopped")

    async def async_stop(self) -> None:
        """Unsubscribe and stop the render task.

        Safe to call on a runtime that never started, which is what makes the
        unload path in ``__init__`` unconditional.
        """
        if self._stop_watching is not None:
            self._stop_watching()
            self._stop_watching = None
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

        task = self._task
        self._task = None
        if task is None or task.done():
            return

        task.cancel()
        # The cancellation we just requested, so it is the expected outcome
        # rather than a failure. The driver is disconnected by run_dashboard's
        # async-with on the way out.
        with suppress(asyncio.CancelledError):
            await task
