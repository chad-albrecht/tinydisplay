"""Finding things for the config flow to offer, so nobody has to type a path.

All of this is blocking filesystem and USB work, so every function here is
synchronous and meant to be called through ``async_add_executor_job``. Keeping
that boundary explicit is why they live in their own module rather than inside
the flow.

The design principle throughout: **offer only what will work.** A dropdown of
every YAML file in a Home Assistant config directory is worse than a text box,
because most of those files are somebody else's and picking one produces a
confusing error. A dropdown of files that have been parsed and accepted cannot
do that.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from tinydisplay.homeassistant import DashboardConfigError, load_dashboard

from .const import (
    DASHBOARD_DIR,
    DASHBOARD_NAME,
    DASHBOARD_SEARCH_DIRS,
    DRIVER_HT32,
    DRIVER_MEMORY,
    MAX_DASHBOARD_CANDIDATES,
    STARTER_DASHBOARD,
)

_LOGGER = logging.getLogger(__name__)

#: Home Assistant's own YAML, which is never a TinyDisplay dashboard. Skipping
#: these by name avoids parsing them only to reject them, and avoids offering a
#: tempting-looking `configuration.yaml` in the list.
_NOT_DASHBOARDS = frozenset(
    {
        "automations.yaml",
        "configuration.yaml",
        "customize.yaml",
        "groups.yaml",
        "known_devices.yaml",
        "scenes.yaml",
        "scripts.yaml",
        "secrets.yaml",
        "ui-lovelace.yaml",
    }
)


def starter_path(config_dir: str) -> Path:
    """Where the starter dashboard is written."""
    return Path(config_dir) / DASHBOARD_DIR / DASHBOARD_NAME


def ensure_starter_dashboard(config_dir: str) -> Path | None:
    """Copy the starter dashboard into the config directory if it is absent.

    Returns the path, or ``None`` if it could not be written -- a read-only
    config directory is unusual but not worth failing setup over, since the
    user can still name a file of their own.

    An existing file is never overwritten. Someone who has edited the starter
    has made it theirs, and a setup flow that silently reverted their work
    would be a nasty surprise.
    """
    destination = starter_path(config_dir)
    if destination.exists():
        return destination

    source = Path(__file__).parent / STARTER_DASHBOARD
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    except OSError:
        _LOGGER.warning("could not write a starter dashboard to %s", destination, exc_info=True)
        return None

    _LOGGER.info("wrote a starter dashboard to %s", destination)
    return destination


def find_dashboards(config_dir: str) -> list[str]:
    """Every file under the config directory that parses as a dashboard.

    Parsing each candidate rather than trusting its name is the point: the list
    then contains only paths that will actually work, so choosing from it
    cannot fail later. It costs a few milliseconds per file and there are
    rarely more than a handful.
    """
    root = Path(config_dir)
    found: list[str] = []
    checked = 0

    for relative in DASHBOARD_SEARCH_DIRS:
        directory = root / relative if relative else root
        if not directory.is_dir():
            continue

        for path in sorted(directory.glob("*.y*ml")):
            if path.name in _NOT_DASHBOARDS or not path.is_file():
                continue
            if checked >= MAX_DASHBOARD_CANDIDATES:
                _LOGGER.debug("stopped after %d candidates in %s", checked, directory)
                break
            checked += 1
            try:
                load_dashboard(path)
            except DashboardConfigError:
                continue
            except OSError:
                continue
            found.append(str(path))

    return found


def detect_default_driver() -> str:
    """Which panel to preselect: the real one if it is attached, else preview.

    Defaulting to the HT32 on a machine that has none makes the first thing a
    new user does fail, and defaulting to the preview driver on a machine with
    a panel wired in makes them change it for no reason. Asking the bus is
    cheap and gets it right both ways.
    """
    try:
        from tinydisplay.ht32.usbfs import find_usb_panel  # noqa: PLC0415
    except ImportError:
        return DRIVER_MEMORY

    try:
        return DRIVER_HT32 if find_usb_panel() is not None else DRIVER_MEMORY
    except OSError:
        # Discovery reads sysfs. If that is not readable from here, the driver
        # would not be able to open the panel either.
        return DRIVER_MEMORY
