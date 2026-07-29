"""Exceptions raised by the simulator.

Everything here derives from :class:`~tinydisplay.core.errors.DriverError`, so
code that already handles a real panel failing handles the simulator failing
too, without a simulator-specific ``except`` clause.
"""

from __future__ import annotations

from tinydisplay.core.errors import DriverError

__all__ = ["DashboardError", "SimulatorError", "WindowUnavailableError"]


class SimulatorError(DriverError):
    """Base class for simulator failures."""


class WindowUnavailableError(SimulatorError):
    """Raised when a preview window cannot be opened.

    The usual causes are a missing Tk installation (``python3-tk`` on Debian
    and Ubuntu) or no display server, which is the normal state of a CI runner.
    """


class DashboardError(SimulatorError):
    """Raised when a dashboard file cannot be loaded, or raises while drawing.

    The offending exception is always chained, so ``__cause__`` carries the
    original traceback. The simulator catches this to paint the message onto
    the panel instead of tearing the render loop down.
    """
