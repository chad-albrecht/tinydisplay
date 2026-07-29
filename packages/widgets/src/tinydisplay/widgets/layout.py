"""Layout containers: widgets whose job is positioning other widgets.

Core's :class:`~tinydisplay.core.Widget` carries absolute ``bounds`` and knows
nothing about layout, which is what keeps it cheap. Everything here works by
*assigning* those bounds: a layout container is handed a rectangle and divides
it among its children.

Layout runs when it needs to rather than on every frame. A container recomputes
its children's bounds when its own bounds change or when its contents change,
and remembers what it last laid out into; repainting an unchanged tree costs
nothing beyond the drawing itself.

Sizing along the layout axis is either fixed or weighted. Fixed children take
exactly what they ask for; whatever remains is divided among the weighted ones
in proportion. Two weighted children with weights 1 and 3 take a quarter and
three quarters of the leftover space. This covers the cases a status panel
actually has -- a fixed-height header over a filling body, a row of equal
columns -- without a constraint solver.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from tinydisplay.core import Container, Rect, Widget
from tinydisplay.widgets.errors import LayoutError

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from tinydisplay.core import Canvas

__all__ = [
    "Align",
    "Axis",
    "Grid",
    "Padding",
    "Slot",
    "Spacer",
    "Stack",
]


class Axis(StrEnum):
    """Which way a stack grows."""

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"

    @property
    def cross(self) -> Axis:
        """The perpendicular axis."""
        return Axis.VERTICAL if self is Axis.HORIZONTAL else Axis.HORIZONTAL


class Align(StrEnum):
    """How a child sits on the axis it does not control.

    ``STRETCH`` is the default because a widget that fills its slot is the
    common case, and because a widget with no intrinsic size has nothing to
    align.
    """

    START = "start"
    CENTER = "center"
    END = "end"
    STRETCH = "stretch"


@dataclass(frozen=True, slots=True)
class Slot:
    """A child and how much room it should get along the layout axis.

    Attributes:
        widget: The child.
        size: A fixed extent in pixels, or ``None`` to share what is left.
        weight: Share of the remaining space, when ``size`` is ``None``.
        align: Placement on the cross axis.
        cross_size: A fixed cross-axis extent, or ``None`` to use the
            container's. Ignored when ``align`` is ``STRETCH``.
    """

    widget: Widget
    size: int | None = None
    weight: float = 1.0
    align: Align = Align.STRETCH
    cross_size: int | None = None

    def __post_init__(self) -> None:
        if self.size is not None and self.size < 0:
            msg = f"slot size must be non-negative, got {self.size}"
            raise LayoutError(msg)
        if self.size is None and self.weight <= 0:
            msg = f"a flexible slot needs a positive weight, got {self.weight}"
            raise LayoutError(msg)


def _place(offset: int, extent: int, available: int, align: Align) -> tuple[int, int]:
    """Position a child of ``extent`` within ``available``, returning start and size."""
    if align is Align.STRETCH or extent >= available:
        return (offset, available)
    if align is Align.CENTER:
        return (offset + (available - extent) // 2, extent)
    if align is Align.END:
        return (offset + available - extent, extent)
    return (offset, extent)


class Stack(Container):
    """Lays children out in a row or a column.

    Args:
        axis: Which way to grow.
        bounds: The area to fill.
        slots: Children with their sizing. Bare widgets are accepted and get a
            weight of 1.
        spacing: Pixels between adjacent children.
        name: Identifier for debugging.

    Example:
        >>> from tinydisplay.core import Rect
        >>> from tinydisplay.widgets import Axis, Slot, Stack
        >>> from tinydisplay.widgets import Label
        >>> stack = Stack(
        ...     Axis.VERTICAL,
        ...     Rect(0, 0, 100, 60),
        ...     slots=[Slot(Label("top"), size=20), Slot(Label("rest"))],
        ...     spacing=0,
        ... )
        >>> stack.layout()  # also happens automatically before painting
        >>> [child.bounds.height for child in stack]
        [20, 40]
    """

    __slots__ = ("_axis", "_laid_out", "_slots", "_spacing")

    def __init__(
        self,
        axis: Axis = Axis.VERTICAL,
        bounds: Rect | None = None,
        *,
        slots: Iterable[Slot | Widget] = (),
        spacing: int = 0,
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        if spacing < 0:
            msg = f"spacing must be non-negative, got {spacing}"
            raise LayoutError(msg)

        self._axis = axis
        self._spacing = spacing
        self._slots: list[Slot] = []
        self._laid_out: Rect | None = None
        super().__init__(bounds, visible=visible, name=name)

        for slot in slots:
            self.add_slot(slot if isinstance(slot, Slot) else Slot(slot))

    @property
    def axis(self) -> Axis:
        """Which way this stack grows."""
        return self._axis

    @property
    def spacing(self) -> int:
        """Pixels between adjacent children."""
        return self._spacing

    @property
    def slots(self) -> Sequence[Slot]:
        """The children with their sizing, in order."""
        return tuple(self._slots)

    def add_slot(self, slot: Slot) -> Widget:
        """Append a sized child and return the widget."""
        self._slots.append(slot)
        self.add(slot.widget)
        self._laid_out = None
        return slot.widget

    def add(self, child: Widget) -> Widget:
        """Append a child with default sizing.

        Overrides the base method so that a widget added directly still gets a
        slot; otherwise it would be painted but never positioned.
        """
        widget = super().add(child)
        if not any(slot.widget is child for slot in self._slots):
            self._slots.append(Slot(child))
            self._laid_out = None
        return widget

    def remove(self, child: Widget) -> None:
        """Detach a child and its slot."""
        super().remove(child)
        self._slots = [slot for slot in self._slots if slot.widget is not child]
        self._laid_out = None

    def clear(self) -> None:
        """Detach every child."""
        super().clear()
        self._slots.clear()
        self._laid_out = None

    # -- Layout ------------------------------------------------------------

    def layout(self) -> None:
        """Assign bounds to every child.

        Called automatically before painting. Safe to call directly when a
        caller needs child geometry before the first frame.
        """
        area = self.bounds
        self._laid_out = area
        if not self._slots:
            return

        horizontal = self._axis is Axis.HORIZONTAL
        total = area.width if horizontal else area.height
        cross_total = area.height if horizontal else area.width

        gaps = self._spacing * (len(self._slots) - 1)
        fixed = sum(slot.size for slot in self._slots if slot.size is not None)
        flexible = [slot for slot in self._slots if slot.size is None]
        spare = max(0, total - gaps - fixed)

        extents = self._distribute(spare, flexible)

        offset = area.x if horizontal else area.y
        for slot in self._slots:
            extent = slot.size if slot.size is not None else extents.pop(0)
            cross_extent = slot.cross_size if slot.cross_size is not None else cross_total
            cross_start, cross_size = _place(
                area.y if horizontal else area.x,
                cross_extent,
                cross_total,
                slot.align,
            )
            slot.widget.bounds = (
                Rect(offset, cross_start, extent, cross_size)
                if horizontal
                else Rect(cross_start, offset, cross_size, extent)
            )
            offset += extent + self._spacing

    def _distribute(self, spare: int, flexible: Sequence[Slot]) -> list[int]:
        """Split ``spare`` pixels among ``flexible`` slots by weight.

        Rounding is accumulated rather than applied per slot, so the children
        together fill the space exactly. Rounding each independently would
        leave a gap of up to one pixel per child, which is visible as a seam
        on a panel this small.
        """
        if not flexible:
            return []

        total_weight = sum(slot.weight for slot in flexible)
        extents: list[int] = []
        consumed = 0
        for index, slot in enumerate(flexible):
            if index == len(flexible) - 1:
                extents.append(spare - consumed)
                break
            extent = int(spare * slot.weight / total_weight)
            extents.append(extent)
            consumed += extent
        return extents

    def render(self, canvas: Canvas) -> None:
        """Lay out if needed, then paint the children."""
        if self._laid_out != self.bounds:
            self.layout()
        super().render(canvas)


class Padding(Container):
    """Insets a single child from its own bounds.

    A separate widget rather than a property on every widget: padding is a
    relationship between a widget and the space around it, and putting it in
    one place keeps it out of the constructor of everything else.

    Example:
        >>> from tinydisplay.core import Rect
        >>> from tinydisplay.widgets import Label, Padding
        >>> padded = Padding(Label("hi"), Rect(0, 0, 40, 20), all=4)
        >>> padded.layout()
        >>> padded.child.bounds
        Rect(x=4, y=4, width=32, height=12)
    """

    __slots__ = ("_bottom", "_child", "_laid_out", "_left", "_right", "_top")

    def __init__(
        self,
        child: Widget,
        bounds: Rect | None = None,
        *,
        all: int | None = None,  # noqa: A002 - reads correctly at the call site
        horizontal: int | None = None,
        vertical: int | None = None,
        left: int = 0,
        top: int = 0,
        right: int = 0,
        bottom: int = 0,
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        self._left = _first(all, horizontal, left)
        self._right = _first(all, horizontal, right)
        self._top = _first(all, vertical, top)
        self._bottom = _first(all, vertical, bottom)
        for value in (self._left, self._right, self._top, self._bottom):
            if value < 0:
                msg = f"padding must be non-negative, got {value}"
                raise LayoutError(msg)

        self._child = child
        self._laid_out: Rect | None = None
        super().__init__(bounds, children=(child,), visible=visible, name=name)

    @property
    def child(self) -> Widget:
        """The widget being inset."""
        return self._child

    @property
    def insets(self) -> tuple[int, int, int, int]:
        """The insets as ``(left, top, right, bottom)``."""
        return (self._left, self._top, self._right, self._bottom)

    def layout(self) -> None:
        """Position the child inside the padding."""
        area = self.bounds
        self._laid_out = area
        self._child.bounds = Rect(
            area.x + self._left,
            area.y + self._top,
            max(0, area.width - self._left - self._right),
            max(0, area.height - self._top - self._bottom),
        )

    def render(self, canvas: Canvas) -> None:
        """Lay out if needed, then paint the child."""
        if self._laid_out != self.bounds:
            self.layout()
        super().render(canvas)


def _first(*values: int | None) -> int:
    """The first value that is not ``None``, or zero."""
    for value in values:
        if value is not None:
            return value
    return 0


class Spacer(Widget):
    """Occupies space and draws nothing.

    Useful for pushing siblings apart in a :class:`Stack` without inventing an
    invisible label.
    """

    __slots__ = ()

    def render(self, canvas: Canvas) -> None:
        """Draw nothing."""


class Grid(Container):
    """Lays children out on a fixed grid of equal cells.

    Args:
        rows: Number of rows.
        columns: Number of columns.
        bounds: The area to fill.
        spacing: Pixels between cells, applied both ways.
        name: Identifier for debugging.

    Cells are equal by design. Proportional columns are what :class:`Stack`
    weights are for, and supporting both here would mean two ways to express
    the same layout.

    Example:
        >>> from tinydisplay.core import Rect
        >>> from tinydisplay.widgets import Grid, Label
        >>> grid = Grid(2, 2, Rect(0, 0, 100, 100), spacing=0)
        >>> bottom_right = grid.place(Label("b"), row=1, column=1)
        >>> grid.layout()
        >>> bottom_right.bounds
        Rect(x=50, y=50, width=50, height=50)
    """

    __slots__ = ("_columns", "_laid_out", "_placements", "_rows", "_spacing")

    def __init__(
        self,
        rows: int,
        columns: int,
        bounds: Rect | None = None,
        *,
        spacing: int = 0,
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        if rows <= 0 or columns <= 0:
            msg = f"a grid needs at least one row and column, got {rows}x{columns}"
            raise LayoutError(msg)
        if spacing < 0:
            msg = f"spacing must be non-negative, got {spacing}"
            raise LayoutError(msg)

        self._rows = rows
        self._columns = columns
        self._spacing = spacing
        self._placements: list[tuple[Widget, int, int, int, int]] = []
        self._laid_out: Rect | None = None
        super().__init__(bounds, visible=visible, name=name)

    @property
    def rows(self) -> int:
        """Number of rows."""
        return self._rows

    @property
    def columns(self) -> int:
        """Number of columns."""
        return self._columns

    def place(
        self,
        widget: Widget,
        *,
        row: int,
        column: int,
        row_span: int = 1,
        column_span: int = 1,
    ) -> Widget:
        """Put ``widget`` in a cell, optionally spanning several.

        Raises:
            LayoutError: If the placement falls outside the grid.
        """
        if not 0 <= row < self._rows or not 0 <= column < self._columns:
            msg = f"cell ({row}, {column}) is outside a {self._rows}x{self._columns} grid"
            raise LayoutError(msg)
        if row_span < 1 or column_span < 1:
            msg = f"spans must be at least 1, got {row_span}x{column_span}"
            raise LayoutError(msg)
        if row + row_span > self._rows or column + column_span > self._columns:
            msg = (
                f"a {row_span}x{column_span} span at ({row}, {column}) runs off "
                f"a {self._rows}x{self._columns} grid"
            )
            raise LayoutError(msg)

        self._placements.append((widget, row, column, row_span, column_span))
        self.add(widget)
        self._laid_out = None
        return widget

    def layout(self) -> None:
        """Assign bounds to every placed child."""
        area = self.bounds
        self._laid_out = area

        # Cell edges are computed from the full extent rather than by
        # multiplying a rounded cell size, so the rightmost column reaches the
        # edge instead of leaving a stray pixel column behind.
        inner_width = max(0, area.width - self._spacing * (self._columns - 1))
        inner_height = max(0, area.height - self._spacing * (self._rows - 1))

        def edge(index: int, total: int, count: int, gap: int) -> int:
            return (total * index) // count + gap * index

        for widget, row, column, row_span, column_span in self._placements:
            left = edge(column, inner_width, self._columns, self._spacing)
            right = edge(column + column_span, inner_width, self._columns, self._spacing)
            top = edge(row, inner_height, self._rows, self._spacing)
            bottom = edge(row + row_span, inner_height, self._rows, self._spacing)
            widget.bounds = Rect(
                area.x + left,
                area.y + top,
                max(0, right - left - self._spacing),
                max(0, bottom - top - self._spacing),
            )

    def render(self, canvas: Canvas) -> None:
        """Lay out if needed, then paint the children."""
        if self._laid_out != self.bounds:
            self.layout()
        super().render(canvas)
