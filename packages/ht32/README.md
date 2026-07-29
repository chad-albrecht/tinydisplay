# tinydisplay-ht32

A `DisplayDriver` for the [HT32 panel][ht32]: 320x170, RGB565, over USB HID.

| Property | Value |
| --- | --- |
| Resolution | 320 x 170 |
| Colour format | RGB565, **big-endian** on the wire |
| Transport | USB HID, interface 1 |
| Hardware ID | VID:PID `04D9:FD01` |
| Frame | 108,800 bytes in 27 packets of 4,105 |
| LED control | CH340 serial bridge, 10000 baud |

## Install

The USB and serial backends are optional extras, so the protocol layer installs
anywhere — including a CI runner with no USB stack:

```bash
pip install tinydisplay-ht32[all]     # panel and LEDs
pip install tinydisplay-ht32[hid]     # panel only
pip install tinydisplay-ht32          # packet building only, no I/O
```

## Use

```python
import asyncio
from tinydisplay.core import Color
from tinydisplay.ht32 import HT32Driver


async def main() -> None:
    async with HT32Driver() as panel:
        canvas = panel.create_canvas()
        canvas.clear(Color.BLACK)
        canvas.text(x=10, y=10, text="Hello", color=Color.WHITE)
        await panel.show(canvas)


asyncio.run(main())
```

The panel's size and pixel format are not arguments. The HT32 is 320x170
RGB565-BE and nothing else, and a driver that accepted 320x171 could only fail
later and less clearly.

With no hardware attached, swap the transport for a recorder and assert on the
bytes that would have gone out:

```python
from tinydisplay.ht32 import HT32Driver, RecordingHidTransport

transport = RecordingHidTransport()
panel = HT32Driver(transport=transport)
# ... show a frame ...
assert len(transport.packets) == 27
```

LEDs are a separate device on a separate bridge, so they are a separate object:

```python
from tinydisplay.ht32 import LedController, LedTheme

async with LedController() as leds:
    await leds.set_theme(LedTheme.BREATHING, intensity=4, speed=2)
```

## Layout

| Module | Responsibility |
| --- | --- |
| `protocol` | Constants and packet building. **No I/O**, so it is exhaustively testable. |
| `device` | Finding panels on the USB bus. |
| `transport` | Writing packets: `HidTransport` for real, `RecordingHidTransport` for memory. |
| `driver` | The `DisplayDriver`: geometry, frame assembly, reconnection. |
| `led` | The CH340 bridge. Deliberately not part of the driver. |

## Decision: talk to the device directly

Upstream also ships `ht32paneld` (a D-Bus daemon with an HTMX web UI) and
`ht32panelctl` (a D-Bus client). **This package does not use them.** It writes
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

## The wire protocol

Reconstructed from the [upstream Rust implementation][hw]; there is no
published specification. A packet is a fixed 4,105 bytes: one HID report-ID
byte, an eight-byte header, and 4,096 bytes of data that the last chunk leaves
partly zeroed.

```text
byte 0      HID report ID, always 0x00
byte 1      signature, always 0x55
byte 2      command      0xA1 config / 0xA2 refresh / 0xA3 redraw
byte 3      sub-command, or redraw phase  0xF0 start / 0xF1 continue / 0xF2 end
byte 4      sequence number, 1-based
byte 5      reserved, always 0x00
bytes 6-7   pixel offset into the frame, big-endian
byte 8      high byte of the chunk size
bytes 9+    pixel data, RGB565 big-endian
```

A full frame is 320 x 170 x 2 = 108,800 bytes, which is 26 chunks of 4,096 plus
a remainder of 2,304 — the 27 chunks and 2,304-byte tail that upstream
hard-codes. This package derives those numbers instead, so the arithmetic is
checked rather than trusted.

Two details look like bugs until you do the arithmetic:

- **The chunk size is effectively one byte.** Upstream writes it as a
  big-endian pair at bytes 8 and 9, then starts copying pixels at byte 9 — so
  the low byte is overwritten before it ever reaches the panel. It does not
  matter: both legal chunk sizes (4,096 and 2,304) are exact multiples of 256,
  so that byte is always zero. This package writes only byte 8, which produces
  a byte-identical packet without the dead store.
- **The offset is counted in pixels, not bytes.** The field is 16 bits. The
  frame is 108,800 bytes, which does not fit, but 54,400 pixels, which does. A
  byte offset would overflow partway through every frame.

### Unverified against hardware

Everything above is derived from source, not from a panel on a desk. The
framing is asserted byte-for-byte by the unit tests, but **no packet in this
package has yet been acknowledged by a real device.** The most likely place for
it to be wrong is the offset field, since that is the one field whose meaning
was inferred rather than read. It is written in exactly one place
(`protocol.build_redraw_packet`) so bring-up can change it once.

Run the integration tests with a panel attached:

```bash
uv run pytest -m hardware -v
```

They are deselected in CI, and skip with a reason when nothing is plugged in.

### Not implemented

- **Brightness.** Upstream exposes no brightness or backlight command, and
  none is documented, so this package does not invent one.
- **Orientation.** The `0xA1 / 0xF1` framing is known; the orientation codes
  are not. `build_config_packet` takes a raw sub-command so bring-up can probe
  them without patching the module.

## LED protocol

Five bytes at 10,000 baud, 8N1, over the CH340 bridge (`1A86:7523`):

```text
byte 0   signature, always 0xFA
byte 1   theme    0x01 rainbow / 0x02 breathing / 0x03 colors / 0x04 off / 0x05 auto
byte 2   intensity, inverted: 6 - level
byte 3   speed, inverted: 6 - level
byte 4   checksum, the low byte of the sum of bytes 0-3
```

Levels run 1 to 5 and are inverted on the wire. This package takes them the way
round a person would expect — 5 is brightest — and inverts when building the
packet. Bytes are paced 5 ms apart; the bridge drops them written back to back.

## Access on Linux

hidraw nodes are root-only by default, so an unplugged panel and an unreadable
one look identical. A udev rule fixes it:

```text
# /etc/udev/rules.d/70-tinydisplay-ht32.rules
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="04d9", ATTRS{idProduct}=="fd01", MODE="0660", TAG+="uaccess"
```

LED control additionally needs membership of the `dialout` group.

[ht32]: https://ananthb.github.io/ht32-panel/index.html
[hw]: https://github.com/ananthb/ht32-panel/tree/main/crates/ht32-panel-hw
