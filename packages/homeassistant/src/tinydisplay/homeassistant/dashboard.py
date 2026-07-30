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

    __slots__ = ("_built", "_source_path", "_spec")

    def __init__(self, spec: DashboardSpec, *, source_path: Path | None = None) -> None:
        self._spec = spec
        self._built = build_dashboard(spec)
        self._source_path = source_path

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
        """The top of the widget tree."""
        return self._built.root

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

        A dashboard with no updaters draws the same pixels forever, which is
        worth knowing before scheduling a render loop for it.
        """
        return not self._built.updaters

    def __repr__(self) -> str:
        where = f", {self._source_path}" if self._source_path is not None else ""
        return f"Dashboard({self._spec.theme_name}, {len(self.entity_ids)} entities{where})"

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
        canvas.clear(self.background)
        self._built.root.bounds = Rect(0, 0, canvas.width, canvas.height)
        self._built.root.draw(canvas)

    def render(self, canvas: Canvas, source: StateSource) -> None:
        """Update from ``source`` and draw, in one call.

        The convenient entry point, and the right one when a caller has no
        reason to separate the two -- a one-shot preview, or a test.
        """
        self.update(source)
        self.draw(canvas)
