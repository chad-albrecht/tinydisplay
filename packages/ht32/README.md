# tinydisplay-ht32

**Not yet implemented — planned for [Phase 3](../../docs/roadmap.md).**

A `DisplayDriver` for the [HT32 panel][ht32].

Known device characteristics, from the upstream documentation:

| Property | Value |
| --- | --- |
| Resolution | 320 x 170 |
| Colour format | RGB565 |
| Transport | USB HID |
| Hardware ID | VID:PID `04D9:FD01` |
| LED control | CH340 serial bridge, 10000 baud |

Upstream also ships `ht32paneld` (a D-Bus daemon with an HTMX web UI) and
`ht32panelctl` (a D-Bus client). Whether this package talks to the device
directly over HID or goes through that daemon is an open question to settle
before implementation.

This directory holds no code yet, and is excluded from the uv workspace until
it does.

[ht32]: https://ananthb.github.io/ht32-panel/index.html
