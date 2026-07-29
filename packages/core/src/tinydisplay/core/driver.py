"""Display driver abstraction.

A driver is the only part of TinyDisplay that touches hardware. It takes a
finished :class:`~tinydisplay.core.canvas.Canvas`, encodes it into the panel's
wire format, and pushes the bytes over whatever transport the device speaks.

Drivers are asynchronous because their work is I/O: USB HID writes, SPI
transfers and network round trips all block, and a Home Assistant integration
driving several panels must not serialise on any one of them. Rendering itself
stays synchronous -- it is CPU-bound NumPy work with nothing to await.

Subclasses implement three hooks -- :meth:`~DisplayDriver._connect`,
:meth:`~DisplayDriver._disconnect` and :meth:`~DisplayDriver._write` -- and
inherit connection-state checking, frame validation and encoding.

Example:
    >>> import asyncio
    >>> async def main() -> int:
    ...     async with MemoryDriver(64, 32) as driver:
    ...         canvas = driver.create_canvas()
    ...         canvas.clear(Color.WHITE)
    ...         await driver.show(canvas)
    ...         return len(driver.frames)
    >>> asyncio.run(main())
    1
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING, Self

from tinydisplay.core.canvas import Canvas
from tinydisplay.core.color import Color
from tinydisplay.core.errors import DriverError, DriverNotConnectedError
from tinydisplay.core.geometry import Size

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import TracebackType

__all__ = ["DisplayDriver", "MemoryDriver", "PixelFormat"]


class PixelFormat(StrEnum):
    """Wire formats a panel may accept.

    RGB565 is by far the most common on small panels, but endianness varies
    between controllers, so it is spelled out rather than left to chance.
    """

    RGB888 = "rgb888"
    RGB565_LE = "rgb565_le"
    RGB565_BE = "rgb565_be"

    @property
    def bytes_per_pixel(self) -> int:
        """Bytes each pixel occupies on the wire."""
        return 3 if self is PixelFormat.RGB888 else 2


class DisplayDriver(ABC):
    """Base class for display drivers.

    Args:
        width: Panel width in pixels.
        height: Panel height in pixels.
        pixel_format: Wire format frames are encoded to before writing.
        name: Human-readable identifier used in errors and logs.
    """

    def __init__(
        self,
        width: int,
        height: int,
        *,
        pixel_format: PixelFormat = PixelFormat.RGB565_LE,
        name: str | None = None,
    ) -> None:
        if width <= 0 or height <= 0:
            msg = f"display dimensions must be positive, got {width}x{height}"
            raise DriverError(msg)
        self._width = width
        self._height = height
        self._pixel_format = pixel_format
        self._name = name or type(self).__name__
        self._connected = False

    # -- Introspection -----------------------------------------------------

    @property
    def width(self) -> int:
        """Panel width in pixels."""
        return self._width

    @property
    def height(self) -> int:
        """Panel height in pixels."""
        return self._height

    @property
    def size(self) -> Size:
        """Panel dimensions."""
        return Size(self._width, self._height)

    @property
    def pixel_format(self) -> PixelFormat:
        """The wire format frames are encoded to."""
        return self._pixel_format

    @property
    def name(self) -> str:
        """Human-readable driver identifier."""
        return self._name

    @property
    def is_connected(self) -> bool:
        """Whether the transport is currently open."""
        return self._connected

    @property
    def frame_size(self) -> int:
        """Size in bytes of one full-screen frame in the driver's pixel format."""
        return self._width * self._height * self._pixel_format.bytes_per_pixel

    def __repr__(self) -> str:
        state = "connected" if self._connected else "disconnected"
        return f"{type(self).__name__}({self._width}x{self._height}, {state})"

    # -- Canvas helpers ----------------------------------------------------

    def create_canvas(self, *, background: Color = Color.BLACK) -> Canvas:
        """Create a canvas matching this display's dimensions."""
        return Canvas(self._width, self._height, background=background)

    def encode(self, canvas: Canvas) -> bytes:
        """Encode a canvas into this driver's wire format.

        Raises:
            DriverError: If the canvas does not match the display's dimensions.
        """
        if canvas.size != self.size:
            msg = (
                f"{self._name} expects a {self._width}x{self._height} canvas, "
                f"got {canvas.width}x{canvas.height}"
            )
            raise DriverError(msg)

        if self._pixel_format is PixelFormat.RGB888:
            return canvas.to_rgb888()
        if self._pixel_format is PixelFormat.RGB565_BE:
            return canvas.to_rgb565(byte_order="big")
        return canvas.to_rgb565(byte_order="little")

    # -- Lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        """Open the transport. Calling this on a connected driver is a no-op."""
        if self._connected:
            return
        await self._connect()
        self._connected = True

    async def disconnect(self) -> None:
        """Close the transport. Calling this on a disconnected driver is a no-op.

        The driver is marked disconnected even if the underlying close fails,
        so a failing transport cannot leave the object wedged in a state where
        it refuses both reads and reconnection.
        """
        if not self._connected:
            return
        try:
            await self._disconnect()
        finally:
            self._connected = False

    async def show(self, canvas: Canvas) -> None:
        """Encode ``canvas`` and push it to the display.

        Raises:
            DriverNotConnectedError: If :meth:`connect` has not been called.
            DriverError: If the canvas does not match the display's dimensions.
        """
        if not self._connected:
            msg = f"{self._name} is not connected; call connect() first"
            raise DriverNotConnectedError(msg)
        await self._write(self.encode(canvas))

    async def __aenter__(self) -> Self:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.disconnect()

    # -- Subclass hooks ----------------------------------------------------

    @abstractmethod
    async def _connect(self) -> None:
        """Open the underlying transport. Called only when disconnected."""

    @abstractmethod
    async def _disconnect(self) -> None:
        """Close the underlying transport. Called only when connected."""

    @abstractmethod
    async def _write(self, frame: bytes) -> None:
        """Push one encoded frame. Called only when connected."""


