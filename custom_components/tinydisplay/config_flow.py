"""Config flow: pick a panel and a dashboard file.

The validation this flow performs is a single call into
``tinydisplay.homeassistant``. That is deliberate -- the rules about what makes
a dashboard valid are covered by the test suite one layer down, and duplicating
any of them here would create two answers to the same question, one of which
would eventually be wrong.

What the flow adds is the part that only makes sense with Home Assistant
running: checking the entities a dashboard names actually exist. That is a
warning rather than an error, because a dashboard written before the sensor it
watches is perfectly reasonable and blocking it would be obnoxious.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Final

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from tinydisplay.homeassistant import Dashboard, DashboardConfigError, missing_entities

from .const import (
    CONF_DASHBOARD,
    CONF_DRIVER,
    CONF_LANDSCAPE,
    CONF_MAX_INTERVAL,
    CONF_MIN_INTERVAL,
    CONF_SERIAL_NUMBER,
    DEFAULT_LANDSCAPE,
    DEFAULT_MAX_INTERVAL,
    DEFAULT_MIN_INTERVAL,
    DOMAIN,
    DRIVER_HT32,
    DRIVER_MEMORY,
    DRIVERS,
)
from .discovery import detect_default_driver, ensure_starter_dashboard, find_dashboards
from .runtime import HassStateSource

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)


def _user_schema(*, driver: str, dashboards: list[str], suggested: str | None) -> vol.Schema:
    """Build the form, preselecting what this machine actually has.

    The dashboard field is a select with ``custom_value`` rather than a plain
    text box: the options are files already parsed and accepted, so picking one
    cannot fail, and anyone with a dashboard somewhere else can still type a
    path.
    """
    dashboard_selector = SelectSelector(
        SelectSelectorConfig(
            options=dashboards,
            custom_value=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )
    dashboard_field = (
        vol.Required(CONF_DASHBOARD, default=suggested)
        if suggested
        else vol.Required(CONF_DASHBOARD)
    )
    return vol.Schema(
        {
            vol.Required(CONF_DRIVER, default=driver): vol.In(DRIVERS),
            dashboard_field: dashboard_selector,
            vol.Optional(CONF_SERIAL_NUMBER): str,
        }
    )


# `domain=` is how Home Assistant registers a flow class. mypy cannot see the
# keyword because ConfigFlow resolves to Any without Home Assistant installed,
# and installing it here is exactly the dependency this repository avoids.
class TinyDisplayConfigFlow(ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Set up one TinyDisplay panel."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Collect the driver and dashboard, validating the latter."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        if user_input is not None:
            driver = user_input[CONF_DRIVER]
            serial = user_input.get(CONF_SERIAL_NUMBER)

            # One entry per physical panel. Two entries writing frames into the
            # same USB endpoint would interleave them and paint garbage, which
            # is a confusing way to discover you set it up twice.
            await self.async_set_unique_id(f"{driver}:{serial or 'default'}")
            self._abort_if_unique_id_configured()

            try:
                dashboard = await self.hass.async_add_executor_job(
                    Dashboard.load, user_input[CONF_DASHBOARD]
                )
            except DashboardConfigError as exc:
                errors[CONF_DASHBOARD] = "invalid_dashboard"
                placeholders["error"] = str(exc)
            else:
                self._warn_about_missing_entities(dashboard)
                return self.async_create_entry(
                    title=_entry_title(driver, user_input[CONF_DASHBOARD]),
                    data=user_input,
                )

        # Done in the executor: writing the starter and parsing candidates both
        # touch the filesystem, and detection reads the USB bus.
        suggested, dashboards, driver = await self.hass.async_add_executor_job(self._survey)

        return self.async_show_form(
            step_id="user",
            data_schema=_user_schema(
                driver=driver,
                dashboards=dashboards,
                suggested=(user_input or {}).get(CONF_DASHBOARD) or suggested,
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    def _survey(self) -> tuple[str | None, list[str], str]:
        """Work out what to offer: a starter, the dashboards found, the panel.

        Runs in the executor. Writing the starter first means it is among the
        candidates, so a machine with nothing prepared still gets a working
        default rather than an empty dropdown.
        """
        config_dir = self.hass.config.config_dir
        starter = ensure_starter_dashboard(config_dir)
        dashboards = find_dashboards(config_dir)
        suggested = str(starter) if starter else (dashboards[0] if dashboards else None)
        return (suggested, dashboards, detect_default_driver())

    def _warn_about_missing_entities(self, dashboard: Dashboard) -> None:
        """Log the entities a dashboard names that do not exist yet."""
        missing = missing_entities(HassStateSource(self.hass), sorted(dashboard.entity_ids))
        if missing:
            _LOGGER.warning(
                "dashboard %s references %d unknown entities: %s",
                dashboard.source_path or "<inline>",
                len(missing),
                ", ".join(missing),
            )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow for an existing entry.

        The entry is not passed on: since Home Assistant 2024.11 the framework
        sets ``config_entry`` on the flow itself, and assigning it here is
        deprecated.
        """
        del config_entry
        return TinyDisplayOptionsFlow()


class TinyDisplayOptionsFlow(OptionsFlow):
    """Adjust how often a configured panel repaints."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Collect the repaint intervals and the orientation."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        options = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MIN_INTERVAL,
                    default=options.get(CONF_MIN_INTERVAL, DEFAULT_MIN_INTERVAL),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=60)),
                vol.Optional(
                    CONF_MAX_INTERVAL,
                    default=options.get(CONF_MAX_INTERVAL, DEFAULT_MAX_INTERVAL),
                ): vol.All(vol.Coerce(float), vol.Range(min=1, max=3600)),
                vol.Optional(
                    CONF_LANDSCAPE,
                    default=options.get(CONF_LANDSCAPE, DEFAULT_LANDSCAPE),
                ): bool,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)


#: What each panel is called in the entry title. `MEMORY (dashboard.yaml)` was
#: a debug string wearing a name's clothing; these read like something a person
#: chose.
_PANEL_NAMES: Final = {
    DRIVER_HT32: "HT32 panel",
    DRIVER_MEMORY: "Preview",
}


def _entry_title(driver: str, dashboard_path: str) -> str:
    """Name the entry after the panel, and the dashboard only when it helps.

    Someone running one panel does not need the filename in the title at all,
    and someone running two is telling them apart by which dashboard they show
    -- so the stem is included only when it is not the starter everyone gets.
    """
    panel = _PANEL_NAMES.get(driver, driver.upper())
    stem = dashboard_path.replace("\\", "/").rsplit("/", 1)[-1].removesuffix(".yaml")
    if stem in {"dashboard", ""}:
        return panel
    return f"{panel} - {stem.replace('_', ' ')}"
