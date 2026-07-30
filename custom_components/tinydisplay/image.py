"""An image entity showing the last frame that reached the panel.

This is the answer to two questions that were previously only answerable by
reading container logs: *is it actually rendering?* and *what does the panel
look like right now?* Putting the frame on a Lovelace card answers both, from
a phone, without going near the machine.

It works on the preview driver too, which is the more useful half. A dashboard
can be written, previewed and corrected with no hardware attached at all --
the render loop draws exactly the same picture either way, because deciding
what to draw and deciding where to send it were separated three packages down.

The frame is encoded to PNG **when something asks for it**, not when it is
drawn. Most frames are never looked at, and a panel repainting every few
seconds should not be paying for a picture nobody has open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.image import ImageEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN

if TYPE_CHECKING:
    from datetime import datetime

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import TinyDisplayConfigEntry
    from .runtime import TinyDisplayRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TinyDisplayConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the panel preview."""
    async_add_entities([TinyDisplayPreview(hass, entry)])


class TinyDisplayPreview(ImageEntity):
    """The most recent frame, as a picture.

    Diagnostic rather than a primary entity: it reports on the integration
    rather than on the house, and someone who has not gone looking for it does
    not want it in their default dashboard.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "preview"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_content_type = "image/png"

    def __init__(self, hass: HomeAssistant, entry: TinyDisplayConfigEntry) -> None:
        super().__init__(hass)
        self._runtime: TinyDisplayRuntime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_preview"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="TinyDisplay",
            model=self._runtime.driver.name,
            sw_version=self._runtime.driver_version,
        )

    @property
    def image_last_updated(self) -> datetime | None:
        """When the frame on show was drawn.

        Home Assistant refetches the picture when this moves, so it is what
        makes the card follow the panel rather than a stale snapshot.
        """
        return self._runtime.last_frame_at

    async def async_image(self) -> bytes | None:
        """The last frame as a PNG, or ``None`` before the first one."""
        return await self.hass.async_add_executor_job(self._runtime.encode_last_frame)
