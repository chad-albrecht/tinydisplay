"""Tests for CH340 LED control.

The five-byte packet is small enough to assert in full, which is worth doing:
the levels are inverted on the wire and the checksum wraps, and both are the
kind of detail that works by accident until it does not.
"""

from __future__ import annotations

import asyncio

import pytest

from tinydisplay.ht32 import LedController, LedError, LedTheme, RecordingLedTransport
from tinydisplay.ht32.led import (
    DEFAULT_BAUD_RATE,
    DEFAULT_HOLD_HZ,
    HELD_COLOURS,
    INTER_BYTE_DELAY,
    LED_PACKET_SIZE,
    LED_SIGNATURE,
    LEVEL_MAX,
    LEVEL_MIN,
    MAX_HOLD_HZ,
    LedTransport,
    SerialLedTransport,
    build_led_packet,
    led_packet_summary,
)


class TestPacket:
    def test_layout(self) -> None:
        packet = build_led_packet(LedTheme.RAINBOW, intensity=3, speed=3)
        assert len(packet) == 5
        assert packet[0] == LED_SIGNATURE
        assert packet[1] == LedTheme.RAINBOW

    def test_levels_are_inverted_on_the_wire(self) -> None:
        # A caller asks for "brightest"; the firmware counts the other way.
        packet = build_led_packet(LedTheme.COLORS, intensity=LEVEL_MAX, speed=LEVEL_MIN)
        assert packet[2] == 1
        assert packet[3] == 5

    def test_checksum_is_the_low_byte_of_the_sum(self) -> None:
        for intensity in range(LEVEL_MIN, LEVEL_MAX + 1):
            for speed in range(LEVEL_MIN, LEVEL_MAX + 1):
                packet = build_led_packet(LedTheme.AUTO, intensity=intensity, speed=speed)
                assert packet[4] == sum(packet[:4]) & 0xFF

    def test_checksum_wraps_rather_than_overflows(self) -> None:
        # 0xFA alone already exceeds a byte once anything is added to it.
        packet = build_led_packet(LedTheme.BREATHING, intensity=5, speed=1)
        assert sum(packet[:4]) > 0xFF
        assert packet[4] == 2

    @pytest.mark.parametrize("theme", list(LedTheme))
    def test_every_theme_builds(self, theme: LedTheme) -> None:
        assert build_led_packet(theme)[1] == theme

    @pytest.mark.parametrize("level", [0, 6, -1, 100])
    def test_out_of_range_intensity_is_rejected(self, level: int) -> None:
        with pytest.raises(LedError, match="intensity must be between"):
            build_led_packet(LedTheme.RAINBOW, intensity=level)

    @pytest.mark.parametrize("level", [0, 6, -1, 100])
    def test_out_of_range_speed_is_rejected(self, level: int) -> None:
        with pytest.raises(LedError, match="speed must be between"):
            build_led_packet(LedTheme.RAINBOW, speed=level)


class TestRecordingTransport:
    def test_satisfies_the_transport_protocol(self) -> None:
        assert isinstance(RecordingLedTransport(), LedTransport)

    def test_records_packets(self) -> None:
        transport = RecordingLedTransport()
        transport.open()
        transport.write(b"12345")
        assert transport.packets == (b"12345",)
        assert transport.last_packet == b"12345"

    def test_writing_while_closed_fails(self) -> None:
        with pytest.raises(LedError, match="not open"):
            RecordingLedTransport().write(b"12345")

    def test_open_is_idempotent(self) -> None:
        transport = RecordingLedTransport()
        transport.open()
        transport.open()
        assert transport.open_count == 1

    def test_fail_on_open_looks_like_an_absent_bridge(self) -> None:
        with pytest.raises(LedError):
            RecordingLedTransport(fail_on_open=True).open()


