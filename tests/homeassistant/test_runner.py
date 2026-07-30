"""Tests for the change-driven render loop.

Everything here runs in-process against a :class:`MemoryDriver`, with intervals
measured in tens of milliseconds so that the timing behaviour is exercised for
real rather than mocked. The loop's contract is small and worth stating:

- the first frame is drawn immediately, not at the first deadline;
- a keep-alive, if there is one, goes out before that first frame;
- repaints are bounded below by ``min_interval`` and above by ``max_interval``;
- a dashboard that raises costs a frame, not the loop.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from tinydisplay.core import Canvas, MemoryDriver
from tinydisplay.homeassistant import (
    Dashboard,
    HomeAssistantError,
    StaticStateSource,
    run_dashboard,
)

if TYPE_CHECKING:
    from tinydisplay.homeassistant import StateSource

#: Short enough to keep the suite quick, long enough that the event loop can
#: tell the deadlines apart on a loaded CI machine.
TICK = 0.02


@pytest.fixture
def dashboard() -> Dashboard:
    return Dashboard.from_yaml(
        """
        root:
          type: label
          text: "{{ sensor.a }}"
        """
    )


@pytest.fixture
def source() -> StaticStateSource:
    return StaticStateSource({"sensor.a": "1"})


@pytest.fixture
def driver() -> MemoryDriver:
    return MemoryDriver(32, 16, name="test", max_frames=None)


class Recorder:
    """Counts keep-alives and connection hooks, in order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def keepalive(self) -> None:
        self.events.append("keepalive")

    async def on_connect(self) -> None:
        self.events.append("on_connect")

    def note_frame(self) -> None:
        self.events.append("frame")


class ExplodingDashboard:
    """A dashboard that fails a fixed number of times, then works.

    Not a mock of :class:`Dashboard` -- it satisfies the one method the loop
    calls, which is the whole surface the loop depends on.
    """

    def __init__(self, failures: int) -> None:
        self.remaining = failures
        self.renders = 0

    def render(self, canvas: Canvas, source: StateSource) -> None:
        del canvas, source
        self.renders += 1
        if self.remaining > 0:
            self.remaining -= 1
            message = "sensor exploded"
            raise RuntimeError(message)


