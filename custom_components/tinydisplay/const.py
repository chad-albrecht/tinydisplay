"""Constants shared across the TinyDisplay integration.

Kept in one module so that the config flow, the setup entry point and the
translations cannot drift apart: every configuration key is spelled once here
and imported everywhere else.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "tinydisplay"

#: Where the render task and its driver are kept on the config entry, so that
#: unloading can stop what setup started.
DATA_RUNTIME: Final = "runtime"

# -- Configuration keys ----------------------------------------------------

#: Which panel driver to use. A selection rather than free text, because the
#: set of drivers is known at build time and a typo would otherwise surface as
#: an import error during setup.
CONF_DRIVER: Final = "driver"

#: Absolute path to the dashboard YAML file.
CONF_DASHBOARD: Final = "dashboard"

#: Optional USB serial number, to pick one panel out of several.
CONF_SERIAL_NUMBER: Final = "serial_number"

#: Seconds; the floor between repaints.
CONF_MIN_INTERVAL: Final = "min_interval"

#: Seconds; the ceiling between repaints.
CONF_MAX_INTERVAL: Final = "max_interval"

#: Whether the panel should draw the long way round.
CONF_LANDSCAPE: Final = "landscape"

# -- Drivers ---------------------------------------------------------------

#: The HT32 panel: 320x170, RGB565, raw USB. The only hardware driver so far.
DRIVER_HT32: Final = "ht32"

#: A driver that renders frames and keeps them in memory. Useful for confirming
#: a dashboard is valid and repainting on a machine with no panel attached.
DRIVER_MEMORY: Final = "memory"

DRIVERS: Final = (DRIVER_HT32, DRIVER_MEMORY)

# -- Defaults --------------------------------------------------------------

DEFAULT_MIN_INTERVAL: Final = 0.2
DEFAULT_MAX_INTERVAL: Final = 30.0
DEFAULT_LANDSCAPE: Final = True

#: Panel size used by the memory driver, which has no size of its own. Chosen
#: to match the HT32 so that a dashboard checked without hardware is checked at
#: the size it will actually be drawn at.
MEMORY_PANEL_WIDTH: Final = 320
MEMORY_PANEL_HEIGHT: Final = 170
