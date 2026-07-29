"""A :class:`~tinydisplay.core.DisplayDriver` for the HT32 panel.

The driver is deliberately thin. Encoding belongs to core, framing belongs to
:mod:`tinydisplay.ht32.protocol`, and writing belongs to
:mod:`tinydisplay.ht32.transport`; what is left here is the panel's fixed
geometry, the decision to build a whole frame before writing any of it, and
reconnection.

Panel size and pixel format are not constructor arguments. The HT32 is 320x170
RGB565 big-endian and nothing else, and a driver that let you ask for 320x171
would only be able to fail later and less clearly.

Example:
    >>> import asyncio
    >>> from tinydisplay.ht32 import HT32Driver, RecordingHidTransport
    >>> async def main() -> tuple[int, int]:
    ...     transport = RecordingHidTransport()
    ...     async with HT32Driver(transport=transport) as driver:
    ...         canvas = driver.create_canvas()
    ...         canvas.clear(Color.WHITE)
    ...         await driver.show(canvas)
    ...     return len(transport.packets), driver.frame_count
    >>> asyncio.run(main())  # 27 chunks make one frame
    (27, 1)
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Final

from tinydisplay.core import DisplayDriver
from tinydisplay.core.errors import DriverNotConnectedError
from tinydisplay.ht32.errors import HT32Error, TransportError
from tinydisplay.ht32.protocol import (
    PANEL_HEIGHT,
    PANEL_PIXEL_FORMAT,
    PANEL_WIDTH,
    build_refresh_packet,
    iter_redraw_packets,
)
from tinydisplay.ht32.transport import create_panel_transport

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tinydisplay.ht32.transport import PanelTransport

__all__ = ["DEFAULT_RECONNECT_ATTEMPTS", "DEFAULT_RECONNECT_DELAY", "HT32Driver"]

#: How many times a failed frame is retried, each attempt re-opening the panel
#: first. Two covers the common case -- a panel that was unplugged and put back
#: -- without turning a genuinely absent device into a long stall.
DEFAULT_RECONNECT_ATTEMPTS: Final = 2

#: Seconds between reconnection attempts. The panel needs a moment to enumerate
#: after being plugged in; retrying instantly would just fail again.
DEFAULT_RECONNECT_DELAY: Final = 0.5


class HT32Driver(DisplayDriver):
    """Drive an HT32 panel over USB HID.

    Args:
        transport: Where packets go. Defaults to whichever real transport this
            machine can use -- Linux ``hidraw`` when a panel node is visible,
            hidapi otherwise. Pass a
            :class:`~tinydisplay.ht32.transport.RecordingHidTransport` to run
            with no hardware attached.
        name: Human-readable identifier used in errors and logs.
        serial_number: Restrict discovery to a panel with this serial. Ignored
            when ``transport`` is given.
        auto_reconnect: Whether a failed write should re-open the panel and try
            again. Turn it off when a failure should surface immediately.
        reconnect_attempts: How many times to retry a failed frame.
        reconnect_delay: Seconds to wait between attempts.
    """

    def __init__(
        self,
        *,
        transport: PanelTransport | None = None,
        name: str | None = None,
        serial_number: str | None = None,
        auto_reconnect: bool = True,
        reconnect_attempts: int = DEFAULT_RECONNECT_ATTEMPTS,
        reconnect_delay: float = DEFAULT_RECONNECT_DELAY,
    ) -> None:
        super().__init__(
            PANEL_WIDTH,
            PANEL_HEIGHT,
            pixel_format=PANEL_PIXEL_FORMAT,
            name=name or "HT32",
        )
        if reconnect_attempts < 0:
            msg = f"reconnect_attempts must not be negative, got {reconnect_attempts}"
            raise HT32Error(msg)

        self._transport = (
            transport
            if transport is not None
            else create_panel_transport(serial_number=serial_number)
        )
        self._owns_transport = transport is None
        self._auto_reconnect = auto_reconnect
        self._reconnect_attempts = reconnect_attempts
        self._reconnect_delay = reconnect_delay
        self._frame_count = 0
        self._reconnect_count = 0
        self._failure_count = 0

    # -- Introspection -----------------------------------------------------

    @property
    def transport(self) -> PanelTransport:
        """Where packets are written."""
        return self._transport

    @property
    def frame_count(self) -> int:
        """How many frames have reached the panel since construction."""
        return self._frame_count

    @property
    def reconnect_count(self) -> int:
        """How many times the panel has been re-opened after a failed write."""
        return self._reconnect_count

    @property
    def failure_count(self) -> int:
        """How many individual write attempts have failed, retries included."""
        return self._failure_count

    @property
    def auto_reconnect(self) -> bool:
        """Whether failed writes trigger a reconnection attempt."""
        return self._auto_reconnect

    # -- Driver hooks ------------------------------------------------------

    async def _connect(self) -> None:
        """Open the panel.

        The open is blocking -- it enumerates USB devices and then waits a
        second for the panel to initialise -- so it runs off the event loop.
        """
        await asyncio.to_thread(self._transport.open)

    async def _disconnect(self) -> None:
        """Close the panel, if this driver opened it.

        A transport passed in by the caller belongs to the caller, mirroring
        the simulator's treatment of a borrowed preview window.
        """
        if self._owns_transport:
            await asyncio.to_thread(self._transport.close)

    async def _write(self, frame: bytes) -> None:
        """Frame, chunk and write one encoded frame.

        Every packet is built before the first one is written. Framing errors
        are then caught with the panel untouched, rather than halfway through a
        redraw whose end phase will never arrive.

        Raises:
            TransportError: If the frame could not be written, after any
                configured reconnection attempts.
        """
        packets = iter_redraw_packets(frame)
        await self._write_frame(packets)
        self._frame_count += 1

    # -- Panel commands ----------------------------------------------------

    async def refresh(self) -> None:
        """Ask the panel to repaint the frame it already holds.

        Raises:
            DriverNotConnectedError: If the driver is not connected.
            TransportError: If the command could not be written.
        """
        self._require_connected()
        await self._write_frame((build_refresh_packet(),))

    # -- Reconnection ------------------------------------------------------

    async def _write_frame(self, packets: Sequence[bytes]) -> None:
        """Write ``packets``, re-opening the panel and retrying on failure.

        A frame is all-or-nothing: a retry rewrites every packet from the start
        phase, because a panel that saw half a frame before the cable moved is
        not in a state where continuing makes sense.
        """
        attempts = 1 + (self._reconnect_attempts if self._auto_reconnect else 0)
        last_error: Exception | None = None

        for attempt in range(attempts):
            if attempt > 0:
                await asyncio.sleep(self._reconnect_delay)

            try:
                if not self._transport.is_open:
                    if not self._auto_reconnect:
                        msg = f"{self.name} transport is closed and auto_reconnect is off"
                        raise TransportError(msg)
                    await asyncio.to_thread(self._transport.open)
                    self._reconnect_count += 1
                await asyncio.to_thread(self._write_packets, packets)
            except HT32Error as exc:
                self._failure_count += 1
                last_error = exc
                continue
            else:
                return

        msg = f"{self.name} failed to write a frame after {attempts} attempt(s): {last_error}"
        raise TransportError(msg) from last_error

    def _write_packets(self, packets: Sequence[bytes]) -> None:
        """Write every packet in order. Runs on a worker thread."""
        for packet in packets:
            self._transport.write(packet)

    def _require_connected(self) -> None:
        """Raise unless :meth:`connect` has been called."""
        if not self.is_connected:
            msg = f"{self.name} is not connected; call connect() first"
            raise DriverNotConnectedError(msg)
