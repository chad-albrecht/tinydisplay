# tinydisplay-widgets

The vocabulary a dashboard is written in: layout, labels, indicators, icons and
theming, on top of [`tinydisplay-core`](../core).

Everything here is an ordinary `Widget`, so these compose with hand-written
widgets and render identically on the simulator and on a panel.

## Use

```python
from tinydisplay.core import Canvas, Rect
from tinydisplay.widgets import MIDNIGHT, Axis, Label, Slot, Stack

theme = MIDNIGHT.quantized()

panel = Stack(
    Axis.VERTICAL,
    Rect(0, 0, 320, 170),
    slots=[
        Slot(Label("Kitchen", color=theme.text), size=30),
        Slot(Label("21.5 C", color=theme.accent)),
    ],
    spacing=4,
)

canvas = Canvas(320, 170)
canvas.clear(theme.background)
panel.draw(canvas)
```

[`examples/widget_dashboard.py`](../../examples/widget_dashboard.py) is a fuller
one: a header, a two-column body, tiles and a footer, with no arithmetic
positioning anything.

## What is here

| Widget | Purpose |
| --- | --- |
| `Stack` | A row or column, with fixed and weighted children. |
| `Grid` | Equal cells, with spans. |
| `Padding` | Insets a single child. |
| `Spacer` | Occupies space and draws nothing. |
| `Label` | Text, wrapped, optionally shrink-to-fit. |
| `ProgressBar` | A continuous fill. |
| `Gauge` | Discrete segments, with a warning threshold. |
| `Sparkline` | A series over time, auto-scaled. |
| `Icon` | One of a small set of drawn symbols. |
| `ImageWidget` | Anything an icon cannot express. |
| `Theme` | Colour named by role, resolved to the panel's depth. |

## Three ideas

### Layout assigns bounds

Core widgets carry an absolute rectangle and no layout logic, which is what
keeps them cheap. Layout containers divide the space they are given among their
children, so a dashboard describes structure instead of computing coordinates.

Sizing along a stack's axis is fixed or weighted: fixed children take what they
ask for, and the rest is divided in proportion. That covers a fixed header over
a filling body, or a row of equal columns, without a constraint solver.

Children fill their space *exactly*. Rounding is accumulated across the row
rather than applied per child, because on a 320-pixel panel a one-pixel gap per
child is a visible seam rather than a rounding detail.

### Colour is named by role, and resolved to the hardware

A theme has `danger`, not `red`. More importantly, `Theme.quantized()` maps the
palette through the target's colour depth:

```python
theme = MIDNIGHT.quantized()  # what a 16-bit panel will really show
assert theme.is_legible()  # contrast checked against those values
```

RGB565 has 32 levels of red and blue and 64 of green. Two colours chosen as
distinct can collapse onto one, and a contrast ratio checked against the
unrounded values is a ratio you do not have. The built-in themes are tested for
legibility *after* quantisation, which is the only version that reaches a user.

### Widgets clamp, constructors raise

A gauge handed 150% draws full. A sensor returning something surprising should
make the panel look odd for a frame, not stop a render loop.

A gauge built with zero segments raises immediately, because that is a bug in
the dashboard and would otherwise produce a silently wrong panel.

## Icons are drawn, not loaded

The icon set is small and every symbol is a shape the canvas primitives can
make — lines, rectangles, circles, and triangles and trapezoids filled by
scanline. Drawn icons scale to their box, recolour with the theme, and need no
asset pipeline.

| Group | Symbols |
| --- | --- |
| Shapes and marks | `circle` `dot` `square` `check` `cross` `warning` `info` `plus` `minus` `arrow-up` `arrow-down` |
| Home and entity domains | `home` `door` `lock` `unlock` `lightbulb` `person` `plug` |
| Sensors and weather | `thermometer` `droplet` `sun` `cloud` `wind` `fan` `flame` |
| Status and connectivity | `bolt` `battery` `power` `wifi` `signal` `clock` `bell` |

Anything requiring a genuine arc is absent rather than approximated badly: no
crescent moon, no gapped power ring, and a square-topped padlock shackle. For a
logo or a weather glyph, use `ImageWidget`.

## Not here

- **No intrinsic sizing.** Widgets do not report a preferred size, so a stack
  cannot size a child to its content. Slots are fixed or weighted. Adding
  measurement would mean a second layout pass and a `measure()` on every
  widget, and the panel-sized layouts this library is for have not needed it.
- **No scrolling or animation.** A status panel shows a state, and the render
  loop already redraws whenever that state changes.
