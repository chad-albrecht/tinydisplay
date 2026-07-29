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

## Phase 2 — Simulator ✅

**`tinydisplay-simulator`.** Complete. Before hardware, because it makes
hardware easier to debug: if the simulator and the panel disagree, the driver
is at fault.

- `SimulatorDriver`: a `DisplayDriver` rendering to a Tk window, with
  nearest-neighbour magnification so quantisation banding stays visible.
- The preview decodes the *encoded* frame rather than the source canvas, so a
  byte-order or packing bug is as visible on screen as it would be on the
  panel. RGB565 quantisation preview falls out of that by construction; the
  pixel format is the fidelity control, with `RGB888` giving the flattering
  24-bit view.
- `PreviewWindow` protocol with a `NullPreviewWindow` recorder, so the whole
  stack runs headless in CI.
- `DashboardLoader`: hot-reload of a `render(canvas)` module, polling
  `st_mtime_ns`. A failed reload keeps the last working dashboard and paints
  the error onto the panel.
- A render loop and a CLI: `python -m tinydisplay.simulator dashboard.py`.

## Phase 3 — HT32 driver ✅

**`tinydisplay-ht32`.** Complete and **verified on hardware** — an AceMagic S1
running Home Assistant OS, drawing colour bars in the correct order with red
rendering as red, which validates byte order, framing and header layout at
once. 320x170, RGB565, VID:PID `04D9:FD01`, with LED control over a CH340
serial link at 10000 baud.

- `protocol`: the wire format as pure functions, no I/O — 27 packets of 4,105
  bytes per frame, derived from the panel's geometry rather than hard-coded.
  This is the layer worth testing exhaustively, and the only one that can be.
- `HidTransport` and `RecordingHidTransport` behind one protocol, the same
  split the simulator uses for its preview window. The recorder is not a mock:
  the driver cannot tell them apart, so framing and reconnection both run in
  CI with nothing attached, asserting on the exact bytes.
- Device discovery by VID:PID, preferring the display interface and falling
  back to the first — macOS reports no interface numbers.
- `HT32Driver`: a whole frame is built before any of it is written, so a
  framing error cannot leave the panel awaiting an end phase that never comes.
  A failed write re-opens the panel and rewrites the frame from the start.
- LED control as a separate object, because the LEDs are a separate device: the
  strip failing is not a reason to stop drawing.
- Integration tests marked `hardware`, deselected in CI and skipped with a
  reason when no panel is attached.

**Brightness** is deliberately absent: upstream exposes no such command and
none is documented, so this package does not invent one.

Bring-up corrected three things no document got right, and the first is the
reason this phase took a while:

- **`hidraw` cannot drive this panel.** Its HID interface declares 64-byte
  output reports, so a 4,104-byte chunk cannot travel that path however it is
  framed — the kernel accepts every write and the device acts on none. The
  failure is silent, which is what made it expensive. Both independent
  implementations of this protocol use libusb for exactly this reason, and
  `UsbfsTransport` does the same thing with no library at all.
- **The display is not interface 1.** Upstream hard-codes it; the S1 has no
  interface 1. Transports now choose by capability instead.
- **The heartbeat is not optional.** Without one a second, the firmware paints
  its own disconnection banner over the frame.

See [the package README](../packages/ht32/README.md) for the reconstructed wire
protocol and what each source got wrong.

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
