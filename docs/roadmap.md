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
running Home Assistant OS. 320x170, RGB565, VID:PID `04D9:FD01`, with LED
control over a CH340 serial link at 10000 baud.

What that verification established, precisely, matters — because for a while
this section claimed more than it had. The colour-bar image confirmed the byte
order (red renders as red), the 27-chunk framing and the header layout. It did
**not** confirm the image's orientation, and the claim that the bars appeared
"in the correct order" was wrong: the panel is mounted upside down, so they
were reversed and their labels were at the top, inverted. Vertical bars are
symmetric top to bottom, so the pattern's own check — "the bar labelled red is
red" — passes on an upside-down panel. Phase 5 found this and added a
`corners` pattern that cannot be fooled the same way.

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

Bring-up corrected several things no document got right, and the first is the
reason this phase took a while. One of the corrections was itself wrong and is
struck through below — left in place rather than deleted, because the way it
went wrong is the useful part:

- **`hidraw` cannot drive this panel.** Its HID interface declares 64-byte
  output reports, so a 4,104-byte chunk cannot travel that path however it is
  framed — the kernel accepts every write and the device acts on none. The
  failure is silent, which is what made it expensive. Both independent
  implementations of this protocol use libusb for exactly this reason, and
  `UsbfsTransport` does the same thing with no library at all.
- **~~The display is not interface 1.~~** This correction was itself wrong, and
  Phase 5 retracted it. The S1 *does* publish an interface 1, and it is the
  display: it carries the only bare OUT endpoint and has no kernel driver bound
  to it. The reason it looked absent is the item above — the original survey
  enumerated through `hidraw`, which only shows interfaces `usbhid` has claimed,
  and interface 1 is precisely the one it has not. So upstream's hard-coded
  number was right after all. Choosing by capability is still the better rule —
  it does not depend on the number being 1 — but it was arrived at from a false
  premise.
- **The heartbeat is not optional.** Without one a second, the firmware paints
  its own disconnection banner over the frame. Independently reproduced in
  Phase 5, where the `frame` subcommand's missing keep-alive made working
  hardware look broken.
- **The panel is mounted upside down.** Found in Phase 5. The frame has to be
  turned half a revolution before encoding; the panel's own orientation
  command does nothing at any value.

See [the package README](../packages/ht32/README.md) for the reconstructed wire
protocol and what each source got wrong.

## Phase 4 — Widget library ✅

**`tinydisplay-widgets`.** The vocabulary a dashboard is written in.

- Layout: `Stack` with fixed and weighted slots, `Grid` with spans, `Padding`,
  `Spacer`, cross-axis alignment. Containers assign bounds to their children,
  because core widgets deliberately carry geometry and no layout logic.
- Content: `Label` with wrapping and shrink-to-fit, `ProgressBar`, `Gauge`,
  `Sparkline`, `Icon`, `ImageWidget`.
- Theming: `Theme` names colour by role, and `Theme.quantized()` resolves the
  palette against the panel's colour depth.

Three decisions worth knowing:

**Children fill their space exactly.** Rounding is accumulated across a row
rather than applied per child. A one-pixel gap per child is a rounding detail
on a monitor and a visible seam on a 320-pixel panel.

**Themes are checked after quantisation.** RGB565 has 32 levels of red, so a
contrast ratio measured on the colours a designer picked is not the ratio the
hardware delivers. The built-in themes are tested for legibility as the panel
renders them.

**Widgets clamp, constructors raise.** A gauge fed 150% draws full, because a
surprising sensor reading must not stop a render loop. A gauge built with zero
segments raises, because that is a bug that would otherwise ship as a silently
wrong panel.

Deliberately absent: intrinsic sizing (no `measure()`, so slots are fixed or
weighted rather than content-sized) and scrolling or animation.

## Phase 5 — Home Assistant integration ✅

**`tinydisplay-homeassistant`**, plus the custom component in
`custom_components/tinydisplay/`. Last, deliberately: by this point the
rendering stack is proven, so integration bugs are unambiguously integration
bugs.

The phase is split in two, along the same line Phase 3 drew between `protocol`
and `transport` — **what can be tested without the thing attached, and what
cannot.**

The **library** is everything that does not need Home Assistant:

- `state`: `EntityState` and the one-method `StateSource` protocol. This is the
  seam. The integration implements it over `hass.states`; the tests and the
  simulator use a dictionary, and nothing above can tell them apart.
- `template`: placeholder substitution with a closed set of filters. Parsing is
  strict — an unknown filter raises when the dashboard loads — and rendering is
  total, because a sensor dropping out must not stop a panel.
- `schema`: YAML to a validated description, with unknown keys rejected and
  every error carrying its path in the document.
