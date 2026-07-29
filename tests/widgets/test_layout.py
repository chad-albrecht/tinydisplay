"""Tests for the layout containers.

Layout on a 320-pixel panel is arithmetic with no slack: a rounding error that
would be invisible on a monitor is a visible seam here. So the assertions are
mostly about exactness -- children summing to the available space, the last
column reaching the edge -- rather than about approximate placement.
"""

from __future__ import annotations

import pytest

from tinydisplay.core import Canvas, Rect, Widget
from tinydisplay.widgets import Align, Axis, Grid, LayoutError, Padding, Slot, Spacer, Stack


class Box(Widget):
    """A widget that records whether it was painted."""

    __slots__ = ("painted",)

    def __init__(self, name: str = "box") -> None:
        super().__init__(name=name)
        self.painted = 0

    def render(self, canvas: Canvas) -> None:  # noqa: ARG002 - counting only
        self.painted += 1


class TestStackSizing:
    def test_fixed_slots_get_exactly_what_they_ask(self) -> None:
        stack = Stack(
            Axis.VERTICAL,
            Rect(0, 0, 100, 100),
            slots=[Slot(Box(), size=30), Slot(Box(), size=20)],
        )
        stack.layout()
        assert [child.bounds.height for child in stack] == [30, 20]

    def test_flexible_slots_share_what_is_left(self) -> None:
        stack = Stack(
            Axis.VERTICAL,
            Rect(0, 0, 100, 100),
            slots=[Slot(Box(), size=40), Slot(Box()), Slot(Box())],
        )
        stack.layout()
        assert [child.bounds.height for child in stack] == [40, 30, 30]

    def test_weights_divide_in_proportion(self) -> None:
        stack = Stack(
            Axis.HORIZONTAL,
            Rect(0, 0, 100, 10),
            slots=[Slot(Box(), weight=1), Slot(Box(), weight=3)],
        )
        stack.layout()
        assert [child.bounds.width for child in stack] == [25, 75]

    def test_children_fill_the_space_exactly(self) -> None:
        # Three into 100 does not divide. Rounding each child independently
        # would leave a one-pixel seam, which is visible on a small panel.
        stack = Stack(Axis.VERTICAL, Rect(0, 0, 50, 100), slots=[Slot(Box()) for _ in range(3)])
        stack.layout()
        assert sum(child.bounds.height for child in stack) == 100

    def test_spacing_is_taken_from_the_shared_space(self) -> None:
        stack = Stack(
            Axis.VERTICAL,
            Rect(0, 0, 50, 100),
            slots=[Slot(Box()), Slot(Box())],
            spacing=10,
        )
        stack.layout()
        heights = [child.bounds.height for child in stack]
        assert heights == [45, 45]
        assert stack.children[1].bounds.y - stack.children[0].bounds.bottom == 10

    def test_children_do_not_overlap(self) -> None:
        stack = Stack(
            Axis.HORIZONTAL,
            Rect(0, 0, 97, 20),
            slots=[Slot(Box()) for _ in range(4)],
            spacing=3,
        )
        stack.layout()
        for left, right in zip(stack.children, stack.children[1:], strict=False):
            assert left.bounds.right <= right.bounds.x

    def test_overcommitted_fixed_slots_do_not_produce_negative_sizes(self) -> None:
        # A dashboard asking for more than the panel has is a mistake, but it
        # must degrade rather than produce an invalid rectangle.
        stack = Stack(
            Axis.VERTICAL,
            Rect(0, 0, 50, 30),
            slots=[Slot(Box(), size=40), Slot(Box())],
        )
        stack.layout()
        assert all(child.bounds.height >= 0 for child in stack)

    def test_an_empty_stack_lays_out_harmlessly(self) -> None:
        Stack(Axis.VERTICAL, Rect(0, 0, 10, 10)).layout()


class TestStackAxes:
    def test_vertical_stacks_downwards(self) -> None:
        stack = Stack(Axis.VERTICAL, Rect(5, 7, 40, 60), slots=[Slot(Box()), Slot(Box())])
        stack.layout()
        first, second = stack.children
        assert first.bounds.y == 7
        assert second.bounds.y == first.bounds.bottom
        assert first.bounds.x == second.bounds.x == 5

    def test_horizontal_stacks_rightwards(self) -> None:
        stack = Stack(Axis.HORIZONTAL, Rect(5, 7, 40, 60), slots=[Slot(Box()), Slot(Box())])
        stack.layout()
        first, second = stack.children
        assert first.bounds.x == 5
        assert second.bounds.x == first.bounds.right
        assert first.bounds.y == second.bounds.y == 7

    def test_cross_returns_the_other_axis(self) -> None:
        assert Axis.HORIZONTAL.cross is Axis.VERTICAL
        assert Axis.VERTICAL.cross is Axis.HORIZONTAL


