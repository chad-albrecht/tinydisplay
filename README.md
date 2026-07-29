# TinyDisplay

**Lovelace for tiny displays.** A framework for driving small hardware panels
from Home Assistant, with configurable dashboards that are not tied to any one
device.

> **Status: Phase 1, pre-alpha.** The rendering engine (`tinydisplay-core`) is
> implemented and tested. Hardware drivers, widgets, the simulator, and the
> Home Assistant integration are not yet written.

## Why

Small displays are everywhere — desk panels, shelf clocks, 3D-printer status
screens — and each one ships with its own bespoke, throwaway firmware. Home
Assistant already knows the state of your house. What is missing is a
*rendering* layer: something that turns entity state into pixels, and that does
not have to be rewritten for the next panel you buy.

TinyDisplay is that layer. The [HT32 panel][ht32] is the first supported
device, not the target.

[ht32]: https://ananthb.github.io/ht32-panel/index.html

## Architecture

Five packages, layered so that each depends only on the ones beneath it:

```text
   Home Assistant integration        entity state -> dashboards
                |
                v
            Widgets                  gauges, readouts, charts
                |
                v
             Core                    Widget -> Canvas -> DisplayDriver
             /    \
            v      v
   Hardware drivers   Simulator      HT32, ...        desktop preview
```

The rule that makes this work: **nothing in `core` imports a hardware library,
and nothing in `core` knows Home Assistant exists.** A widget can be rendered
and pixel-asserted with no device attached, and a driver can be exercised with
a canvas it never drew.

| Package | Status | Purpose |
| --- | --- | --- |
| [`packages/core`](packages/core) | **Implemented** | Rendering engine: canvas, widgets, driver abstraction |
| [`packages/ht32`](packages/ht32) | Planned | HT32 panel driver (320x170, RGB565, USB HID) |
| [`packages/widgets`](packages/widgets) | Planned | Built-in widget library |
| [`packages/simulator`](packages/simulator) | Planned | Desktop preview, no hardware needed |
| [`packages/homeassistant`](packages/homeassistant) | Planned | Home Assistant custom integration |

See [docs/architecture.md](docs/architecture.md) for the reasoning behind the
layering, and [docs/roadmap.md](docs/roadmap.md) for what lands when.

## Quick start

```bash
git clone https://github.com/chad-albrecht/tinydisplay
cd tinydisplay
uv sync
uv run python examples/hello_world.py
```

Without `uv`:

```bash
python -m venv .venv && . .venv/bin/activate   # .venv\Scripts\activate on Windows
pip install -e packages/core
pip install pytest pytest-asyncio pytest-cov mypy ruff
python examples/hello_world.py
```

Then the API itself:

```python
from tinydisplay.core import Canvas, Color

canvas = Canvas(240, 240)
canvas.clear(Color.BLACK)
canvas.text(x=10, y=10, text="Hello", color=Color.WHITE)
canvas.save("preview.png")
```

[`examples/dashboard.py`](examples/dashboard.py) is a fuller walkthrough: custom
widgets, a container, and a frame pushed through a driver at HT32 resolution.

## Development

Requires **Python 3.12+**.

```bash
uv sync                 # install the workspace and dev tooling
uv run pytest           # tests, including doctests
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy             # type check (strict)
uv run pre-commit install
```

Docstring examples run as part of the suite via `--doctest-modules`, so the
documentation cannot quietly drift from the code.

Standards this repo holds itself to:

- Type hints everywhere; `mypy --strict` passes with no ignores.
- Async only where there is real I/O to await — drivers, not rendering.
- Every public method carries a docstring explaining *why*, not just *what*.

## Design notes

A few decisions worth knowing before reading the code:

- **NumPy owns the framebuffer; Pillow only rasterises.** Glyphs, ellipses and
  image decoding go through Pillow, but the `uint8[H, W, 3]` array is always
  the source of truth, and it is what drivers read.
- **RGB565 lives in core, not in a driver.** Most small panels are 16-bit, so
  `Canvas.to_rgb565()` and `Color.quantized_rgb565()` are part of the engine.
  Endianness is explicit because controllers disagree.
- **Drawing clips; reading raises.** Out-of-bounds draws are silently trimmed
  so widgets need no bounds-checking, but `get_pixel` raises `IndexError`,
  because a silent sentinel there would mask test bugs.
- **Widgets draw in canvas coordinates.** `Widget.draw()` clips to the
  widget's bounds before calling `render()`, so a miscalculating widget cannot
  corrupt its neighbours — and there is no translating-canvas wrapper in the
  hot path.

## Licence

[MIT](LICENSE).
