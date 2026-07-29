"""RGBA colour values and conversions.

:class:`Color` is an immutable 8-bit-per-channel RGBA value. Canvases store
opaque RGB pixels; the alpha channel exists so that drawing operations can be
composited onto a canvas, not so that canvases themselves can be transparent.

Small panels are rarely 24-bit. The HT32 panel, TinyDisplay's first target,
takes RGB565, so packing and unpacking that format lives here in core rather
than in any one driver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Final, Self

__all__ = ["Color"]

_CHANNEL_MAX: Final = 255
_RGB565_MAX: Final = 0xFFFF
_SRGB_LINEAR_CUTOFF: Final = 0.04045


def _clamp_channel(value: int) -> int:
    """Clamp an integer into the valid ``0..255`` channel range."""
    return max(0, min(_CHANNEL_MAX, value))


def _linearise(channel: int) -> float:
    """Convert one sRGB channel to linear light, per IEC 61966-2-1."""
    c = channel / _CHANNEL_MAX
    if c <= _SRGB_LINEAR_CUTOFF:
        return c / 12.92
    # float.__pow__ is typed as returning Any (a negative base with a
    # fractional exponent yields complex); the base here is always >= 0.
    return float(((c + 0.055) / 1.055) ** 2.4)


@dataclass(frozen=True, slots=True)
class Color:
    """An immutable 8-bit RGBA colour.

    Example:
        >>> Color(255, 0, 0).to_hex()
        '#ff0000'
        >>> Color.from_hex("#336699") == Color(0x33, 0x66, 0x99)
        True
        >>> hex(Color.WHITE.to_rgb565())
        '0xffff'
    """

    r: int
    g: int
    b: int
    a: int = _CHANNEL_MAX

    # Populated immediately after the class body.
    BLACK: ClassVar[Color]
    WHITE: ClassVar[Color]
    RED: ClassVar[Color]
    GREEN: ClassVar[Color]
    BLUE: ClassVar[Color]
    CYAN: ClassVar[Color]
    MAGENTA: ClassVar[Color]
    YELLOW: ClassVar[Color]
    ORANGE: ClassVar[Color]
    GRAY: ClassVar[Color]
    DARK_GRAY: ClassVar[Color]
    LIGHT_GRAY: ClassVar[Color]
    TRANSPARENT: ClassVar[Color]

    def __post_init__(self) -> None:
        for name, value in (("r", self.r), ("g", self.g), ("b", self.b), ("a", self.a)):
            if not 0 <= value <= _CHANNEL_MAX:
                msg = f"colour channel {name!r} must be in 0..255, got {value}"
                raise ValueError(msg)

    # -- Constructors ------------------------------------------------------

    @classmethod
    def from_hex(cls, value: str) -> Self:
        """Parse ``#rgb``, ``#rrggbb`` or ``#rrggbbaa`` (the leading ``#`` is optional).

        Raises:
            ValueError: If the string is not one of the supported lengths or
                contains non-hexadecimal characters.
        """
        text = value.strip().removeprefix("#")
        try:
            digits = int(text, 16)
        except ValueError:
            msg = f"invalid hex colour: {value!r}"
            raise ValueError(msg) from None

        match len(text):
            case 3:
                # #rgb -> #rrggbb by nibble duplication.
                r, g, b = ((digits >> 8) & 0xF, (digits >> 4) & 0xF, digits & 0xF)
                return cls(r * 0x11, g * 0x11, b * 0x11)
            case 6:
                return cls((digits >> 16) & 0xFF, (digits >> 8) & 0xFF, digits & 0xFF)
            case 8:
                return cls(
                    (digits >> 24) & 0xFF,
                    (digits >> 16) & 0xFF,
                    (digits >> 8) & 0xFF,
                    digits & 0xFF,
                )
            case _:
                msg = f"invalid hex colour {value!r}: expected 3, 6 or 8 digits"
                raise ValueError(msg)

    @classmethod
    def from_rgb565(cls, value: int) -> Self:
        """Unpack a 16-bit RGB565 word, replicating high bits into the low bits.

        Bit replication (rather than zero-fill) keeps white at ``#ffffff``
        instead of ``#f8fcf8``, so a pack/unpack round trip is stable.
        """
        if not 0 <= value <= _RGB565_MAX:
            msg = f"RGB565 value must be in 0..65535, got {value}"
            raise ValueError(msg)
        r5 = (value >> 11) & 0x1F
        g6 = (value >> 5) & 0x3F
        b5 = value & 0x1F
        return cls(
            (r5 << 3) | (r5 >> 2),
            (g6 << 2) | (g6 >> 4),
            (b5 << 3) | (b5 >> 2),
        )

    # -- Conversions -------------------------------------------------------

    @property
    def rgb(self) -> tuple[int, int, int]:
        """The colour as an ``(r, g, b)`` tuple, discarding alpha."""
        return (self.r, self.g, self.b)

    @property
    def rgba(self) -> tuple[int, int, int, int]:
        """The colour as an ``(r, g, b, a)`` tuple."""
        return (self.r, self.g, self.b, self.a)

    @property
    def is_opaque(self) -> bool:
        """``True`` when the colour fully covers whatever is beneath it."""
        return self.a == _CHANNEL_MAX

    @property
    def is_transparent(self) -> bool:
        """``True`` when the colour contributes nothing when composited."""
        return self.a == 0

    def to_hex(self, *, include_alpha: bool = False) -> str:
        """Format as ``#rrggbb``, or ``#rrggbbaa`` when ``include_alpha`` is set."""
        if include_alpha:
            return f"#{self.r:02x}{self.g:02x}{self.b:02x}{self.a:02x}"
        return f"#{self.r:02x}{self.g:02x}{self.b:02x}"

    def to_rgb565(self) -> int:
        """Pack into a 16-bit RGB565 word, discarding alpha."""
        return ((self.r & 0xF8) << 8) | ((self.g & 0xFC) << 3) | (self.b >> 3)

    def quantized_rgb565(self) -> Color:
        """Return this colour as it will appear after an RGB565 round trip.

        Useful for asserting on rendered output in tests, and for previewing in
        the simulator how a colour will actually look on a 16-bit panel.
        """
        return Color.from_rgb565(self.to_rgb565()).with_alpha(self.a)

    # -- Derivations -------------------------------------------------------

    def with_alpha(self, alpha: int) -> Color:
        """Return a copy with the given alpha."""
        return Color(self.r, self.g, self.b, _clamp_channel(alpha))

    def blend_over(self, background: Color) -> Color:
        """Composite this colour over ``background`` using source-over alpha.

        The result is opaque whenever ``background`` is opaque, which is the
        case that matters when drawing onto a canvas.
        """
        if self.is_opaque:
            return self
        if self.is_transparent:
            return background

        src_a = self.a / _CHANNEL_MAX
        dst_a = background.a / _CHANNEL_MAX
        out_a = src_a + dst_a * (1.0 - src_a)
        if out_a == 0.0:
            return Color(0, 0, 0, 0)

        def channel(src: int, dst: int) -> int:
            blended = (src * src_a + dst * dst_a * (1.0 - src_a)) / out_a
            return _clamp_channel(round(blended))

        return Color(
            channel(self.r, background.r),
            channel(self.g, background.g),
            channel(self.b, background.b),
            _clamp_channel(round(out_a * _CHANNEL_MAX)),
        )

    def lerp(self, other: Color, t: float) -> Color:
        """Linearly interpolate towards ``other``; ``t`` is clamped to ``0.0..1.0``."""
        ratio = max(0.0, min(1.0, t))

        def channel(start: int, end: int) -> int:
            return _clamp_channel(round(start + (end - start) * ratio))

        return Color(
            channel(self.r, other.r),
            channel(self.g, other.g),
            channel(self.b, other.b),
            channel(self.a, other.a),
        )

    def relative_luminance(self) -> float:
        """WCAG relative luminance in ``0.0..1.0``, ignoring alpha."""
        return (
            0.2126 * _linearise(self.r) + 0.7152 * _linearise(self.g) + 0.0722 * _linearise(self.b)
        )

    def contrast_ratio(self, other: Color) -> float:
        """WCAG contrast ratio against ``other``, from ``1.0`` to ``21.0``.

        Widgets use this to pick legible foregrounds; WCAG AA large text wants
        at least ``3.0``, and body text at least ``4.5``.
        """
        light, dark = sorted((self.relative_luminance(), other.relative_luminance()), reverse=True)
        return (light + 0.05) / (dark + 0.05)

    def __str__(self) -> str:
        return self.to_hex(include_alpha=not self.is_opaque)


Color.BLACK = Color(0, 0, 0)
Color.WHITE = Color(255, 255, 255)
Color.RED = Color(255, 0, 0)
Color.GREEN = Color(0, 255, 0)
Color.BLUE = Color(0, 0, 255)
Color.CYAN = Color(0, 255, 255)
Color.MAGENTA = Color(255, 0, 255)
Color.YELLOW = Color(255, 255, 0)
Color.ORANGE = Color(255, 165, 0)
Color.GRAY = Color(128, 128, 128)
Color.DARK_GRAY = Color(64, 64, 64)
Color.LIGHT_GRAY = Color(192, 192, 192)
Color.TRANSPARENT = Color(0, 0, 0, 0)
