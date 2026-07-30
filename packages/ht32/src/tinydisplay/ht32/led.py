"""LED strip control over the panel's CH340 serial bridge.

The LEDs are a physically separate device from the panel: a CH340 USB-to-serial
bridge on its own interface, speaking a five-byte protocol at 10,000 baud. That
separation is reflected here -- the LED controller is not part of
:class:`~tinydisplay.ht32.driver.HT32Driver`, and the LEDs failing is not a
reason to stop drawing frames.

The protocol is five bytes::

    byte 0   signature, always 0xFA
    byte 1   theme
    byte 2   intensity, inverted: 6 - level
    byte 3   speed, inverted: 6 - level
    byte 4   checksum, the low byte of the sum of bytes 0-3

Levels run 1 to 5 and are inverted on the wire. This package takes them the way
round a person would expect -- 5 is brightest and fastest -- and does the
inversion when building the packet, because a caller should not have to know
that the firmware counts backwards.

10,000 baud is not a standard rate, and the bridge is slow enough that bytes
must be paced: upstream waits 5 ms between them, and so does this.

**The protocol carries no colour.** One byte selects an effect and there is no
colour field, which is a property of the hardware rather than a gap in this
reconstruction: the CH340 is only a UART bridge, and behind it sits a custom
microcontroller on the S1's motherboard whose firmware owns the colours. Three
independent implementations agree on the same five-byte packet and the same
five effects, and sweeping theme bytes 0x06 through 0x0C against a real strip
produced no response at all.

**A solid colour is reported to be reachable by restarting an effect faster
than it animates**, since each one begins from a fixed colour --
``fsncps/acemagic-ledctl`` holds red this way from :attr:`LedTheme.COLORS` and
blue-purple from :attr:`LedTheme.RAINBOW`, at roughly 40 restarts a second.

**It does not work on the AceMagic S1.** Implemented here and tried against
real hardware, it flickered rather than holding: the strip visibly restarts on
each command instead of sitting at the first frame. The implementation was
removed rather than left in as something that half-works. That project targets
the ACEMAGIC T9, so the likeliest explanation is that the two machines' LED
microcontrollers differ in how they handle a command arriving mid-animation.

The arithmetic is worth keeping even so, because it bounds any retry: a packet
is five bytes paced 5 ms apart with no delay after the last, so 20 ms, so fifty
a second is the ceiling. "Roughly 40 Hz" is the link running flat out, not a
tuned figure -- there is no headroom to go faster with.

Example:
    >>> from tinydisplay.ht32.led import LedTheme, build_led_packet
    >>> list(build_led_packet(LedTheme.BREATHING, intensity=5, speed=1))
    [250, 2, 1, 5, 2]
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from enum import IntEnum
from typing import TYPE_CHECKING, Any, Final, Protocol, runtime_checkable

from tinydisplay.ht32.errors import LedError

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "DEFAULT_BAUD_RATE",
    "INTER_BYTE_DELAY",
    "LED_SIGNATURE",
    "LEVEL_MAX",
    "LEVEL_MIN",
    "LedController",
    "LedTheme",
    "LedTransport",
    "RecordingLedTransport",
    "SerialLedTransport",
    "build_led_packet",
    "find_led_port",
    "led_packet_summary",
]

LED_SIGNATURE: Final = 0xFA
PACKET_LENGTH: Final = 5

#: Not a standard rate. The bridge is configured for it in firmware, and a
#: nearby standard rate does not work.
DEFAULT_BAUD_RATE: Final = 10_000

#: Seconds between bytes. The bridge drops bytes written back to back.
INTER_BYTE_DELAY: Final = 0.005

LEVEL_MIN: Final = 1
LEVEL_MAX: Final = 5

#: Levels are sent inverted, so a level of 5 goes out as 1.
_LEVEL_INVERSION: Final = LEVEL_MAX + 1

#: QinHeng Electronics CH340. Used to pick the right serial port when the
#: caller does not name one.
CH340_VENDOR_ID: Final = 0x1A86
CH340_PRODUCT_ID: Final = 0x7523


class LedTheme(IntEnum):
    """Lighting effect.

    These five are the whole command set, not the subset anyone has decoded so
    far -- see the module docstring. ``COLORS`` cycles solid colours, which is
    as close as this hardware comes to being told to show one.
    """

    RAINBOW = 0x01
    BREATHING = 0x02
    COLORS = 0x03
    OFF = 0x04
    AUTO = 0x05


def _validate_level(name: str, level: int) -> int:
    """Check that ``level`` is in range and return its inverted wire value."""
    if not LEVEL_MIN <= level <= LEVEL_MAX:
        msg = f"{name} must be between {LEVEL_MIN} and {LEVEL_MAX}, got {level}"
        raise LedError(msg)
    return _LEVEL_INVERSION - level


def build_led_packet(theme: LedTheme, *, intensity: int = 3, speed: int = 3) -> bytes:
    """Build one LED command packet.

    Args:
        theme: The lighting effect.
        intensity: Brightness from 1 to 5, where 5 is brightest.
        speed: Effect speed from 1 to 5, where 5 is fastest.

    Raises:
        LedError: If a level is out of range.

    Example:
        >>> from tinydisplay.ht32.led import LedTheme, build_led_packet
        >>> packet = build_led_packet(LedTheme.OFF, intensity=1, speed=1)
        >>> list(packet), packet[4] == sum(packet[:4]) & 0xFF
        ([250, 4, 5, 5, 8], True)
    """
    body = bytes(
        [
            LED_SIGNATURE,
            int(theme),
            _validate_level("intensity", intensity),
            _validate_level("speed", speed),
        ]
    )
    return body + bytes([sum(body) & 0xFF])


@runtime_checkable
class LedTransport(Protocol):
    """Somewhere LED packets can be written."""

    @property
    def is_open(self) -> bool:
        """Whether the transport is able to accept packets."""
        ...

    def open(self) -> None:
        """Make the transport ready. Idempotent."""
        ...

    def write(self, packet: bytes) -> None:
        """Write one packet, pacing the bytes as the bridge requires."""
        ...

    def close(self) -> None:
        """Release the transport. Idempotent."""
        ...


class RecordingLedTransport:
    """An LED transport that records packets instead of sending them.

    Example:
        >>> from tinydisplay.ht32.led import LedTheme, RecordingLedTransport
        >>> transport = RecordingLedTransport()
        >>> transport.open()
        >>> transport.write(bytes([250, 4, 5, 5, 8]))
        >>> len(transport.packets)
        1
    """

    def __init__(self, *, fail_on_open: bool = False) -> None:
        self.fail_on_open = fail_on_open
        self._packets: list[bytes] = []
        self._is_open = False
        self._open_count = 0

    @property
    def is_open(self) -> bool:
        """Whether :meth:`open` has been called without a matching :meth:`close`."""
        return self._is_open

    @property
    def packets(self) -> tuple[bytes, ...]:
        """Every packet written, oldest first."""
        return tuple(self._packets)

    @property
    def last_packet(self) -> bytes | None:
        """The most recent packet, or ``None``."""
        return self._packets[-1] if self._packets else None

    @property
    def open_count(self) -> int:
        """How many times the transport has been opened."""
        return self._open_count

    def open(self) -> None:
        """Mark the transport open."""
        if self.fail_on_open:
            msg = "no CH340 LED bridge found (RecordingLedTransport configured to fail)"
            raise LedError(msg)
        if self._is_open:
            return
        self._is_open = True
        self._open_count += 1

    def write(self, packet: bytes) -> None:
        """Record ``packet``.

        Raises:
            LedError: If the transport is closed.
        """
        if not self._is_open:
            msg = "LED transport is not open"
            raise LedError(msg)
        self._packets.append(packet)

    def close(self) -> None:
        """Mark the transport closed."""
        self._is_open = False


def find_led_port(
    *,
    vendor_id: int = CH340_VENDOR_ID,
    product_id: int = CH340_PRODUCT_ID,
) -> str:
    """Find the CH340 bridge's serial port.

    Matching is by USB VID:PID, falling back to any port whose description
    mentions CH340 -- some drivers report the bridge without USB identifiers.

    Raises:
        LedError: If pyserial is not installed, or no bridge is attached.
    """
    try:
        from serial.tools import list_ports
    except ImportError as exc:
        msg = (
            "the pyserial package is required for LED control; "
            "install it with `pip install tinydisplay-ht32[led]`"
        )
        raise LedError(msg) from exc

    ports = list(list_ports.comports())
    for port in ports:
        if port.vid == vendor_id and port.pid == product_id:
            return str(port.device)
    for port in ports:
        if "ch340" in f"{port.description} {port.manufacturer}".lower():
            return str(port.device)

    msg = (
        f"no CH340 LED bridge found at {vendor_id:04X}:{product_id:04X}; "
        "check that the panel is plugged in, and on Linux that you are in the dialout group"
    )
    raise LedError(msg)


class SerialLedTransport:
    """Writes LED packets over a CH340 serial bridge.

    Args:
        port: Serial port to open, such as ``/dev/ttyUSB0`` or ``COM3``.
            Defaults to discovering the bridge.
        baud_rate: Line rate. The default is the only one the bridge accepts.
        inter_byte_delay: Seconds to wait between bytes.

    pyserial is imported lazily, so the LED protocol stays importable and
    testable on a machine without it.
    """

    def __init__(
        self,
        *,
        port: str | None = None,
        baud_rate: int = DEFAULT_BAUD_RATE,
        inter_byte_delay: float = INTER_BYTE_DELAY,
    ) -> None:
        self._port = port
        self._baud_rate = baud_rate
        self._inter_byte_delay = inter_byte_delay
        self._serial: Any = None
        self._is_open = False

    @property
    def is_open(self) -> bool:
        """Whether the serial port is open."""
        return self._is_open

    @property
    def port(self) -> str | None:
        """The port this transport opened, once known."""
        return self._port

    def open(self) -> None:
        """Open the serial port.

        Raises:
            LedError: If pyserial is missing, or the port cannot be opened.
        """
        if self._is_open:
            return
        try:
            import serial
        except ImportError as exc:
            msg = (
                "the pyserial package is required for LED control; "
                "install it with `pip install tinydisplay-ht32[led]`"
            )
            raise LedError(msg) from exc

        port = self._port or find_led_port()
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=self._baud_rate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=1.0,
            )
        except (OSError, serial.SerialException) as exc:
            msg = f"could not open the LED bridge on {port}: {exc}"
            raise LedError(msg) from exc

        self._port = port
        self._is_open = True

    def write(self, packet: bytes) -> None:
        """Write ``packet`` one byte at a time, pacing as the bridge requires.

        Raises:
            LedError: If the transport is closed or the write fails.
        """
        if not self._is_open or self._serial is None:
            msg = "LED transport is not open; call open() first"
            raise LedError(msg)

        try:
            for index, byte in enumerate(packet):
                self._serial.write(bytes([byte]))
                self._serial.flush()
                if index < len(packet) - 1:
                    time.sleep(self._inter_byte_delay)
        except OSError as exc:
            self.close()
            msg = f"LED write failed: {exc}"
            raise LedError(msg) from exc

    def close(self) -> None:
        """Close the serial port if it is open."""
        serial_port, self._serial = self._serial, None
        self._is_open = False
        if serial_port is None:
            return
        # An already-gone bridge is the expected case; nothing left to release.
        with contextlib.suppress(OSError):
            serial_port.close()


class LedController:
    """Set the panel's LED effect.

    Args:
        transport: Where packets go. Defaults to a real serial connection; pass
            a :class:`RecordingLedTransport` to run with no hardware.
        port: Serial port, when using the default transport.

    Example:
        >>> import asyncio
        >>> from tinydisplay.ht32.led import LedController, LedTheme
        >>> from tinydisplay.ht32.led import RecordingLedTransport
        >>> async def main() -> bytes | None:
        ...     transport = RecordingLedTransport()
        ...     async with LedController(transport=transport) as leds:
        ...         await leds.set_theme(LedTheme.RAINBOW, intensity=5, speed=2)
        ...     return transport.last_packet
        >>> list(asyncio.run(main()))
        [250, 1, 1, 4, 0]
    """

    def __init__(
        self,
        *,
        transport: LedTransport | None = None,
        port: str | None = None,
    ) -> None:
        self._transport = transport if transport is not None else SerialLedTransport(port=port)
        self._owns_transport = transport is None
        self._theme: LedTheme | None = None

    @property
    def transport(self) -> LedTransport:
        """Where packets are written."""
        return self._transport

    @property
    def is_connected(self) -> bool:
        """Whether the bridge is open."""
        return self._transport.is_open

    @property
    def theme(self) -> LedTheme | None:
        """The most recently set theme, or ``None`` if none has been set."""
        return self._theme

    async def connect(self) -> None:
        """Open the bridge."""
        await asyncio.to_thread(self._transport.open)

    async def disconnect(self) -> None:
        """Close the bridge, if this controller opened it."""
        if self._owns_transport:
            await asyncio.to_thread(self._transport.close)

    async def set_theme(
        self,
        theme: LedTheme,
        *,
        intensity: int = 3,
        speed: int = 3,
    ) -> None:
        """Set the lighting effect.

        Raises:
            LedError: If a level is out of range, or the write fails.
        """
        packet = build_led_packet(theme, intensity=intensity, speed=speed)
        await self._send(packet)
        self._theme = theme

    async def off(self) -> None:
        """Turn the LEDs off."""
        await self.set_theme(LedTheme.OFF, intensity=LEVEL_MIN, speed=LEVEL_MIN)

    async def _send(self, packet: bytes) -> None:
        """Write one packet off the event loop."""
        if not self._transport.is_open:
            await asyncio.to_thread(self._transport.open)
        await asyncio.to_thread(self._transport.write, packet)

    async def __aenter__(self) -> LedController:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.disconnect()


def led_packet_summary(packets: Sequence[bytes]) -> str:
    """A one-line description of LED packets, for logs and test failures."""
    if not packets:
        return "0 LED packets"
    themes = ", ".join(LedTheme(packet[1]).name.lower() for packet in packets)
    return f"{len(packets)} LED packets: {themes}"