class TestController:
    async def test_set_theme_writes_one_packet(self) -> None:
        transport = RecordingLedTransport()
        async with LedController(transport=transport) as leds:
            await leds.set_theme(LedTheme.RAINBOW, intensity=4, speed=2)

        assert transport.packets == (build_led_packet(LedTheme.RAINBOW, intensity=4, speed=2),)

    async def test_theme_is_remembered(self) -> None:
        transport = RecordingLedTransport()
        controller = LedController(transport=transport)
        assert controller.theme is None

        async with controller as leds:
            await leds.set_theme(LedTheme.BREATHING)
        assert controller.theme is LedTheme.BREATHING

    async def test_off_sends_the_off_theme(self) -> None:
        transport = RecordingLedTransport()
        async with LedController(transport=transport) as leds:
            await leds.off()

        assert transport.last_packet is not None
        assert transport.last_packet[1] == LedTheme.OFF
        assert leds.theme is LedTheme.OFF

    async def test_a_rejected_level_writes_nothing(self) -> None:
        transport = RecordingLedTransport()
        async with LedController(transport=transport) as leds:
            with pytest.raises(LedError):
                await leds.set_theme(LedTheme.RAINBOW, intensity=9)

        assert transport.packets == ()
        assert leds.theme is None

    async def test_sending_opens_the_bridge_if_needed(self) -> None:
        # The LEDs are a separate device; a caller should not have to sequence
        # connect() against the panel's lifecycle to set a colour.
        transport = RecordingLedTransport()
        controller = LedController(transport=transport)
        await controller.set_theme(LedTheme.AUTO)

        assert transport.is_open
        assert len(transport.packets) == 1

    async def test_a_borrowed_transport_is_left_open(self) -> None:
        transport = RecordingLedTransport()
        async with LedController(transport=transport):
            pass
        assert transport.is_open

    def test_transport_is_exposed(self) -> None:
        transport = RecordingLedTransport()
        assert LedController(transport=transport).transport is transport


class TestSerialTransport:
    def test_satisfies_the_transport_protocol(self) -> None:
        # Constructing does not open the port, so this is safe with no bridge.
        assert isinstance(SerialLedTransport(port="COM_NOT_REAL"), LedTransport)

    def test_writing_while_closed_fails(self) -> None:
        with pytest.raises(LedError, match="not open"):
            SerialLedTransport(port="COM_NOT_REAL").write(b"12345")

    def test_closing_an_unopened_transport_is_safe(self) -> None:
        transport = SerialLedTransport(port="COM_NOT_REAL")
        transport.close()
        assert not transport.is_open

    def test_the_odd_baud_rate_is_the_default(self) -> None:
        assert DEFAULT_BAUD_RATE == 10_000


class TestSummary:
    def test_names_the_themes(self) -> None:
        packets = [build_led_packet(LedTheme.RAINBOW), build_led_packet(LedTheme.OFF)]
        assert led_packet_summary(packets) == "2 LED packets: rainbow, off"

    def test_handles_no_packets(self) -> None:
        assert led_packet_summary([]) == "0 LED packets"


