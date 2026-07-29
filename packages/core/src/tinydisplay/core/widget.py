"""The widget tree.

A widget is anything that knows how to paint itself into a rectangle of a
:class:`~tinydisplay.core.canvas.Canvas`. Widgets never talk to drivers and
never allocate their own framebuffer; that keeps them cheap to compose and
trivial to test.

**Coordinate system.** :meth:`Widget.render` receives the canvas in *canvas*
coordinates, not widget-local ones, and should draw relative to
``self.bounds``. :meth:`Widget.draw` clips to those bounds first, so a widget
that miscalculates cannot corrupt its neighbours. This avoids a translating
canvas wrapper and keeps the hot path allocation-free.

**Dirty tracking.** Widgets start dirty and mark themselves dirty whenever
their geometry or state changes. Marking propagates up to the parent, so a
render loop can ask the root whether anything needs repainting before
rebuilding a frame.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from tinydisplay.core.geometry import Rect

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from tinydisplay.core.canvas import Canvas

__all__ = ["Container", "Widget"]


class Widget(ABC):
    """Base class for everything that can paint itself onto a canvas."""

    __slots__ = ("_bounds", "_dirty", "_name", "_parent", "_visible")

    def __init__(
        self,
        bounds: Rect | None = None,
        *,
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        """Create a widget occupying ``bounds`` (defaulting to an empty rect)."""
        self._bounds = bounds if bounds is not None else Rect(0, 0, 0, 0)
        self._visible = visible
        self._name = name or type(self).__name__
        self._parent: Widget | None = None
        self._dirty = True

    # -- Geometry and state ------------------------------------------------

    @property
    def bounds(self) -> Rect:
        """The area this widget paints into, in canvas coordinates."""
        return self._bounds

    @bounds.setter
    def bounds(self, value: Rect) -> None:
        if value != self._bounds:
            self._bounds = value
            self.mark_dirty()

    @property
    def visible(self) -> bool:
        """Whether :meth:`draw` paints anything."""
        return self._visible

    @visible.setter
    def visible(self, value: bool) -> None:
        if value != self._visible:
            self._visible = value
            self.mark_dirty()

    @property
    def name(self) -> str:
        """A human-readable identifier, used in ``repr`` and debugging."""
        return self._name

    @property
    def parent(self) -> Widget | None:
        """The containing widget, or ``None`` for a root."""
        return self._parent

    # -- Dirty tracking ----------------------------------------------------

    @property
    def is_dirty(self) -> bool:
        """Whether this widget needs repainting."""
        return self._dirty

    def mark_dirty(self) -> None:
        """Flag this widget -- and its ancestors -- as needing a repaint."""
        self._dirty = True
        parent = self._parent
        while parent is not None and not parent._dirty:  # noqa: SLF001 - same class
            parent._dirty = True  # noqa: SLF001 - same class
            parent = parent._parent  # noqa: SLF001 - same class

    def mark_clean(self) -> None:
        """Clear this widget's dirty flag. Called by the render loop after painting."""
        self._dirty = False

    # -- Painting ----------------------------------------------------------

    def draw(self, canvas: Canvas) -> None:
        """Paint this widget onto ``canvas``, clipped to :attr:`bounds`.

        This is the entry point a render loop calls. Subclasses override
        :meth:`render` instead, so that clipping and visibility handling stay
        in one place.
        """
        if not self._visible or self._bounds.is_empty:
            self.mark_clean()
            return
        with canvas.clip(self._bounds) as visible_area:
            if not visible_area.is_empty:
                self.render(canvas)
        self.mark_clean()

    @abstractmethod
    def render(self, canvas: Canvas) -> None:
        """Paint the widget's own content.

        The canvas is already clipped to :attr:`bounds`; draw in canvas
        coordinates, offsetting from ``self.bounds.x`` and ``self.bounds.y``.
        """

    def __repr__(self) -> str:
        return f"{type(self).__name__}(name={self._name!r}, bounds={self._bounds!r})"


class Container(Widget):
    """A widget that paints child widgets in insertion order.

    Children are painted back-to-front, so later children draw on top. The
    container clips to its own bounds, meaning a child that strays outside is
    trimmed rather than allowed to overdraw its siblings' neighbours.
    """

    __slots__ = ("_children",)

    def __init__(
        self,
        bounds: Rect | None = None,
        *,
        children: Iterable[Widget] = (),
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        """Create a container, optionally pre-populated with ``children``."""
        super().__init__(bounds, visible=visible, name=name)
        self._children: list[Widget] = []
        for child in children:
            self.add(child)

    @property
    def children(self) -> Sequence[Widget]:
        """The child widgets, in paint order."""
        return tuple(self._children)

    def add(self, child: Widget) -> Widget:
        """Append ``child`` and return it, for fluent construction.

        Raises:
            ValueError: If the widget already belongs to a container, or if a
                widget is added to itself.
        """
        if child is self:
            msg = "a container cannot contain itself"
            raise ValueError(msg)
        if child._parent is not None:  # noqa: SLF001 - same hierarchy
            msg = f"{child!r} already belongs to {child.parent!r}"
            raise ValueError(msg)
        child._parent = self  # noqa: SLF001 - same hierarchy
        self._children.append(child)
        self.mark_dirty()
        return child

    def remove(self, child: Widget) -> None:
        """Detach ``child``.

        Raises:
            ValueError: If ``child`` is not in this container.
        """
        try:
            self._children.remove(child)
        except ValueError:
            msg = f"{child!r} is not a child of {self!r}"
            raise ValueError(msg) from None
        child._parent = None  # noqa: SLF001 - same hierarchy
        self.mark_dirty()

    def clear(self) -> None:
        """Detach every child."""
        for child in self._children:
            child._parent = None  # noqa: SLF001 - same hierarchy
        self._children.clear()
        self.mark_dirty()

    @property
    def is_dirty(self) -> bool:
        """Whether this container or any descendant needs repainting."""
        return super().is_dirty or any(child.is_dirty for child in self._children)

    def mark_clean(self) -> None:
        """Clear the dirty flag on this container and every descendant."""
        super().mark_clean()
        for child in self._children:
            child.mark_clean()

    def render(self, canvas: Canvas) -> None:
        """Paint every visible child, in order."""
        for child in self._children:
            child.draw(canvas)

    def __iter__(self) -> Iterator[Widget]:
        return iter(self._children)

    def __len__(self) -> int:
        return len(self._children)
