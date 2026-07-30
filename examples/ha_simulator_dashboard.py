"""Preview a Home Assistant dashboard with no Home Assistant and no hardware.

Run it with::

    python -m tinydisplay.simulator examples/ha_simulator_dashboard.py

This loads ``examples/ha_dashboard.yaml`` and drives it from fake entity state
that drifts on its own, so the panel visibly does something. Edit the *YAML*
while the window is open and the change appears on the next frame -- the
simulator reloads this module when it changes, and this module reloads the
dashboard when the YAML changes underneath it.

The point of the example is the seam. The integration swaps
:class:`~tinydisplay.homeassistant.StaticStateSource` for one reading
``hass.states`` and changes nothing else; everything visible here is the same
code that runs on the appliance.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import TYPE_CHECKING

from tinydisplay.homeassistant import Dashboard, StaticStateSource

if TYPE_CHECKING:
    from tinydisplay.core import Canvas

DASHBOARD_PATH = Path(__file__).with_name("ha_dashboard.yaml")

#: How many seconds out of every eight the fake front door is open. Only here
#: so the state-dependent icon colour is visibly doing something.
DOOR_OPEN_SECONDS = 3

_states = StaticStateSource()
_dashboard: Dashboard | None = None
_loaded_at: int | None = None


def _fake_states(seconds: float) -> StaticStateSource:
    """Entity state that moves, so the preview is not a still life."""
    _states.set(
        "sensor.living_room_temperature",
        f"{21.0 + 1.5 * math.sin(seconds / 4):.2f}",
        friendly_name="Living Room",
        unit_of_measurement="C",
    )
    _states.set(
        "sensor.living_room_humidity",
        f"{48 + 6 * math.sin(seconds / 3):.1f}",
        friendly_name="Humidity",
    )
    _states.set("sensor.processor_use", f"{50 + 45 * math.sin(seconds / 5):.0f}")
    _states.set("sensor.phone_battery", f"{int(60 + 35 * math.sin(seconds / 7))}")
    _states.set(
        "binary_sensor.front_door",
        "on" if int(seconds) % 8 < DOOR_OPEN_SECONDS else "off",
    )
    return _states


def _dashboard_for_now() -> Dashboard:
    """Load the YAML, reloading it whenever the file changes on disk."""
    global _dashboard, _loaded_at  # noqa: PLW0603 - module-level cache for a script

    stamp = DASHBOARD_PATH.stat().st_mtime_ns
    if _dashboard is None or stamp != _loaded_at:
        # A failed reload propagates: the simulator paints the error onto the
        # panel and keeps the last working dashboard, which is exactly the
        # behaviour wanted while editing the YAML.
        _dashboard = Dashboard.load(DASHBOARD_PATH)
        _loaded_at = stamp
    return _dashboard


def render(canvas: Canvas) -> None:
    """Draw one frame. Called by the simulator at the configured frame rate.

    A broken YAML file raises out of here on purpose. The simulator wraps
    whatever ``render`` throws and paints it onto the panel, so a typo in the
    dashboard shows up as the parser's message -- offending key path and all --
    on the screen you are already looking at.
    """
    _dashboard_for_now().render(canvas, _fake_states(time.monotonic()))
