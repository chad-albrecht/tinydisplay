"""Tests for the panel render loop.

The loop's whole reason for existing is the heartbeat, so most of these are
about *when* keep-alives go out rather than whether the function returns. The
interesting cases are the ones where naive scheduling would get it wrong: a
frame rate slower than the heartbeat, and a render that raises.

Everything runs against a recording transport with tiny intervals, so the suite
does not spend real seconds waiting for a panel that is not there.
"""

from __future__ import annotations

import logging

import pytest

from tinydisplay.core import Canvas, Color
from tinydisplay.ht32 import (
    DEFAULT_FPS,
    DEFAULT_HEARTBEAT_INTERVAL,
    HT32Driver,
    HT32Error,
    RecordingHidTransport,
    run_panel,
)
from tinydisplay.ht32.protocol import CHUNK_COUNT, Command, SubCommand

FAST = 1000.0
TINY = 0.001


def make_driver() -> tuple[HT32Driver, RecordingHidTransport]:
    transport = RecordingHidTransport()
    return HT32Driver(transport=transport, reconnect_delay=0.0), transport


def paint(canvas: Canvas) -> None:
    canvas.clear(Color.from_hex("#123456"))


def commands(transport: RecordingHidTransport, command: Command) -> list[bytes]:
    """Every packet carrying ``command``."""
    return [packet for packet in transport.packets if packet[2] == command]


class TestValidation:
    async def test_zero_fps_is_refused(self) -> None:
        driver, _ = make_driver()
        with pytest.raises(HT32Error, match="fps must be positive"):
            await run_panel(driver, paint, fps=0)

    async def test_negative_fps_is_refused(self) -> None:
        driver, _ = make_driver()
        with pytest.raises(HT32Error, match="fps must be positive"):
            await run_panel(driver, paint, fps=-1)

    async def test_a_non_positive_heartbeat_interval_is_refused(self) -> None:
        driver, _ = make_driver()
        with pytest.raises(HT32Error, match="heartbeat_interval must be positive"):
            await run_panel(driver, paint, heartbeat_interval=0)

    async def test_validation_happens_before_connecting(self) -> None:
        driver, transport = make_driver()
        with pytest.raises(HT32Error):
            await run_panel(driver, paint, fps=0)
        assert transport.open_count == 0


class TestFrames:
    async def test_draws_the_requested_number_of_frames(self) -> None:
        driver, _ = make_driver()
        drawn = await run_panel(driver, paint, fps=FAST, max_frames=3, heartbeat_interval=None)
        assert drawn == 3
        assert driver.frame_count == 3

    async def test_each_frame_is_a_full_chunk_run(self) -> None:
        driver, transport = make_driver()
        await run_panel(driver, paint, fps=FAST, max_frames=2, heartbeat_interval=None)
        assert len(commands(transport, Command.REDRAW)) == 2 * CHUNK_COUNT

    async def test_the_panel_receives_what_render_drew(self) -> None:
        driver, transport = make_driver()
        await run_panel(driver, paint, fps=FAST, max_frames=1, heartbeat_interval=None)

        expected = driver.create_canvas()
        paint(expected)
        rebuilt = b"".join(
            packet[9:] for packet in commands(transport, Command.REDRAW)[:CHUNK_COUNT]
        )
        assert rebuilt[: driver.frame_size] == driver.encode(expected)

    async def test_zero_frames_is_a_legal_request(self) -> None:
        driver, _ = make_driver()
        assert await run_panel(driver, paint, max_frames=0) == 0

    async def test_the_driver_is_disconnected_afterwards(self) -> None:
        driver, _ = make_driver()
        await run_panel(driver, paint, fps=FAST, max_frames=1, heartbeat_interval=None)
        assert not driver.is_connected


class TestOrientation:
    async def test_orientation_is_set_before_any_frame(self) -> None:
        driver, transport = make_driver()
        await run_panel(driver, paint, fps=FAST, max_frames=1, heartbeat_interval=None)

        first = transport.packets[0]
        assert first[2] == Command.CONFIG
        assert first[3] == SubCommand.ORIENTATION

    async def test_orientation_is_set_once_not_per_frame(self) -> None:
        driver, transport = make_driver()
        await run_panel(driver, paint, fps=FAST, max_frames=4, heartbeat_interval=None)

        orientation = [
            packet
            for packet in commands(transport, Command.CONFIG)
            if packet[3] == SubCommand.ORIENTATION
        ]
        assert len(orientation) == 1

    async def test_portrait_can_be_requested(self) -> None:
        driver, transport = make_driver()
        await run_panel(
            driver,
            paint,
            fps=FAST,
            max_frames=1,
            heartbeat_interval=None,
            landscape=False,
        )
        assert transport.packets[0][4] == 0x02


