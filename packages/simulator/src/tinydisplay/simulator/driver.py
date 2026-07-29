"""A :class:`~tinydisplay.core.DisplayDriver` that draws to a desktop window.

The simulator earns its place by being an ordinary driver. It inherits the same
connection-state checks, canvas-size validation and pixel-format encoding as a
real panel, so a dashboard written against it needs no changes to run against
hardware -- and a bug in the base class shows up here first.

Example:
    >>> import asyncio
    >>> from tinydisplay.simulator import NullPreviewWindow, SimulatorDriver
    >>> async def main() -> tuple[int, int]:
    ...     window = NullPreviewWindow()
    ...     async with SimulatorDriver(8, 4, window=window, scale=2) as driver:
    ...         canvas = driver.create_canvas()
    ...         canvas.clear(Color.WHITE)
    ...         await driver.show(canvas)
    ...     frame = window.last_frame
    ...     return (frame.shape[0], frame.shape[1])
    >>> asyncio.run(main())
    (8, 16)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from tinydisplay.core import DisplayDriver, PixelFormat
from tinydisplay.simulator.preview import decode_frame, scale_nearest, validate_scale
from tinydisplay.simulator.window import TkPreviewWindow

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt

    from tinydisplay.simulator.window import PreviewWindow

__all__ = ["DEFAULT_SCALE", "SimulatorDriver"]

# Small panels are genuinely small. A 320x170 HT32 at 1:1 is a postage stamp on
# a modern monitor, and quantisation banding is invisible at that size.
DEFAULT_SCALE: Final = 3


class SimulatorDriver(DisplayDriver):
    """Render frames to a desktop window instead of a panel.

    Args:
        width: Panel width in pixels.
        height: Panel height in pixels.
        pixel_format: Wire format frames are encoded to. This doubles as the
            fidelity control: the preview decodes whatever was encoded, so
            ``RGB565_LE`` shows the quantised image a 16-bit panel would
            display, and ``RGB888`` shows the flattering 24-bit version.
        name: Human-readable identifier used in errors and logs.
        scale: Integer magnification, nearest-neighbour so banding stays visible.
        title: Window title. Defaults to a description of the simulated panel.
        window: Where to draw. Defaults to a real Tk window; pass a
            :class:`~tinydisplay.simulator.window.NullPreviewWindow` to run
            headless.

    Raises:
        SimulatorError: If ``scale`` is out of range.
    """

    def __init__(
        self,
        width: int,
        height: int,
        *,
        pixel_format: PixelFormat = PixelFormat.RGB565_LE,
        name: str | None = None,
        scale: int = DEFAULT_SCALE,
        title: str | None = None,
        window: PreviewWindow | None = None,
    ) -> None:
        super().__init__(width, height, pixel_format=pixel_format, name=name)
        self._scale = validate_scale(scale)
        self._title = title or f"TinyDisplay {width}x{height} ({pixel_format.value})"
        self._window = window if window is not None else TkPreviewWindow(title=self._title)
        self._owns_window = window is None
        self._last_preview: npt.NDArray[np.uint8] | None = None
        self._frame_count = 0

    # -- Introspection -----------------------------------------------------

    @property
    def scale(self) -> int:
        """Integer magnification applied before display."""
        return self._scale

    @property
    def title(self) -> str:
        """The preview window's title."""
        return self._title

    @property
    def window(self) -> PreviewWindow:
        """The window frames are drawn to."""
        return self._window

    @property
    def is_window_open(self) -> bool:
        """Whether the preview window is still accepting frames.

        A render loop watches this to notice that the operator closed the
        window, which is the simulator's equivalent of unplugging the panel.
        """
        return self._window.is_open

    @property
    def frame_count(self) -> int:
        """How many frames have been shown since construction."""
        return self._frame_count

    @property
    def last_preview(self) -> npt.NDArray[np.uint8] | None:
        """The most recent decoded frame, before scaling.

        This is what the panel would show, so assertions against it are
        assertions about the encoded output rather than about the source
        canvas. ``None`` until the first frame.
        """
        return self._last_preview

    # -- Driver hooks ------------------------------------------------------

    async def _connect(self) -> None:
        """Open the preview window."""
        self._window.open()

    async def _disconnect(self) -> None:
        """Close the preview window, if this driver created it.

        A window passed in by the caller belongs to the caller: a run loop that
        reconnects the driver should not find its window destroyed.
        """
        if self._owns_window:
            self._window.close()

    async def _write(self, frame: bytes) -> None:
        """Decode, magnify and display one encoded frame."""
        pixels = decode_frame(frame, self.width, self.height, self.pixel_format)
        self._last_preview = pixels
        self._frame_count += 1
        self._window.update(scale_nearest(pixels, self._scale))