class MemoryDriver(DisplayDriver):
    """A driver that keeps frames in memory instead of writing to hardware.

    This is the reference implementation of the driver contract and the
    backbone of the test suite: it lets the rendering engine be exercised
    end-to-end with no device attached.

    Args:
        max_frames: How many recent frames to retain. ``None`` keeps every
            frame, which is convenient in tests but unbounded -- pass a limit
            when driving a long-running loop.
    """

    def __init__(
        self,
        width: int,
        height: int,
        *,
        pixel_format: PixelFormat = PixelFormat.RGB565_LE,
        name: str | None = None,
        max_frames: int | None = None,
    ) -> None:
        super().__init__(width, height, pixel_format=pixel_format, name=name)
        if max_frames is not None and max_frames <= 0:
            msg = f"max_frames must be positive or None, got {max_frames}"
            raise DriverError(msg)
        self._max_frames = max_frames
        self._frames: list[bytes] = []
        self._connect_count = 0

    @property
    def frames(self) -> Sequence[bytes]:
        """Every retained frame, oldest first."""
        return tuple(self._frames)

    @property
    def last_frame(self) -> bytes | None:
        """The most recently written frame, or ``None`` if nothing was shown."""
        return self._frames[-1] if self._frames else None

    @property
    def connect_count(self) -> int:
        """How many times the transport has been opened, for lifecycle assertions."""
        return self._connect_count

    def reset(self) -> None:
        """Discard all retained frames."""
        self._frames.clear()

    async def _connect(self) -> None:
        self._connect_count += 1

    async def _disconnect(self) -> None:
        return

    async def _write(self, frame: bytes) -> None:
        self._frames.append(frame)
        if self._max_frames is not None and len(self._frames) > self._max_frames:
            del self._frames[: len(self._frames) - self._max_frames]
