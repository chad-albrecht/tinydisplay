# tinydisplay-simulator

A `DisplayDriver` that renders to a desktop window instead of hardware, so
dashboards can be developed with nothing plugged in.

```bash
uv run python -m tinydisplay.simulator examples/simulator_dashboard.py
```

Leave that window open and edit the file. Every save is picked up on the next
frame.

## What it is

`SimulatorDriver` is an ordinary driver. It subclasses the same
`DisplayDriver` as a real panel and inherits the same connection-state checks,
canvas-size validation and pixel-format encoding, so a dashboard written
against the simulator runs against hardware unchanged.

```python
import asyncio

from tinydisplay.core import Color
from tinydisplay.simulator import SimulatorDriver


async def main() -> None:
    async with SimulatorDriver(320, 170, scale=3) as driver:
        canvas = driver.create_canvas()
        canvas.clear(Color.from_hex("#101820"))
        canvas.text(10, 10, "Hello", Color.WHITE)
        await driver.show(canvas)
        await asyncio.sleep(5)


asyncio.run(main())
```

## Why it previews the encoded frame

The simulator does not display the canvas it was handed. It encodes that canvas
exactly as a real panel would, then decodes those bytes back for the window.

This is the design decision the rest of the package hangs off. Previewing the
canvas directly would show a perfect 24-bit image no matter what the driver did
to it — which would hide precisely the class of bug the simulator exists to
catch. Decoding the wire bytes means a byte-order mistake or a dropped channel
looks as wrong on your monitor as it would on the panel.

The RGB565 quantisation preview then costs nothing extra: decoding a 16-bit
frame *is* the quantised image, so there is no separate "pretend to be a cheap
panel" code path that could drift from the real one.

Fidelity is therefore controlled by the pixel format rather than by a flag:

| `pixel_format`                | What the window shows                       |
| ----------------------------- | ------------------------------------------- |
| `RGB565_LE` (default)         | What a 16-bit panel displays, banding and all |
| `RGB565_BE`                   | The same, big-endian on the wire            |
| `RGB888`                      | The flattering unquantised original         |

Magnification is nearest-neighbour, deliberately. A smooth filter would blur
away the banding you are meant to be able to see.

## Hot reload

A dashboard is an ordinary Python module exposing `render(canvas)`:

```python
from tinydisplay.core import Color


def render(canvas):
    canvas.clear(Color.BLACK)
    canvas.text(4, 4, "Hello", Color.WHITE)
```

Changes are detected by polling `st_mtime_ns` once a frame. That needs no
third-party watcher, cannot miss an event, and copes with editors that save by
rename — which is most of them.

A dashboard that fails to load, or raises while drawing, does not stop the
loop. The message is painted onto the panel and the last dashboard that worked
stays loaded, so a typo mid-edit shows up as a readable red screen and fixing
the file brings the dashboard straight back.

## Running headless

Everything above the window is display-free. `NullPreviewWindow` satisfies the
same protocol and records frames in memory, which is how this package's own
tests run in CI:

```python
from tinydisplay.simulator import NullPreviewWindow, SimulatorDriver

window = NullPreviewWindow()
async with SimulatorDriver(320, 170, window=window) as driver:
    await driver.show(canvas)

assert window.last_frame is not None
```

`driver.last_preview` gives the decoded frame *before* magnification, which is
usually what an assertion wants: it is what the panel would show.

## Tk

The window is Tk, which ships with CPython and adds no PyPI dependency. It is
imported lazily, so importing this package on a machine with no GUI toolkit
works fine and only opening a window fails — with a readable
`WindowUnavailableError` rather than a bare `TclError`.

Tk's event queue is pumped between frames rather than by handing control to
`mainloop()`. That keeps the whole simulator single-threaded, which matters
because Tk objects are bound to the thread that made them. The trade-off is
that the window repaints only as often as frames arrive; at the default 30 fps
this is not noticeable, but a dashboard rendering at 1 fps will feel sluggish
to drag.

## CLI

```
python -m tinydisplay.simulator DASHBOARD [options]

  --width N        Panel width (default: 320, the HT32)
  --height N       Panel height (default: 170)
  --scale N        Integer magnification (default: 3)
  --fps N          Target frame rate (default: 30)
  --format FMT     rgb565_le | rgb565_be | rgb888 (default: rgb565_le)
  --max-frames N   Stop after N frames instead of running until closed
  --verbose        Log every reload and dashboard error
```

It ships before the HT32 driver on purpose: when the simulator and the panel
disagree, the driver is at fault.