class TestHoldTheme:
    """Holding an effect at its first frame, which is how a solid colour is made.

    The hardware has no colour command; every animated effect simply starts
    from a fixed colour, so restarting it faster than it advances pins it
    there. The technique comes from ``fsncps/acemagic-ledctl``.
    """

    async def test_writes_the_same_packet_repeatedly(self) -> None:
        transport = RecordingLedTransport()
        async with LedController(transport=transport) as leds:
            writes = await leds.hold_theme(LedTheme.COLORS, max_writes=5)

        assert writes == 5
        assert len(transport.packets) == 5
        # One packet, sent over and over -- the restart is the whole mechanism.
        assert len(set(transport.packets)) == 1
        assert transport.packets[0] == build_led_packet(
            LedTheme.COLORS, intensity=3, speed=LEVEL_MIN
        )

    async def test_the_held_theme_is_reported(self) -> None:
        transport = RecordingLedTransport()
        async with LedController(transport=transport) as leds:
            await leds.hold_theme(LedTheme.RAINBOW, max_writes=2)
            assert leds.theme is LedTheme.RAINBOW

    async def test_speed_defaults_to_the_slowest(self) -> None:
        # The point is to stop the animation, not to run it quickly.
        transport = RecordingLedTransport()
        async with LedController(transport=transport) as leds:
            await leds.hold_theme(LedTheme.COLORS, max_writes=1)
        assert transport.packets[0][3] == LEVEL_MAX + 1 - LEVEL_MIN

    async def test_intensity_is_honoured(self) -> None:
        transport = RecordingLedTransport()
        async with LedController(transport=transport) as leds:
            await leds.hold_theme(LedTheme.COLORS, intensity=5, max_writes=1)
        assert transport.packets[0] == build_led_packet(
            LedTheme.COLORS, intensity=5, speed=LEVEL_MIN
        )

    async def test_it_runs_at_about_the_requested_rate(self) -> None:
        transport = RecordingLedTransport()
        loop = asyncio.get_running_loop()
        started = loop.time()
        async with LedController(transport=transport) as leds:
            await leds.hold_theme(LedTheme.COLORS, hz=50.0, max_writes=4)
        # Three intervals between four writes, and the loop must not sleep
        # after the last one.
        assert loop.time() - started >= 3 / 50.0

    async def test_it_can_be_cancelled(self) -> None:
        # The colour lasts as long as the loop does, so cancelling is how a
        # caller stops -- there is no other exit when max_writes is None.
        transport = RecordingLedTransport()
        async with LedController(transport=transport) as leds:
            task = asyncio.create_task(leds.hold_theme(LedTheme.COLORS))
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert transport.packets

    @pytest.mark.parametrize("hz", [0.0, -1.0])
    async def test_a_non_positive_rate_is_rejected(self, hz: float) -> None:
        async with LedController(transport=RecordingLedTransport()) as leds:
            with pytest.raises(LedError, match="hz must be positive"):
                await leds.hold_theme(LedTheme.COLORS, hz=hz)

    async def test_a_rate_beyond_the_link_is_rejected(self) -> None:
        # Five bytes with INTER_BYTE_DELAY between them is 20 ms, so the bridge
        # cannot take more than fifty packets a second however hard we ask.
        async with LedController(transport=RecordingLedTransport()) as leds:
            with pytest.raises(LedError, match="must not exceed"):
                await leds.hold_theme(LedTheme.COLORS, hz=MAX_HOLD_HZ + 1)

    async def test_the_ceiling_itself_is_allowed(self) -> None:
        transport = RecordingLedTransport()
        async with LedController(transport=transport) as leds:
            assert await leds.hold_theme(LedTheme.COLORS, hz=MAX_HOLD_HZ, max_writes=1) == 1

    async def test_a_bad_level_still_raises(self) -> None:
        async with LedController(transport=RecordingLedTransport()) as leds:
            with pytest.raises(LedError, match="intensity"):
                await leds.hold_theme(LedTheme.COLORS, intensity=9, max_writes=1)

    def test_the_default_rate_is_under_the_ceiling(self) -> None:
        # A little headroom, so a slow moment does not become a visible flicker.
        assert DEFAULT_HOLD_HZ < MAX_HOLD_HZ

    def test_the_ceiling_matches_the_pacing(self) -> None:
        assert pytest.approx(1.0 / ((LED_PACKET_SIZE - 1) * INTER_BYTE_DELAY)) == MAX_HOLD_HZ
        assert len(build_led_packet(LedTheme.OFF)) == LED_PACKET_SIZE

    @pytest.mark.parametrize("theme", sorted(HELD_COLOURS))
    def test_only_animated_effects_are_listed_as_holdable(self, theme: LedTheme) -> None:
        # OFF and AUTO have no first frame to hold, and BREATHING's is a pulse
        # rather than a colour.
        assert theme in {LedTheme.COLORS, LedTheme.RAINBOW}
        assert HELD_COLOURS[theme]
