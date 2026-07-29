"""Tests for the HT32 driver.

These run against RecordingHidTransport, which satisfies the same protocol as
the USB transport. The driver cannot tell the difference, so the framing, the
lifecycle and the reconnection logic are all exercised with nothing attached --
and the assertions are about the exact bytes that would have gone out.
"""

from __future__ import annotations

import pytest

from tinydisplay.core import Canvas, Color, DisplayDriver, DriverError, PixelFormat
from tinydisplay.core.errors import DriverNotConnectedError
from tinydisplay.ht32 import (
    HT32Driver,
    HT32Error,
    RecordingHidTransport,
    TransportError,
)
from tinydisplay.ht32.protocol import (
    CHUNK_COUNT,
    CHUNK_SIZES,
    HEADER_SIZE,
    PANEL_HEIGHT,
    PANEL_WIDTH,
    REPORT_SIZE,
    Command,
)

DATA_START = REPORT_SIZE + HEADER_SIZE

# The driver retries with a delay; tests should not pay for it in wall clock.
NO_DELAY = 0.0


def make_driver() -> tuple[HT32Driver, RecordingHidTransport]:
    transport = RecordingHidTransport()
    return HT32Driver(transport=transport, reconnect_delay=NO_DELAY), transport


async def make_unreachable_driver(attempts: int) -> tuple[HT32Driver, RecordingHidTransport]:
    """A connected driver whose panel has since gone away for good.

    The panel is pulled *after* connecting, because a driver that cannot
    connect at all fails in :meth:`connect` and never reaches the retry path
    this is meant to exercise.
    """
    transport = RecordingHidTransport()
    driver = HT32Driver(
        transport=transport,
        reconnect_delay=NO_DELAY,
        reconnect_attempts=attempts,
    )
    await driver.connect()
    transport.close()
    transport.fail_on_open = True
    return driver, transport


def reassemble(transport: RecordingHidTransport, *, start: int = 0) -> bytes:
    """Rebuild the frame the panel would have received from recorded packets."""
    frame = bytearray()
    for index, packet in enumerate(transport.packets[start : start + CHUNK_COUNT]):
        frame += packet[DATA_START : DATA_START + CHUNK_SIZES[index]]
    return bytes(frame)


class TestConstruction:
    def test_is_a_display_driver(self) -> None:
        driver, _ = make_driver()
        assert isinstance(driver, DisplayDriver)

    def test_geometry_is_the_panel_and_not_negotiable(self) -> None:
        driver, _ = make_driver()
        assert driver.size.width == PANEL_WIDTH
        assert driver.size.height == PANEL_HEIGHT
        assert driver.pixel_format is PixelFormat.RGB565_BE

    def test_frame_size_matches_the_protocol(self) -> None:
        driver, _ = make_driver()
        assert driver.frame_size == PANEL_WIDTH * PANEL_HEIGHT * 2

    def test_counters_start_at_zero(self) -> None:
        driver, _ = make_driver()
        assert driver.frame_count == 0
        assert driver.reconnect_count == 0
        assert driver.failure_count == 0

    def test_default_name_identifies_the_panel(self) -> None:
        driver, _ = make_driver()
        assert driver.name == "HT32"

    def test_negative_reconnect_attempts_are_rejected(self) -> None:
        with pytest.raises(HT32Error, match="must not be negative"):
            HT32Driver(transport=RecordingHidTransport(), reconnect_attempts=-1)


class TestLifecycle:
    async def test_connect_opens_the_transport(self) -> None:
        driver, transport = make_driver()
        assert transport.open_count == 0

        await driver.connect()
        assert transport.is_open
        assert driver.is_connected
        assert transport.open_count == 1

    async def test_connect_is_idempotent(self) -> None:
        driver, transport = make_driver()
        await driver.connect()
        await driver.connect()
        assert transport.open_count == 1

    async def test_a_borrowed_transport_is_left_open(self) -> None:
        # The caller owns a transport they passed in -- a supervisor that
        # reconnects the driver should not find it destroyed.
        driver, transport = make_driver()
        await driver.connect()
        await driver.disconnect()
        assert transport.is_open
        assert not driver.is_connected

    async def test_context_manager_connects_and_disconnects(self) -> None:
        driver, _ = make_driver()
        async with driver:
            assert driver.is_connected
        assert not driver.is_connected

    async def test_show_before_connect_is_refused(self) -> None:
        driver, transport = make_driver()
        with pytest.raises(DriverNotConnectedError):
            await driver.show(driver.create_canvas())
        assert transport.packets == ()

    async def test_refresh_before_connect_is_refused(self) -> None:
        driver, _ = make_driver()
        with pytest.raises(DriverNotConnectedError):
            await driver.refresh()


