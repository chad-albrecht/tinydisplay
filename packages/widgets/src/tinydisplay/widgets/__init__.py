"""TinyDisplay widgets: the vocabulary a dashboard is written in.

Core gives you a canvas and a widget tree; this package gives you the pieces
worth putting in one. Everything here is an ordinary
:class:`~tinydisplay.core.Widget`, so a dashboard built from these composes
with hand-written widgets and renders identically on the simulator and on a
panel.

Three ideas shape the library:

**Layout assigns bounds.** Core widgets carry absolute rectangles and no layout
logic, which keeps them cheap. :class:`Stack`, :class:`Grid` and
:class:`Padding` divide the space they are given among their children, so a
dashboard describes structure rather than arithmetic.

**Colour is named by role.** A :class:`Theme` has ``danger`` rather than
``red``, and :meth:`Theme.quantized` resolves the palette against the panel's
colour depth -- 16-bit panels round colours, and a contrast ratio checked
against the unrounded value is a ratio you do not actually have.

**Widgets clamp, constructors raise.** A gauge fed 150% draws full rather than
throwing, because a surprising sensor reading should not stop a render loop.
A gauge built with zero segments raises immediately, because that is a bug in
the dashboard.

Example:
    >>> from tinydisplay.core import Canvas, Rect
    >>> from tinydisplay.widgets import Axis, Label, MIDNIGHT, Slot, Stack
    >>> theme = MIDNIGHT.quantized()
    >>> panel = Stack(
    ...     Axis.VERTICAL,
    ...     Rect(0, 0, 320, 170),
    ...     slots=[
    ...         Slot(Label("Kitchen", color=theme.text), size=30),
    ...         Slot(Label("21.5 C", color=theme.accent)),
    ...     ],
    ...     spacing=4,
    ... )
    >>> canvas = Canvas(320, 170)
    >>> canvas.clear(theme.background)
    >>> panel.draw(canvas)
    >>> [child.bounds.height for child in panel]
    [30, 136]
"""

from __future__ import annotations

from tinydisplay.widgets.errors import LayoutError, WidgetError
from tinydisplay.widgets.icon import Icon, IconName
from tinydisplay.widgets.image import ImageWidget
from tinydisplay.widgets.indicators import Gauge, ProgressBar, Sparkline
from tinydisplay.widgets.label import MIN_FONT_SIZE, Label, wrap_text
from tinydisplay.widgets.layout import Align, Axis, Grid, Padding, Slot, Spacer, Stack
from tinydisplay.widgets.theme import (
    HIGH_CONTRAST,
    MIDNIGHT,
    MIN_TEXT_CONTRAST,
    PAPER,
    THEMES,
    Theme,
)

__version__ = "0.1.0"

__all__ = [
    "HIGH_CONTRAST",
    "MIDNIGHT",
    "MIN_FONT_SIZE",
    "MIN_TEXT_CONTRAST",
    "PAPER",
    "THEMES",
    "Align",
    "Axis",
    "Gauge",
    "Grid",
    "Icon",
    "IconName",
    "ImageWidget",
    "Label",
    "LayoutError",
    "Padding",
    "ProgressBar",
    "Slot",
    "Spacer",
    "Sparkline",
    "Stack",
    "Theme",
    "WidgetError",
    "__version__",
    "wrap_text",
]
