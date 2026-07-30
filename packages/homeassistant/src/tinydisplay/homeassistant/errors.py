"""Exceptions raised by the Home Assistant layer.

Everything derives from :class:`~tinydisplay.core.errors.TinyDisplayError`, so
an embedder can catch the whole framework in one clause.

The split here mirrors the one the widget library makes, for the same reason:
a dashboard *definition* is written by a person and can be wrong, so parsing it
is strict and the error says exactly where the mistake is. A dashboard's
*data* arrives from sensors at runtime and is routinely missing, stale or
nonsense, so reading it is forgiving -- an unavailable entity draws a
placeholder rather than stopping the panel.
"""

from __future__ import annotations

from tinydisplay.core.errors import TinyDisplayError

__all__ = ["DashboardConfigError", "HomeAssistantError", "TemplateError"]


class HomeAssistantError(TinyDisplayError):
    """Base class for failures in the Home Assistant layer."""


class DashboardConfigError(HomeAssistantError):
    """Raised when a dashboard definition cannot be understood.

    The message carries the location of the problem in the document -- for
    example ``root.children[2].warning_at`` -- because a dashboard is a nested
    mapping and "invalid value" without a path is close to useless when the
    file is forty lines long.
    """


class TemplateError(HomeAssistantError):
    """Raised when a placeholder expression is malformed.

    This is a mistake in the dashboard definition, not a runtime condition: a
    template referring to an entity that does not exist renders as the
    unavailable placeholder, but a template with an unclosed brace or an
    unknown filter cannot render as anything and is rejected at load time.
    """