class TestShow:
    async def test_one_frame_is_one_chunk_run(self) -> None:
        driver, transport = make_driver()
        async with driver:
            await driver.show(driver.create_canvas())

        assert len(transport.packets) == CHUNK_COUNT
        assert driver.frame_count == 1

    async def test_packets_are_all_redraws_in_sequence(self) -> None:
        driver, transport = make_driver()
        async with driver:
            await driver.show(driver.create_canvas())

        assert {packet[2] for packet in transport.packets} == {Command.REDRAW}
        assert [packet[4] for packet in transport.packets] == list(range(1, CHUNK_COUNT + 1))

    async def test_the_panel_receives_exactly_the_encoded_canvas(self) -> None:
        driver, transport = make_driver()
        canvas = driver.create_canvas()
        canvas.clear(Color.from_hex("#3366cc"))

        async with driver:
            await driver.show(canvas)

        assert reassemble(transport) == driver.encode(canvas)

    async def test_encoding_is_big_endian_on_the_wire(self) -> None:
        driver, transport = make_driver()
        canvas = driver.create_canvas()
        canvas.clear(Color.from_hex("#ff0000"))

        async with driver:
            await driver.show(canvas)

        assert reassemble(transport) == canvas.to_rgb565(byte_order="big")

    async def test_a_wrong_sized_canvas_is_refused_before_any_write(self) -> None:
        driver, transport = make_driver()
        async with driver:
            with pytest.raises(DriverError, match="expects a 320x170 canvas"):
                await driver.show(Canvas(64, 64))
        assert transport.packets == ()

    async def test_successive_frames_accumulate(self) -> None:
        driver, transport = make_driver()
        async with driver:
            await driver.show(driver.create_canvas())
            await driver.show(driver.create_canvas())

        assert driver.frame_count == 2
        assert len(transport.packets) == 2 * CHUNK_COUNT


class TestRefresh:
    async def test_refresh_sends_one_command(self) -> None:
        driver, transport = make_driver()
        async with driver:
            await driver.refresh()

        assert len(transport.packets) == 1
        assert transport.packets[0][2] == Command.REFRESH

    async def test_refresh_does_not_count_as_a_frame(self) -> None:
        driver, _ = make_driver()
        async with driver:
            await driver.refresh()
        assert driver.frame_count == 0


class TestReconnection:
    async def test_a_mid_frame_failure_is_retried_from_the_start(self) -> None:
        transport = RecordingHidTransport(fail_after=5)
        driver = HT32Driver(transport=transport, reconnect_delay=NO_DELAY)

        async with driver:
            await driver.show(driver.create_canvas())

        # Five packets went out before the panel vanished, then the whole frame
        # was written again: a panel that saw half a frame cannot be continued.
        assert len(transport.packets) == 5 + CHUNK_COUNT
        assert driver.reconnect_count == 1
        assert driver.failure_count == 1
        assert driver.frame_count == 1

    async def test_the_retried_frame_is_complete_and_correct(self) -> None:
        transport = RecordingHidTransport(fail_after=5)
        driver = HT32Driver(transport=transport, reconnect_delay=NO_DELAY)
        canvas = driver.create_canvas()
        canvas.clear(Color.from_hex("#00ff88"))

        async with driver:
            await driver.show(canvas)

        assert reassemble(transport, start=5) == driver.encode(canvas)

    async def test_reconnection_reopens_the_transport(self) -> None:
        transport = RecordingHidTransport(fail_after=1)
        driver = HT32Driver(transport=transport, reconnect_delay=NO_DELAY)

        async with driver:
            await driver.show(driver.create_canvas())

        assert transport.open_count == 2

    async def test_giving_up_reports_how_many_attempts_were_made(self) -> None:
        driver, _ = await make_unreachable_driver(attempts=2)

        with pytest.raises(TransportError, match="after 3 attempt"):
            await driver.show(driver.create_canvas())

    async def test_failures_are_counted_per_attempt(self) -> None:
        driver, _ = await make_unreachable_driver(attempts=2)

        with pytest.raises(TransportError):
            await driver.show(driver.create_canvas())
        assert driver.failure_count == 3

    async def test_auto_reconnect_off_surfaces_the_failure_immediately(self) -> None:
        transport = RecordingHidTransport(fail_after=3)
        driver = HT32Driver(transport=transport, auto_reconnect=False)

        async with driver:
            with pytest.raises(TransportError, match="after 1 attempt"):
                await driver.show(driver.create_canvas())

        assert driver.reconnect_count == 0
        assert len(transport.packets) == 3

    async def test_auto_reconnect_off_does_not_reopen_a_closed_transport(self) -> None:
        transport = RecordingHidTransport()
        driver = HT32Driver(transport=transport, auto_reconnect=False)
        await driver.connect()
        transport.close()

        with pytest.raises(TransportError, match="auto_reconnect is off"):
            await driver.show(driver.create_canvas())
        assert transport.open_count == 1

    async def test_a_failed_frame_does_not_count_as_shown(self) -> None:
        driver, _ = await make_unreachable_driver(attempts=0)

        with pytest.raises(TransportError):
            await driver.show(driver.create_canvas())
        assert driver.frame_count == 0


class TestIntrospection:
    def test_transport_is_exposed(self) -> None:
        driver, transport = make_driver()
        assert driver.transport is transport

    def test_auto_reconnect_is_reported(self) -> None:
        driver, _ = make_driver()
        assert driver.auto_reconnect is True

    def test_repr_states_the_connection_state(self) -> None:
        driver, _ = make_driver()
        assert "320x170" in repr(driver)
        assert "disconnected" in repr(driver)
