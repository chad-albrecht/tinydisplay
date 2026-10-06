"""What every entity of one panel shares: the device it belongs to."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceInfo

from .const import DOMAIN

if TYPE_CHECKING:
    from . import TinyDisplayConfigEntry


def device_info(entry: TinyDisplayConfigEntry) -> DeviceInfo:
    """The panel's device, so the preview and the light bar sit together."""
    runtime = entry.runtime_data
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="TinyDisplay",
        model=runtime.driver.name,
        sw_version=runtime.driver_version,
    )
