"""The light bar's effect speed, 1 to 5.

A separate entity because a light has no speed attribute. Its value is
restored by the light, which owns the state across restarts; this only shows
and changes it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from tinydisplay.core import TinyDisplayError
from tinydisplay.ht32.led import LEVEL_MAX, LEVEL_MIN

from .entity import device_info

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import TinyDisplayConfigEntry
    from .leds import TinyDisplayLeds


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TinyDisplayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the speed control, if this panel has a light bar."""
    del hass
    if entry.runtime_data.leds is not None:
        async_add_entities([TinyDisplayLightSpeed(entry)])


class TinyDisplayLightSpeed(NumberEntity):
    """How fast the light bar's effect runs."""

    _attr_has_entity_name = True
    _attr_translation_key = "light_bar_speed"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_assumed_state = True
    _attr_native_min_value = LEVEL_MIN
    _attr_native_max_value = LEVEL_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry: TinyDisplayConfigEntry) -> None:
        leds = entry.runtime_data.leds
        assert leds is not None
        self._leds: TinyDisplayLeds = leds
        self._attr_unique_id = f"{entry.entry_id}_light_bar_speed"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> int:
        return self._leds.speed

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._leds.add_listener(self.async_write_ha_state))

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self._leds.async_update(speed=round(value))
        except TinyDisplayError as exc:
            msg = f"could not set the light bar speed: {exc}"
            raise HomeAssistantError(msg) from exc
