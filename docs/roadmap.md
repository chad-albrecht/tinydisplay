# Roadmap

Phases are ordered by dependency, not by excitement. Each one should leave the
repository releasable.

## Phase 1 — Rendering engine ✅

**`tinydisplay-core`.** Complete.

- `Color` with hex parsing, alpha compositing, WCAG contrast, RGB565 packing.
- `Point` / `Size` / `Rect` immutable geometry with half-open bounds.
- `Canvas`: pixels, lines, rectangles, rounded rectangles, circles, text,
  images, blitting, nested clipping, PNG export, RGB565/RGB888 output.
- `Font`: cached loading, metrics, multi-line measurement, alignment.
- `Widget` / `Container`: composition, clipping, dirty tracking.
- `DisplayDriver` abstract base class plus `MemoryDriver`.

## Phase 2 — Simulator

**`tinydisplay-simulator`.** Before hardware, because it makes hardware easier
to debug: if the simulator and the panel disagree, the driver is at fault.

- A `DisplayDriver` that renders to a desktop window.
- Optional RGB565 quantisation preview, so the desktop shows what the panel
  will actually display rather than a flattering 24-bit version.
- Hot-reload of a dashboard definition from disk.

## Phase 3 — HT32 driver

**`tinydisplay-ht32`.** The first real device: 320x170, RGB565, USB HID
(VID:PID `04D9:FD01`), with LED control over a CH340 serial link at 10000 baud.

- Device discovery and a HID transport.
- Frame chunking to the panel's report size.
- Brightness and LED control.
- Reconnection handling — USB devices disappear.
- Integration tests marked so CI skips them when no device is attached.

## Phase 4 — Widget library

**`tinydisplay-widgets`.** The vocabulary a dashboard is written in.

- Layout: stack, grid, padding, alignment.
- Content: label, icon, gauge, sparkline, progress bar, image.
- Theming: a palette resolved against the target panel's colour depth.

## Phase 5 — Home Assistant integration

**`tinydisplay-homeassistant`.** Last, deliberately: by this point the
rendering stack is proven, so integration bugs are unambiguously integration
bugs.

- A custom component with config-flow setup.
- Entity subscription and a change-driven render loop.
- YAML dashboard definitions, Lovelace-flavoured.
- HACS packaging.

## Beyond

- Partial-region updates driven by the existing dirty tracking.
- Touch and button input routed back into the widget tree.
- Additional drivers, which is the whole point of the layering.
