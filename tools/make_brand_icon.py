#!/usr/bin/env python3
"""Render the Home Assistant brand icon, using TinyDisplay's own engine.

Home Assistant 2026.3 and later look for brand images inside the integration
before falling back to the brands CDN, so this writes them straight into
``custom_components/tinydisplay/brand/``. No manifest change is needed.

Drawing the icon with :class:`~tinydisplay.core.Canvas` rather than shipping
an asset from a design tool is not a stunt. It means the icon is reproducible
from source, it uses the same palette the dashboards do, and if the rendering
engine ever breaks in a way the tests miss, the icon stops looking right too.

Run it with::

    uv run python tools/make_brand_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from tinydisplay.core import Canvas, Color
from tinydisplay.widgets import MIDNIGHT

#: Rendered at 2x and downsampled, so the small icon gets real antialiasing
#: rather than whatever the drawing primitives manage at 256 pixels.
SIZE = 512

BRAND_DIR = Path(__file__).resolve().parents[1] / "custom_components" / "tinydisplay" / "brand"

#: Home Assistant renders these small. Two shapes -- a readout and a gauge --
#: are legible at 48 pixels; a faithful miniature dashboard is not.
_BEZEL_RADIUS = 88
_SCREEN_RADIUS = 40


def draw_icon(canvas: Canvas, *, theme_background: Color, outline: Color) -> None:
    """Draw a panel showing a readout above a segmented gauge."""
    canvas.clear(theme_background)
    scale = canvas.width / SIZE
    unit = lambda value: round(value * scale)  # noqa: E731 - a local shorthand

    theme = MIDNIGHT.quantized()

    # The panel body, inset so the rounded corners have room to breathe.
    body = (unit(28), unit(28), canvas.width - unit(56), canvas.height - unit(56))
    canvas.rounded_rect(*body, outline, radius=unit(_BEZEL_RADIUS))
    canvas.rounded_rect(
        body[0] + unit(18),
        body[1] + unit(18),
        body[2] - unit(36),
        body[3] - unit(36),
        theme.background,
        radius=unit(_SCREEN_RADIUS),
    )

    # The readout: one wide bar, the shape a temperature or a name takes.
    screen_left = body[0] + unit(60)
    screen_width = body[2] - unit(120)
    canvas.rounded_rect(
        screen_left,
        body[1] + unit(96),
        screen_width,
        unit(96),
        theme.accent,
        radius=unit(24),
    )

    # The gauge: four segments, the last one unlit, which is what makes it read
    # as a meter rather than a second bar.
    segments = 4
    gap = unit(20)
    segment_width = (screen_width - gap * (segments - 1)) // segments
    for index in range(segments):
        lit = index < segments - 1
        canvas.rounded_rect(
            screen_left + index * (segment_width + gap),
            body[1] + unit(236),
            segment_width,
            unit(72),
            theme.success if lit else theme.outline,
            radius=unit(16),
        )


def rounded_alpha(image: Image.Image, radius: int) -> Image.Image:
    """Return ``image`` as RGBA with its corners rounded off.

    The canvas is deliberately opaque -- panels are -- so transparency is added
    here rather than being something the engine has to carry for one caller.
    """
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width - 1, image.height - 1), radius, 255)
    result = image.convert("RGBA")
    result.putalpha(mask)
    return result


def render(*, dark: bool) -> Image.Image:
    """Render one icon at full size.

    Two variants, because Home Assistant shows the icon against both a light
    and a dark surface. The light-surface icon is a dark panel; the
    dark-surface one brightens the bezel so the silhouette does not disappear.
    """
    theme = MIDNIGHT.quantized()
    canvas = Canvas(SIZE, SIZE)
    draw_icon(
        canvas,
        theme_background=theme.surface if dark else theme.background,
        outline=theme.text if dark else theme.accent,
    )
    return rounded_alpha(canvas.to_pil(), radius=round(SIZE * 0.22))


def main() -> int:
    """Write every brand image. Returns a process exit status."""
    BRAND_DIR.mkdir(parents=True, exist_ok=True)

    for dark in (False, True):
        prefix = "dark_" if dark else ""
        full = render(dark=dark)
        full.save(BRAND_DIR / f"{prefix}icon@2x.png")
        full.resize((SIZE // 2, SIZE // 2), Image.LANCZOS).save(BRAND_DIR / f"{prefix}icon.png")

    for path in sorted(BRAND_DIR.glob("*.png")):
        with Image.open(path) as image:
            print(f"{path.name:20} {image.width}x{image.height} {image.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