class TestHeartbeat:
    async def test_keep_alives_are_sent(self) -> None:
        driver, _ = make_driver()
        await run_panel(driver, paint, fps=FAST, heartbeat_interval=TINY, max_frames=5)
        assert driver.heartbeat_count > 0

    async def test_heartbeats_are_set_time_commands(self) -> None:
        driver, transport = make_driver()
        await run_panel(driver, paint, fps=FAST, heartbeat_interval=TINY, max_frames=5)

        beats = [
            packet
            for packet in commands(transport, Command.CONFIG)
            if packet[3] == SubCommand.SET_TIME
        ]
        assert beats
        assert all(packet[4] <= 23 for packet in beats)

    async def test_none_disables_them(self) -> None:
        driver, _ = make_driver()
        await run_panel(driver, paint, fps=FAST, max_frames=3, heartbeat_interval=None)
        assert driver.heartbeat_count == 0

    async def test_they_still_go_out_between_slow_frames(self) -> None:
        # The case naive scheduling gets wrong. At 20fps the gap between frames
        # is 50ms; sleeping that whole budget in one go would let zero
        # keep-alives out, and late is the same as absent as far as the
        # firmware's banner is concerned.
        #
        # The bar is "more than once", not a count: asyncio's timer resolution
        # is about 15ms on Windows against 1ms on Linux, so the actual number
        # varies by an order of magnitude across the machines this runs on.
        driver, _ = make_driver()
        await run_panel(driver, paint, fps=20, heartbeat_interval=TINY, max_frames=2)
        assert driver.heartbeat_count >= 2

    async def test_they_survive_a_broken_render(self) -> None:
        # The panel must keep being told the host is alive even while the
        # dashboard is throwing, or a code bug becomes a hardware-looking one.
        calls: list[int] = []

        def fail_then_work(canvas: Canvas) -> None:
            calls.append(1)
            if len(calls) <= 2:
                msg = "dashboard is broken"
                raise ValueError(msg)
            paint(canvas)

        driver, _ = make_driver()
        await run_panel(driver, fail_then_work, fps=20, heartbeat_interval=TINY, max_frames=1)

        assert driver.frame_count == 1
        assert driver.heartbeat_count >= 2


class TestRenderErrors:
    async def test_a_raising_render_does_not_stop_the_loop(self) -> None:
        calls: list[int] = []

        def sometimes(canvas: Canvas) -> None:
            calls.append(1)
            if len(calls) < 3:
                msg = "not yet"
                raise ValueError(msg)
            paint(canvas)

        driver, _ = make_driver()
        drawn = await run_panel(driver, sometimes, fps=FAST, max_frames=1, heartbeat_interval=None)

        assert drawn == 1
        assert len(calls) == 3

    async def test_a_failed_frame_is_not_counted_or_sent(self) -> None:
        state = {"calls": 0}

        def fail_once(canvas: Canvas) -> None:
            state["calls"] += 1
            if state["calls"] == 1:
                msg = "boom"
                raise ValueError(msg)
            paint(canvas)

        driver, transport = make_driver()
        await run_panel(driver, fail_once, fps=FAST, max_frames=1, heartbeat_interval=None)

        assert driver.frame_count == 1
        assert len(commands(transport, Command.REDRAW)) == CHUNK_COUNT

    async def test_repeated_errors_are_logged_once(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # A dashboard left broken would otherwise write a traceback per frame,
        # forever, which buries anything else in the log.
        calls: list[int] = []

        def always_fail(canvas: Canvas) -> None:
            calls.append(1)
            if len(calls) > 6:
                paint(canvas)
                return
            msg = "same every time"
            raise ValueError(msg)

        driver, _ = make_driver()
        with caplog.at_level(logging.WARNING, logger="tinydisplay.ht32.runner"):
            await run_panel(driver, always_fail, fps=FAST, max_frames=1, heartbeat_interval=None)

        warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
        assert len(warnings) == 1

    async def test_recovery_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        calls: list[int] = []

        def fail_then_work(canvas: Canvas) -> None:
            calls.append(1)
            if len(calls) == 1:
                msg = "boom"
                raise ValueError(msg)
            paint(canvas)

        driver, _ = make_driver()
        with caplog.at_level(logging.INFO, logger="tinydisplay.ht32.runner"):
            await run_panel(driver, fail_then_work, fps=FAST, max_frames=1, heartbeat_interval=None)

        assert any("recovered" in record.message for record in caplog.records)


class TestDefaults:
    def test_frame_rate_is_modest(self) -> None:
        # A frame is 27 USB transfers; a monitor's frame rate would saturate
        # the bus for no benefit on a status panel.
        assert 0 < DEFAULT_FPS <= 10

    def test_heartbeat_matches_the_firmware_expectation(self) -> None:
        assert DEFAULT_HEARTBEAT_INTERVAL == 1.0
