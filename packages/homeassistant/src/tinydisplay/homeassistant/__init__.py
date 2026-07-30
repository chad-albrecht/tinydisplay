"""TinyDisplay for Home Assistant: entity state in, rendered frames out.

This package is the top of the stack, and it is deliberately split in two.

**Everything here runs without Home Assistant.** Dashboard definitions are
parsed, validated, built into widget trees and rendered against a
:class:`StateSource` that is just a dictionary in tests and in the simulator.
That is what makes the layer testable: the parser, the templating, the widget
binding and the render loop are all exercised in CI with nothing installed and
nothing plugged in.

**The custom component is the thin part.** The Home Assistant integration lives
outside this package, in ``custom_components/tinydisplay/`` at the repository
root, because that is where Home Assistant and HACS look for it. It is the only
code permitted to ``import homeassistant``, and it does as little as possible:
adapt ``hass.states`` to :class:`StateSource`, subscribe to the entities a
dashboard names, pick a driver, and hand all four to :func:`run_dashboard`.

It is the same division the HT32 driver makes between ``protocol`` and
``transport``, for the same reason -- the part that can be tested exhaustively
is separated from the part that cannot, and the second is kept small enough to
read in one sitting.

Example:
    >>> from tinydisplay.homeassistant import Dashboard, StaticStateSource
    >>> dashboard = Dashboard.from_yaml(
    ...     '''
    ...     theme: midnight
    ...     root:
    ...       type: stack
    ...       axis: vertical
    ...       children:
    ...         - type: label
    ...           size: 16
    ...           text: "{{ sensor.kitchen.name }}"
    ...           color: muted
    ...         - type: label
    ...           text: "{{ sensor.kitchen | round(1) }} C"
    ...           color: accent
    ...     '''
    ... )
    >>> source = StaticStateSource()
    >>> _ = source.set("sensor.kitchen", "21.53", friendly_name="Kitchen")
    >>> canvas = Canvas(160, 60)
    >>> dashboard.render(canvas, source)
    >>> sorted(dashboard.entity_ids)
    ['sensor.kitchen']
"""

from __future__ import annotations

from tinydisplay.homeassistant.build import BuiltDashboard, build_dashboard, build_node
from tinydisplay.homeassistant.dashboard import Dashboard
from tinydisplay.homeassistant.errors import (
    DashboardConfigError,
    HomeAssistantError,
    TemplateError,
)
from tinydisplay.homeassistant.runner import (
    DEFAULT_KEEPALIVE_INTERVAL,
    DEFAULT_MAX_INTERVAL,
    DEFAULT_MIN_INTERVAL,
    run_dashboard,
)
from tinydisplay.homeassistant.schema import (
    NODE_TYPES,
    THEME_ROLES,
    ColorRef,
    DashboardSpec,
    Insets,
    NodeSpec,
    ValueRef,
    load_dashboard,
    parse_dashboard,
    parse_dashboard_yaml,
)
from tinydisplay.homeassistant.state import (
    UNAVAILABLE,
    UNAVAILABLE_STATES,
    UNKNOWN,
    EntityState,
    StateSource,
    StaticStateSource,
    is_entity_id,
    missing_entities,
    split_entity_id,
)
from tinydisplay.homeassistant.template import (
    FILTERS,
    UNAVAILABLE_TEXT,
    Template,
    template_entity_ids,
)

__version__ = "0.2.0"

__all__ = [
    "DEFAULT_KEEPALIVE_INTERVAL",
    "DEFAULT_MAX_INTERVAL",
    "DEFAULT_MIN_INTERVAL",
    "FILTERS",
    "NODE_TYPES",
    "THEME_ROLES",
    "UNAVAILABLE",
    "UNAVAILABLE_STATES",
    "UNAVAILABLE_TEXT",
    "UNKNOWN",
    "BuiltDashboard",
    "ColorRef",
    "Dashboard",
    "DashboardConfigError",
    "DashboardSpec",
    "EntityState",
    "HomeAssistantError",
    "Insets",
    "NodeSpec",
    "StateSource",
    "StaticStateSource",
    "Template",
    "TemplateError",
    "ValueRef",
    "__version__",
    "build_dashboard",
    "build_node",
    "is_entity_id",
    "load_dashboard",
    "missing_entities",
    "parse_dashboard",
    "parse_dashboard_yaml",
    "run_dashboard",
    "split_entity_id",
    "template_entity_ids",
]
