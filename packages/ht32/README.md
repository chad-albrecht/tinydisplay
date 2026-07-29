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

## Decision: talk to the device directly

Upstream also ships `ht32paneld` (a D-Bus daemon with an HTMX web UI) and
`ht32panelctl` (a D-Bus client). **This package will not use them.** It writes
to the panel directly over USB HID.

Why:

- **Portability.** D-Bus is effectively Linux-only. Direct HID keeps the driver
  usable on Windows and macOS, which matters for the simulator-to-hardware
  workflow and for contributors who do not run Linux on their desktop.
- **Deployment.** A Home Assistant add-on or container cannot assume it can
  reach a host D-Bus daemon. Owning the transport removes that coupling.
- **Fewer moving parts.** No second process to install, version-match against,
  or debug when a frame does not appear.
- **Control.** Frame timing and, later, partial-region updates need direct
  access to the write path rather than a generic IPC boundary.

What this package therefore owns:

- Device discovery and HID enumeration by VID:PID.
- Chunking frames to the panel's HID report size.
- Reconnection handling — USB devices disappear and come back.
- LED control over the CH340 serial link.
- Access permissions, including udev rules on Linux.

## Status

This directory holds no code yet, and is excluded from the uv workspace until
it does.

[ht32]: https://ananthb.github.io/ht32-panel/index.html
