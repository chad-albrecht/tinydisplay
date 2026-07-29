"""The canvas: an in-memory RGB framebuffer with drawing primitives.

A :class:`Canvas` knows nothing about hardware. It owns a contiguous
``uint8[height, width, 3]`` NumPy array and exposes primitives that composite
onto it. Display drivers consume the finished buffer -- typically via
:meth:`Canvas.to_rgb565` for 16-bit panels or :meth:`Canvas.to_rgb888` for
24-bit ones -- and never draw into it themselves.

Pixels on the canvas are always opaque. Alpha lives on the *source* side: any
:class:`~tinydisplay.core.color.Color` with ``a < 255`` is alpha-composited
onto what is already there.

Rasterisation of glyphs, ellipses and image decoding is delegated to Pillow;
everything else is done directly against the NumPy buffer.

Example:
    >>> canvas = Canvas(240, 240)
    >>> canvas.clear(Color.BLACK)
    >>> _ = canvas.text(10, 10, "Hello", Color.WHITE)
    >>> len(canvas.to_rgb565()) == 240 * 240 * 2
    True
"""

from __future__ import annotations

import io
import math
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, Self

import numpy as np
from PIL import Image, ImageDraw, UnidentifiedImageError

from tinydisplay.core.color import Color
from tinydisplay.core.errors import CanvasError, ImageError
from tinydisplay.core.font import Font, HorizontalAlign, VerticalAlign
from tinydisplay.core.geometry import Rect, Size

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    import numpy.typing as npt

__all__ = ["Canvas", "ImageSource"]

#: Anything :meth:`Canvas.image` knows how to draw.
type ImageSource = str | Path | Image.Image | "Canvas"

#: Guard against accidental multi-gigabyte allocations from bad config.
MAX_DIMENSION: Final = 8192

_CHANNEL_MAX: Final = 255
_ROUNDING_BIAS: Final = 127

_default_font_cache: Font | None = None


def _shared_default_font() -> Font:
    """Return a lazily created process-wide default font."""
    global _default_font_cache  # noqa: PLW0603 - intentional module-level cache
    if _default_font_cache is None:
        _default_font_cache = Font.default()
    return _default_font_cache


