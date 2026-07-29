"""A widget wrapping an image.

Thin on purpose: :meth:`~tinydisplay.core.Canvas.image` already decodes,
scales and composites, so this adds only what a widget needs -- bounds to draw
into and a source that can be swapped without rebuilding the tree.

Anything a drawn :class:`~tinydisplay.widgets.icon.Icon` cannot express belongs
here: logos, weather glyphs, photographs. The trade runs the other way too, so
prefer an icon for anything that should follow the theme's colours.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from tinydisplay.core import Widget

if TYPE_CHECKING:
    from tinydisplay.core import Canvas, Rect

__all__ = ["ImageWidget"]


class ImageWidget(Widget):
    """Draws an image inside its bounds.

    Args:
        source: Anything :meth:`~tinydisplay.core.Canvas.image` accepts -- a
            path, a Pillow image, or raw bytes.
        fit: Scale the image to the widget's bounds. When ``False`` the image
            is drawn at its own size, anchored at the top-left and clipped by
            the widget.
        bounds: Where to draw. Usually assigned by a layout container.
    """

    __slots__ = ("_fit", "_source")

    def __init__(
        self,
        source: Any,
        *,
        fit: bool = True,
        bounds: Rect | None = None,
        visible: bool = True,
        name: str | None = None,
    ) -> None:
        super().__init__(bounds, visible=visible, name=name)
        self._source = source
        self._fit = fit

    @property
    def source(self) -> Any:
        """The image being drawn."""
        return self._source

    @source.setter
    def source(self, value: Any) -> None:
        self._source = value
        self.mark_dirty()

    def render(self, canvas: Canvas) -> None:
        """Draw the image, scaled to the bounds when ``fit`` is set."""
        area = self.bounds
        if area.is_empty:
            return
        if self._fit:
            canvas.image(area.x, area.y, self._source, size=area.size)
        else:
            canvas.image(area.x, area.y, self._source)
