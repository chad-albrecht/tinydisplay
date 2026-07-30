"""A dashboard: one object tying a definition, a widget tree and a panel together.

Everything below this module does one job -- :mod:`~tinydisplay.homeassistant.schema`
validates, :mod:`~tinydisplay.homeassistant.build` constructs,
:mod:`~tinydisplay.homeassistant.template` formats. This is the object a caller
actually holds, and the only one the Home Assistant integration needs to know
about.

The split between :meth:`Dashboard.update` and :meth:`Dashboard.draw` is the
point of the class. Updating is what a state change causes and is cheap;
drawing is what a frame causes and is not. Keeping them separate is what lets
the render loop be driven by entity changes rather than by a clock, and lets a
burst of twenty state changes between two frames cost twenty dictionary lookups
rather than twenty repaints.

Example:
    >>> from tinydisplay.homeassistant import Dashboard, StaticStateSource
    >>> dashboard = Dashboard.from_yaml(
    ...     '''
    ...     theme: midnight
    ...     root:
    ...       type: label
    ...       text: "{{ sensor.kitchen | round(1) }} C"
    ...       color: accent
    ...     '''
    ... )
    >>> sorted(dashboard.entity_ids)
    ['sensor.kitchen']
    >>> canvas = Canvas(160, 40)
    >>> dashboard.render(canvas, StaticStateSource({"sensor.kitchen": "21.53"}))
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tinydisplay.core import Rect
from tinydisplay.homeassistant.build import build_dashboard
from tinydisplay.homeassistant.schema import load_dashboard, parse_dashboard, parse_dashboard_yaml

if TYPE_CHECKING:
    from tinydisplay.core import Canvas, Color, Widget
    from tinydisplay.homeassistant.schema import DashboardSpec
    from tinydisplay.homeassistant.state import StateSource
    from tinydisplay.widgets import Theme

__all__ = ["Dashboard"]


def _modified_at(path: Path | None) -> int | None:
    """The file's modification time in nanoseconds, or ``None``.

    Nanoseconds because a dashboard edited twice within the same second is an
    ordinary thing to do while getting a layout right, and a second-resolution
    stamp would miss the second edit.
    """
    if path is None:
        return None
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


class Dashboard:
    """A validated dashboard, built and ready to draw.

    Args:
        spec: The validated definition.
        source_path: Where it was read from, if it came from a file. Carried
            for diagnostics only.

    Build one with :meth:`load`, :meth:`from_yaml` or :meth:`from_document`
    rather than calling the constructor, so that validation happens exactly
    once and in one place.
    """

    __slots__ = ("_built", "_current", "_source_path", "_spec", "_stamp")

    def __init__(self, spec: DashboardSpec, *, source_path: Path | None = None) -> None:
        self._spec = spec
        self._built = build_dashboard(spec)
        self._source_path = source_path
        self._stamp = _modified_at(source_path)
        self._current = 0

    # -- Construction ------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> Dashboard:
        """Read, validate and build a dashboard from a YAML file.

        Raises:
            DashboardConfigError: If the file cannot be read or is not a valid
                dashboard.
        """
        return cls(load_dashboard(path), source_path=Path(path))

    @classmethod
    def from_yaml(cls, text: str) -> Dashboard:
        """Validate and build a dashboard from YAML text.

        Raises:
            DashboardConfigError: If the text is not a valid dashboard.
        """
        return cls(parse_dashboard_yaml(text))

    @classmethod
    def from_document(cls, document: object) -> Dashboard:
        """Validate and build a dashboard from an already-loaded mapping.

        This is the entry point Home Assistant uses: the YAML has usually been
        loaded already, by Home Assistant's own loader.

        Raises:
            DashboardConfigError: If the document is not a valid dashboard.
        """
        return cls(parse_dashboard(document))

    # -- Introspection -----------------------------------------------------

    @property
    def spec(self) -> DashboardSpec:
        """The validated definition this was built from."""
        return self._spec

    @property
    def root(self) -> Widget:
        """The top of the widget tree currently on show."""
        return self._built.screens[self._current].root

    # -- Screens -----------------------------------------------------------

    @property
    def screen_count(self) -> int:
        """How many screens this dashboard cycles through. Always at least one."""
        return len(self._built.screens)

    @property
    def current_screen(self) -> int:
        """Which screen is on show, counting from zero."""
        return self._current

    @property
    def screen_name(self) -> str | None:
        """What the document called the screen on show, if anything."""
        return self._built.screens[self._current].name

    @property
    def rotate_every(self) -> float | None:
        """Seconds between screens, or ``None`` to hold the first one.

        ``None`` for a single-screen dashboard however the document was
        written: rotating through one screen is a repaint on a timer, which is
        what ``max_interval`` is for.
        """
        return self._spec.rotate_every if self._spec.rotates else None

    def advance(self) -> int:
        """Move to the next screen, wrapping. Returns the new index.

        Only changes which tree is drawn. Every screen is kept current by
        :meth:`update` whether or not it is showing, so the one arriving is
        already up to date rather than a snapshot of whenever it was last seen.
        """
        self._current = (self._current + 1) % self.screen_count
        return self._current

    def show_screen(self, index: int) -> None:
        """Jump to a screen by index.

        Raises:
            IndexError: If there is no such screen.
        """
        if not 0 <= index < self.screen_count:
            msg = f"no screen {index}; this dashboard has {self.screen_count}"
            raise IndexError(msg)
        self._current = index

    @property
    def theme(self) -> Theme:
        """The palette, already quantised for the panel's colour depth."""
        return self._spec.theme

    @property
    def background(self) -> Color:
        """What the canvas is cleared to before drawing."""
        return self._spec.background.resolve(self._spec.theme, None)

    @property
    def entity_ids(self) -> frozenset[str]:
        """Every entity this dashboard reads.

        The integration subscribes to exactly this set. An empty set is
        meaningful and legal: a dashboard of fixed text needs no subscriptions
        and will only ever be repainted by the periodic refresh.
        """
        return self._spec.entity_ids

    @property
    def source_path(self) -> Path | None:
        """The file this was read from, if any."""
        return self._source_path

    @property
    def is_static(self) -> bool:
        """Whether anything in this dashboard can change.

        False for a rotating dashboard even if every screen is fixed text: the
        picture on the panel still changes, which is what a caller deciding
        whether to run a render loop is actually asking about.
        """
        return not self._built.updaters and self.rotate_every is None

    def __repr__(self) -> str:
        where = f", {self._source_path}" if self._source_path is not None else ""
        screens = f", {self.screen_count} screens" if self.screen_count > 1 else ""
        return (
            f"Dashboard({self._spec.theme_name}, {len(self.entity_ids)} entities{screens}{where})"
        )

    # -- Rendering ---------------------------------------------------------

    def update(self, source: StateSource) -> None:
        """Bring the widget tree in line with current entity state.

        Cheap, and safe to call as often as state changes arrive. Does not
        draw: widgets that changed are marked dirty and the next
        :meth:`draw` picks them up.
        """
        self._built.update(source)

    def draw(self, canvas: Canvas) -> None:
        """Paint the tree onto ``canvas``, filling it.

        The root is resized to the canvas each time rather than at
        construction. A dashboard is not tied to one panel size -- the same
        definition is previewed in the simulator and drawn on hardware -- and
        the layout containers ignore a resize to the size they already have,
        so this costs nothing on the frames where nothing moved.
        """
        root = self.root
        canvas.clear(self.background)
        root.bounds = Rect(0, 0, canvas.width, canvas.height)
        root.draw(canvas)

    def reload_if_changed(self) -> bool:
        """Re-read the file if it has changed on disk. Returns whether it did.

        The rebuild happens *in place*: a caller holding this object keeps
        holding it, which is what lets a running render loop pick up an edit
        without being restarted. The alternative -- handing back a new
        ``Dashboard`` -- would mean every holder needed to be found and
        updated, and the render loop is deliberately not that kind of object.

        A file that has changed into something invalid is left alone and the
        error is raised, so the last working dashboard stays on the panel. That
        is the same bargain the simulator makes with its hot-reload: an edit
        that does not parse should not blank the screen.

        Raises:
            DashboardConfigError: If the changed file no longer parses. The
                previous dashboard remains intact and in use.
        """
        if self._source_path is None:
            return False

        stamp = _modified_at(self._source_path)
        if stamp is None or stamp == self._stamp:
            return False

        spec = load_dashboard(self._source_path)
        # Only past the parse do we commit: an exception above leaves every
        # attribute as it was, including the stamp, so the next call tries
        # again rather than deciding the broken version is current.
        self._spec = spec
        self._built = build_dashboard(spec)
        self._stamp = stamp
        # An edit that removes screens can leave the index past the end. Hold
        # position where it still exists, so editing screen three does not
        # yank the panel back to screen one on every save.
        self._current = min(self._current, self.screen_count - 1)
        return True

    def render(self, canvas: Canvas, source: StateSource) -> None:
        """Update from ``source`` and draw, in one call.

        The convenient entry point, and the right one when a caller has no
        reason to separate the two -- a one-shot preview, or a test.
        """
        self.update(source)
        self.draw(canvas)
