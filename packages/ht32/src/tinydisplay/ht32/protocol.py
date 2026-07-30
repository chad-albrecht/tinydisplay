"""The HT32 wire protocol: constants and packet construction, and nothing else.

This module performs no I/O. Every function here turns arguments into bytes,
which makes the part of the driver most likely to be wrong -- framing
arithmetic -- exhaustively testable with no device attached. The transport
layer above it does the writing and knows nothing about packet layout.

A packet is a fixed 4105 bytes regardless of payload: one HID report-ID byte,
an eight-byte header, and a 4096-byte data area that the final chunk leaves
partly zeroed. HID output reports are fixed-size, so short-packing the last
chunk would simply be padded by the OS anyway -- doing it here keeps the
written length constant and the write path free of special cases.

Frame layout::

    byte 0      HID report ID, always 0x00
    byte 1      signature, always 0x55
    byte 2      command
    byte 3      sub-command, or redraw phase
    byte 4      sequence number, 1-based
    byte 5      reserved, always 0x00
    bytes 6-7   pixel offset into the frame, big-endian
    bytes 8     high byte of the chunk size
    bytes 9+    pixel data, RGB565 big-endian

Two details of that layout are worth spelling out, because both look like bugs
until you do the arithmetic.

**The chunk size is effectively one byte.** Upstream writes the size as a
big-endian pair at bytes 8 and 9, then copies pixel data starting at byte 9 --
so the low byte is overwritten immediately and never reaches the panel. It does
not matter: both legal chunk sizes (4096 and 2304) are exact multiples of 256,
so the low byte is always zero, and the bytes that go out on the wire are the
same either way. This implementation writes only byte 8 and lets the pixel data
own byte 9, which produces a byte-identical packet without the dead store.

**The offset is counted in pixels, not bytes.** The field is 16 bits, and the
frame is 108,800 bytes -- which does not fit -- but 54,400 pixels, which does.
A byte offset would overflow at the twelfth chunk; a pixel offset tops out at
53,248 and fits with room to spare. See :data:`CHUNK_PIXEL_OFFSETS`.

Hardware bring-up later settled both questions and added a third. Independent
documentation for this panel states that bytes 4 to 7 are *ignored by the
firmware* outright, so the offset reading never mattered. And byte 0 is a HID
report ID, which is a convention of the ``hidraw`` API rather than part of the
protocol: the device's own byte 0 is the signature. Transports that do not go
through hidraw must drop it -- see :func:`device_payload`.

Example:
    >>> from tinydisplay.ht32.protocol import FRAME_BYTES, build_redraw_packet
    >>> packet = build_redraw_packet(bytes(FRAME_BYTES), 0)
    >>> len(packet), packet[1], hex(packet[2])
    (4105, 85, '0xa3')
"""

from __future__ import annotations

from enum import IntEnum
from typing import Final

from tinydisplay.core import PixelFormat
from tinydisplay.ht32.errors import ProtocolError

__all__ = [
    "BYTES_PER_PIXEL",
    "CHUNK_COUNT",
    "CHUNK_PIXEL_OFFSETS",
    "CHUNK_SIZES",
    "DATA_SIZE",
    "FINAL_CHUNK_SIZE",
    "FRAME_BYTES",
    "HEADER_SIZE",
    "ORIENTATION_LANDSCAPE",
    "ORIENTATION_PORTRAIT",
    "PACKET_SIZE",
    "PANEL_HEIGHT",
    "PANEL_PIXEL_FORMAT",
    "PANEL_WIDTH",
    "PRODUCT_ID",
    "REPORT_SIZE",
    "SIGNATURE",
    "VENDOR_ID",
    "Command",
    "RedrawPhase",
    "SubCommand",
    "build_config_packet",
    "build_heartbeat_packet",
    "build_orientation_packet",
    "build_redraw_packet",
    "build_refresh_packet",
    "device_payload",
    "iter_redraw_packets",
]

# -- The panel ---------------------------------------------------------------

PANEL_WIDTH: Final = 320
PANEL_HEIGHT: Final = 170

