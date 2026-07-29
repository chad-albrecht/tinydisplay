# Architecture

TinyDisplay is a rendering framework that happens to have a Home Assistant
integration, not a Home Assistant integration that happens to draw. That
ordering drives every structural decision below.

## The dependency rule

Packages form a strict stack. A package may import from packages below it and
must never import from packages above it.

```text
   homeassistant        entity state -> dashboard config
        |
        v
     widgets            gauges, readouts, charts
        |
        v
      core              Widget -> Canvas -> DisplayDriver
      /    \
     v      v
   ht32   simulator     concrete DisplayDriver implementations
```

`core` sits at the bottom and imports nothing from the framework above it. The
practical test: **`tinydisplay-core` must be usable, and fully testable, by
someone who has never installed Home Assistant and owns no hardware.** If a
change to core would break that, the change belongs somewhere else.

Drivers (`ht32`, `simulator`) depend on core rather than the reverse. Core
defines the `DisplayDriver` abstract base class; concrete drivers implement it.
Core never enumerates the drivers that exist.

## The rendering pipeline

```text
Widget          paints itself into a rectangle
  |
  v
Canvas          an RGB framebuffer with drawing primitives
  |
  v
DisplayDriver   encodes the framebuffer and pushes it to hardware
```

Each stage is independently testable:

- A **widget** can be drawn onto a canvas and asserted pixel-by-pixel.
- A **canvas** can be exported to PNG and compared, with no widget involved.
- A **driver** can be handed any canvas; `MemoryDriver` captures frames in
  memory so the full stack runs in CI with nothing plugged in.

### Why the canvas owns a NumPy array

The canvas stores a contiguous `uint8[height, width, 3]` array. This is the
format drivers want (a flat, row-major buffer they can pack and ship) and the
format compositing wants (vectorised blending instead of per-pixel Python).

Pillow is used only as a rasteriser — glyphs, ellipses, rounded rectangles,
image decoding, PNG export. It never becomes the source of truth. Concretely,
`Canvas._draw_with_pillow` allocates a transparent RGBA layer the size of the
*clipped* region, lets Pillow draw into it, and composites the result back into
the NumPy buffer. The cost is proportional to what is actually painted, not to
the canvas size.

The alternative — making a Pillow `Image` the framebuffer — would have meant a
conversion on every frame for every driver, and no way to do cheap partial
updates later.

### Why alpha is source-side only

Canvas pixels are always opaque. A `Color` with `a < 255` is composited *onto*
the canvas; the canvas never carries an alpha channel of its own.

Panels are opaque. Storing a fourth channel would cost 33% more memory and a
conversion step on every frame, in exchange for a capability no display has.
Offscreen composition, where transparency genuinely helps, is served by drawing
into a separate `Canvas` and using `blit`.

### Why drawing clips but reading raises

Out-of-bounds *writes* are silently trimmed. Widgets compute coordinates from
layout arithmetic, and forcing every primitive to bounds-check its callers
would put a branch in every widget for no benefit.

Out-of-bounds *reads* (`get_pixel`) raise `IndexError`. A read is almost always
a test assertion or a debugging probe, and returning a sentinel colour there
would turn a bug into a passing test.

### Why widgets draw in canvas coordinates

`Widget.render()` receives the canvas in canvas coordinates and draws relative
to `self.bounds`. `Widget.draw()` — the method the render loop calls — clips to
those bounds first.

The obvious alternative is a translating canvas wrapper that gives each widget
a local origin. That is friendlier to write against, but it puts an object
allocation and an indirection in the hot path of every widget, every frame, and
it makes the "which coordinate space am I in?" question ambiguous at exactly
the moment you are debugging a layout. Clipping gives the safety (a widget
cannot overdraw its neighbours) without the cost.

## Colour and the 16-bit reality

Most small panels are not 24-bit. The HT32 is RGB565 over USB HID, and it is
representative rather than unusual.

So RGB565 is a core concern, not a driver detail:

- `Canvas.to_rgb565(byte_order=...)` packs a whole frame, vectorised.
- `Color.to_rgb565()` / `Color.from_rgb565()` convert single values, using bit
  replication rather than zero-fill so that white round-trips to white.
- `Color.quantized_rgb565()` answers "what will this colour *actually* look
  like on the panel?", which matters for both tests and the simulator.

Byte order is an explicit parameter with no default guess, because controllers
disagree and a wrong guess produces a plausible-looking but wrong image.

## Async, and where it stops

Drivers are async. Rendering is not.

Driver work is I/O: USB HID writes, SPI transfers, network round trips. A Home
Assistant integration driving several panels must not serialise on any one of
them, so `connect`, `disconnect` and `show` are coroutines.

Rendering is CPU-bound NumPy work with nothing to await. Making it async would
add ceremony and colour the whole widget API for no concurrency gain. If a
frame ever becomes slow enough to block the event loop, the fix is
`asyncio.to_thread` at the call site — NumPy releases the GIL — not an async
canvas.

`DisplayDriver` is a template-method base class: subclasses implement
`_connect`, `_disconnect` and `_write`, and inherit connection-state checking,
canvas-size validation and pixel-format encoding. This keeps the invariants
("you cannot `show` before `connect`") in one place instead of in every driver.

A deliberate detail: `disconnect()` marks the driver disconnected even when the
underlying close *fails*. A transport that errors on close must not leave the
object wedged in a state where it refuses both further writes and reconnection.

## Why the simulator previews the encoded frame

`SimulatorDriver` does not display the canvas it is handed. It encodes that
canvas exactly as a real panel would, then decodes those bytes back for the
window.

Previewing the canvas directly would be simpler and wrong. It would show a
perfect 24-bit image regardless of what the driver did to it, hiding precisely
the class of bug — swapped endianness, a dropped channel, a wrong stride — that
a simulator is supposed to catch before hardware arrives.

Decoding the wire bytes also means the RGB565 quantisation preview is not a
feature. It is a consequence: decoding a 16-bit frame *is* the quantised image.
There is no second "simulate a cheap panel" code path that could drift from the
real conversion, and the decoder is pinned to `Color.from_rgb565` by a test that
compares them pixel for pixel.

The corollary is that fidelity is controlled by `pixel_format` rather than by a
flag. `RGB565_LE` shows what the panel shows; `RGB888` shows the flattering
original. A boolean would have allowed the two to disagree.

Magnification is nearest-neighbour for the same reason: a smooth filter would
blur away the banding the operator is meant to see.

## Dirty tracking

Widgets carry a dirty flag that propagates to ancestors on change. Nothing in
Phase 1 consumes it — the current render loop repaints everything.

It exists now because retrofitting change-notification into a widget tree after
the fact means touching every widget. The propagation is cheap (a walk up the
parent chain that stops at the first already-dirty node), and it is what
partial-region updates will be built on once a driver supports them.

## Testing strategy

- **Pixel assertions over golden images.** `canvas.get_pixel(...)` and painted-
  pixel counts state the intent directly; a golden PNG only says "something
  changed" and breaks on every antialiasing tweak.
- **Exact blending arithmetic is asserted.** Drawing 50%-alpha white onto black
  must give exactly `#808080`, not approximately. Rounding drift is a real bug
  class in compositing code.
- **Doctests run in CI** via `--doctest-modules`, so every docstring example is
  executable and verified.
- **`MemoryDriver` is the reference driver**, not a mock. It implements the
  real contract, so it validates the base class as well as the tests using it.
