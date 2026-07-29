"""Tests for :mod:`tinydisplay.core.geometry`."""

from __future__ import annotations

import pytest

from tinydisplay.core import Point, Rect, Size


class TestPoint:
    def test_arithmetic(self) -> None:
        assert Point(1, 2) + Point(3, 4) == Point(4, 6)
        assert Point(5, 5) - Point(1, 2) == Point(4, 3)
        assert Point(1, 1).translated(-2, 3) == Point(-1, 4)

    def test_as_tuple(self) -> None:
        assert Point(7, 8).as_tuple() == (7, 8)


class TestSize:
    def test_rejects_negative_dimensions(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            Size(-1, 10)

    def test_area_and_emptiness(self) -> None:
        assert Size(4, 5).area == 20
        assert Size(0, 5).is_empty
        assert not Size(4, 5).is_empty


class TestRectBounds:
    def test_edges_are_half_open(self) -> None:
        r = Rect(10, 20, 100, 50)
        assert (r.left, r.top, r.right, r.bottom) == (10, 20, 110, 70)

    def test_from_bounds_round_trips(self) -> None:
        assert Rect.from_bounds(10, 20, 110, 70) == Rect(10, 20, 100, 50)

    def test_from_size_defaults_to_origin(self) -> None:
        assert Rect.from_size(Size(4, 5)) == Rect(0, 0, 4, 5)
        assert Rect.from_size(Size(4, 5), origin=Point(1, 2)) == Rect(1, 2, 4, 5)

    def test_rejects_negative_size(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            Rect(0, 0, -1, 5)

    def test_centre_rounds_down(self) -> None:
        assert Rect(0, 0, 5, 5).center == Point(2, 2)
        assert Rect(0, 0, 4, 4).center == Point(2, 2)


class TestRectQueries:
    def test_contains_point_excludes_far_edges(self) -> None:
        r = Rect(0, 0, 10, 10)
        assert r.contains_point(0, 0)
        assert r.contains_point(9, 9)
        assert not r.contains_point(10, 10)
        assert not r.contains_point(-1, 0)

    def test_contains_rect(self) -> None:
        outer = Rect(0, 0, 10, 10)
        assert outer.contains_rect(Rect(1, 1, 5, 5))
        assert outer.contains_rect(outer)
        assert not outer.contains_rect(Rect(5, 5, 10, 10))

    def test_empty_rects_are_never_contained(self) -> None:
        assert not Rect(0, 0, 10, 10).contains_rect(Rect(1, 1, 0, 0))

    def test_touching_rects_do_not_intersect(self) -> None:
        assert not Rect(0, 0, 10, 10).intersects(Rect(10, 0, 10, 10))
        assert Rect(0, 0, 10, 10).intersects(Rect(9, 0, 10, 10))


class TestRectDerivations:
    def test_intersection_of_overlapping_rects(self) -> None:
        assert Rect(0, 0, 10, 10).intersection(Rect(5, 5, 10, 10)) == Rect(5, 5, 5, 5)

    def test_intersection_of_disjoint_rects_is_empty(self) -> None:
        assert Rect(0, 0, 5, 5).intersection(Rect(50, 50, 5, 5)).is_empty

    def test_union_covers_both(self) -> None:
        assert Rect(0, 0, 5, 5).union(Rect(10, 10, 5, 5)) == Rect(0, 0, 15, 15)

    def test_union_ignores_empty_operands(self) -> None:
        r = Rect(3, 4, 5, 6)
        assert r.union(Rect(0, 0, 0, 0)) == r
        assert Rect(0, 0, 0, 0).union(r) == r

    def test_translated_preserves_size(self) -> None:
        assert Rect(0, 0, 5, 6).translated(3, -2) == Rect(3, -2, 5, 6)

    def test_inset_shrinks_on_every_edge(self) -> None:
        assert Rect(0, 0, 10, 10).inset(2) == Rect(2, 2, 6, 6)

    def test_negative_inset_grows(self) -> None:
        assert Rect(5, 5, 10, 10).inset(-1) == Rect(4, 4, 12, 12)

    def test_over_inset_collapses_to_empty(self) -> None:
        assert Rect(0, 0, 4, 4).inset(10).is_empty

    def test_as_tuple(self) -> None:
        assert Rect(1, 2, 3, 4).as_tuple() == (1, 2, 3, 4)
