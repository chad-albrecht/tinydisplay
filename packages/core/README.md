# tinydisplay-core

The hardware-agnostic rendering engine behind [TinyDisplay](../../README.md).

`tinydisplay-core` knows how to turn widgets into pixels. It does not know what
a display is made of, how it is wired, or that Home Assistant exists. Those
concerns live in sibling packages that depend on this one.

## The three layers

```text
Widget          paints itself into a rectangle
  |
  v
Canvas          an RGB framebuffer with drawing primitives
  |
  v
DisplayDriver   encodes the framebuffer and pushes it to hardware
```

Each layer depends only on the one below it. A widget can be rendered and
asserted on with no device attached; a driver can be exercised with a canvas
it never drew.

## Quick start

```python
from tinydisplay.core import Canvas, Color, HorizontalAlign

canvas = Canvas(240, 240)
canvas.clear(Color.BLACK)
canvas.text(
    x=10,
    y=10,
    text="Hello",
    color=Color.WHITE,
)
canvas.save("preview.png")
```

## Canvas

`Canvas` owns a contiguous `uint8[height, width, 3]` NumPy array. Canvas pixels
are always opaque; alpha lives on the *source* side, so any `Color` with
`a < 255` is composited onto what is already there.

| Operation | Method |
| --- | --- |
| Clear the surface | `clear(color=None)` |
| Single pixel | `pixel(x, y, color)` / `get_pixel(x, y)` |
| Rectangle | `rect(x, y, w, h, color, fill=True, thickness=1)` |
| Rounded rectangle | `rounded_rect(..., radius=4)` |
| Line | `line(x0, y0, x1, y1, color, thickness=1)` |
| Circle | `circle(cx, cy, radius, color, fill=True)` |
| Text | `text(x, y, text, color, font=..., align=..., valign=...)` |
| Image | `image(x, y, source, size=None, opacity=255)` |
| Canvas-to-canvas | `blit(source, x, y)` |
| Export | `save(path)`, `to_png_bytes()`, `to_pil()` |
| Wire formats | `to_rgb565(byte_order=...)`, `to_rgb888()` |

Drawing is clipped, never wrapped, and out-of-bounds coordinates are silently
trimmed. `get_pixel` is the one exception: it raises `IndexError`, because a
silent sentinel there would mask test bugs.

### Clipping

`clip()` is a context manager and clips nest, so a container can constrain its
children without any of them needing to cooperate:

```python
with canvas.clip(Rect(0, 0, 64, 64)) as visible:
    if not visible.is_empty:
        canvas.rect(0, 0, 240, 240, Color.RED)  # only the top-left 64x64 is painted
```

### 16-bit output

Most small panels are not 24-bit. `to_rgb565()` packs the framebuffer for them,
and `Color.quantized_rgb565()` shows what a colour will actually look like once
quantised — useful in tests and in the simulator:

```python
Color.from_hex("#88ff88").quantized_rgb565()  # what the panel really shows
```

Endianness differs between controllers, so `byte_order` is explicit rather than
guessed.

## Widgets

Subclass `Widget` and implement `render()`. The canvas arrives already clipped
to `self.bounds`; draw in canvas coordinates, offset from `self.bounds.x/y`.

```python
from tinydisplay.core import Canvas, Color, Rect, Widget


class Bar(Widget):
    def __init__(self, bounds: Rect, fraction: float) -> None:
        super().__init__(bounds)
        self.fraction = fraction

    def render(self, canvas: Canvas) -> None:
        b = self.bounds
        canvas.rect(b.x, b.y, b.width, b.height, Color.DARK_GRAY)
        canvas.rect(b.x, b.y, int(b.width * self.fraction), b.height, Color.GREEN)
```

`Container` composes widgets and paints them back-to-front. Widgets track a
dirty flag that propagates to ancestors, so a render loop can skip untouched
subtrees.

## Drivers

Implement three hooks — `_connect`, `_disconnect` and `_write` — and inherit
connection-state checking, frame validation and encoding:

```python
async with MemoryDriver(320, 170, pixel_format=PixelFormat.RGB565_LE) as driver:
    canvas = driver.create_canvas()
    canvas.clear(Color.BLUE)
    await driver.show(canvas)
```

Drivers are async because their work is I/O — USB HID writes, SPI transfers,
network round trips. Rendering stays synchronous: it is CPU-bound NumPy work
with nothing to await.

`MemoryDriver` is the reference implementation and retains frames in memory,
which makes the whole stack testable with no device attached.

## Dependencies

- **NumPy** — framebuffer storage and vectorised compositing.
- **Pillow** — glyph rasterisation, image decoding, PNG export.

Pillow is confined to rasterising; the NumPy buffer is always the source of
truth, and it is what drivers read.
