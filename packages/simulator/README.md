# tinydisplay-simulator

**Not yet implemented — planned for [Phase 2](../../docs/roadmap.md).**

A `DisplayDriver` that renders to a desktop window instead of hardware, so
dashboards can be developed with nothing plugged in.

Planned scope:

- A windowed driver honouring the same `DisplayDriver` contract as real panels.
- An RGB565 quantisation preview, so the desktop shows what the panel will
  actually display rather than a flattering 24-bit version — see
  `Color.quantized_rgb565()` in core.
- Hot-reload of a dashboard definition from disk.

It ships before the HT32 driver on purpose: when the simulator and the panel
disagree, the driver is at fault.

This directory holds no code yet, and is excluded from the uv workspace until
it does.
