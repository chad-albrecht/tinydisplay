"""The smallest useful TinyDisplay program: draw text and save a preview.

Run it with::

    python examples/hello_world.py

It writes ``hello.png`` to the current directory. No hardware required -- the
rendering engine has no idea whether a display exists.
"""

from __future__ import annotations

from pathlib import Path

from tinydisplay.core import Canvas, Color, HorizontalAlign, VerticalAlign

OUTPUT = Path("hello.png")


def main() -> None:
    """Draw a greeting onto a 240x240 canvas and save it as a PNG."""
    canvas = Canvas(240, 240)
    canvas.clear(Color.BLACK)

    canvas.text(
        x=10,
        y=10,
        text="Hello",
        color=Color.WHITE,
    )

    # Anchoring is explicit, so centring needs no manual measurement.
    canvas.text(
        x=canvas.width // 2,
        y=canvas.height // 2,
        text="TinyDisplay",
        color=Color.CYAN,
        align=HorizontalAlign.CENTER,
        valign=VerticalAlign.MIDDLE,
    )

    canvas.save(OUTPUT)
    print(f"wrote {OUTPUT.resolve()}")


if __name__ == "__main__":
    main()
