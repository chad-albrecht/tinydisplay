"""Preview windows: the only part of the simulator that knows about a GUI.

The window is a protocol with two implementations so that everything above it
stays testable with no display attached:

- :class:`TkPreviewWindow` puts frames on screen.
- :class:`NullPreviewWindow` records them in memory.

CI runs the second one. That is not a mock of the first -- it satisfies the
same protocol, and the driver cannot tell them apart.

Tkinter is imported inside :meth:`TkPreviewWindow.open` rather than at module
scope. It is a system package on most Linux distributions (``python3-tk``), and
a missing GUI toolkit should surface as a readable error when somebody asks for
a window, not as an ImportError when they merely import the package.

Tk is driven by pumping its event queue between frames instead of by calling
``mainloop()``. The render loop already owns the process's control flow, and
handing it to Tk would mean pushing rendering onto a callback or a second
thread -- and Tk objects are bound to the thread that created them. Pumping
keeps the whole simulator single-threaded, at the cost of a window that only
repaints as often as frames arrive.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tinydisplay.simulator.errors import WindowUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np
    import numpy.typing as npt

__all__ = ["NullPreviewWindow", "PreviewWindow", "TkPreviewWindow"]


@runtime_checkable
class PreviewWindow(Protocol):
    """Somewhere a decoded frame can be shown."""

    @property
    def is_open(self) -> bool:
        """Whether the window is currently accepting frames."""
        ...

    def open(self) -> None:
        """Make the window ready to receive frames. Idempotent."""
        ...

    def update(self, pixels: npt.NDArray[np.uint8]) -> None:
        """Display one ``uint8[height, width, 3]`` frame, already scaled."""
        ...

    def close(self) -> None:
        """Release the window. Idempotent."""
        ...


class NullPreviewWindow:
    """A window that records frames instead of drawing them.

    This is the headless half of the driver's test strategy, and it is also
    genuinely useful in anger: pointing the simulator at one lets a dashboard
    be exercised in CI, with assertions on what it drew.

    Args:
        max_frames: How many recent frames to retain. ``None`` keeps every
            frame, which is fine for a test and unbounded for a long run.

    Example:
        >>> import numpy as np
        >>> from tinydisplay.simulator import NullPreviewWindow
        >>> window = NullPreviewWindow()
        >>> window.open()
        >>> window.update(np.zeros((2, 2, 3), dtype=np.uint8))
        >>> len(window.frames)
        1
    """

    def __init__(self, *, max_frames: int | None = None) -> None:
        self._max_frames = max_frames
        self._frames: list[npt.NDArray[np.uint8]] = []
        self._is_open = False
        self._open_count = 0

    @property
    def is_open(self) -> bool:
        """Whether :meth:`open` has been called without a matching :meth:`close`."""
        return self._is_open

    @property
    def frames(self) -> tuple[npt.NDArray[np.uint8], ...]:
        """Every retained frame, oldest first."""
        return tuple(self._frames)

    @property
    def last_frame(self) -> npt.NDArray[np.uint8] | None:
        """The most recent frame, or ``None`` if nothing has been shown."""
        return self._frames[-1] if self._frames else None

    @property
    def open_count(self) -> int:
        """How many times the window has been opened, for lifecycle assertions."""
        return self._open_count

    def open(self) -> None:
        """Mark the window open."""
        if self._is_open:
            return
        self._is_open = True
        self._open_count += 1

    def update(self, pixels: npt.NDArray[np.uint8]) -> None:
        """Record a copy of ``pixels``.

        The frame is copied because callers are free to reuse their buffer, and
        a recorder that aliased it would report every frame as the last one.
        """
        self._frames.append(pixels.copy())
        if self._max_frames is not None and len(self._frames) > self._max_frames:
            del self._frames[: len(self._frames) - self._max_frames]

    def close(self) -> None:
        """Mark the window closed, keeping recorded frames."""
        self._is_open = False

    def reset(self) -> None:
        """Discard recorded frames."""
        self._frames.clear()


class TkPreviewWindow:
    """A resizable Tk window showing decoded frames.

    Args:
        title: Window title.
        on_close: Called when the operator closes the window. The driver uses
            this to notice that the run should stop.

    Closing the window does not raise. A simulator that crashed because
    somebody clicked the X would be worse than one that simply stops drawing,
    so :meth:`update` becomes a no-op once the window is gone.
    """

    def __init__(
        self,
        *,
        title: str = "TinyDisplay",
        on_close: Callable[[], None] | None = None,
    ) -> None:
        self._title = title
        self._on_close = on_close
        self._root: Any = None
        self._label: Any = None
        self._photo: Any = None
        self._is_open = False

    @property
    def is_open(self) -> bool:
        """Whether the window exists and has not been closed by the operator."""
        return self._is_open

    def open(self) -> None:
        """Create the window.

        Raises:
            WindowUnavailableError: If Tk is missing or no display is available.
        """
        if self._is_open:
            return
        try:
            import tkinter as tk
        except ImportError as exc:  # pragma: no cover - depends on the interpreter build
            msg = (
                "tkinter is not available; install your platform's Tk package "
                "(python3-tk on Debian and Ubuntu) or use NullPreviewWindow"
            )
            raise WindowUnavailableError(msg) from exc

        try:
            self._root = tk.Tk()
            self._root.title(self._title)
            self._root.resizable(width=False, height=False)
            self._label = tk.Label(self._root, borderwidth=0, highlightthickness=0)
            self._label.pack()
            self._root.protocol("WM_DELETE_WINDOW", self._handle_close)
        except tk.TclError as exc:
            self._root = None
            self._label = None
            msg = f"could not open a preview window: {exc}"
            raise WindowUnavailableError(msg) from exc

        self._is_open = True

    def update(self, pixels: npt.NDArray[np.uint8]) -> None:
        """Draw one frame and pump Tk's event queue.

        Does nothing once the window has been closed.
        """
        if not self._is_open or self._root is None or self._label is None:
            return

        from tkinter import TclError

        from PIL import Image, ImageTk

        try:
            image = Image.fromarray(pixels, mode="RGB")
            # The PhotoImage must outlive this call: Tk holds only a weak
            # reference, and a garbage-collected one renders as an empty box.
            self._photo = ImageTk.PhotoImage(image, master=self._root)
            self._label.configure(image=self._photo)
            self._root.update_idletasks()
            self._root.update()
        except TclError:
            # The window went away between the is_open check and the draw --
            # for instance because the operator closed it mid-frame.
            self._handle_close()

    def close(self) -> None:
        """Destroy the window if it still exists."""
        root, self._root = self._root, None
        self._label = None
        self._photo = None
        self._is_open = False
        if root is None:
            return
        from tkinter import TclError

        # Already-destroyed is the expected case when the operator closed the
        # window; there is nothing left to release.
        with contextlib.suppress(TclError):
            root.destroy()

    def _handle_close(self) -> None:
        """Note that the operator dismissed the window, then tear it down."""
        callback = self._on_close
        self.close()
        if callback is not None:
            callback()
