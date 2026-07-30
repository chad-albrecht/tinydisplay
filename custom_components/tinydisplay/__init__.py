"""The TinyDisplay integration: entity state onto a small hardware panel.

Setup does four things and delegates everything else:

1. Load and validate the dashboard file. A dashboard that does not parse is a
   permanent failure, not a transient one -- retrying will not fix a typo -- so
   it raises :class:`ConfigEntryError` and the entry shows the parser's message
   with the offending key's path in it.
2. Build the driver. Selecting the HT32 transport touches the USB bus, so it
   runs in the executor.
3. Start the render loop, which owns the connection from there on.
4. Arrange for all of that to be undone on unload.

Nothing here decides what a dashboard means or when to repaint. Those live in
``tinydisplay.homeassistant``, which is testable without Home Assistant
installed; this module is the part that is not, and it is kept small for that
reason.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from tinydisplay.core import TinyDisplayError
from tinydisplay.homeassistant import Dashboard, DashboardConfigError

from .const import CONF_DASHBOARD, CONF_DRIVER, CONF_SERIAL_NUMBER, DRIVER_MEMORY, PLATFORMS
from .runtime import HassStateSource, TinyDisplayRuntime, create_driver

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    type TinyDisplayConfigEntry = ConfigEntry[TinyDisplayRuntime]

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: TinyDisplayConfigEntry) -> bool:
    """Set up one panel from a config entry."""
    dashboard_path = entry.data[CONF_DASHBOARD]

    try:
        dashboard = await hass.async_add_executor_job(Dashboard.load, dashboard_path)
    except DashboardConfigError as exc:
        # Permanent by nature: the file is wrong, and it will still be wrong in
        # thirty seconds. Home Assistant shows this message on the entry.
        raise ConfigEntryError(str(exc)) from exc

    driver_name = entry.data.get(CONF_DRIVER, DRIVER_MEMORY)
    try:
        driver = await hass.async_add_executor_job(
            lambda: create_driver(driver_name, serial_number=entry.data.get(CONF_SERIAL_NUMBER))
        )
        # Opened here rather than left to the render loop. Building a driver
        # only *selects* a transport -- it touches no hardware -- so a panel
        # that is absent, or a USB node this container cannot write to, would
        # otherwise surface as a background task dying quietly behind an entry
        # that reports itself set up. Connecting now turns that into Home
        # Assistant's own "retrying setup" with the reason on the card.
        #
        # The render loop's `async with driver` finds it already connected and
        # its connect() is a no-op, so it still owns the disconnect.
        await driver.connect()
    except (TinyDisplayError, ValueError, OSError) as exc:
        # Transient by nature: the panel may be unplugged, or still enumerating
        # after a reboot. Home Assistant will retry the entry.
        message = f"cannot open the {driver_name} panel: {exc}"
        raise ConfigEntryNotReady(message) from exc

    runtime = TinyDisplayRuntime(
        dashboard=dashboard,
        driver=driver,
        source=HassStateSource(hass),
        options=dict(entry.options),
    )
    # Attached before starting, because the preview entity reads it from the
    # entry the moment the platform constructs it.
    entry.runtime_data = runtime
    try:
        await runtime.async_start(hass)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # Setup failed after the panel was opened. Releasing it matters: Home
        # Assistant will retry, and a USB node still held by the previous
        # attempt fails the next one for a reason that looks nothing like this.
        await runtime.async_stop()
        await driver.disconnect()
        raise

    _LOGGER.debug(
        "started %s on %s, watching %d entities",
        dashboard,
        driver.name,
        len(dashboard.entity_ids),
    )

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TinyDisplayConfigEntry) -> bool:
    """Stop the render loop and release the panel."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await entry.runtime_data.async_stop()
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: TinyDisplayConfigEntry) -> None:
    """Reload the entry when its options change.

    A full reload rather than reaching into the running loop: the dashboard
    file may have changed alongside the options, and rebuilding is cheap next
    to the alternative of keeping two code paths for "start" and "restart".
    """
    await hass.config_entries.async_reload(entry.entry_id)
