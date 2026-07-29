"""TinyDisplay core: a hardware-agnostic rendering engine for small displays.

The engine is three layers, each ignorant of the one below it:

.. code-block:: text

    Widget          paints itself into a rectangle
      |
      v
    Canvas          an RGB framebuffer with drawing primitives
      |
      v
    DisplayDriver   encodes the framebuffer and pushes it to hardware

Nothing in this package imports a hardware library, and nothing here knows
Home Assistant exists. Both live in sibling packages that depend on this one.

Example:
    >>> canvas = Canvas(240, 240)
    >>> canvas.clear(Color.BLACK)
    >>> _ = canvas.text(10, 10, "Hello", Color.WHITE)
    >>> canvas.save("preview.png")  # doctest: +SKIP
"""

from __future__ import annotations

from tinydisplay.core.canvas import MAX_DIMENSION, Canvas, ImageSource
from tinydisplay.core.color import Color
from tinydisplay.core.driver import DisplayDriver, MemoryDriver, PixelFormat
from tinydisplay.core.errors import (
    CanvasError,
    DriverError,
    DriverNotConnectedError,
    FontError,
    ImageError,
    TinyDisplayError,
)
from tinydisplay.core.font import (
    DEFAULT_FONT_SIZE,
    Font,
    HorizontalAlign,
    VerticalAlign,
)
from tinydisplay.core.geometry import Point, Rect, Size
from tinydisplay.core.widget import Container, Widget

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_FONT_SIZE",
    "MAX_DIMENSION",
    "Canvas",
    "CanvasError",
    "Color",
    "Container",
    "DisplayDriver",
    "DriverError",
    "DriverNotConnectedError",
    "Font",
    "FontError",
    "HorizontalAlign",
    "ImageError",
    "ImageSource",
    "MemoryDriver",
    "PixelFormat",
    "Point",
    "Rect",
    "Size",
    "TinyDisplayError",
    "VerticalAlign",
    "Widget",
    "__version__",
]
