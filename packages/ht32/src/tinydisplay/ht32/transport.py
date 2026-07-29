"""Transports: the only part of the HT32 package that touches USB.

The transport is a protocol with two implementations, exactly as the simulator
does with its preview window:

- :class:`HidTransport` writes packets to a real panel.
- :class:`RecordingHidTransport` keeps them in memory.

The recorder is not a mock. It satisfies the same protocol, the driver cannot
tell them apart, and it is what lets the entire framing and reconnection path
run in CI with nothing plugged in -- while still asserting on the exact bytes
that would have gone out on the wire.

Everything here is synchronous and blocking. USB writes are blocking calls, and
pretending otherwise inside the transport would mean either a thread pool per
transport or a false promise. :class:`~tinydisplay.ht32.driver.HT32Driver` hands
whole frames to :func:`asyncio.to_thread`, so the event loop stays free and the
transport stays simple.
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING, Any, Final, NoReturn, Protocol, runtime_checkable

from tinydisplay.ht32.device import HT32DeviceInfo, find_panel, import_hid
from tinydisplay.ht32.errors import DeviceNotFoundError, TransportError
from tinydisplay.ht32.hidraw import HidrawTransport, enumerate_hidraw, is_hidraw_available
from tinydisplay.ht32.protocol import PACKET_SIZE, PRODUCT_ID, VENDOR_ID

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "DEFAULT_INIT_DELAY",
    "HidTransport",
    "PanelTransport",
    "RecordingHidTransport",
    "create_panel_transport",
    "packet_summary",
]

#: The panel enumerates before it is ready to be spoken to. Upstream waits a
#: full second after opening the device and before the first command; frames
#: written inside that window are silently dropped.
DEFAULT_INIT_DELAY: Final = 1.0


@runtime_checkable
class PanelTransport(Protocol):
    """Somewhere packets can be written."""

    @property
    def is_open(self) -> bool:
        """Whether the transport is currently able to accept packets."""
        ...

    def open(self) -> None:
        """Make the transport ready to accept packets. Idempotent."""
        ...

    def write(self, packet: bytes) -> None:
        """Write one packet. Blocks until the OS has taken it."""
        ...

    def close(self) -> None:
        """Release the transport. Idempotent."""
        ...


class RecordingHidTransport:
    """A transport that records packets instead of writing them to hardware.

    Args:
        max_packets: How many recent packets to retain. ``None`` keeps every
            packet, which is right for a test and unbounded for a long run.
        fail_after: Raise :class:`~tinydisplay.ht32.errors.TransportError` once
            this many packets have been written successfully, then close. This
            is how the reconnection path is tested: it is what an unplugged
            panel looks like from inside :meth:`write`. The failure fires
            **once** -- a panel that is plugged back in starts working again,
            and a recorder that failed forever could not test recovery.
        fail_on_open: Raise
            :class:`~tinydisplay.ht32.errors.DeviceNotFoundError` from
            :meth:`open`, standing in for a panel that is not attached.

    Example:
        >>> from tinydisplay.ht32.transport import RecordingHidTransport
        >>> transport = RecordingHidTransport()
        >>> transport.open()
        >>> transport.write(bytes(4105))
        >>> len(transport.packets), transport.open_count
        (1, 1)
    """

    def __init__(
        self,
        *,
        max_packets: int | None = None,
        fail_after: int | None = None,
        fail_on_open: bool = False,
    ) -> None:
        self._max_packets = max_packets
        self._fail_after = fail_after
        self.fail_on_open = fail_on_open
        self._packets: list[bytes] = []
        self._write_count = 0
        self._open_count = 0
        self._is_open = False

    @property
    def is_open(self) -> bool:
        """Whether :meth:`open` has been called without a matching :meth:`close`."""
        return self._is_open

    @property
    def packets(self) -> tuple[bytes, ...]:
        """Every retained packet, oldest first."""
        return tuple(self._packets)

    @property
    def last_packet(self) -> bytes | None:
        """The most recent packet, or ``None`` if nothing has been written."""
        return self._packets[-1] if self._packets else None

    @property
    def write_count(self) -> int:
        """How many packets have been written, including any dropped by ``max_packets``."""
        return self._write_count

    @property
    def open_count(self) -> int:
        """How many times the transport has been opened, for reconnection assertions."""
        return self._open_count

    def open(self) -> None:
        """Mark the transport open."""
        if self.fail_on_open:
            msg = "no HT32 panel found (RecordingHidTransport configured to fail)"
            raise DeviceNotFoundError(msg)
        if self._is_open:
            return
        self._is_open = True
        self._open_count += 1

    def write(self, packet: bytes) -> None:
        """Record ``packet``.

        Raises:
            TransportError: If the transport is closed, or if ``fail_after``
                writes have already succeeded.
        """
        if not self._is_open:
            msg = "transport is not open"
            raise TransportError(msg)
        if self._fail_after is not None and self._write_count >= self._fail_after:
            failed_after, self._fail_after = self._fail_after, None
            self._is_open = False
            msg = f"simulated write failure after {failed_after} packets"
            raise TransportError(msg)

        self._write_count += 1
        self._packets.append(packet)
        if self._max_packets is not None and len(self._packets) > self._max_packets:
            del self._packets[: len(self._packets) - self._max_packets]

    def close(self) -> None:
        """Mark the transport closed, keeping recorded packets."""
        self._is_open = False

    def reset(self) -> None:
        """Discard recorded packets, keeping lifecycle counters."""
        self._packets.clear()


class HidTransport:
    """Writes packets to an HT32 panel over USB HID.

    Args:
        device: A specific interface to open. Defaults to discovering one.
        serial_number: Restrict discovery to a panel with this serial.
        init_delay: Seconds to wait after opening before the panel is
            considered usable. Zero is legal and only sensible in tests.

    Raises:
        DeviceNotFoundError: From :meth:`open`, if no panel can be opened.
    """

    def __init__(
        self,
        *,
        device: HT32DeviceInfo | None = None,
        serial_number: str | None = None,
        init_delay: float = DEFAULT_INIT_DELAY,
    ) -> None:
        self._device = device
        self._serial_number = serial_number
        self._init_delay = init_delay
        self._handle: Any = None
        self._is_open = False

    @property
    def is_open(self) -> bool:
        """Whether the device handle is currently open."""
        return self._is_open

    @property
    def device(self) -> HT32DeviceInfo | None:
        """The interface this transport opened, once known."""
        return self._device

    def open(self) -> None:
        """Discover and open the panel, then wait for it to initialise.

        Raises:
            DeviceNotFoundError: If no panel is attached, or the OS refuses to
                open it -- which on Linux usually means a missing udev rule.
        """
        if self._is_open:
            return

        hid = import_hid()
        device = self._device or find_panel(serial_number=self._serial_number)

        handle = hid.device()
        try:
            if device.path:
                handle.open_path(device.path)
            else:  # pragma: no cover - only when a backend reports no path
                handle.open(VENDOR_ID, PRODUCT_ID)
        except OSError as exc:
            msg = (
                f"could not open {device}: {exc}; on Linux this is usually a permissions "
                "problem -- add a udev rule granting access to the hidraw node"
            )
            raise DeviceNotFoundError(msg) from exc

        self._handle = handle
        self._device = device
        self._is_open = True

        # The panel enumerates before it can be spoken to. Anything written
        # inside this window is accepted by the OS and dropped by the device.
        if self._init_delay > 0:
            time.sleep(self._init_delay)

    def write(self, packet: bytes) -> None:
        """Write one packet to the panel.

        Raises:
            TransportError: If the transport is closed, the packet is the wrong
                size, or the OS rejects the write -- which is what unplugging
                the panel mid-frame looks like.
        """
        if not self._is_open or self._handle is None:
            msg = "transport is not open; call open() first"
            raise TransportError(msg)
        if len(packet) != PACKET_SIZE:
            msg = f"HT32 packets are {PACKET_SIZE} bytes, got {len(packet)}"
            raise TransportError(msg)

        try:
            written = self._handle.write(packet)
        except OSError as exc:
            self._fail(f"write failed: {exc}", exc)
        if written < 0:
            self._fail(f"device rejected the write (returned {written})", None)

    def write_all(self, packets: Iterable[bytes]) -> None:
        """Write several packets in order.

        Exists so a whole frame crosses the thread boundary once rather than
        27 times -- see the module docstring.
        """
        for packet in packets:
            self.write(packet)

    def close(self) -> None:
        """Close the device handle if it is open."""
        handle, self._handle = self._handle, None
        self._is_open = False
        if handle is None:
            return
        # An already-gone panel is the expected case here: there is nothing
        # left to release, and refusing to close would strand the object in a
        # state where it accepts neither writes nor reconnection.
        with contextlib.suppress(OSError):
            handle.close()

    def _fail(self, message: str, cause: OSError | None) -> NoReturn:
        """Tear the handle down and report a write failure.

        The handle is closed first: once a write has failed the handle is
        unusable, and leaving it open would make the next reconnection attempt
        a no-op.
        """
        self.close()
        raise TransportError(message) from cause


def create_panel_transport(
    *,
    serial_number: str | None = None,
    prefer_hidraw: bool = True,
) -> PanelTransport:
    """Build the best transport this machine can actually use.

    Prefers Linux ``hidraw`` when a matching node is visible, because it needs
    no compiled USB library -- which is precisely the situation on the
    appliance-style machines this panel tends to be built into. Falls back to
    hidapi everywhere else.

    Args:
        serial_number: Restrict hidapi discovery to a panel with this serial.
            Ignored by the hidraw path, which identifies nodes by hardware ID.
        prefer_hidraw: Set False to force hidapi even where hidraw would work.

    Note that this only *selects*; nothing is opened until the driver connects,
    so a wrong guess surfaces as a connection error rather than a silent
    fallback to a transport that cannot work.
    """
    if prefer_hidraw and serial_number is None and is_hidraw_available() and enumerate_hidraw():
        return HidrawTransport()

    return HidTransport(serial_number=serial_number)


def packet_summary(packets: Sequence[bytes]) -> str:
    """A one-line description of a frame's packets, for logs and test failures.

    Example:
        >>> from tinydisplay.ht32.protocol import FRAME_BYTES, iter_redraw_packets
        >>> from tinydisplay.ht32.transport import packet_summary
        >>> packet_summary(iter_redraw_packets(bytes(FRAME_BYTES)))
        '27 packets, phases f0..f2, 110835 bytes'
    """
    if not packets:
        return "0 packets"
    total = sum(len(packet) for packet in packets)
    return (
        f"{len(packets)} packets, phases {packets[0][3]:02x}..{packets[-1][3]:02x}, {total} bytes"
    )