class TestFirstFrame:
    async def test_draws_immediately(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        # An integration that has just started should put something on the
        # panel now, not at the first deadline.
        frames = await run_dashboard(driver, dashboard, source, max_frames=1)
        assert frames == 1
        assert len(driver.frames) == 1

    async def test_connects_and_disconnects(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        await run_dashboard(driver, dashboard, source, max_frames=1)
        assert driver.connect_count == 1
        assert not driver.is_connected

    async def test_accepts_a_driver_that_is_already_connected(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        # The integration connects during setup, so that an unreachable panel
        # fails the config entry instead of killing a background task behind an
        # entry that reports itself healthy. The loop still owns the disconnect.
        await driver.connect()
        frames = await run_dashboard(driver, dashboard, source, max_frames=1)
        assert frames == 1
        assert driver.connect_count == 1
        assert not driver.is_connected

    async def test_disconnects_even_when_the_loop_raises(
        self, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        class FailingDriver(MemoryDriver):
            async def _write(self, frame: bytes) -> None:
                del frame
                message = "cable fell out"
                raise OSError(message)

        driver = FailingDriver(32, 16)
        with pytest.raises(OSError, match="cable fell out"):
            await run_dashboard(driver, dashboard, source, max_frames=1)
        assert not driver.is_connected


class TestKeepalive:
    async def test_goes_out_before_the_first_frame(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        # A panel that paints its own disconnection banner starts every session
        # with that banner up, so the first keep-alive is an introduction.
        recorder = Recorder()
        await run_dashboard(
            driver,
            dashboard,
            source,
            keepalive=recorder.keepalive,
            keepalive_interval=10.0,
            max_frames=1,
        )
        assert recorder.events == ["keepalive"]

    async def test_on_connect_runs_after_the_keepalive(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        recorder = Recorder()
        await run_dashboard(
            driver,
            dashboard,
            source,
            keepalive=recorder.keepalive,
            on_connect=recorder.on_connect,
            keepalive_interval=10.0,
            max_frames=1,
        )
        assert recorder.events == ["keepalive", "on_connect"]

    async def test_repeats_on_its_own_schedule(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        recorder = Recorder()
        await run_dashboard(
            driver,
            dashboard,
            source,
            keepalive=recorder.keepalive,
            keepalive_interval=TICK,
            min_interval=TICK * 10,
            max_interval=TICK * 10,
            max_frames=2,
        )
        # The frames are spaced ten ticks apart, so several keep-alives have to
        # fit between them: a loop that slept a whole frame budget would starve
        # them, which is the bug this asserts against.
        assert recorder.events.count("keepalive") > 2

    async def test_absent_by_default(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        # A driver that needs no keep-alive must not be sent one.
        frames = await run_dashboard(driver, dashboard, source, max_frames=1)
        assert frames == 1


class TestChangeDriven:
    async def test_repaints_when_the_signal_is_set(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        changed = asyncio.Event()
        task = asyncio.create_task(
            run_dashboard(
                driver,
                dashboard,
                source,
                changed=changed,
                min_interval=0.0,
                max_interval=None,
                max_frames=2,
            )
        )
        # The first frame is unconditional; the second needs the signal.
        await asyncio.sleep(TICK)
        assert len(driver.frames) == 1

        changed.set()
        assert await asyncio.wait_for(task, timeout=1.0) == 2

    async def test_waits_indefinitely_with_nothing_to_do(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        # With no ceiling and no change, the loop should consume nothing at all
        # rather than spinning on a deadline it keeps recomputing.
        task = asyncio.create_task(
            run_dashboard(
                driver, dashboard, source, min_interval=0.0, max_interval=None, max_frames=5
            )
        )
        await asyncio.sleep(TICK * 5)
        assert len(driver.frames) == 1
        assert not task.done()

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_min_interval_coalesces_a_burst(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        # A light group turning on emits a dozen state changes in a few
        # milliseconds; they must become one frame, not a dozen.
        changed = asyncio.Event()
        loop = asyncio.get_running_loop()
        started = loop.time()

        task = asyncio.create_task(
            run_dashboard(
                driver,
                dashboard,
                source,
                changed=changed,
                min_interval=TICK * 5,
                max_interval=None,
                max_frames=2,
            )
        )
        await asyncio.sleep(TICK)
        for _ in range(12):
            changed.set()

        assert await asyncio.wait_for(task, timeout=2.0) == 2
        assert loop.time() - started >= TICK * 5

    async def test_max_interval_repaints_without_any_change(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        frames = await run_dashboard(
            driver,
            dashboard,
            source,
            min_interval=0.0,
            max_interval=TICK,
            max_frames=3,
        )
        assert frames == 3

    async def test_state_reaches_the_panel(
        self, driver: MemoryDriver, dashboard: Dashboard
    ) -> None:
        states = StaticStateSource({"sensor.a": "1"})
        changed = asyncio.Event()
        task = asyncio.create_task(
            run_dashboard(
                driver,
                dashboard,
                states,
                changed=changed,
                min_interval=0.0,
                max_interval=None,
                max_frames=2,
            )
        )
        await asyncio.sleep(TICK)
        states.set("sensor.a", "999")
        changed.set()
        await asyncio.wait_for(task, timeout=1.0)

        # The two frames differ, which is the end-to-end proof that a state
        # change becomes different pixels.
        assert driver.frames[0] != driver.frames[1]


class TestErrorHandling:
    async def test_a_failing_render_costs_a_frame_not_the_loop(
        self, driver: MemoryDriver, source: StaticStateSource
    ) -> None:
        broken = ExplodingDashboard(failures=2)
        frames = await run_dashboard(
            driver,
            broken,  # type: ignore[arg-type]
            source,
            min_interval=0.0,
            max_interval=TICK,
            max_frames=1,
        )
        assert frames == 1
        assert broken.renders == 3
        assert len(driver.frames) == 1

    async def test_a_failing_render_does_not_stop_the_keepalive(
        self, driver: MemoryDriver, source: StaticStateSource
    ) -> None:
        # The panel going dark because a dashboard has a bug is exactly the
        # failure the keep-alive exists to prevent.
        recorder = Recorder()
        broken = ExplodingDashboard(failures=3)
        await run_dashboard(
            driver,
            broken,  # type: ignore[arg-type]
            source,
            keepalive=recorder.keepalive,
            keepalive_interval=TICK,
            min_interval=0.0,
            max_interval=TICK,
            max_frames=1,
        )
        assert recorder.events.count("keepalive") >= 2

    async def test_repeated_errors_are_logged_once(
        self,
        driver: MemoryDriver,
        source: StaticStateSource,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A dashboard left broken must not write a traceback per repaint into
        # Home Assistant's log forever.
        broken = ExplodingDashboard(failures=4)
        with caplog.at_level("WARNING", logger="tinydisplay.homeassistant.runner"):
            await run_dashboard(
                driver,
                broken,  # type: ignore[arg-type]
                source,
                min_interval=0.0,
                max_interval=TICK,
                max_frames=1,
            )
        warnings = [record for record in caplog.records if record.levelname == "WARNING"]
        assert len(warnings) == 1
        assert "sensor exploded" in warnings[0].message

    async def test_recovery_is_logged(
        self,
        driver: MemoryDriver,
        source: StaticStateSource,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        broken = ExplodingDashboard(failures=1)
        with caplog.at_level("INFO", logger="tinydisplay.homeassistant.runner"):
            await run_dashboard(
                driver,
                broken,  # type: ignore[arg-type]
                source,
                min_interval=0.0,
                max_interval=TICK,
                max_frames=1,
            )
        assert any("recovered" in record.message for record in caplog.records)


class TestValidation:
    @pytest.mark.parametrize(
        ("kwargs", "message"),
        [
            ({"min_interval": -1.0}, "min_interval must not be negative"),
            ({"max_interval": 0.0}, "max_interval must be positive"),
            ({"min_interval": 5.0, "max_interval": 1.0}, "below min_interval"),
            ({"keepalive_interval": 0.0}, "keepalive_interval must be positive"),
        ],
    )
    async def test_rejects_impossible_intervals(
        self,
        driver: MemoryDriver,
        dashboard: Dashboard,
        source: StaticStateSource,
        kwargs: dict[str, Any],
        message: str,
    ) -> None:
        with pytest.raises(HomeAssistantError, match=message):
            await run_dashboard(driver, dashboard, source, max_frames=1, **kwargs)

    async def test_validation_happens_before_connecting(
        self, driver: MemoryDriver, dashboard: Dashboard, source: StaticStateSource
    ) -> None:
        with pytest.raises(HomeAssistantError):
            await run_dashboard(driver, dashboard, source, min_interval=-1.0)
        assert driver.connect_count == 0
