"""Exceptions raised by the widget library.

Everything derives from :class:`~tinydisplay.core.errors.TinyDisplayError`, so
an embedder can catch the whole framework in one clause.

Widgets are deliberately forgiving about *drawing*: a value outside a gauge's
range is clamped rather than rejected, because a dashboard fed a surprising
sensor reading should show something rather than crash a render loop. They are
strict about *construction*, where a bad argument is a programming error that
will otherwise produce a silently wrong panel.
"""

from __future__ import annotations

from tinydisplay.core.errors import TinyDisplayError

__all__ = ["LayoutError", "WidgetError"]


class WidgetError(TinyDisplayError):
    """Base class for widget-library failures."""


class LayoutError(WidgetError):
    """Raised when a layout is described impossibly.

    A negative padding, a zero-column grid, a cell outside the grid: all
    mistakes in the caller's code rather than conditions to recover from.
    """
