"""Tests for :mod:`tinydisplay.core.widget`."""

from __future__ import annotations

import numpy as np
import pytest

from tinydisplay.core import Canvas, Color, Container, Rect, Widget


class FillWidget(Widget):
    """A widget that floods a colour across a deliberately oversized area.

    Overdrawing is the point: it proves that :meth:`Widget.draw` clips to the
    widget's bounds rather than trusting the widget to behave.
    """

    __slots__ = ("color", "render_count")

    def __init__(
        self,
        bounds: Rect,
        color: Color = Color.WHITE,
        *,
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        super().__init__(bounds, visible=visible, name=name)
        self.color = color
        self.render_count = 0

    def render(self, canvas: Canvas) -> None:
        self.render_count += 1
        canvas.rect(0, 0, canvas.width, canvas.height, self.color)


def painted(canvas: Canvas) -> int:
    return int(np.count_nonzero(np.any(canvas.buffer != Color.BLACK.rgb, axis=-1)))


class TestWidgetBasics:
    def test_is_abstract(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            Widget()  # type: ignore[abstract]

    def test_defaults(self) -> None:
        widget = FillWidget(Rect(0, 0, 4, 4))
        assert widget.visible
        assert widget.is_dirty
        assert widget.parent is None
        assert widget.name == "FillWidget"

    def test_custom_name(self) -> None:
        assert FillWidget(Rect(0, 0, 1, 1), name="cpu").name == "cpu"

    def test_repr_includes_bounds(self) -> None:
        assert "bounds=" in repr(FillWidget(Rect(1, 2, 3, 4)))


class TestPainting:
    def test_draw_clips_to_bounds(self) -> None:
        canvas = Canvas(16, 16)
        FillWidget(Rect(2, 2, 4, 4)).draw(canvas)
        assert painted(canvas) == 16
        assert canvas.get_pixel(2, 2) == Color.WHITE
        assert canvas.get_pixel(6, 6) == Color.BLACK

    def test_invisible_widgets_do_not_render(self) -> None:
        canvas = Canvas(16, 16)
        widget = FillWidget(Rect(0, 0, 8, 8), visible=False)
        widget.draw(canvas)
        assert painted(canvas) == 0
        assert widget.render_count == 0

    def test_empty_bounds_do_not_render(self) -> None:
        canvas = Canvas(16, 16)
        widget = FillWidget(Rect(0, 0, 0, 0))
        widget.draw(canvas)
        assert widget.render_count == 0

    def test_fully_offscreen_widgets_do_not_render(self) -> None:
        canvas = Canvas(16, 16)
        widget = FillWidget(Rect(100, 100, 8, 8))
        widget.draw(canvas)
        assert widget.render_count == 0

    def test_draw_restores_the_clip_region(self) -> None:
        canvas = Canvas(16, 16)
        FillWidget(Rect(2, 2, 4, 4)).draw(canvas)
        assert canvas.clip_rect == canvas.bounds


class TestDirtyTracking:
    def test_draw_marks_clean(self) -> None:
        widget = FillWidget(Rect(0, 0, 4, 4))
        widget.draw(Canvas(8, 8))
        assert not widget.is_dirty

    def test_changing_bounds_marks_dirty(self) -> None:
        widget = FillWidget(Rect(0, 0, 4, 4))
        widget.mark_clean()
        widget.bounds = Rect(1, 1, 4, 4)
        assert widget.is_dirty

    def test_setting_identical_bounds_does_not_mark_dirty(self) -> None:
        widget = FillWidget(Rect(0, 0, 4, 4))
        widget.mark_clean()
        widget.bounds = Rect(0, 0, 4, 4)
        assert not widget.is_dirty

    def test_toggling_visibility_marks_dirty(self) -> None:
        widget = FillWidget(Rect(0, 0, 4, 4))
        widget.mark_clean()
        widget.visible = False
        assert widget.is_dirty

    def test_dirtiness_propagates_to_ancestors(self) -> None:
        leaf = FillWidget(Rect(0, 0, 2, 2))
        middle = Container(Rect(0, 0, 8, 8), children=[leaf])
        root = Container(Rect(0, 0, 16, 16), children=[middle])
        root.mark_clean()
        assert not root.is_dirty

        leaf.mark_dirty()
        assert leaf.is_dirty
        assert middle.is_dirty
        assert root.is_dirty


class TestContainer:
    def test_children_are_painted_in_order(self) -> None:
        canvas = Canvas(8, 8)
        container = Container(
            Rect(0, 0, 8, 8),
            children=[
                FillWidget(Rect(0, 0, 8, 8), Color.RED),
                FillWidget(Rect(0, 0, 8, 8), Color.GREEN),
            ],
        )
        container.draw(canvas)
        assert canvas.get_pixel(0, 0) == Color.GREEN

    def test_container_clips_its_children(self) -> None:
        canvas = Canvas(16, 16)
        container = Container(
            Rect(0, 0, 4, 4),
            children=[FillWidget(Rect(0, 0, 16, 16), Color.WHITE)],
        )
        container.draw(canvas)
        assert painted(canvas) == 16

    def test_add_returns_the_child_and_sets_the_parent(self) -> None:
        container = Container(Rect(0, 0, 8, 8))
        child = container.add(FillWidget(Rect(0, 0, 2, 2)))
        assert child.parent is container
        assert list(container) == [child]
        assert len(container) == 1

    def test_add_rejects_a_widget_that_already_has_a_parent(self) -> None:
        first = Container(Rect(0, 0, 8, 8))
        child = first.add(FillWidget(Rect(0, 0, 2, 2)))
        with pytest.raises(ValueError, match="already belongs"):
            Container(Rect(0, 0, 8, 8)).add(child)

    def test_add_rejects_self_nesting(self) -> None:
        container = Container(Rect(0, 0, 8, 8))
        with pytest.raises(ValueError, match="cannot contain itself"):
            container.add(container)

    def test_remove_detaches_the_child(self) -> None:
        container = Container(Rect(0, 0, 8, 8))
        child = container.add(FillWidget(Rect(0, 0, 2, 2)))
        container.remove(child)
        assert child.parent is None
        assert len(container) == 0

    def test_remove_rejects_a_stranger(self) -> None:
        with pytest.raises(ValueError, match="is not a child"):
            Container(Rect(0, 0, 8, 8)).remove(FillWidget(Rect(0, 0, 2, 2)))

    def test_removed_children_can_be_re_added_elsewhere(self) -> None:
        first = Container(Rect(0, 0, 8, 8))
        child = first.add(FillWidget(Rect(0, 0, 2, 2)))
        first.remove(child)
        second = Container(Rect(0, 0, 8, 8))
        assert second.add(child) is child

    def test_clear_detaches_every_child(self) -> None:
        container = Container(Rect(0, 0, 8, 8))
        child = container.add(FillWidget(Rect(0, 0, 2, 2)))
        container.clear()
        assert len(container) == 0
        assert child.parent is None

    def test_children_is_a_snapshot(self) -> None:
        container = Container(Rect(0, 0, 8, 8))
        container.add(FillWidget(Rect(0, 0, 2, 2)))
        snapshot = container.children
        container.clear()
        assert len(snapshot) == 1

    def test_mark_clean_is_recursive(self) -> None:
        leaf = FillWidget(Rect(0, 0, 2, 2))
        container = Container(Rect(0, 0, 8, 8), children=[leaf])
        container.mark_clean()
        assert not leaf.is_dirty
        assert not container.is_dirty

    def test_invisible_container_skips_its_children(self) -> None:
        leaf = FillWidget(Rect(0, 0, 8, 8))
        container = Container(Rect(0, 0, 8, 8), children=[leaf], visible=False)
        container.draw(Canvas(8, 8))
        assert leaf.render_count == 0
