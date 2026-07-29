"""Exceptions raised by the HT32 driver.

Everything here derives from :class:`~tinydisplay.core.errors.DriverError`, so
code that already handles a panel failing handles this panel failing too,
without an HT32-specific ``except`` clause.

The distinction that matters at runtime is between
:class:`DeviceNotFoundError` -- nothing is plugged in, so retrying later is
reasonable -- and :class:`ProtocolError`, which means the framing arithmetic is
wrong and retrying will fail identically.
"""

from __future__ import annotations

from tinydisplay.core.errors import DriverError

__all__ = [
    "DeviceNotFoundError",
    "HT32Error",
    "LedError",
    "ProtocolError",
    "TransportError",
]


class HT32Error(DriverError):
    """Base class for HT32 panel failures."""


class DeviceNotFoundError(HT32Error):
    """Raised when no HT32 panel is attached, or it cannot be opened.

    On Linux this is very often a permissions problem rather than a missing
    device: hidraw nodes are root-only until a udev rule says otherwise. The
    message says so, because the failure otherwise looks identical to an
    unplugged panel.
    """


class TransportError(HT32Error):
    """Raised when a write to an opened device fails.

    Usually means the panel was unplugged mid-frame. The driver treats it as
    recoverable and will re-open on the next frame if reconnection is enabled.
    """


class ProtocolError(HT32Error):
    """Raised when a frame cannot be framed into packets the panel accepts.

    This is a programming error rather than a hardware one -- a chunk that does
    not fit the report, or a frame that is not the panel's size.
    """


class LedError(HT32Error):
    """Raised when the CH340 LED bridge cannot be opened or written to.

    Separate from :class:`TransportError` because the LED strip and the panel
    are physically different devices behind different USB interfaces: the LEDs
    failing is not a reason to stop drawing.
    """
