"""Exception hierarchy for TinyDisplay.

Every exception raised deliberately by TinyDisplay derives from
:class:`TinyDisplayError`, so embedders can catch the whole framework with a
single ``except`` clause without also swallowing genuine bugs such as
``TypeError``.
"""

from __future__ import annotations

__all__ = [
    "CanvasError",
    "DriverError",
    "DriverNotConnectedError",
    "FontError",
    "ImageError",
    "TinyDisplayError",
]


class TinyDisplayError(Exception):
    """Base class for all errors raised by TinyDisplay."""


class CanvasError(TinyDisplayError):
    """Raised when a canvas is constructed or manipulated illegally."""


class FontError(TinyDisplayError):
    """Raised when a font cannot be loaded or measured."""


class ImageError(TinyDisplayError):
    """Raised when an image cannot be decoded or converted."""


class DriverError(TinyDisplayError):
    """Base class for display-driver failures."""


class DriverNotConnectedError(DriverError):
    """Raised when a driver operation requires an established connection.

    Drivers raise this from :meth:`~tinydisplay.core.driver.DisplayDriver.show`
    and similar methods when ``connect()`` has not been called, or when the
    transport has since been closed.
    """
