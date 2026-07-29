"""Pytest configuration shared by the unit tests and the module doctests.

Docstring examples are executed as part of the suite (``--doctest-modules``),
which keeps the documentation honest. To stop every example from opening with
the same block of imports, the core public API is injected into the doctest
namespace here.
"""

from __future__ import annotations

import pytest

from tinydisplay.core import (
    Canvas,
    Color,
    Container,
    Font,
    HorizontalAlign,
    MemoryDriver,
    PixelFormat,
    Point,
    Rect,
    Size,
    VerticalAlign,
    Widget,
)


@pytest.fixture(autouse=True)
def _inject_doctest_namespace(doctest_namespace: dict[str, object]) -> None:
    """Make the core public API available to every doctest without imports."""
    doctest_namespace.update(
        {
            "Canvas": Canvas,
            "Color": Color,
            "Container": Container,
            "Font": Font,
            "HorizontalAlign": HorizontalAlign,
            "MemoryDriver": MemoryDriver,
            "PixelFormat": PixelFormat,
            "Point": Point,
            "Rect": Rect,
            "Size": Size,
            "VerticalAlign": VerticalAlign,
            "Widget": Widget,
        }
    )
