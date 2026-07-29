"""TinyDisplay HT32 driver: a 320x170 RGB565 panel over USB HID.

This is the first real hardware in the stack, and it is built the same way the
simulator is: a thin driver over a transport that can be swapped for a recorder,
so the framing, the retry logic and the exact bytes on the wire are all testable
with nothing plugged in.

The package talks to the panel directly rather than through upstream's
``ht32paneld`` D-Bus daemon. D-Bus is effectively Linux-only, a container cannot
assume it can reach a host daemon, and frame timing wants direct access to the
write path. See the package README for the full argument.

Layering, lowest first:

- :mod:`tinydisplay.ht32.protocol` -- constants and packet building. No I/O.
- :mod:`tinydisplay.ht32.device` -- finding panels on the USB bus.
- :mod:`tinydisplay.ht32.transport` -- writing packets, for real or to memory.
- :mod:`tinydisplay.ht32.driver` -- the :class:`~tinydisplay.core.DisplayDriver`.
- :mod:`tinydisplay.ht32.led` -- the CH340 LED bridge, which is a separate
  device and deliberately not part of the driver.

Example:
    >>> import asyncio
    >>> from tinydisplay.ht32 import HT32Driver, RecordingHidTransport
    >>> async def main() -> int:
    ...     transport = RecordingHidTransport()
    ...     async with HT32Driver(transport=transport) as driver:
    ...         canvas = driver.create_canvas()
    ...         canvas.clear(Color.from_hex("#ff0000"))
    ...         await driver.show(canvas)
    ...     return len(transport.packets)
    >>> asyncio.run(main())
    27
"""

from __future__ import annotations

from tinydisplay.ht32.device import (
    HT32DeviceInfo,
    enumerate_panels,
    find_panel,
    is_hid_available,
)
from tinydisplay.ht32.driver import (
    DEFAULT_RECONNECT_ATTEMPTS,
    DEFAULT_RECONNECT_DELAY,
    HT32Driver,
)
from tinydisplay.ht32.errors import (
    DeviceNotFoundError,
    HT32Error,
    LedError,
    ProtocolError,
    TransportError,
)
from tinydisplay.ht32.hidraw import (
    HidrawDeviceInfo,
    HidrawTransport,
    enumerate_hidraw,
    find_hidraw_panel,
    is_hidraw_available,
)
from tinydisplay.ht32.led import (
    DEFAULT_BAUD_RATE,
    LedController,
    LedTheme,
    LedTransport,
    RecordingLedTransport,
    SerialLedTransport,
    build_led_packet,
    find_led_port,
)
from tinydisplay.ht32.protocol import (
    CHUNK_COUNT,
    FRAME_BYTES,
    PACKET_SIZE,
    PANEL_HEIGHT,
    PANEL_PIXEL_FORMAT,
    PANEL_WIDTH,
    PRODUCT_ID,
    VENDOR_ID,
    Command,
    RedrawPhase,
    SubCommand,
    build_config_packet,
    build_redraw_packet,
    build_refresh_packet,
    iter_redraw_packets,
)
from tinydisplay.ht32.transport import (
    DEFAULT_INIT_DELAY,
    HidTransport,
    PanelTransport,
    RecordingHidTransport,
    create_panel_transport,
)

__version__ = "0.1.0"

__all__ = [
    "CHUNK_COUNT",
    "DEFAULT_BAUD_RATE",
    "DEFAULT_INIT_DELAY",
    "DEFAULT_RECONNECT_ATTEMPTS",
    "DEFAULT_RECONNECT_DELAY",
    "FRAME_BYTES",
    "PACKET_SIZE",
    "PANEL_HEIGHT",
    "PANEL_PIXEL_FORMAT",
    "PANEL_WIDTH",
    "PRODUCT_ID",
    "VENDOR_ID",
    "Command",
    "DeviceNotFoundError",
    "HT32DeviceInfo",
    "HT32Driver",
    "HT32Error",
    "HidTransport",
    "HidrawDeviceInfo",
    "HidrawTransport",
    "LedController",
    "LedError",
    "LedTheme",
    "LedTransport",
    "PanelTransport",
    "ProtocolError",
    "RecordingHidTransport",
    "RecordingLedTransport",
    "RedrawPhase",
    "SerialLedTransport",
    "SubCommand",
    "TransportError",
    "__version__",
    "build_config_packet",
    "build_led_packet",
    "build_redraw_packet",
    "build_refresh_packet",
    "create_panel_transport",
    "enumerate_hidraw",
    "enumerate_panels",
    "find_hidraw_panel",
    "find_led_port",
    "find_panel",
    "is_hid_available",
    "is_hidraw_available",
    "iter_redraw_packets",
]