#: The panel takes RGB565 most-significant byte first. Core already packs this
#: byte order, so the driver encodes once and the chunker copies slices
#: verbatim -- there is no per-pixel byte swapping anywhere in the write path.
PANEL_PIXEL_FORMAT: Final = PixelFormat.RGB565_BE

VENDOR_ID: Final = 0x04D9
PRODUCT_ID: Final = 0xFD01

#: The panel exposes several HID interfaces; display data goes to this one.
LCD_INTERFACE: Final = 1

# -- Packet geometry ---------------------------------------------------------

SIGNATURE: Final = 0x55
REPORT_SIZE: Final = 1
HEADER_SIZE: Final = 8
DATA_SIZE: Final = 4096
PACKET_SIZE: Final = REPORT_SIZE + HEADER_SIZE + DATA_SIZE

BYTES_PER_PIXEL: Final = 2
PIXELS_PER_CHUNK: Final = DATA_SIZE // BYTES_PER_PIXEL

FRAME_PIXELS: Final = PANEL_WIDTH * PANEL_HEIGHT
FRAME_BYTES: Final = FRAME_PIXELS * BYTES_PER_PIXEL

#: 108,800 bytes is 26 full chunks plus a 2,304-byte remainder.
CHUNK_COUNT: Final = -(-FRAME_BYTES // DATA_SIZE)
FINAL_CHUNK_SIZE: Final = FRAME_BYTES - (CHUNK_COUNT - 1) * DATA_SIZE

#: Payload size of each chunk, in order. Derived rather than hard-coded so the
#: 27/2304 figures in the upstream source are reproduced, not trusted.
CHUNK_SIZES: Final = tuple(
    DATA_SIZE if index < CHUNK_COUNT - 1 else FINAL_CHUNK_SIZE for index in range(CHUNK_COUNT)
)

#: Where each chunk starts, counted in pixels -- see the module docstring.
CHUNK_PIXEL_OFFSETS: Final = tuple(index * PIXELS_PER_CHUNK for index in range(CHUNK_COUNT))

#: The offset field is two bytes wide, so the frame must not outgrow it.
_MAX_PIXEL_OFFSET: Final = 0xFFFF


class Command(IntEnum):
    """Top-level command, written to byte 2."""

    CONFIG = 0xA1
    REFRESH = 0xA2
    REDRAW = 0xA3


class SubCommand(IntEnum):
    """Qualifier for :attr:`Command.CONFIG`, written to byte 3.

    Upstream defines these two. Neither the orientation codes nor a brightness
    command are documented anywhere the wire format can be recovered from, so
    this package does not guess at them -- see
    :func:`build_config_packet` for the escape hatch.
    """

    ORIENTATION = 0xF1
    SET_TIME = 0xF2


#: Byte 4 of an orientation command, as upstream writes them.
#:
#: **These do nothing on the AceMagic S1.** An earlier version of this comment
#: said "confirmed against hardware"; that was never true. The only bring-up
#: that had run went through the ``frame`` subcommand, which sends no
#: orientation packet at all, so what had been confirmed was the panel's
#: power-on state and not these values.
#:
#: Sweeping 0x00 through 0x04 against a real panel changed nothing, so the
#: command appears to be unimplemented in this firmware. The S1's panel is
#: mounted upside down and the host has to compensate -- see
#: :class:`~tinydisplay.ht32.driver.HT32Driver`'s ``rotate_180``.
ORIENTATION_LANDSCAPE: Final = 0x01
ORIENTATION_PORTRAIT: Final = 0x02


class RedrawPhase(IntEnum):
    """Which part of a frame a redraw packet carries, written to byte 3.

    The panel uses these to bracket a frame: it begins accepting pixels at
    :attr:`START` and latches the result at :attr:`END`.
    """

    START = 0xF0
    CONTINUE = 0xF1
    END = 0xF2


def _phase_for(chunk_index: int) -> RedrawPhase:
    """Which phase the chunk at ``chunk_index`` belongs to."""
    if chunk_index == 0:
        return RedrawPhase.START
    if chunk_index == CHUNK_COUNT - 1:
        return RedrawPhase.END
    return RedrawPhase.CONTINUE


def _new_packet() -> bytearray:
    """A zeroed packet with the report ID and signature already in place."""
    packet = bytearray(PACKET_SIZE)
    packet[1] = SIGNATURE
    return packet


def build_redraw_packet(frame: bytes, chunk_index: int) -> bytes:
    """Build the redraw packet carrying chunk ``chunk_index`` of ``frame``.

    Args:
        frame: A full frame, already encoded as RGB565 big-endian -- exactly
            what :attr:`PANEL_PIXEL_FORMAT` produces.
        chunk_index: Which chunk to build, from 0 to :data:`CHUNK_COUNT` - 1.

    Returns:
        A :data:`PACKET_SIZE`-byte packet, zero-padded on the final chunk.

    Raises:
        ProtocolError: If the frame is the wrong size or the index is out of
            range. Both mean the caller's arithmetic is wrong, not the panel's.

    Example:
        >>> from tinydisplay.ht32.protocol import CHUNK_COUNT, FRAME_BYTES
        >>> from tinydisplay.ht32.protocol import build_redraw_packet
        >>> last = build_redraw_packet(bytes(FRAME_BYTES), CHUNK_COUNT - 1)
        >>> hex(last[3]), last[4]  # end phase, 1-based sequence number
        ('0xf2', 27)
    """
    if len(frame) != FRAME_BYTES:
        msg = (
            f"HT32 expects a {FRAME_BYTES}-byte RGB565 frame "
            f"({PANEL_WIDTH}x{PANEL_HEIGHT}), got {len(frame)} bytes"
        )
        raise ProtocolError(msg)
    if not 0 <= chunk_index < CHUNK_COUNT:
        msg = f"chunk index must be in [0, {CHUNK_COUNT}), got {chunk_index}"
        raise ProtocolError(msg)

    size = CHUNK_SIZES[chunk_index]
    pixel_offset = CHUNK_PIXEL_OFFSETS[chunk_index]
    if pixel_offset > _MAX_PIXEL_OFFSET:  # pragma: no cover - fixed-size panel
        msg = f"pixel offset {pixel_offset} does not fit the panel's 16-bit offset field"
        raise ProtocolError(msg)

    packet = _new_packet()
    packet[2] = Command.REDRAW
    packet[3] = _phase_for(chunk_index)
    packet[4] = chunk_index + 1
    packet[5] = 0x00
    packet[6] = (pixel_offset >> 8) & 0xFF
    packet[7] = pixel_offset & 0xFF
    # Only the high byte: byte 9 belongs to the pixel data. See the module
    # docstring for why that loses nothing.
    packet[8] = (size >> 8) & 0xFF

    start = chunk_index * DATA_SIZE
    data_start = REPORT_SIZE + HEADER_SIZE
    packet[data_start : data_start + size] = frame[start : start + size]
    return bytes(packet)


def iter_redraw_packets(frame: bytes) -> tuple[bytes, ...]:
    """Build every packet needed to paint ``frame``, in transmission order.

    Returns a tuple rather than a generator: the packets are built before the
    first write goes out, so a framing error fails the frame cleanly instead of
    leaving the panel halfway through a redraw it will never see the end of.

    Example:
        >>> from tinydisplay.ht32.protocol import FRAME_BYTES, iter_redraw_packets
        >>> packets = iter_redraw_packets(bytes(FRAME_BYTES))
        >>> len(packets), {len(p) for p in packets}
        (27, {4105})
    """
    return tuple(build_redraw_packet(frame, index) for index in range(CHUNK_COUNT))


def build_config_packet(sub_command: SubCommand | int, params: bytes = b"") -> bytes:
    """Build a configuration packet.

    This is the escape hatch for commands whose payloads are not documented:
    the framing is known even where the parameters are not, so bring-up work
    against a real panel can probe them without patching this module.

    Args:
        sub_command: Byte 3 of the packet.
        params: Bytes 4 onward. At most :data:`DATA_SIZE` bytes.

    Raises:
        ProtocolError: If ``params`` is too long to fit the packet.

    Example:
        >>> from tinydisplay.ht32.protocol import SubCommand, build_config_packet
        >>> packet = build_config_packet(SubCommand.SET_TIME, bytes([12, 30, 0]))
        >>> hex(packet[2]), hex(packet[3]), list(packet[4:7])
        ('0xa1', '0xf2', [12, 30, 0])
    """
    params_start = REPORT_SIZE + 3
    if len(params) > PACKET_SIZE - params_start:
        msg = f"config parameters must fit in {PACKET_SIZE - params_start} bytes, got {len(params)}"
        raise ProtocolError(msg)

    packet = _new_packet()
    packet[2] = Command.CONFIG
    packet[3] = int(sub_command)
    packet[params_start : params_start + len(params)] = params
    return bytes(packet)


def device_payload(packet: bytes) -> bytes:
    """Strip the HID report-ID byte, leaving what the device actually receives.

    ``hidraw`` and hidapi both take the report ID as the first byte of the
    buffer and remove it before the report reaches the device. A transport that
    talks to the USB endpoint directly does no such thing, so it must drop the
    byte itself or the firmware sees its signature one position late and
    silently discards the frame.

    Example:
        >>> from tinydisplay.ht32.protocol import SIGNATURE, build_refresh_packet
        >>> from tinydisplay.ht32.protocol import device_payload
        >>> payload = device_payload(build_refresh_packet())
        >>> len(payload), payload[0] == SIGNATURE
        (4104, True)
    """
    return packet[REPORT_SIZE:]


def build_orientation_packet(*, landscape: bool = True) -> bytes:
    """Build the orientation command upstream sends.

    **This has no observable effect on an AceMagic S1.** Values 0x00 through
    0x04 were swept against a real panel and none of them changed the image,
    so the command appears to be unimplemented in this firmware. The framing is
    still known to be right -- it is the same config packet as the heartbeat --
    so this is kept for panels that may honour it, and for anyone probing
    further with :func:`build_config_packet`.

    Do not reach for this to correct a sideways or upside-down picture. On the
    S1 the panel is mounted upside down and the host compensates; see
    :class:`~tinydisplay.ht32.driver.HT32Driver`'s ``rotate_180``.

    Example:
        >>> from tinydisplay.ht32.protocol import build_orientation_packet
        >>> list(build_orientation_packet()[:5])
        [0, 85, 161, 241, 1]
    """
    return build_config_packet(
        SubCommand.ORIENTATION,
        bytes([ORIENTATION_LANDSCAPE if landscape else ORIENTATION_PORTRAIT]),
    )


def build_heartbeat_packet(hour: int, minute: int, second: int) -> bytes:
    """Build the keep-alive the panel expects roughly once a second.

    The panel treats a missing heartbeat as the host having gone away, and
    paints its own "Disconnection, content information display will not be
    allowed!" banner over whatever was on screen. A driver that draws once and
    stops therefore sees its frame defaced a moment later, which looks like a
    rendering bug and is not one.

    Raises:
        ProtocolError: If any component is out of range.

    Example:
        >>> from tinydisplay.ht32.protocol import build_heartbeat_packet
        >>> list(build_heartbeat_packet(14, 30, 5)[:7])
        [0, 85, 161, 242, 14, 30, 5]
    """
    for name, value, limit in (
        ("hour", hour, 23),
        ("minute", minute, 59),
        ("second", second, 60),
    ):
        if not 0 <= value <= limit:
            msg = f"{name} must be between 0 and {limit}, got {value}"
            raise ProtocolError(msg)
    return build_config_packet(SubCommand.SET_TIME, bytes([hour, minute, second]))


def build_refresh_packet() -> bytes:
    """Build a refresh packet, asking the panel to repaint what it already has.

    Example:
        >>> from tinydisplay.ht32.protocol import build_refresh_packet
        >>> packet = build_refresh_packet()
        >>> hex(packet[2]), len(packet)
        ('0xa2', 4105)
    """
    packet = _new_packet()
    packet[2] = Command.REFRESH
    return bytes(packet)
