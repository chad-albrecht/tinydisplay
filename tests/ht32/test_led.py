"""Tests for CH340 LED control.

The five-byte packet is small enough to assert in full, which is worth doing:
the levels are inverted on the wire and the checksum wraps, and both are the
kind of detail that works by accident until it does not.
"""

from __future__ import annotations

import pytest

from tinydisplay.ht32 import LedController, LedError, LedTheme, RecordingLedTransport
from tinydisplay.ht32.led import (
    DEFAULT_BAUD_RATE,
    LED_SIGNATURE,
    LEVEL_MAX,
    LEVEL_MIN,
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