class Canvas:
    """A fixed-size RGB framebuffer with 2D drawing primitives."""

    __slots__ = ("_background", "_buffer", "_clip", "_height", "_width")

    def __init__(self, width: int, height: int, *, background: Color = Color.BLACK) -> None:
        """Create a canvas of ``width`` x ``height`` pixels filled with ``background``.

        Raises:
            CanvasError: If either dimension is not in ``1..MAX_DIMENSION``.
        """
        if width <= 0 or height <= 0:
            msg = f"canvas dimensions must be positive, got {width}x{height}"
            raise CanvasError(msg)
        if width > MAX_DIMENSION or height > MAX_DIMENSION:
            msg = f"canvas dimensions must not exceed {MAX_DIMENSION}px, got {width}x{height}"
            raise CanvasError(msg)

        self._width = width
        self._height = height
        self._background = background
        self._buffer: npt.NDArray[np.uint8] = np.zeros((height, width, 3), dtype=np.uint8)
        self._clip = Rect(0, 0, width, height)
        self.clear()

    # -- Introspection -----------------------------------------------------

    @property
    def width(self) -> int:
        """Canvas width in pixels."""
        return self._width

    @property
    def height(self) -> int:
        """Canvas height in pixels."""
        return self._height

    @property
    def size(self) -> Size:
        """Canvas dimensions."""
        return Size(self._width, self._height)

    @property
    def bounds(self) -> Rect:
        """The full canvas area, at the origin."""
        return Rect(0, 0, self._width, self._height)

    @property
    def background(self) -> Color:
        """The colour :meth:`clear` uses when called without an argument."""
        return self._background

    @property
    def clip_rect(self) -> Rect:
        """The region drawing is currently restricted to."""
        return self._clip

    @property
    def buffer(self) -> npt.NDArray[np.uint8]:
        """A read-only ``uint8[height, width, 3]`` view of the pixel data.

        The view shares memory with the canvas, so it reflects later drawing.
        Call ``.copy()`` if you need a stable snapshot.
        """
        view = self._buffer.view()
        view.flags.writeable = False
        return view

    def __repr__(self) -> str:
        return f"Canvas({self._width}x{self._height})"

    # -- Clipping ----------------------------------------------------------

    @contextmanager
    def clip(self, rect: Rect) -> Iterator[Rect]:
        """Restrict drawing to ``rect`` for the duration of the block.

        Clips nest: the effective region is the intersection of ``rect`` with
        the region already in force. The yielded rect is that intersection, so
        callers can cheaply detect a fully clipped-out region via
        ``.is_empty``.

        Example:
            >>> canvas = Canvas(64, 64)
            >>> with canvas.clip(Rect(0, 0, 8, 8)):
            ...     canvas.rect(0, 0, 64, 64, Color.RED)  # only 8x8 is painted
            >>> canvas.get_pixel(9, 9) == Color.BLACK
            True
        """
        previous = self._clip
        self._clip = previous.intersection(rect)
        try:
            yield self._clip
        finally:
            self._clip = previous

    # -- Whole-surface operations -----------------------------------------

    def clear(self, color: Color | None = None) -> None:
        """Fill the entire canvas, ignoring the current clip region.

        Passing ``None`` uses the background colour given to ``__init__``.
        Clearing deliberately bypasses clipping because it is a surface reset,
        not a drawing operation; use :meth:`rect` to fill a clipped area.
        """
        fill = self._background if color is None else color
        if fill.is_opaque:
            self._buffer[...] = fill.rgb
            return
        if fill.is_transparent:
            return
        alpha = fill.a
        source = np.array(fill.rgb, dtype=np.uint16)
        blended = (
            source * alpha
            + self._buffer.astype(np.uint16) * (_CHANNEL_MAX - alpha)
            + _ROUNDING_BIAS
        )
        self._buffer[...] = (blended // _CHANNEL_MAX).astype(np.uint8)

    def copy(self) -> Canvas:
        """Return an independent canvas with identical pixels and background."""
        clone = Canvas(self._width, self._height, background=self._background)
        clone._buffer[...] = self._buffer
        return clone

    # -- Pixels ------------------------------------------------------------

    def pixel(self, x: int, y: int, color: Color) -> None:
        """Set a single pixel, honouring clipping and alpha."""
        self._set_pixel(x, y, color)

    def get_pixel(self, x: int, y: int) -> Color:
        """Read a single pixel as an opaque colour.

        Raises:
            IndexError: If the coordinate lies outside the canvas. Reads are
                strict because a silent sentinel would mask test bugs, whereas
                writes clip silently so drawing code need not bounds-check.
        """
        if not self.bounds.contains_point(x, y):
            msg = f"pixel ({x}, {y}) is outside {self._width}x{self._height} canvas"
            raise IndexError(msg)
        r, g, b = (int(v) for v in self._buffer[y, x])
        return Color(r, g, b)

    # -- Primitives --------------------------------------------------------

    def rect(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        color: Color,
        *,
        fill: bool = True,
        thickness: int = 1,
    ) -> None:
        """Draw a rectangle, filled by default.

        When ``fill`` is ``False``, an outline of ``thickness`` pixels is drawn
        *inside* the rectangle's bounds, so the shape never grows beyond the
        geometry you asked for.
        """
        if width <= 0 or height <= 0:
            return
        target = Rect(x, y, width, height)
        if fill:
            self._fill_rect(target, color)
            return

        if thickness <= 0:
            return
        edge = min(thickness, (min(width, height) + 1) // 2)
        inner = target.inset(edge)
        if inner.is_empty:
            self._fill_rect(target, color)
            return
        # Four bands: top, bottom, left and right, avoiding double-blending
        # the corners (which would darken them when the colour is translucent).
        self._fill_rect(Rect(target.left, target.top, target.width, edge), color)
        self._fill_rect(Rect(target.left, inner.bottom, target.width, edge), color)
        self._fill_rect(Rect(target.left, inner.top, edge, inner.height), color)
        self._fill_rect(Rect(inner.right, inner.top, edge, inner.height), color)

    def rounded_rect(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        color: Color,
        *,
        radius: int = 4,
        fill: bool = True,
        thickness: int = 1,
    ) -> None:
        """Draw a rectangle with rounded corners.

        ``radius`` is clamped to half the shorter side, so an over-large radius
        degrades gracefully into a stadium shape instead of raising.
        """
        if width <= 0 or height <= 0:
            return
        corner = max(0, min(radius, min(width, height) // 2))
        if corner == 0:
            self.rect(x, y, width, height, color, fill=fill, thickness=thickness)
            return

        def draw(painter: ImageDraw.ImageDraw, ox: int, oy: int) -> None:
            box = (x + ox, y + oy, x + ox + width - 1, y + oy + height - 1)
            if fill:
                painter.rounded_rectangle(box, radius=corner, fill=color.rgba)
            else:
                painter.rounded_rectangle(
                    box, radius=corner, outline=color.rgba, width=max(1, thickness)
                )

        self._draw_with_pillow(Rect(x, y, width, height), draw)

    def line(
        self,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: Color,
        *,
        thickness: int = 1,
    ) -> None:
        """Draw a straight line between two inclusive endpoints."""
        if thickness <= 0:
            return
        if thickness == 1:
            self._line_thin(x0, y0, x1, y1, color)
            return

        # Thick lines need joins and end caps; Pillow already does that well.
        margin = thickness
        region = Rect.from_bounds(
            min(x0, x1) - margin,
            min(y0, y1) - margin,
            max(x0, x1) + margin + 1,
            max(y0, y1) + margin + 1,
        )

        def draw(painter: ImageDraw.ImageDraw, ox: int, oy: int) -> None:
            painter.line(
                (x0 + ox, y0 + oy, x1 + ox, y1 + oy),
                fill=color.rgba,
                width=thickness,
            )

        self._draw_with_pillow(region, draw)

    def circle(
        self,
        center_x: int,
        center_y: int,
        radius: int,
        color: Color,
        *,
        fill: bool = True,
        thickness: int = 1,
    ) -> None:
        """Draw a circle centred on ``(center_x, center_y)``.

        A ``radius`` of ``r`` produces a shape ``2r + 1`` pixels across, so the
        centre pixel is genuinely centred.
        """
        if radius <= 0:
            return
        diameter = 2 * radius + 1
        region = Rect(center_x - radius, center_y - radius, diameter, diameter)

        def draw(painter: ImageDraw.ImageDraw, ox: int, oy: int) -> None:
            box = (
                center_x - radius + ox,
                center_y - radius + oy,
                center_x + radius + ox,
                center_y + radius + oy,
            )
            if fill:
                painter.ellipse(box, fill=color.rgba)
            else:
                painter.ellipse(box, outline=color.rgba, width=max(1, thickness))

        self._draw_with_pillow(region, draw)

    def text(
        self,
        x: int,
        y: int,
        text: str,
        color: Color,
        *,
        font: Font | None = None,
        align: HorizontalAlign = HorizontalAlign.LEFT,
        valign: VerticalAlign = VerticalAlign.TOP,
    ) -> Rect:
        """Draw text and return the rectangle its ink actually occupies.

        ``\\n`` starts a new line. The returned rect is the tight ink bounding
        box in canvas coordinates, *unclipped*, so callers can use it for
        layout even when part of the text was clipped away. For whitespace-only
        text it is the zero-width layout box.

        Example:
            >>> canvas = Canvas(120, 40)
            >>> box = canvas.text(
            ...     60,
            ...     20,
            ...     "Hi",
            ...     Color.WHITE,
            ...     align=HorizontalAlign.CENTER,
            ...     valign=VerticalAlign.MIDDLE,
            ... )
            >>> box.width > 0
            True
        """
        face = font or _shared_default_font()
        if not text:
            return Rect(x, y, 0, 0)

        lines = text.split("\n")
        line_height = face.line_height
        block_height = line_height * len(lines)
        top = _vertical_origin(y, valign, block_height=block_height, ascent=face.ascent)

        placements = [
            (
                _horizontal_origin(x, align, width=face.text_width(line)),
                top + index * line_height,
                line,
            )
            for index, line in enumerate(lines)
        ]

        ink = _ink_bounds(face, placements)
        region = ink if not ink.is_empty else Rect(x, top, 0, block_height)
        if region.is_empty:
            return region

        def draw(painter: ImageDraw.ImageDraw, ox: int, oy: int) -> None:
            for left, line_top, line in placements:
                if line:
                    painter.text(
                        (left + ox, line_top + oy), line, font=face.pil_font, fill=color.rgba
                    )

        self._draw_with_pillow(region, draw)
        return region

    def image(
        self,
        x: int,
        y: int,
        source: ImageSource,
        *,
        size: Size | None = None,
        opacity: int = _CHANNEL_MAX,
    ) -> Rect:
        """Draw an image, returning the rectangle it was drawn into.

        ``source`` may be a filesystem path, an already-open Pillow image, or
        another :class:`Canvas`. ``size`` rescales with Lanczos resampling, and
        ``opacity`` (``0..255``) scales the image's own alpha channel.

        Raises:
            ImageError: If the source cannot be read or decoded.
        """
        if not 0 <= opacity <= _CHANNEL_MAX:
            msg = f"opacity must be in 0..255, got {opacity}"
            raise ValueError(msg)

        rgba = _load_rgba(source)
        if size is not None:
            if size.is_empty:
                return Rect(x, y, 0, 0)
            rgba = rgba.resize(size.as_tuple(), Image.Resampling.LANCZOS)

        pixels = np.array(rgba, dtype=np.uint8)
        if opacity < _CHANNEL_MAX:
            scaled = pixels[:, :, 3].astype(np.uint16) * opacity
            pixels[:, :, 3] = ((scaled + _ROUNDING_BIAS) // _CHANNEL_MAX).astype(np.uint8)

        self._composite(pixels, x, y)
        return Rect(x, y, rgba.width, rgba.height)

    def blit(self, source: Canvas, x: int = 0, y: int = 0) -> None:
        """Copy another canvas's pixels onto this one.

        This is the fast path for compositing offscreen canvases: both surfaces
        are opaque RGB, so it is a clipped memory copy with no blending.
        """
        target = Rect(x, y, source.width, source.height).intersection(self._clip)
        if target.is_empty:
            return
        src = source._buffer[
            target.top - y : target.bottom - y,
            target.left - x : target.right - x,
        ]
        self._buffer[target.top : target.bottom, target.left : target.right] = src

    # -- Export ------------------------------------------------------------

    def to_pil(self) -> Image.Image:
        """Return a Pillow ``RGB`` image copy of the framebuffer."""
        return Image.fromarray(self._buffer.copy(), mode="RGB")

    @classmethod
    def from_pil(cls, image: Image.Image, *, background: Color = Color.BLACK) -> Self:
        """Build a canvas from a Pillow image, flattening alpha onto ``background``."""
        if image.width <= 0 or image.height <= 0:
            msg = f"cannot build a canvas from a {image.width}x{image.height} image"
            raise CanvasError(msg)
        canvas = cls(image.width, image.height, background=background)
        canvas.image(0, 0, image)
        return canvas

    def save(self, path: str | Path, *, image_format: str | None = None) -> Path:
        """Write the canvas to disk and return the resolved path.

        The format is inferred from the extension unless ``image_format`` is
        given. PNG is the expected choice for tests and golden images.

        Raises:
            ImageError: If the file cannot be written.
        """
        destination = Path(path)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            self.to_pil().save(destination, format=image_format)
        except (OSError, ValueError) as exc:
            msg = f"could not save canvas to {destination}: {exc}"
            raise ImageError(msg) from exc
        return destination

    def to_png_bytes(self) -> bytes:
        """Encode the canvas as PNG bytes, for tests and HTTP responses."""
        stream = io.BytesIO()
        self.to_pil().save(stream, format="PNG")
        return stream.getvalue()

    def to_rgb888(self) -> bytes:
        """Return the framebuffer as packed 24-bit RGB, row-major."""
        return self._buffer.tobytes()

    def to_rgb565(self, *, byte_order: Literal["little", "big"] = "little") -> bytes:
        """Return the framebuffer packed as 16-bit RGB565, row-major.

        This is the wire format for most small SPI and USB HID panels,
        including the HT32. ``byte_order`` selects the endianness of each
        16-bit word; panels differ, so drivers must state which they need.
        """
        buffer = self._buffer.astype(np.uint16)
        packed = (
            ((buffer[:, :, 0] >> 3) << 11) | ((buffer[:, :, 1] >> 2) << 5) | (buffer[:, :, 2] >> 3)
        )
        dtype = "<u2" if byte_order == "little" else ">u2"
        return packed.astype(dtype).tobytes()

    # -- Internals ---------------------------------------------------------

    def _set_pixel(self, x: int, y: int, color: Color) -> None:
        """Write one pixel with clipping and alpha blending."""
        if not self._clip.contains_point(x, y) or color.is_transparent:
            return
        if color.is_opaque:
            self._buffer[y, x] = color.rgb
            return
        alpha = color.a
        inverse = _CHANNEL_MAX - alpha
        current = self._buffer[y, x]
        for channel, value in enumerate(color.rgb):
            blended = value * alpha + int(current[channel]) * inverse + _ROUNDING_BIAS
            current[channel] = blended // _CHANNEL_MAX

    def _fill_rect(self, rect: Rect, color: Color) -> None:
        """Fill a rectangle with a solid colour, clipped and blended."""
        target = rect.intersection(self._clip)
        if target.is_empty or color.is_transparent:
            return
        region = self._buffer[target.top : target.bottom, target.left : target.right]
        if color.is_opaque:
            region[...] = color.rgb
            return
        alpha = color.a
        inverse = _CHANNEL_MAX - alpha
        source = np.array(color.rgb, dtype=np.uint16)
        blended = source * alpha + region.astype(np.uint16) * inverse + _ROUNDING_BIAS
        region[...] = (blended // _CHANNEL_MAX).astype(np.uint8)

    def _line_thin(self, x0: int, y0: int, x1: int, y1: int, color: Color) -> None:
        """Rasterise a one-pixel line with Bresenham's algorithm."""
        dx = abs(x1 - x0)
        dy = -abs(y1 - y0)
        step_x = 1 if x0 < x1 else -1
        step_y = 1 if y0 < y1 else -1
        error = dx + dy
        x, y = x0, y0

        while True:
            self._set_pixel(x, y, color)
            if x == x1 and y == y1:
                return
            doubled = 2 * error
            if doubled >= dy:
                error += dy
                x += step_x
            if doubled <= dx:
                error += dx
                y += step_y

    def _draw_with_pillow(
        self,
        region: Rect,
        draw: Callable[[ImageDraw.ImageDraw, int, int], None],
    ) -> None:
        """Rasterise into a transparent layer the size of the visible region.

        ``draw`` receives the painter plus an ``(ox, oy)`` offset to add to
        canvas coordinates. Allocating only the clipped region keeps the cost
        proportional to what is actually painted.
        """
        target = region.intersection(self._clip)
        if target.is_empty:
            return
        layer = Image.new("RGBA", target.size.as_tuple(), (0, 0, 0, 0))
        draw(ImageDraw.Draw(layer), -target.x, -target.y)
        self._composite(np.array(layer, dtype=np.uint8), target.x, target.y)

    def _composite(self, source: npt.NDArray[np.uint8], x: int, y: int) -> None:
        """Alpha-composite an ``(h, w, 4)`` RGBA array onto the buffer at ``(x, y)``."""
        height, width = source.shape[:2]
        target = Rect(x, y, width, height).intersection(self._clip)
        if target.is_empty:
            return

        cropped = source[
            target.top - y : target.bottom - y,
            target.left - x : target.right - x,
        ]
        region = self._buffer[target.top : target.bottom, target.left : target.right]

        alpha = cropped[:, :, 3:4].astype(np.uint16)
        if bool(np.all(alpha == _CHANNEL_MAX)):
            region[...] = cropped[:, :, :3]
            return
        if bool(np.all(alpha == 0)):
            return

        blended = (
            cropped[:, :, :3].astype(np.uint16) * alpha
            + region.astype(np.uint16) * (_CHANNEL_MAX - alpha)
            + _ROUNDING_BIAS
        )
        region[...] = (blended // _CHANNEL_MAX).astype(np.uint8)


def _horizontal_origin(x: int, align: HorizontalAlign, *, width: int) -> int:
    """Resolve the left edge of a text run for the requested alignment."""
    if align is HorizontalAlign.LEFT:
        return x
    if align is HorizontalAlign.CENTER:
        return x - width // 2
    return x - width


def _vertical_origin(
    y: int,
    valign: VerticalAlign,
    *,
    block_height: int,
    ascent: int,
) -> int:
    """Resolve the top edge of a text block for the requested alignment."""
    if valign is VerticalAlign.TOP:
        return y
    if valign is VerticalAlign.MIDDLE:
        return y - block_height // 2
    if valign is VerticalAlign.BOTTOM:
        return y - block_height
    return y - ascent


def _ink_bounds(font: Font, placements: list[tuple[int, int, str]]) -> Rect:
    """Union the tight ink boxes of every placed line, in canvas coordinates."""
    bounds = Rect(0, 0, 0, 0)
    for left, top, line in placements:
        if not line:
            continue
        # Pillow reports sub-pixel bounds; round outwards so antialiased edges
        # are never trimmed from the reported box.
        box = font.pil_font.getbbox(line)
        x0, y0 = math.floor(box[0]), math.floor(box[1])
        x1, y1 = math.ceil(box[2]), math.ceil(box[3])
        if x1 <= x0 or y1 <= y0:
            continue
        bounds = bounds.union(Rect(left + x0, top + y0, x1 - x0, y1 - y0))
    return bounds


def _load_rgba(source: ImageSource) -> Image.Image:
    """Coerce any supported image source into a Pillow ``RGBA`` image."""
    if isinstance(source, Canvas):
        return source.to_pil().convert("RGBA")
    if isinstance(source, Image.Image):
        return source if source.mode == "RGBA" else source.convert("RGBA")

    path = Path(source)
    try:
        with Image.open(path) as opened:
            return opened.convert("RGBA")
    except FileNotFoundError as exc:
        msg = f"image not found: {path}"
        raise ImageError(msg) from exc
    except (OSError, UnidentifiedImageError) as exc:
        msg = f"could not decode image {path}: {exc}"
        raise ImageError(msg) from exc
