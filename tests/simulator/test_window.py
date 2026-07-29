"""Tests for the preview windows.

The Tk tests are skipped when no display is available, which is the normal
state of a Linux CI runner. Everything the driver depends on is exercised
through NullPreviewWindow, so that skip costs no meaningful coverage.
"""

from __future__ import annotations

import numpy as np
import pytest

from tinydisplay.simulator import (
    NullPreviewWindow,
    PreviewWindow,
    TkPreviewWindow,
    WindowUnavailableError,
)


def display_is_available() -> bool:
    """Whether a Tk window can actually be created here."""
    try:
        import tkinter as tk
    except ImportError:
        return False
    try:
        root = tk.Tk()
    except tk.TclError:
        return False
    root.destroy()
    return True


requires_display = pytest.mark.skipif(
    not display_is_available(),
    reason="no GUI display available",
)


def frame(value: int = 0) -> np.ndarray:
    pixels = np.zeros((4, 4, 3), dtype=np.uint8)
    pixels[:] = value
    return pixels


class TestNullPreviewWindow:
    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(NullPreviewWindow(), PreviewWindow)

    def test_starts_closed(self) -> None:
        assert not NullPreviewWindow().is_open

    def test_open_and_close(self) -> None:
        window = NullPreviewWindow()
        window.open()
        assert window.is_open
        window.close()
        assert not window.is_open

    def test_open_is_idempotent(self) -> None:
        window = NullPreviewWindow()
        window.open()
        window.open()
        assert window.open_count == 1

    def test_close_is_idempotent(self) -> None:
        window = NullPreviewWindow()
        window.open()
        window.close()
        window.close()
        assert not window.is_open

    def test_records_frames(self) -> None:
        window = NullPreviewWindow()
        window.open()
        window.update(frame(1))
        window.update(frame(2))

        assert len(window.frames) == 2
        last = window.last_frame
        assert last is not None
        assert last[0, 0, 0] == 2

    def test_last_frame_is_none_before_any_update(self) -> None:
        assert NullPreviewWindow().last_frame is None

    def test_frames_are_copied_not_aliased(self) -> None:
        """Callers reuse buffers; a recorder that aliased would show one frame."""
        window = NullPreviewWindow()
        window.open()
        buffer = frame(1)
        window.update(buffer)
        buffer[:] = 99
        window.update(buffer)

        assert window.frames[0][0, 0, 0] == 1
        assert window.frames[1][0, 0, 0] == 99

    def test_max_frames_evicts_oldest(self) -> None:
        window = NullPreviewWindow(max_frames=2)
        window.open()
        for value in (1, 2, 3):
            window.update(frame(value))

        assert len(window.frames) == 2
        assert window.frames[0][0, 0, 0] == 2
        assert window.frames[1][0, 0, 0] == 3

    def test_close_keeps_recorded_frames(self) -> None:
        window = NullPreviewWindow()
        window.open()
        window.update(frame(1))
        window.close()
        assert len(window.frames) == 1

    def test_reset_discards_frames(self) -> None:
        window = NullPreviewWindow()
        window.open()
        window.update(frame(1))
        window.reset()
        assert window.frames == ()


class TestTkPreviewWindow:
    def test_satisfies_the_protocol(self) -> None:
        assert isinstance(TkPreviewWindow(), PreviewWindow)

    def test_construction_opens_nothing(self) -> None:
        """Constructing must not touch the display; only open() may."""
        assert not TkPreviewWindow().is_open

    def test_close_before_open_is_safe(self) -> None:
        window = TkPreviewWindow()
        window.close()
        assert not window.is_open

    def test_update_before_open_is_a_no_op(self) -> None:
        TkPreviewWindow().update(frame(1))

    @requires_display
    def test_opens_and_closes(self) -> None:
        window = TkPreviewWindow(title="test")
        window.open()
        assert window.is_open
        window.close()
        assert not window.is_open

    @requires_display
    def test_open_is_idempotent(self) -> None:
        window = TkPreviewWindow()
        window.open()
        window.open()
        try:
            assert window.is_open
        finally:
            window.close()

    @requires_display
    def test_displays_a_frame(self) -> None:
        window = TkPreviewWindow()
        window.open()
        try:
            window.update(frame(128))
            assert window.is_open
        finally:
            window.close()

    @requires_display
    def test_update_after_close_is_a_no_op(self) -> None:
        window = TkPreviewWindow()
        window.open()
        window.close()
        window.update(frame(1))
        assert not window.is_open

    @requires_display
    def test_on_close_callback_fires(self) -> None:
        closed: list[bool] = []
        window = TkPreviewWindow(on_close=lambda: closed.append(True))
        window.open()
        window._handle_close()

        assert closed == [True]
        assert not window.is_open


class TestWindowUnavailable:
    def test_is_a_simulator_error(self) -> None:
        from tinydisplay.simulator import SimulatorError

        assert issubclass(WindowUnavailableError, SimulatorError)

    def test_reports_a_missing_display(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Opening without a display must be a readable error, not a TclError."""
        import tkinter as tk

        def explode(*_args: object, **_kwargs: object) -> None:
            msg = "no display name and no $DISPLAY environment variable"
            raise tk.TclError(msg)

        monkeypatch.setattr(tk, "Tk", explode)

        with pytest.raises(WindowUnavailableError, match="could not open a preview window"):
            TkPreviewWindow().open()
