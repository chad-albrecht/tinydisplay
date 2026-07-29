"""Test patterns for hardware bring-up.

These exist because a panel gives almost no feedback. A write that the OS
accepts tells you nothing about whether the packet was understood, so the only
real instrument is the glass itself -- and that only helps if what is drawn
makes a specific failure look specific.

Each pattern is chosen to fail distinctively:

- :func:`draw_colour_bars` catches byte order. Pure primaries are the worst
  case for a swapped RGB565 pair, so red rendering as blue-grey means the
  endianness is wrong, not the wiring.
- :func:`draw_gradient` catches quantisation and stride. A smooth sweep with a
  wrong row stride shears visibly; banding that looks like steps rather than
  noise is normal RGB565.
- :func:`draw_chunk_marks` catches framing. One band per HID chunk, alternating
  shade, so a dropped or misordered packet appears as a band in the wrong place
  rather than as a vaguely wrong picture.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from tinydisplay.core import Color, Font, HorizontalAlign
from tinydisplay.ht32.protocol import CHUNK_COUNT, PIXELS_PER_CHUNK

if TYPE_CHECKING:
    from collections.abc import Callable

    from tinydisplay.core import Canvas

__all__ = [
    "PATTERNS",
    "draw_black",
    "draw_chunk_marks",
    "draw_colour_bars",
    "draw_gradient",
    "draw_pattern",
    "draw_solid",
]

#: Primaries first: these are the values a byte swap mangles most obviously.
_BARS: Final = (
    ("red", Color.from_hex("#ff0000")),
    ("green", Color.from_hex("#00ff00")),
    ("blue", Color.from_hex("#0000ff")),
    ("white", Color.from_hex("#ffffff")),
    ("yellow", Color.from_hex("#ffff00")),
    ("cyan", Color.from_hex("#00ffff")),
    ("magenta", Color.from_hex("#ff00ff")),
    ("black", Color.from_hex("#000000")),
)

_LABEL_HEIGHT: Final = 16


def draw_colour_bars(canvas: Canvas) -> None:
    """Vertical primary-colour bars, labelled.

    The check to make with your eyes: the bar labelled ``red`` must be red. On
    a panel fed byte-swapped RGB565, ``#ff0000`` decodes to a dark blue-grey,
    which is unmistakable next to a correct green.
    """
    canvas.clear(Color.BLACK)
    bar_width = canvas.width // len(_BARS)
    font = Font.default(10)

    for index, (name, colour) in enumerate(_BARS):
        left = index * bar_width
        width = canvas.width - left if index == len(_BARS) - 1 else bar_width
        canvas.rect(left, 0, width, canvas.height - _LABEL_HEIGHT, colour)
        canvas.text(
            left + width // 2,
            canvas.height - _LABEL_HEIGHT + 2,
            name,
            Color.WHITE,
            font=font,
            align=HorizontalAlign.CENTER,
        )


def draw_gradient(canvas: Canvas) -> None:
    """A horizontal sweep, plus a vertical one, to expose stride errors.

    A correct frame shows two smooth ramps. A wrong row stride turns the
    horizontal ramp into a diagonal shear, which is far easier to spot than a
    subtly wrong colour.
    """
    canvas.clear(Color.BLACK)
    split = canvas.height // 2
    left = Color.from_hex("#00b4d8")
    right = Color.from_hex("#f72585")

    for x in range(canvas.width):
        position = x / max(1, canvas.width - 1)
        canvas.rect(x, 0, 1, split, left.lerp(right, position))

    for y in range(split, canvas.height):
        position = (y - split) / max(1, canvas.height - split - 1)
        canvas.rect(0, y, canvas.width, 1, Color.BLACK.lerp(Color.WHITE, position))


def draw_chunk_marks(canvas: Canvas) -> None:
    """One alternating band per HID chunk, so framing errors localise.

    Each band is exactly the run of pixels carried by one packet. If band 14 is
    the wrong shade, packet 14 is the one to look at -- which turns "the image
    is wrong" into a chunk index.
    """
    canvas.clear(Color.BLACK)
    dark = Color.from_hex("#101820")
    light = Color.from_hex("#4895ef")
    font = Font.default(9)

    for index in range(CHUNK_COUNT):
        start = index * PIXELS_PER_CHUNK
        colour = light if index % 2 == 0 else dark
        _fill_pixel_run(canvas, start, PIXELS_PER_CHUNK, colour)

    canvas.text(
        canvas.width // 2,
        canvas.height // 2 - 4,
        f"{CHUNK_COUNT} chunks",
        Color.WHITE,
        font=font,
        align=HorizontalAlign.CENTER,
    )


def _fill_pixel_run(canvas: Canvas, start: int, length: int, colour: Color) -> None:
    """Fill ``length`` pixels from linear index ``start``, wrapping across rows.

    Chunks are runs in the *transmitted* order, which does not respect row
    boundaries -- a chunk of 2048 pixels covers 6.4 rows of a 320-wide panel.
    Drawing them as runs rather than rectangles is what makes the bands line up
    with the packets.
    """
    total = canvas.width * canvas.height
    end = min(start + length, total)
    position = start

    while position < end:
        y, x = divmod(position, canvas.width)
        run = min(canvas.width - x, end - position)
        canvas.rect(x, y, run, 1, colour)
        position += run


def draw_solid(canvas: Canvas, colour: Color = Color.WHITE) -> None:
    """A single flat colour -- the simplest thing that can be visibly right."""
    canvas.clear(colour)


def draw_black(canvas: Canvas) -> None:
    """Blank the panel.

    Worth having as a pattern rather than only as a cleanup step: it is the one
    frame whose correct result is indistinguishable from the panel ignoring us,
    which makes it the wrong thing to test with and the right thing to finish
    with.
    """
    draw_solid(canvas, Color.BLACK)


#: Patterns available by name, for the command line.
PATTERNS: Final[dict[str, Callable[[Canvas], None]]] = {
    "bars": draw_colour_bars,
    "gradient": draw_gradient,
    "chunks": draw_chunk_marks,
    "solid": draw_solid,
    "black": draw_black,
}


def draw_pattern(canvas: Canvas, name: str) -> None:
    """Draw the named pattern.

    Raises:
        KeyError: If ``name`` is not a known pattern.
    """
    PATTERNS[name](canvas)