class TestStackAlignment:
    def test_stretch_fills_the_cross_axis(self) -> None:
        stack = Stack(Axis.VERTICAL, Rect(0, 0, 80, 40), slots=[Slot(Box(), cross_size=20)])
        stack.layout()
        assert stack.children[0].bounds.width == 80

    def test_centre_places_a_sized_child_in_the_middle(self) -> None:
        stack = Stack(
            Axis.VERTICAL,
            Rect(0, 0, 80, 40),
            slots=[Slot(Box(), align=Align.CENTER, cross_size=20)],
        )
        stack.layout()
        assert stack.children[0].bounds.x == 30
        assert stack.children[0].bounds.width == 20

    def test_end_places_a_sized_child_against_the_far_edge(self) -> None:
        stack = Stack(
            Axis.VERTICAL,
            Rect(0, 0, 80, 40),
            slots=[Slot(Box(), align=Align.END, cross_size=20)],
        )
        stack.layout()
        assert stack.children[0].bounds.right == 80

    def test_start_places_a_sized_child_against_the_near_edge(self) -> None:
        stack = Stack(
            Axis.VERTICAL,
            Rect(10, 0, 80, 40),
            slots=[Slot(Box(), align=Align.START, cross_size=20)],
        )
        stack.layout()
        assert stack.children[0].bounds.x == 10


class TestStackMembership:
    def test_a_bare_widget_still_gets_a_slot(self) -> None:
        # Otherwise it would be painted but never positioned, which looks like
        # a rendering bug rather than a missing slot.
        stack = Stack(Axis.VERTICAL, Rect(0, 0, 40, 40))
        stack.add(Box())
        stack.layout()
        assert stack.children[0].bounds.height == 40

    def test_removing_a_child_removes_its_slot(self) -> None:
        stack = Stack(Axis.VERTICAL, Rect(0, 0, 40, 40))
        first = stack.add(Box())
        stack.add(Box())
        stack.remove(first)
        stack.layout()

        assert len(stack.slots) == 1
        assert stack.children[0].bounds.height == 40

    def test_clearing_removes_every_slot(self) -> None:
        stack = Stack(Axis.VERTICAL, Rect(0, 0, 40, 40), slots=[Slot(Box()), Slot(Box())])
        stack.clear()
        assert stack.slots == ()
        assert len(stack) == 0

    def test_slots_accept_bare_widgets_at_construction(self) -> None:
        stack = Stack(Axis.VERTICAL, Rect(0, 0, 40, 40), slots=[Box(), Box()])
        stack.layout()
        assert [child.bounds.height for child in stack] == [20, 20]


class TestStackPainting:
    def test_layout_happens_before_the_first_paint(self) -> None:
        stack = Stack(Axis.VERTICAL, Rect(0, 0, 20, 20), slots=[Slot(Box())])
        stack.draw(Canvas(20, 20))
        assert stack.children[0].bounds.height == 20

    def test_moving_the_stack_relays_out_its_children(self) -> None:
        stack = Stack(Axis.VERTICAL, Rect(0, 0, 20, 20), slots=[Slot(Box())])
        stack.draw(Canvas(40, 40))
        stack.bounds = Rect(10, 10, 20, 20)
        stack.draw(Canvas(40, 40))
        assert stack.children[0].bounds.x == 10

    def test_children_are_painted(self) -> None:
        box = Box()
        stack = Stack(Axis.VERTICAL, Rect(0, 0, 20, 20), slots=[Slot(box)])
        stack.draw(Canvas(20, 20))
        assert box.painted == 1


class TestStackValidation:
    def test_negative_spacing_is_refused(self) -> None:
        with pytest.raises(LayoutError, match="spacing must be non-negative"):
            Stack(Axis.VERTICAL, spacing=-1)

    def test_a_negative_slot_size_is_refused(self) -> None:
        with pytest.raises(LayoutError, match="size must be non-negative"):
            Slot(Box(), size=-5)

    def test_a_flexible_slot_needs_a_positive_weight(self) -> None:
        with pytest.raises(LayoutError, match="positive weight"):
            Slot(Box(), weight=0)


