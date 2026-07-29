"""Immutable integer geometry primitives.

Displays are integer pixel grids, so every coordinate in TinyDisplay is an
``int``. All three types here are frozen and hashable, which makes them safe to
use as dictionary keys, to share between widgets, and to compare in tests.

Rectangles use *half-open* bounds throughout: a rectangle at ``x=0`` with
``width=10`` covers columns ``0..9``, and :attr:`Rect.right` is ``10``. This is
the same convention as Python slices, which lets rectangles index NumPy
framebuffers directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Self

__all__ = ["Point", "Rect", "Size"]


@dataclass(frozen=True, slots=True)
class Point:
    """A single integer coordinate pair."""

    x: int
    y: int

    def translated(self, dx: int, dy: int) -> Point:
        """Return a copy shifted by ``(dx, dy)``."""
        return Point(self.x + dx, self.y + dy)

    def __add__(self, other: Point) -> Point:
        return Point(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Point) -> Point:
        return Point(self.x - other.x, self.y - other.y)

    def as_tuple(self) -> tuple[int, int]:
        """Return ``(x, y)``."""
        return (self.x, self.y)


@dataclass(frozen=True, slots=True)
class Size:
    """A non-negative width/height pair."""

    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < 0 or self.height < 0:
            msg = f"size must be non-negative, got {self.width}x{self.height}"
            raise ValueError(msg)

    @property
    def area(self) -> int:
        """Number of pixels covered by this size."""
        return self.width * self.height

    @property
    def is_empty(self) -> bool:
        """``True`` when either dimension is zero."""
        return self.width == 0 or self.height == 0

    def as_tuple(self) -> tuple[int, int]:
        """Return ``(width, height)``."""
        return (self.width, self.height)


@dataclass(frozen=True, slots=True)
class Rect:
    """An axis-aligned rectangle with half-open bounds.

    Example:
        >>> r = Rect(10, 10, 100, 50)
        >>> r.right, r.bottom
        (110, 60)
        >>> r.contains_point(10, 10), r.contains_point(110, 60)
        (True, False)
    """

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width < 0 or self.height < 0:
            msg = f"rect size must be non-negative, got {self.width}x{self.height}"
            raise ValueError(msg)

    # -- Constructors ------------------------------------------------------

    @classmethod
    def from_bounds(cls, left: int, top: int, right: int, bottom: int) -> Self:
        """Build a rect from half-open edges, where ``right``/``bottom`` are exclusive."""
        return cls(left, top, right - left, bottom - top)

    @classmethod
    def from_size(cls, size: Size, *, origin: Point | None = None) -> Self:
        """Build a rect of ``size``, positioned at ``origin`` (default ``(0, 0)``)."""
        at = origin or Point(0, 0)
        return cls(at.x, at.y, size.width, size.height)

    # -- Edges and derived values ------------------------------------------

    @property
    def left(self) -> int:
        """Inclusive left edge."""
        return self.x

    @property
    def top(self) -> int:
        """Inclusive top edge."""
        return self.y

    @property
    def right(self) -> int:
        """Exclusive right edge (``x + width``)."""
        return self.x + self.width

    @property
    def bottom(self) -> int:
        """Exclusive bottom edge (``y + height``)."""
        return self.y + self.height

    @property
    def position(self) -> Point:
        """Top-left corner."""
        return Point(self.x, self.y)

    @property
    def size(self) -> Size:
        """Width and height."""
        return Size(self.width, self.height)

    @property
    def center(self) -> Point:
        """Centre point, rounded down."""
        return Point(self.x + self.width // 2, self.y + self.height // 2)

    @property
    def area(self) -> int:
        """Number of pixels covered."""
        return self.width * self.height

    @property
    def is_empty(self) -> bool:
        """``True`` when the rectangle covers no pixels."""
        return self.width == 0 or self.height == 0

    # -- Queries -----------------------------------------------------------

    def contains_point(self, x: int, y: int) -> bool:
        """Whether pixel ``(x, y)`` lies inside this rectangle."""
        return self.left <= x < self.right and self.top <= y < self.bottom

    def contains_rect(self, other: Rect) -> bool:
        """Whether ``other`` lies entirely inside this rectangle.

        An empty rectangle is never contained, because it covers no pixels.
        """
        if other.is_empty or self.is_empty:
            return False
        return (
            other.left >= self.left
            and other.top >= self.top
            and other.right <= self.right
            and other.bottom <= self.bottom
        )

    def intersects(self, other: Rect) -> bool:
        """Whether the two rectangles share at least one pixel."""
        return not (
            other.left >= self.right
            or other.right <= self.left
            or other.top >= self.bottom
            or other.bottom <= self.top
        )

    # -- Derivations -------------------------------------------------------

    def intersection(self, other: Rect) -> Rect:
        """Return the overlapping region, or an empty rect at the origin if disjoint."""
        left = max(self.left, other.left)
        top = max(self.top, other.top)
        right = min(self.right, other.right)
        bottom = min(self.bottom, other.bottom)
        if right <= left or bottom <= top:
            return Rect(0, 0, 0, 0)
        return Rect.from_bounds(left, top, right, bottom)

    def union(self, other: Rect) -> Rect:
        """Return the smallest rect containing both, ignoring empty operands."""
        if self.is_empty:
            return other
        if other.is_empty:
            return self
        return Rect.from_bounds(
            min(self.left, other.left),
            min(self.top, other.top),
            max(self.right, other.right),
            max(self.bottom, other.bottom),
        )

    def translated(self, dx: int, dy: int) -> Rect:
        """Return a copy shifted by ``(dx, dy)``, keeping its size."""
        return Rect(self.x + dx, self.y + dy, self.width, self.height)

    def inset(self, amount: int) -> Rect:
        """Shrink by ``amount`` on every edge, clamping to an empty rect.

        Negative values grow the rectangle, which makes this usable for both
        padding and outlining.
        """
        width = self.width - 2 * amount
        height = self.height - 2 * amount
        if width <= 0 or height <= 0:
            return Rect(self.x + amount, self.y + amount, 0, 0)
        return Rect(self.x + amount, self.y + amount, width, height)

    def as_tuple(self) -> tuple[int, int, int, int]:
        """Return ``(x, y, width, height)``."""
        return (self.x, self.y, self.width, self.height)
