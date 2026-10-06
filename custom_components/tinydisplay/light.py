"""The S1's RGB light bar as a light entity.

On and off, an effect, and brightness. The bar has five intensity levels and
Home Assistant has 255, so brightness is quantised to the nearest level and
reported back as that level -- a slider that snaps is honest about the
hardware, where one that kept 200 would claim a precision nothing delivers.

There is no colour: the bar's microcontroller owns its colours, and the only
thing it can be told is which of its effects to run.

The bar cannot be read, so the state is assumed and restored across restarts,
then sent again. That is what makes "off" survive a power cut: the bar wakes
up running its firmware default, and Home Assistant puts it back.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import STATE_ON
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity

from tinydisplay.core import TinyDisplayError

from .entity import device_info
from .leds import EFFECTS, brightness_to_level, level_to_brightness

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import TinyDisplayConfigEntry
    from .leds import TinyDisplayLeds

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TinyDisplayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add the light bar, if this panel has one."""
    del hass
    if entry.runtime_data.leds is not None:
        async_add_entities([TinyDisplayLight(entry)])


class _SpeedData(ExtraStoredData):
    """Speed has no light attribute, so it is restored alongside the light."""

    def __init__(self, speed: int) -> None:
        self.speed = speed

    def as_dict(self) -> dict[str, Any]:
        return {"speed": self.speed}


class TinyDisplayLight(LightEntity, RestoreEntity):
    """The light bar."""

    _attr_has_entity_name = True
    _attr_translation_key = "light_bar"
    _attr_assumed_state = True
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}  # noqa: RUF012
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = list(EFFECTS)  # noqa: RUF012

    def __init__(self, entry: TinyDisplayConfigEntry) -> None:
        leds = entry.runtime_data.leds
        assert leds is not None
        self._leds: TinyDisplayLeds = leds
        self._attr_unique_id = f"{entry.entry_id}_light_bar"
        self._attr_device_info = device_info(entry)

    @property
    def is_on(self) -> bool:
        return self._leds.is_on

    @property
    def brightness(self) -> int:
        return level_to_brightness(self._leds.intensity)

    @property
    def effect(self) -> str:
        return self._leds.effect

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return _SpeedData(self._leds.speed)

    async def async_added_to_hass(self) -> None:
        """Restore the last state and send it, then follow changes."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            extra = await self.async_get_last_extra_data()
            brightness = last.attributes.get(ATTR_BRIGHTNESS)
            self._leds.restore(
                is_on=last.state == STATE_ON,
                effect=last.attributes.get(ATTR_EFFECT),
                intensity=brightness_to_level(brightness) if brightness else None,
                speed=extra.as_dict().get("speed") if extra is not None else None,
            )
            try:
                await self._leds.async_apply()
            except TinyDisplayError as exc:
                # Not fatal: the entity still works once the bridge answers.
                _LOGGER.warning("could not restore the light bar: %s", exc)
        self.async_on_remove(self._leds.add_listener(self.async_write_ha_state))

    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        await self._update(
            is_on=True,
            effect=kwargs.get(ATTR_EFFECT),
            intensity=brightness_to_level(brightness) if brightness is not None else None,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        del kwargs
        await self._update(is_on=False)

    async def _update(self, **changes: Any) -> None:
        try:
            await self._leds.async_update(**changes)
        except TinyDisplayError as exc:
            msg = f"could not set the light bar: {exc}"
            raise HomeAssistantError(msg) from exc