class TestPadding:
    def test_uniform_padding(self) -> None:
        padded = Padding(Box(), Rect(0, 0, 40, 20), all=4)
        padded.layout()
        assert padded.child.bounds == Rect(4, 4, 32, 12)

    def test_axis_padding(self) -> None:
        padded = Padding(Box(), Rect(0, 0, 40, 20), horizontal=5, vertical=2)
        padded.layout()
        assert padded.child.bounds == Rect(5, 2, 30, 16)

    def test_per_edge_padding(self) -> None:
        padded = Padding(Box(), Rect(0, 0, 40, 20), left=1, top=2, right=3, bottom=4)
        padded.layout()
        assert padded.insets == (1, 2, 3, 4)
        assert padded.child.bounds == Rect(1, 2, 36, 14)

    def test_padding_larger_than_the_box_gives_an_empty_child(self) -> None:
        padded = Padding(Box(), Rect(0, 0, 10, 10), all=20)
        padded.layout()
        assert padded.child.bounds.is_empty

    def test_negative_padding_is_refused(self) -> None:
        with pytest.raises(LayoutError, match="padding must be non-negative"):
            Padding(Box(), all=-1)

    def test_the_child_is_painted(self) -> None:
        box = Box()
        Padding(box, Rect(0, 0, 20, 20), all=2).draw(Canvas(20, 20))
        assert box.painted == 1


class TestGrid:
    def test_cells_divide_the_area(self) -> None:
        grid = Grid(2, 2, Rect(0, 0, 100, 80))
        top_left = grid.place(Box(), row=0, column=0)
        bottom_right = grid.place(Box(), row=1, column=1)
        grid.layout()

        assert top_left.bounds == Rect(0, 0, 50, 40)
        assert bottom_right.bounds == Rect(50, 40, 50, 40)

    def test_the_last_column_reaches_the_edge(self) -> None:
        # 100 does not divide by 3; multiplying a rounded cell width would
        # leave a stray column of background at the right.
        grid = Grid(1, 3, Rect(0, 0, 100, 10))
        last = grid.place(Box(), row=0, column=2)
        grid.layout()
        assert last.bounds.right == 100

    def test_spacing_separates_cells(self) -> None:
        grid = Grid(1, 2, Rect(0, 0, 100, 10), spacing=10)
        left = grid.place(Box(), row=0, column=0)
        right = grid.place(Box(), row=0, column=1)
        grid.layout()
        assert right.bounds.x - left.bounds.right == 10

    def test_a_span_covers_several_cells(self) -> None:
        grid = Grid(2, 2, Rect(0, 0, 100, 80))
        wide = grid.place(Box(), row=0, column=0, column_span=2)
        grid.layout()
        assert wide.bounds.width == 100

    def test_offset_grids_place_relative_to_their_own_origin(self) -> None:
        grid = Grid(1, 1, Rect(20, 30, 40, 50))
        cell = grid.place(Box(), row=0, column=0)
        grid.layout()
        assert cell.bounds == Rect(20, 30, 40, 50)

    @pytest.mark.parametrize(("rows", "columns"), [(0, 1), (1, 0), (-1, 2)])
    def test_an_empty_grid_is_refused(self, rows: int, columns: int) -> None:
        with pytest.raises(LayoutError, match="at least one row and column"):
            Grid(rows, columns)

    def test_a_cell_outside_the_grid_is_refused(self) -> None:
        with pytest.raises(LayoutError, match="outside a 2x2 grid"):
            Grid(2, 2).place(Box(), row=2, column=0)

    def test_a_span_running_off_the_grid_is_refused(self) -> None:
        with pytest.raises(LayoutError, match="runs off"):
            Grid(2, 2).place(Box(), row=0, column=1, column_span=2)

    def test_a_zero_span_is_refused(self) -> None:
        with pytest.raises(LayoutError, match="spans must be at least 1"):
            Grid(2, 2).place(Box(), row=0, column=0, row_span=0)

    def test_children_are_painted(self) -> None:
        grid = Grid(1, 1, Rect(0, 0, 20, 20))
        box = Box()
        grid.place(box, row=0, column=0)
        grid.draw(Canvas(20, 20))
        assert box.painted == 1


class TestSpacer:
    def test_it_draws_nothing_but_occupies_a_slot(self) -> None:
        canvas = Canvas(20, 20)
        canvas.clear()
        stack = Stack(Axis.VERTICAL, Rect(0, 0, 20, 20), slots=[Slot(Spacer()), Slot(Box())])
        stack.draw(canvas)

        assert stack.children[0].bounds.height == 10
        assert canvas.get_pixel(0, 0) == canvas.background
