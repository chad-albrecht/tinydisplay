"""Font loading, measurement and text alignment.

TinyDisplay does not implement a glyph rasteriser. :class:`Font` wraps a Pillow
font object and adds the pieces a layout engine needs: cached loading, stable
line metrics, multi-line measurement, and alignment enums.

Fonts are cached by ``(path, size)`` so that a widget tree rebuilding itself
every frame does not re-read font files from disk.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Final

from PIL import ImageFont

from tinydisplay.core.errors import FontError
from tinydisplay.core.geometry import Size

if TYPE_CHECKING:
    from PIL.ImageFont import FreeTypeFont
    from PIL.ImageFont import ImageFont as BitmapFont

__all__ = [
    "DEFAULT_FONT_SIZE",
    "Font",
    "HorizontalAlign",
    "VerticalAlign",
]

DEFAULT_FONT_SIZE: Final = 12

# Pillow exposes two unrelated font classes; both satisfy the small surface we
# use (``getlength``, ``getbbox``, and rendering via ImageDraw). The ``type``
# statement is lazily evaluated, so the TYPE_CHECKING-only names above are
# never resolved at runtime.
type PILFont = FreeTypeFont | BitmapFont


class HorizontalAlign(StrEnum):
    """Horizontal anchoring of text relative to the supplied ``x`` coordinate."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


class VerticalAlign(StrEnum):
    """Vertical anchoring of text relative to the supplied ``y`` coordinate.

    ``TOP`` anchors the top of the line box, which is the most predictable
    option for layout. ``BASELINE`` anchors the glyph baseline, which is what
    you want when aligning text of different sizes on a shared line.
    """

    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"
    BASELINE = "baseline"


class Font:
    """A loaded font at a fixed pixel size.

    Example:
        >>> font = Font.default(16)
        >>> font.measure("Hello").height == font.line_height
        True
    """

    __slots__ = ("_ascent", "_descent", "_line_spacing", "_name", "_pil_font", "_size")

    def __init__(
        self,
        pil_font: PILFont,
        *,
        size: int,
        name: str,
        line_spacing: float = 1.0,
    ) -> None:
        """Wrap an already-loaded Pillow font.

        Prefer :meth:`load` or :meth:`default`; this constructor exists for
        callers that obtain a Pillow font by other means (an in-memory buffer,
        a bundled bitmap font, and so on).
        """
        _validate_size(size)
        if line_spacing <= 0:
            msg = f"line spacing must be positive, got {line_spacing}"
            raise ValueError(msg)

        self._pil_font = pil_font
        self._size = size
        self._name = name
        self._line_spacing = line_spacing
        self._ascent, self._descent = _font_metrics(pil_font, fallback_size=size)

    # -- Constructors ------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path, size: int, *, line_spacing: float = 1.0) -> Font:
        """Load a TrueType/OpenType font from disk, caching by ``(path, size)``.

        Raises:
            FontError: If the file is missing or cannot be parsed as a font.
        """
        _validate_size(size)
        resolved = Path(path)
        pil_font = _load_truetype(str(resolved), size)
        return cls(pil_font, size=size, name=resolved.stem, line_spacing=line_spacing)

    @classmethod
    def default(cls, size: int = DEFAULT_FONT_SIZE, *, line_spacing: float = 1.0) -> Font:
        """Return Pillow's bundled default font at ``size``.

        This keeps ``tinydisplay-core`` free of bundled font assets and their
        licence obligations. For production dashboards, load a font you have
        chosen deliberately -- the default face is a fallback, not a design.
        """
        _validate_size(size)
        pil_font = _load_default(size)
        return cls(pil_font, size=size, name="default", line_spacing=line_spacing)

    # -- Properties --------------------------------------------------------

    @property
    def pil_font(self) -> PILFont:
        """The underlying Pillow font, for use by the rasteriser."""
        return self._pil_font

    @property
    def name(self) -> str:
        """A short human-readable identifier, used in ``repr`` and diagnostics."""
        return self._name

    @property
    def size(self) -> int:
        """Nominal pixel size the font was loaded at."""
        return self._size

    @property
    def ascent(self) -> int:
        """Pixels from the baseline to the top of the line box."""
        return self._ascent

    @property
    def descent(self) -> int:
        """Pixels from the baseline to the bottom of the line box (non-negative)."""
        return self._descent

    @property
    def line_height(self) -> int:
        """Baseline-to-baseline distance, including ``line_spacing``."""
        return max(1, round((self._ascent + self._descent) * self._line_spacing))

    # -- Measurement -------------------------------------------------------

    def text_width(self, text: str) -> int:
        """Advance width of a single line, in pixels.

        Newlines are not interpreted; use :meth:`measure` for multi-line text.
        """
        if not text:
            return 0
        return round(self._pil_font.getlength(text))

    def measure(self, text: str) -> Size:
        """Measure a possibly multi-line string as a layout box.

        The width is the widest line's advance width and the height is
        ``line_height`` times the number of lines, so an empty string still
        occupies one line vertically.
        """
        lines = text.split("\n")
        width = max((self.text_width(line) for line in lines), default=0)
        return Size(width, self.line_height * len(lines))

    def __repr__(self) -> str:
        return f"Font(name={self._name!r}, size={self._size})"


def _validate_size(size: int) -> None:
    """Reject non-positive font sizes before they reach Pillow."""
    if size <= 0:
        msg = f"font size must be positive, got {size}"
        raise ValueError(msg)


def _font_metrics(pil_font: PILFont, *, fallback_size: int) -> tuple[int, int]:
    """Return ``(ascent, descent)``, tolerating fonts that expose no metrics.

    Pillow's bitmap ``ImageFont`` has no ``getmetrics``; in that case we derive
    a plausible line box from the requested size so layout stays sane.
    """
    getmetrics = getattr(pil_font, "getmetrics", None)
    if getmetrics is not None:
        ascent, descent = getmetrics()
        return int(ascent), abs(int(descent))
    return fallback_size, max(1, round(fallback_size * 0.25))


@lru_cache(maxsize=64)
def _load_truetype(path: str, size: int) -> FreeTypeFont:
    """Load and cache a TrueType face. Cached on the string path for hashability."""
    try:
        return ImageFont.truetype(path, size)
    except OSError as exc:
        msg = f"could not load font {path!r} at size {size}: {exc}"
        raise FontError(msg) from exc


@lru_cache(maxsize=32)
def _load_default(size: int) -> PILFont:
    """Load and cache Pillow's default font at ``size``."""
    try:
        # Pillow >= 10.1 returns a scalable face here; older versions ignore
        # the keyword entirely and raise TypeError.
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - depends on the installed Pillow
        return ImageFont.load_default()
    except OSError as exc:  # pragma: no cover - broken Pillow installation
        msg = f"could not load the default font at size {size}: {exc}"
        raise FontError(msg) from exc