- `build`: the description to a widget tree, plus a flat tuple of updaters.
- `runner`: a render loop driven by an `asyncio.Event` rather than a clock.

The **component** is the part that cannot be tested here, and is kept small
because of it: adapt `hass.states`, subscribe, pick a driver, own a task.

Four decisions worth knowing:

**It is not Jinja.** Home Assistant's own templating would have been the
obvious choice and would have made the entire dashboard layer unrunnable
outside Home Assistant — untestable in CI, unpreviewable in the simulator.
`StateSource` is where a Jinja-backed resolver would plug in later; the small
filter table is what makes the trade unnecessary for now.

**Keep-alives are a parameter, not a feature of the loop.** The HT32 needs
`heartbeat()` about once a second, and the loop must not import a driver to
know that — that would invert the stack and mean a second panel required
changes up here. The caller passes the coroutine; the loop schedules it against
the same deadlines as the frames.

**The tree is built once and updated.** Rebuilding per frame would discard
cached layout, dirty tracking and sparkline history every time. Building
produces updaters, and a dashboard with no templates produces none.

**The layering is asserted, not trusted.** A test walks every module under
`packages/` and fails if any of them imports `homeassistant`, and checks that
the component's pinned requirements still match the workspace's versions.

Deliberately absent: recorder history (a sparkline shows what it has seen since
the panel started), touch and button input, and any entity published back to
Home Assistant.

**Verified on hardware.** Home Assistant OS 18.1, Core 2026.7.4, an AceMagic
S1's built-in panel: installed through HACS, configured through the config
flow, drawing live entity state onto the panel the right way up, from inside
the Core container.

Getting there had to re-establish a permission the driver had only ever been
granted elsewhere. Phase 3 reached the panel from an *add-on* container, which
asks for `usb`, `udev` and `full_access` and needs Protection Mode off; an
integration runs in the *Core* container and can request none of that. Core
turned out to both see and write `/dev/bus/usb`, which was the question that
would have sunk this design outright.
[`tools/ht32_usbfs_preflight.py`](../tools/ht32_usbfs_preflight.py) answers it
in one command: standard library only, so it runs inside Core where nothing is
installed, and pinned to the driver's own discovery by a test so its answer is
an answer about `tinydisplay-ht32`. Had write access been absent, the fix would
not have been configuration -- the panel would have had to be driven by a
process that can hold those privileges, talking to Home Assistant over its API.
Everything below `custom_components/` would have survived that unchanged, which
is the argument for having put the `StateSource` seam where it is.

Two things bring-up corrected, both of which had looked like integration bugs:

- **The panel is mounted upside down**, and its orientation command is inert.
  The driver turns each frame half a revolution before encoding. Phase 3's
  colour-bar check could not have noticed -- vertical bars are symmetric top to
  bottom -- which is why a `corners` pattern now exists.
- **Dependencies must be installed the way Home Assistant installs its own**,
  with `PYTHONUSERBASE` and `pip install --user`. A `--target` install lands
  one directory above where it looks, and reports success from every angle
  except the one that matters. This no longer applies to anyone installing the
  integration -- Home Assistant does it -- but it is why the manifest gets to
  be the only place the packages are named.

The hand install is gone. The manifest now requires each package by URL --
a direct reference to this repository's release tarball at the tag that shipped
the component -- so Home Assistant installs them itself, from no index, and a
HACS update brings the libraries the new version needs. Publishing to PyPI
would have bought the same thing and cost an account and a release step; the
detail that makes the URL route work is that Home Assistant never treats a URL
requirement as already satisfied, so a changed URL is always installed.

It got there the hard way: a HACS update shipped a component requiring library
0.3.0 while the appliance had 0.2.0 and nowhere to fetch the difference from,
and the panel stayed down until the packages were installed by hand again.

Uptime is settled: over 24 hours continuous, still rotating screens at the end
and with no disconnection banner, so the keep-alive does what Phase 3 could
only assume. Rotation is the part that matters as evidence — a dead render loop
leaves a last frame that looks exactly like a live one.

Losing the panel is settled too, by de-authorising it out from under a running
instance: the loop stops with one logged error, the firmware raises its banner
within a second, and reloading the entry restores everything. No automatic
reconnection, by design -- a task that relaunched itself would hide a dead
panel behind a busy log. The reload doubles as the first hardware exercise of
entry unload and setup.

Still open: the options flow against a live panel, and everything about a
second machine -- which is now the largest unknown here by some distance. See
[the integration's README](../custom_components/tinydisplay/README.md).

## Beyond

- Partial-region updates driven by the existing dirty tracking.
- Touch and button input routed back into the widget tree.
- Additional drivers, which is the whole point of the layering.
