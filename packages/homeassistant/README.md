# tinydisplay-homeassistant

Home Assistant dashboards for TinyDisplay: YAML definitions, entity binding,
and a render loop driven by state changes rather than by a clock.

This package is the **library**. The Home Assistant *integration* — the thing
you install — lives in [`custom_components/tinydisplay/`](../../custom_components/tinydisplay)
at the repository root, because that is where Home Assistant and HACS look for
it. See [the integration's README](../../custom_components/tinydisplay/README.md)
for installation.

The split is the point: nothing in this package imports `homeassistant`, so the
parser, the templating, the widget binding and the render loop are all covered
by the test suite with Home Assistant nowhere in sight. It is the same division
the HT32 driver makes between `protocol` and `transport`.

```python
from tinydisplay.core import Canvas
from tinydisplay.homeassistant import Dashboard, StaticStateSource

dashboard = Dashboard.load("dashboard.yaml")
states = StaticStateSource({"sensor.kitchen": "21.5"})

canvas = Canvas(320, 170)
dashboard.render(canvas, states)
```

Preview one against fake state, with no Home Assistant and no hardware:

```bash
uv run python -m tinydisplay.simulator examples/ha_simulator_dashboard.py
```

## The document

Lovelace-flavoured: every node has a `type`, containers have `children`, and a
child's sizing is written on the child.

```yaml
theme: midnight          # midnight | paper | high-contrast
background: background   # a theme role, or a hex value
unavailable: "--"        # what an unreadable value renders as

root:
  type: stack
  axis: vertical
  spacing: 6
  padding: 8
  children:
    - type: label
      size: 20
      text: "{{ sensor.kitchen.name }}"
      color: muted

    - type: label
      text: "{{ sensor.kitchen | round(1) }} C"
      color: accent
      align: center
      shrink_to_fit: true

    - type: gauge
      size: 14
      entity: sensor.processor_use
      max: 100
      color: success
      warning_at: 0.8
```

### Several screens

A dashboard can cycle through screens instead of showing one:

```yaml
theme: midnight
rotate_every: 10          # seconds; omit to hold the first screen

screens:
  - name: Living room
    root:
      type: label
      text: "{{ sensor.living_room_temperature | round(1) }}"

  - name: System
    root:
      type: gauge
      entity: sensor.processor_use
      max: 100
```

Use `root:` for one screen or `screens:` for several — never both. Every
dashboard written before screens existed keeps working unchanged, because a
bare `root:` simply *is* a dashboard of one screen.

`rotate_every` lives in the document rather than in the integration's options,
so the file describes the whole dashboard: copy it to another panel and the
rotation comes with it. Hot reload picks up a change within a few seconds, so
adjusting it is edit-and-save. The floor is half a second, and it is ignored
entirely when there is only one screen — rotating through one screen is a
repaint on a timer, which is what `max_interval` already does.

Two behaviours worth knowing:

**Every screen updates, only the current one draws.** A sparkline on a hidden
screen keeps collecting samples, so it comes back with a history that reflects
what actually happened rather than a gap for however long it was away.

**`entity_ids` is the union across screens.** The render loop subscribes once,
so a sensor appearing only on screen three still wakes it — otherwise that
screen would show whatever the sensor said the last time it happened to be up.

**Unknown keys are errors**, and errors carry a path:

```text
root.children[2].warning_at: must be at most 1, got 1.5
```

That is deliberate. The failure mode of a permissive parser is a panel that
renders perfectly while quietly ignoring the `color:` you spelled `colour:`.

### Nodes

| Type | Purpose | Key options |
| --- | --- | --- |
| `stack` | Row or column | `axis`, `spacing`, `children` |
| `grid` | Equal cells | `rows`, `columns`, `spacing`, `children` |
| `label` | Text | `text`, `color`, `align`, `valign`, `wrap`, `shrink_to_fit`, `font_size` |
| `gauge` | Segmented meter | `entity`, `min`, `max`, `segments`, `color`, `track_color`, `warning_at`, `warning_color` |
| `progress` | Continuous bar | `entity`, `min`, `max`, `color`, `track_color`, `radius`, `vertical` |
| `sparkline` | Value over time | `entity`, `color`, `fill_color`, `min`, `max`, `capacity` |
| `icon` | One drawn symbol | `icon`, `color`, `thickness` |
| `image` | A file | `path`, `fit` |
| `spacer` | Occupies space | — |

Every node also accepts `name`, `visible`, `padding`, and the layout hints its
parent reads: `size`, `weight`, `cross_align`, `cross_size` inside a `stack`;
`row`, `column`, `row_span`, `column_span` inside a `grid`.

The cross-axis hint is `cross_align`, not `align`, because a `label` already
spends `align` on its text. When both were spelled the same, one key was parsed
by two different enums and only the value they happened to share — `center` —
was accepted on a label at all; `align: left` was rejected with a message about
`start` and `stretch`. `cross_align` also reads better beside `cross_size`,
which is the axis it acts on.

`padding` is either a number or a mapping of `all` / `horizontal` / `vertical`
/ `left` / `top` / `right` / `bottom`.

### Values

The numeric widgets read a number from one of two places, and exactly one must
be given:

```yaml
entity: sensor.processor_use          # the entity's state
entity: light.desk                    # ...or one of its attributes
attribute: brightness

value: "{{ sensor.a | round(0) }}"    # ...or a template, parsed as a number
```

An unreadable value — a missing entity, an `unavailable` state, a string where
a number was wanted — draws as the minimum rather than raising. Widgets clamp;
constructors raise. A surprising sensor reading must not stop a render loop.

### Colour

Three forms, and the first is the one to prefer:

```yaml
color: accent        # a theme role -- follows a palette swap, checked for contrast
color: "#ff5d73"     # a literal
color:               # keyed on the state of the node's `entity`
  "on": danger
  "off": success
  default: muted
```

Theme roles are `background`, `surface`, `text`, `muted`, `accent`, `success`,
`warning`, `danger`, `outline`.

The palette is **quantised** to the panel's colour depth when the dashboard is
parsed, so the colours the widgets draw with are the colours the hardware
delivers — a contrast ratio measured on the unquantised value is not one you
actually have.

State-dependent colour is rejected where the widget only accepts a colour at
construction (`track_color`, `warning_color`, a sparkline's `color` and
`fill_color`). Accepting it there would silently freeze it at whatever it first
resolved to.

### Templates

Not Jinja. Using Jinja would mean this package could only run inside Home
Assistant, which would put the whole dashboard layer beyond the reach of the
test suite and the simulator.

```text
{{ sensor.kitchen }}                    the state string
{{ sensor.kitchen.name }}               an attribute
{{ sensor.kitchen | round(1) }}         a filter
{{ sensor.a | round(1) | default(--) }} several, left to right
```

Filters: `round(n)`, `int`, `float`, `abs`, `upper`, `lower`, `title`,
`capitalize`, `strip`, `default(value)`.

There is no control flow, no arithmetic and no way to call anything. That is a
feature rather than a limitation waiting to be lifted: a dashboard definition
is a document, and it is evaluated several times a second on an appliance.

`round(n)` formats to exactly `n` decimals rather than returning a number. A
readout that shows `21.5` and then `22` changes width as it changes value,
which on a small panel reads as a glitch.

**Parsing is strict; rendering is total.** An unknown filter or an unclosed
brace raises when the dashboard is loaded. A sensor that has dropped out
renders as the `unavailable` text and nothing else happens.

## The render loop

```python
await run_dashboard(driver, dashboard, source, changed=event)
```

`run_dashboard` sleeps on an `asyncio.Event` and repaints when it wakes, which
is what makes it change-driven. Two bounds keep that honest:

- `min_interval` (0.2 s) is a floor. A light group turning on emits a dozen
  state changes in a few milliseconds; they should become one frame.
- `max_interval` (30 s) is a ceiling, so something is drawn eventually even
  when the house is quiet.

`keepalive` is a **parameter, not a feature of the loop**. The HT32's firmware
paints a disconnection banner over the screen when the host stops checking in,
so its driver needs `heartbeat()` called about once a second. This package must
not import a driver — that would invert the dependency stack — so the caller
passes the coroutine and the loop schedules it against the same deadlines as
the frames. A panel needing no keep-alive passes nothing.

Frames and keep-alives share one loop and one writer, for the same reason the
HT32's own runner does: two coroutines writing multi-packet frames into the
same endpoint would interleave them and paint garbage.

## The seam

Everything above reads entity state through one method:

```python
class StateSource(Protocol):
    def get(self, entity_id: str) -> EntityState | None: ...
```

The integration implements it over `hass.states`; the tests and the simulator
use `StaticStateSource`, which is a dictionary. Neither the dashboard builder
nor the render loop can tell them apart — the same trick the HT32 driver plays
with `RecordingHidTransport` and the simulator plays with its preview window.

`None` means "Home Assistant has never heard of this entity", which is
different from an entity that exists and is currently unavailable. Dashboards
render both as unavailable; the distinction is what lets the config flow warn
about a typo.

## Design notes

**The widget tree is built once and updated, not rebuilt.** Layout containers
cache what they last laid out into, dirty tracking propagates from the widget
that changed, and a sparkline is only a sparkline because it remembers what it
was shown before. Building produces a flat tuple of *updaters* — closures that
each know one widget and one reference — and a dashboard with no templates and
no dynamic colours produces none at all.

**A sparkline samples on change, not on repaint.** The loop repaints for
reasons unrelated to any one entity, and sampling on repaint would let a
neighbouring widget stretch this one's history. Real time-series history
belongs to Home Assistant's recorder, which is a larger feature than this
widget.

**A dashboard is not tied to a panel size.** The root is resized to whatever
canvas it is handed, so the same definition previews in the simulator and draws
on hardware. Layout containers ignore a resize to the size they already have,
so this costs nothing on the frames where nothing moved.

## Deliberately absent

- **Home Assistant's own Jinja templates.** `StateSource` and the filter table
  are the seam where they would go; the reason they are not here yet is that
  the trade — a dashboard layer that cannot be tested or previewed outside Home
  Assistant — is not obviously worth it.
- **Recorder history.** A sparkline shows what it has seen since the panel
  started, not what the database knows.
- **Touch and buttons.** Nothing routes input back into the widget tree yet.
- **Entities exposed *by* the panel.** The integration draws; it publishes no
  sensors of its own.
