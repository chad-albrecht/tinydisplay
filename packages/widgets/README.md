# tinydisplay-widgets

**Not yet implemented — planned for [Phase 4](../../docs/roadmap.md).**

The built-in widget library: the vocabulary a dashboard is written in.

Planned scope:

- **Layout** — stack, grid, padding, alignment.
- **Content** — label, icon, gauge, sparkline, progress bar, image.
- **Theming** — a palette resolved against the target panel's colour depth.

Everything here builds on `Widget` and `Container` from
[`tinydisplay-core`](../core). `examples/dashboard.py` in the repository root
shows the kind of widget this package will provide as a first-class component.

This directory holds no code yet, and is excluded from the uv workspace until
it does.
