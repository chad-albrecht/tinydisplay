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

#: Shipped with the integration and copied into the config directory the first
#: time setup runs, so that a fresh install has something valid to point at.
#: It reads only ``sun.sun``, which every Home Assistant has.
STARTER_DASHBOARD: Final = "starter_dashboard.yaml"

#: Where the starter is copied to. A directory of its own, because a dashboard
#: is a document a person will edit and come back to, not a dotfile.
DASHBOARD_DIR: Final = "tinydisplay"
DASHBOARD_NAME: Final = "dashboard.yaml"

#: Directories searched for dashboards to offer in the config flow, relative to
#: the config directory. Only the top level of each: a recursive walk of a
#: Home Assistant config is slow and mostly finds other integrations' YAML.
DASHBOARD_SEARCH_DIRS: Final = ("", DASHBOARD_DIR)

#: How many candidate files to try parsing before giving up. A guard against a
#: config directory with hundreds of YAML files, not a meaningful limit.
MAX_DASHBOARD_CANDIDATES: Final = 200

#: Panel size used by the memory driver, which has no size of its own. Chosen
#: to match the HT32 so that a dashboard checked without hardware is checked at
#: the size it will actually be drawn at.
MEMORY_PANEL_WIDTH: Final = 320
MEMORY_PANEL_HEIGHT: Final = 170
