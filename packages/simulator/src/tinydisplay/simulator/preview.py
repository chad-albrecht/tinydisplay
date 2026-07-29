"""Turning an encoded frame back into pixels a monitor can show.

The simulator deliberately previews the **encoded** frame rather than the
canvas it came from. A driver's job is to encode; if it packs the wrong
endianness or drops a channel, previewing the canvas would hide exactly the bug
the simulator exists to catch. Decoding the wire bytes means the window shows
what the panel would show, and a byte-order mistake looks as wrong on screen as
it would on hardware.

It also makes the RGB565 quantisation preview fall out for free: decoding a
16-bit frame yields the quantised colours by construction, with no separate
"simulate a cheap panel" code path that could drift from the real one.

Example:
    >>> from tinydisplay.simulator import decode_frame
    >>> canvas = Canvas(2, 1)
    >>> canvas.pixel(0, 0, Color.from_hex("#ff8040"))
    >>> pixels = decode_frame(canvas.to_rgb565(), 2, 1, PixelFormat.RGB565_LE)
    >>> tuple(int(channel) for channel in pixels[0, 0])
    (255, 130, 66)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

import numpy as np
from PIL import Image

from tinydisplay.core import Canvas, PixelFormat
from tinydisplay.simulator.errors import SimulatorError

if TYPE_CHECKING:
    import numpy.typing as npt

__all__ = ["MAX_SCALE", "decode_frame", "frame_to_canvas", "scale_nearest", "validate_scale"]

# An upper bound on the zoom factor. A 320x170 panel at 32x is already a
# 10240x5440 window, so anything beyond this is a typo rather than an intent.
MAX_SCALE: Final = 32


def decode_frame(
    frame: bytes,
    width: int,
    height: int,
    pixel_format: PixelFormat,
) -> npt.NDArray[np.uint8]:
    """Decode one encoded frame into a ``uint8[height, width, 3]`` array.

    The 16-bit formats are expanded by bit replication, matching
    :meth:`Color.from_rgb565 <tinydisplay.core.Color.from_rgb565>`, so white
    survives a pack/unpack round trip as ``#ffffff`` rather than ``#f8fcf8``.

    Raises:
        SimulatorError: If ``frame`` is not exactly the size the geometry and
            pixel format imply.
    """
    expected = width * height * pixel_format.bytes_per_pixel
    if len(frame) != expected:
        msg = (
            f"expected a {expected}-byte {pixel_format.value} frame for "
            f"{width}x{height}, got {len(frame)} bytes"
        )
        raise SimulatorError(msg)

    if pixel_format is PixelFormat.RGB888:
        flat = np.frombuffer(frame, dtype=np.uint8)
        return flat.reshape(height, width, 3).copy()

    dtype = "<u2" if pixel_format is PixelFormat.RGB565_LE else ">u2"
    words = np.frombuffer(frame, dtype=dtype).reshape(height, width)
    r5 = (words >> 11) & 0x1F
    g6 = (words >> 5) & 0x3F
    b5 = words & 0x1F

    pixels = np.empty((height, width, 3), dtype=np.uint8)
    pixels[:, :, 0] = ((r5 << 3) | (r5 >> 2)).astype(np.uint8)
    pixels[:, :, 1] = ((g6 << 2) | (g6 >> 4)).astype(np.uint8)
    pixels[:, :, 2] = ((b5 << 3) | (b5 >> 2)).astype(np.uint8)
    return pixels


def frame_to_canvas(
    frame: bytes,
    width: int,
    height: int,
    pixel_format: PixelFormat,
) -> Canvas:
    """Decode a frame into a :class:`~tinydisplay.core.Canvas`.

    Handy in tests: it lets an assertion be written against ``get_pixel`` on
    what the panel would display, rather than against raw bytes.

    Example:
        >>> from tinydisplay.simulator import frame_to_canvas
        >>> source = Canvas(4, 4)
        >>> source.clear(Color.from_hex("#123456"))
        >>> shown = frame_to_canvas(source.to_rgb565(), 4, 4, PixelFormat.RGB565_LE)
        >>> shown.get_pixel(0, 0) == Color.from_hex("#123456").quantized_rgb565()
        True
    """
    pixels = decode_frame(frame, width, height, pixel_format)
    return Canvas.from_pil(Image.fromarray(pixels, mode="RGB"))


def validate_scale(scale: int) -> int:
    """Check that ``scale`` is a usable magnification factor and return it.

    Exposed separately so the driver can reject a bad scale at construction
    rather than on the first frame.

    Raises:
        SimulatorError: If ``scale`` is not in ``1..MAX_SCALE``.
    """
    if not 1 <= scale <= MAX_SCALE:
        msg = f"scale must be in 1..{MAX_SCALE}, got {scale}"
        raise SimulatorError(msg)
    return scale


def scale_nearest(pixels: npt.NDArray[np.uint8], scale: int) -> npt.NDArray[np.uint8]:
    """Magnify an image by an integer factor using nearest-neighbour sampling.

    Nearest neighbour is not a shortcut here, it is the point. A smooth filter
    would blur away the banding that RGB565 quantisation introduces, which is
    the artefact the operator is meant to be able to see.

    Raises:
        SimulatorError: If ``scale`` is not in ``1..MAX_SCALE``.
    """
    validate_scale(scale)
    if scale == 1:
        return pixels
    return pixels.repeat(scale, axis=0).repeat(scale, axis=1)
