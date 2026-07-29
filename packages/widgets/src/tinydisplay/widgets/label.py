"""Text on a panel.

A label is the widget a dashboard has most of, so it earns two conveniences
that would be noise elsewhere: it wraps to its own width, and it can shrink to
fit rather than overflow.

Shrink-to-fit matters more on a panel than on a screen. There is no scrollbar
and no reflow: text that does not fit is simply gone, and a temperature readout
that vanishes when the value reaches three digits is worse than one drawn a
point smaller. The default is to wrap; shrinking is opt-in because it makes
adjacent labels disagree about size, which looks accidental unless intended.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tinydisplay.core import Color, Font, HorizontalAlign, VerticalAlign, Widget

if TYPE_CHECKING:
    from tinydisplay.core import Canvas, Rect

__all__ = ["Label"]

#: Never shrink below this, or the text stops being text.
MIN_FONT_SIZE = 6


class Label(Widget):
    """A run of text, aligned within its bounds.

    Args:
        text: What to draw. ``\\n`` starts a new line.
        color: Ink colour. Defaults to white.
        font: Face to draw with. Defaults to the shared default font.
        align: Horizontal placement within the bounds.
        valign: Vertical placement within the bounds.
        wrap: Break long lines at the widget's width.
        shrink_to_fit: Reduce the font size until the text fits, down to
            :data:`MIN_FONT_SIZE`. Requires a sized font.
        bounds: Where to draw. Usually assigned by a layout container.

    Example:
        >>> from tinydisplay.core import Canvas, Rect
        >>> from tinydisplay.widgets import Label
        >>> label = Label("hello", bounds=Rect(0, 0, 60, 20))
        >>> canvas = Canvas(60, 20)
        >>> label.draw(canvas)
        >>> label.text
        'hello'
    """

    __slots__ = (
        "_align",
        "_color",
        "_font",
        "_shrink_to_fit",
        "_text",
        "_valign",
        "_wrap",
    )

    def __init__(
        self,
        text: str = "",
        *,
        color: Color = Color.WHITE,
        font: Font | None = None,
        align: HorizontalAlign = HorizontalAlign.LEFT,
        valign: VerticalAlign = VerticalAlign.TOP,
        wrap: bool = True,
        shrink_to_fit: bool = False,
        bounds: Rect | None = None,
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        super().__init__(bounds, visible=visible, name=name)
        self._text = text
        self._color = color
        self._font = font
        self._align = align
        self._valign = valign
        self._wrap = wrap
        self._shrink_to_fit = shrink_to_fit

    # -- Content -----------------------------------------------------------

    @property
    def text(self) -> str:
        """The text being drawn."""
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        if value != self._text:
            self._text = value
            self.mark_dirty()

    @property
    def color(self) -> Color:
        """Ink colour."""
        return self._color

    @color.setter
    def color(self, value: Color) -> None:
        if value != self._color:
            self._color = value
            self.mark_dirty()

    @property
    def font(self) -> Font | None:
        """The face this label draws with, or ``None`` for the default."""
        return self._font

    @font.setter
    def font(self, value: Font | None) -> None:
        self._font = value
        self.mark_dirty()

    # -- Painting ----------------------------------------------------------

    def render(self, canvas: Canvas) -> None:
        """Draw the text, wrapped and shrunk as configured."""
        if not self._text:
            return

        area = self.bounds
        face = self._font or Font.default()
        content = self._text

        if self._shrink_to_fit:
            face, content = self._fit(face, content, area)
        elif self._wrap:
            content = "\n".join(wrap_text(content, face, area.width))

        canvas.text(
            _anchor_x(area, self._align),
            _anchor_y(area, self._valign),
            content,
            self._color,
            font=face,
            align=self._align,
            valign=self._valign,
        )

    def _fit(self, face: Font, content: str, area: Rect) -> tuple[Font, str]:
        """Find the largest size at which ``content`` fits, and wrap it there.

        Wrapping is redone at each size because the two interact: smaller text
        needs fewer lines, so trying sizes against a fixed line count would
        shrink further than necessary.
        """
        size = getattr(face, "size", None)
        if not isinstance(size, int):
            # A font with no size cannot be rescaled; wrap and hope.
            return (face, "\n".join(wrap_text(content, face, area.width)))

        for candidate in range(size, MIN_FONT_SIZE - 1, -1):
            trial = Font.default(candidate)
            lines = wrap_text(content, trial, area.width) if self._wrap else content.split("\n")
            fits_height = trial.line_height * len(lines) <= area.height
            fits_width = all(trial.text_width(line) <= area.width for line in lines)
            if fits_height and fits_width:
                return (trial, "\n".join(lines))

        smallest = Font.default(MIN_FONT_SIZE)
        return (smallest, "\n".join(wrap_text(content, smallest, area.width)))


def _anchor_x(area: Rect, align: HorizontalAlign) -> int:
    """The x coordinate ``align`` measures from."""
    if align is HorizontalAlign.CENTER:
        return area.x + area.width // 2
    if align is HorizontalAlign.RIGHT:
        return area.right
    return area.x


def _anchor_y(area: Rect, valign: VerticalAlign) -> int:
    """The y coordinate ``valign`` measures from."""
    if valign is VerticalAlign.MIDDLE:
        return area.y + area.height // 2
    if valign is VerticalAlign.BOTTOM:
        return area.bottom
    return area.y


def wrap_text(text: str, font: Font, width: int) -> list[str]:
    """Break ``text`` into lines no wider than ``width``.

    Greedy by word, then by character for words that do not fit on a line of
    their own -- which happens more often on a 320-pixel panel than it sounds.

    Example:
        >>> from tinydisplay.core import Font
        >>> from tinydisplay.widgets.label import wrap_text
        >>> lines = wrap_text("hello there world", Font.default(12), 60)
        >>> len(lines) > 1
        True
    """
    if width <= 0:
        return [text]

    lines: list[str] = []
    for paragraph in text.split("\n"):
        current = ""
        for word in paragraph.split():
            candidate = f"{current} {word}" if current else word
            if font.text_width(candidate) <= width:
                current = candidate
                continue
            if current:
                lines.append(current)
            current = _break_word(word, font, width, lines)
        lines.append(current)
    return lines


def _break_word(word: str, font: Font, width: int, lines: list[str]) -> str:
    """Split an over-wide ``word``, appending full lines and returning the tail."""
    remainder = word
    while font.text_width(remainder) > width and len(remainder) > 1:
        cut = 1
        while cut < len(remainder) and font.text_width(remainder[: cut + 1]) <= width:
            cut += 1
        lines.append(remainder[:cut])
        remainder = remainder[cut:]
    return remainder
