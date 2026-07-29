"""Semantic colour, resolved against the panel that will actually show it.

A theme names colours by role rather than by appearance -- ``danger`` rather
than ``red`` -- so a dashboard says what it means and a palette swap does not
require touching every widget.

The part specific to this project is :meth:`Theme.quantized`. Most small panels
are 16-bit, and RGB565 has 32 levels of red, 64 of green and 32 of blue instead
of 256 of each. Two colours a designer chose as distinct can collapse onto the
same value, and a carefully checked contrast ratio can quietly drop below the
threshold it was chosen to clear. Quantising the palette up front means the
theme in hand is the theme on the glass, so those problems surface in a test
rather than on a desk.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import TYPE_CHECKING, Final

from tinydisplay.core import Color, PixelFormat

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["HIGH_CONTRAST", "MIDNIGHT", "PAPER", "THEMES", "Theme"]

#: WCAG AA for large text. Panel text is small and viewed from across a room,
#: so this is a floor rather than a target.
MIN_TEXT_CONTRAST: Final = 3.0


@dataclass(frozen=True, slots=True)
class Theme:
    """A palette named by role.

    Attributes:
        background: Behind everything.
        surface: Panels, cards and wells raised off the background.
        text: Primary readable text.
        muted: Secondary text, labels and units.
        accent: The one colour that draws the eye. Used sparingly.
        success: A good state.
        warning: A state worth noticing.
        danger: A state worth acting on.
        outline: Hairlines, borders and tick marks.
    """

    background: Color
    surface: Color
    text: Color
    muted: Color
    accent: Color
    success: Color
    warning: Color
    danger: Color
    outline: Color

    # -- Colour depth ------------------------------------------------------

    def quantized(self, pixel_format: PixelFormat = PixelFormat.RGB565_LE) -> Theme:
        """Return this theme as the panel will actually render it.

        Colours are mapped through the target's colour depth, so what the
        widgets draw with is what appears. For a 24-bit format this returns an
        equal theme.

        Example:
            >>> from tinydisplay.widgets import MIDNIGHT
            >>> shown = MIDNIGHT.quantized()
            >>> shown.background == MIDNIGHT.background.quantized_rgb565()
            True
        """
        if pixel_format is PixelFormat.RGB888:
            return self
        return replace(
            self,
            **{field.name: getattr(self, field.name).quantized_rgb565() for field in fields(self)},
        )

    def colors(self) -> Iterator[tuple[str, Color]]:
        """Yield ``(role, colour)`` for every role in the theme."""
        for field in fields(self):
            yield (field.name, getattr(self, field.name))

    # -- Legibility --------------------------------------------------------

    def contrast(self, role: str, *, against: str = "background") -> float:
        """The WCAG contrast ratio between two roles.

        Raises:
            AttributeError: If either name is not a role in this theme.
        """
        foreground: Color = getattr(self, role)
        return foreground.contrast_ratio(getattr(self, against))

    def worst_text_contrast(self) -> float:
        """The lowest contrast among the roles meant to be read as text.

        A theme is only as legible as its weakest pairing, so this is the
        number worth asserting on -- particularly after
        :meth:`quantized`, which can shift a colour just far enough to matter.

        Example:
            >>> from tinydisplay.widgets import MIDNIGHT
            >>> MIDNIGHT.quantized().worst_text_contrast() > 3.0
            True
        """
        readable = ("text", "muted", "accent", "success", "warning", "danger")
        return min(self.contrast(role) for role in readable)

    def is_legible(self, *, minimum: float = MIN_TEXT_CONTRAST) -> bool:
        """Whether every text role clears ``minimum`` against the background."""
        return self.worst_text_contrast() >= minimum


#: The default. Dark, because a panel on a desk at night is a lamp otherwise,
#: and because dark backgrounds hide the RGB565 banding that pale gradients
#: make obvious.
MIDNIGHT: Final = Theme(
    background=Color.from_hex("#0d1b2a"),
    surface=Color.from_hex("#1b263b"),
    text=Color.from_hex("#e0e1dd"),
    muted=Color.from_hex("#9fb1c7"),
    accent=Color.from_hex("#4cc9f0"),
    success=Color.from_hex("#52d98a"),
    warning=Color.from_hex("#ffb703"),
    danger=Color.from_hex("#ff5d73"),
    outline=Color.from_hex("#33445c"),
)

#: For panels in a bright room, where a dark theme reads as a black rectangle.
PAPER: Final = Theme(
    background=Color.from_hex("#f5f3ee"),
    surface=Color.from_hex("#e4e0d7"),
    text=Color.from_hex("#1b1b1b"),
    muted=Color.from_hex("#55504a"),
    accent=Color.from_hex("#0b6e99"),
    success=Color.from_hex("#0f7a3d"),
    warning=Color.from_hex("#8a5a00"),
    danger=Color.from_hex("#a4123f"),
    outline=Color.from_hex("#bdb7ac"),
)

#: Maximum legibility at the cost of subtlety, for small or distant panels.
HIGH_CONTRAST: Final = Theme(
    background=Color.from_hex("#000000"),
    surface=Color.from_hex("#1a1a1a"),
    text=Color.from_hex("#ffffff"),
    muted=Color.from_hex("#c8c8c8"),
    accent=Color.from_hex("#00e5ff"),
    success=Color.from_hex("#00e676"),
    warning=Color.from_hex("#ffd600"),
    danger=Color.from_hex("#ff5252"),
    outline=Color.from_hex("#4d4d4d"),
)

#: Built-in themes by name, for configuration files and command lines.
THEMES: Final = {
    "midnight": MIDNIGHT,
    "paper": PAPER,
    "high-contrast": HIGH_CONTRAST,
}
