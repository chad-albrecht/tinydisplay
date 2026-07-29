"""Loading a dashboard from a file, and reloading it when it changes.

A dashboard is an ordinary Python module that defines a module-level
``render(canvas)`` function:

.. code-block:: python

    from tinydisplay.core import Color


    def render(canvas):
        canvas.clear(Color.BLACK)
        canvas.text(4, 4, "Hello", Color.WHITE)

A plain function was chosen over a config format because the dashboard is code
either way -- a YAML dialect expressive enough to be useful would just be a
worse Python. Phase 5 layers a declarative format on top for Home Assistant;
this is the layer that format will compile down to.

Changes are detected by polling ``st_mtime_ns`` rather than by a filesystem
watch. Polling costs one ``stat`` per frame, needs no third-party dependency,
and cannot miss an event -- and editors that write via rename, which is most of
them, defeat naive watches anyway.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tinydisplay.simulator.errors import DashboardError

if TYPE_CHECKING:
    from types import ModuleType

    from tinydisplay.core import Canvas

__all__ = ["RENDER_ATTRIBUTE", "DashboardLoader"]

RENDER_ATTRIBUTE = "render"

# Dashboards are loaded under a private module name so that reloading one
# cannot clobber a real installed package that happens to share its filename.
_MODULE_PREFIX = "tinydisplay_dashboard_"


class DashboardLoader:
    """Loads a dashboard module and re-executes it when the file changes.

    Args:
        path: The dashboard file.

    Raises:
        DashboardError: If ``path`` does not exist or is not a file.

    Example:
        >>> import tempfile
        >>> from pathlib import Path
        >>> from tinydisplay.simulator import DashboardLoader
        >>> with tempfile.TemporaryDirectory() as directory:
        ...     script = Path(directory) / "board.py"
        ...     _ = script.write_text(
        ...         "from tinydisplay.core import Color\\n"
        ...         "def render(canvas):\\n"
        ...         "    canvas.clear(Color.WHITE)\\n",
        ...         encoding="utf-8",
        ...     )
        ...     loader = DashboardLoader(script)
        ...     loader.load()
        ...     canvas = Canvas(4, 4)
        ...     loader.render(canvas)
        ...     canvas.get_pixel(0, 0) == Color.WHITE
        True
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser().resolve()
        if not self._path.is_file():
            msg = f"dashboard file not found: {self._path}"
            raise DashboardError(msg)
        self._module: ModuleType | None = None
        self._mtime: int | None = None
        # Deterministic across runs, and distinct for two dashboards that share
        # a filename in different directories.
        digest = hashlib.blake2s(str(self._path).encode("utf-8"), digest_size=6).hexdigest()
        self._module_name = f"{_MODULE_PREFIX}{digest}"

    @property
    def path(self) -> Path:
        """The dashboard file being watched."""
        return self._path

    @property
    def is_loaded(self) -> bool:
        """Whether a dashboard module is currently loaded."""
        return self._module is not None

    def __repr__(self) -> str:
        state = "loaded" if self.is_loaded else "unloaded"
        return f"DashboardLoader({self._path.name!r}, {state})"

    def load(self) -> None:
        """Execute the dashboard file and adopt its ``render`` function.

        Raises:
            DashboardError: If the file cannot be read, raises while executing,
                or does not define a callable ``render``.
        """
        self._mtime = self._read_mtime()

        spec = importlib.util.spec_from_file_location(self._module_name, self._path)
        if spec is None or spec.loader is None:  # pragma: no cover - needs an exotic path
            msg = f"cannot import {self._path} as a Python module"
            raise DashboardError(msg)

        module = importlib.util.module_from_spec(spec)
        # Registered before execution so that a dashboard containing a
        # dataclass or a pickle reference can find its own module by name.
        sys.modules[self._module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            del sys.modules[self._module_name]
            msg = f"{self._path.name} failed to load: {exc}"
            raise DashboardError(msg) from exc

        render = getattr(module, RENDER_ATTRIBUTE, None)
        if not callable(render):
            del sys.modules[self._module_name]
            msg = (
                f"{self._path.name} defines no callable {RENDER_ATTRIBUTE}(canvas); "
                f"a dashboard must expose one"
            )
            raise DashboardError(msg)

        self._module = module

    def unload(self) -> None:
        """Drop the loaded module and forget its ``sys.modules`` entry."""
        self._module = None
        self._mtime = None
        sys.modules.pop(self._module_name, None)

    def has_changed(self) -> bool:
        """Whether the file's modification time differs from the loaded one.

        A file that has been deleted counts as unchanged: reporting a change
        would send the caller into a reload that is certain to fail, and the
        editor that removed it is usually about to write it back.
        """
        if self._mtime is None:
            return True
        try:
            return self._read_mtime() != self._mtime
        except DashboardError:
            return False

    def reload_if_changed(self) -> bool:
        """Reload when the file has changed on disk. Returns whether it did.

        Raises:
            DashboardError: If the reload fails. The previously loaded module
                is left in place, so a caller that keeps rendering keeps
                showing the last dashboard that worked.
        """
        if not self.has_changed():
            return False
        previous = self._module
        try:
            self.load()
        except DashboardError:
            self._module = previous
            raise
        return True

    def render(self, canvas: Canvas) -> None:
        """Draw the loaded dashboard onto ``canvas``.

        Raises:
            DashboardError: If no dashboard is loaded, or ``render`` raises.
        """
        if self._module is None:
            msg = "no dashboard loaded; call load() first"
            raise DashboardError(msg)
        render: Any = getattr(self._module, RENDER_ATTRIBUTE)
        try:
            render(canvas)
        except Exception as exc:
            msg = f"{self._path.name} raised while rendering: {exc}"
            raise DashboardError(msg) from exc

    def _read_mtime(self) -> int:
        """Read the file's modification time in nanoseconds.

        Raises:
            DashboardError: If the file cannot be stat'ed.
        """
        try:
            return self._path.stat().st_mtime_ns
        except OSError as exc:
            msg = f"cannot stat {self._path}: {exc}"
            raise DashboardError(msg) from exc
