"""The light bar's state holder, run against a recording bridge.

``leds.py`` imports no Home Assistant, so it is loaded straight from the
component directory like ``const.py`` is, and driven with the library's
:class:`RecordingLedTransport` instead of a serial port.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from tinydisplay.ht32 import (
    LedController,
    LedError,
    LedTheme,
    RecordingLedTransport,
    build_led_packet,
)

COMPONENT = Path(__file__).resolve().parents[2] / "custom_components" / "tinydisplay"


@pytest.fixture(scope="module")
def leds_module() -> Any:
    spec = importlib.util.spec_from_file_location("_tinydisplay_leds", COMPONENT / "leds.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def transport() -> RecordingLedTransport:
    return RecordingLedTransport()


@pytest.fixture
def leds(leds_module: Any, transport: RecordingLedTransport) -> Any:
    return leds_module.TinyDisplayLeds(LedController(transport=transport))


class TestBrightness:
    @pytest.mark.parametrize(
        ("brightness", "level"),
        [(1, 1), (25, 1), (51, 1), (102, 2), (128, 3), (153, 3), (204, 4), (230, 5), (255, 5)],
    )
    def test_brightness_snaps_to_the_nearest_level(
        self, leds_module: Any, brightness: int, level: int
    ) -> None:
        assert leds_module.brightness_to_level(brightness) == level

    def test_every_level_round_trips(self, leds_module: Any) -> None:
        for level in range(1, 6):
            shown = leds_module.level_to_brightness(level)
            assert leds_module.brightness_to_level(shown) == level
        assert leds_module.level_to_brightness(5) == 255


class TestEffects:
    def test_off_is_not_an_effect(self, leds_module: Any) -> None:
        assert LedTheme.OFF not in leds_module.EFFECTS.values()

    def test_every_other_theme_is_offered(self, leds_module: Any) -> None:
        assert set(leds_module.EFFECTS.values()) == set(LedTheme) - {LedTheme.OFF}


class TestUpdate:
    async def test_a_change_sends_the_whole_state(
        self, leds: Any, transport: RecordingLedTransport
    ) -> None:
        await leds.async_update(effect="Breathing", intensity=5, speed=2)
        assert transport.last_packet == build_led_packet(LedTheme.BREATHING, intensity=5, speed=2)

    async def test_a_partial_change_keeps_the_rest(
        self, leds: Any, transport: RecordingLedTransport
    ) -> None:
        await leds.async_update(effect="Colors", intensity=4, speed=1)
        await leds.async_update(speed=5)
        assert transport.last_packet == build_led_packet(LedTheme.COLORS, intensity=4, speed=5)

    async def test_off_sends_off_and_on_restores_the_effect(
        self, leds: Any, transport: RecordingLedTransport
    ) -> None:
        await leds.async_update(effect="Auto", intensity=2, speed=3)
        await leds.async_update(is_on=False)
        assert transport.last_packet is not None
        assert transport.last_packet[1] == LedTheme.OFF
        await leds.async_update(is_on=True)
        assert transport.last_packet == build_led_packet(LedTheme.AUTO, intensity=2, speed=3)

    async def test_listeners_hear_about_changes_until_removed(self, leds: Any) -> None:
        calls: list[None] = []
        remove = leds.add_listener(lambda: calls.append(None))
        await leds.async_update(speed=4)
        remove()
        await leds.async_update(speed=5)
        assert len(calls) == 1


class TestRestore:
    async def test_restore_sends_nothing_until_applied(
        self, leds: Any, transport: RecordingLedTransport
    ) -> None:
        leds.restore(is_on=False, effect="Breathing", intensity=2, speed=4)
        assert transport.packets == ()
        await leds.async_apply()
        assert transport.last_packet is not None
        assert transport.last_packet[1] == LedTheme.OFF
        assert (leds.effect, leds.intensity, leds.speed) == ("Breathing", 2, 4)

    def test_restore_ignores_values_it_cannot_send(self, leds: Any) -> None:
        leds.restore(is_on=True, effect="Strobe", intensity=9, speed=0)
        assert (leds.effect, leds.intensity, leds.speed) == ("Rainbow", 3, 3)


class TestOpening:
    async def test_no_bridge_means_no_light_rather_than_a_failure(
        self, leds_module: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def missing() -> str:
            msg = "no CH340 LED bridge found"
            raise LedError(msg)

        monkeypatch.setattr(leds_module, "find_led_port", missing)
        assert await leds_module.async_open_leds() is None
