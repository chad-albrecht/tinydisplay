# tinydisplay-ht32

A `DisplayDriver` for the [HT32 panel][ht32]: 320x170, RGB565, over USB HID.

| Property | Value |
| --- | --- |
| Resolution | 320 x 170 |
| Colour format | RGB565, **big-endian** on the wire |
| Transport | USB HID — Linux `hidraw`, or hidapi |
| Hardware ID | VID:PID `04D9:FD01` |
| Frame | 108,800 bytes in 27 packets of 4,105 |
| LED control | CH340 serial bridge, 10000 baud |

## Install

```bash
pip install tinydisplay-ht32          # enough to drive the panel on Linux
pip install tinydisplay-ht32[led]     # adds the CH340 LED bridge
pip install tinydisplay-ht32[hid]     # adds hidapi, for Windows and macOS
```

**On Linux you need no extras.** `/dev/hidrawN` takes an ordinary `write()`
whose first byte is the HID report ID, which is exactly what our packets carry,
so the default transport is a file descriptor and the standard library. That
matters because the machines this panel is built into are the worst places to
install a compiled USB library: Home Assistant OS has no compiler, and PyPI
publishes no `musllinux` wheel for hidapi.

`hidapi` is for the platforms without hidraw. `create_panel_transport()` picks
between them, preferring hidraw when a matching node is visible.

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
| `hidraw` | Linux `/dev/hidrawN`: sysfs discovery and writes, no dependencies. |
| `device` | Finding panels through hidapi, for platforms without hidraw. |
| `transport` | Writing packets: `HidTransport` for hidapi, `RecordingHidTransport` for memory. |
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

### Bring-up

A panel gives almost no feedback: a write the OS accepts says nothing about
whether the packet was understood. The command line exists for that, and its
subcommands escalate:

```bash
tinydisplay-ht32 probe --open              # what does the bus say?
tinydisplay-ht32 frame --pattern bars      # what does the glass say?
tinydisplay-ht32 led --theme rainbow
```

Every subcommand takes `--dry-run`, which swaps in a recorder and reports what
would have been written.

The patterns are chosen so failures look specific rather than merely wrong:

| Pattern | Catches | How it fails |
| --- | --- | --- |
| `bars` | Byte order | The bar labelled `red` is not red. |
| `gradient` | Row stride | The ramp shears diagonally. |
| `chunks` | Framing | A band sits in the wrong place — and its index is the packet number. |
| `black` | — | Blanks the panel when you are done. |

For a machine with nothing installed and no way to install anything —
a Home Assistant OS box, for instance —
[`tools/ht32_standalone_probe.py`](../../tools/ht32_standalone_probe.py) is a
single standard-library file that does the same job. It duplicates the framing
deliberately, and `tests/ht32/test_standalone_probe.py` asserts byte-for-byte
that the copy still agrees with this package. See also
[`deploy/hassio-addon/`](../../deploy/hassio-addon/).

### Verified against hardware

Confirmed on an AceMagic S1 running Home Assistant OS: colour bars rendered in
the correct order, with red rendering as red. That single image validates the
byte order, the 27-chunk framing and the header layout at once.

Bring-up corrected three things that no document got right:

- **`hidraw` does not work at all.** See below — this is the big one.
- **The display is not interface 1.** Upstream hard-codes it; the S1 publishes
  interfaces 0 and 2 and no interface 1. The transport now chooses by
  capability, picking whichever interface publishes an OUT endpoint.
- **Bytes 4-7 are ignored by the firmware.** The offset field this package
  once carried a warning about never mattered.

Run the integration tests with a panel attached:

```bash
uv run pytest -m hardware -v
```

They are deselected in CI, and skip with a reason when nothing is plugged in.

### Why raw USB and not hidraw

The panel's HID interface declares **64-byte output reports**. `hidraw` applies
HID report semantics, so a 4,104-byte frame chunk cannot travel that path
however it is framed: the kernel accepts every write, and the device acts on
none of them. That failure is completely silent — writes succeed, nothing
draws — which is what made it expensive to find.

Both independent implementations of this protocol reach the same conclusion.
`node-hid` is explicitly configured with `setDriverType('libusb')`, and
`s1display` links libusb directly. libusb detaches the kernel driver and writes
to the interface's endpoint, where the host controller splits the transfer into
64-byte USB packets by itself.

`UsbfsTransport` does the same thing without libusb, because libusb is a
wrapper over `usbfs` and usbfs is a device node plus a few ioctls. That keeps
the driver installable on the appliances this panel is built into, which have
no compiler and no package manager. `HidrawTransport` is kept for
experimentation and is not known to drive this panel.

### The heartbeat is not optional

The firmware expects a keep-alive roughly once a second. Without it, it paints
*"Disconnection, content information display will not be allowed!"* over
whatever is on screen — so a dashboard that draws once and stops sees its frame
defaced a moment later, which looks like a rendering bug and is not one.

`run_panel()` handles it. The packet is `55 A1 F2 hh mm ss`, matching the
working reference implementation byte for byte, and the first one is sent
**before** the first frame rather than an interval later: the panel starts
every session already showing the banner, so the first keep-alive is what
clears it, not what maintains it.

**Partly open.** The banner has been seen on hardware during a run that was
sending keep-alives on a timer, with the first one scheduled an interval in
rather than sent immediately. Sending it up front is the fix for the startup
gap and for a watchdog that looks to be about a second long, but it has not yet
been confirmed on hardware that the banner stays away for a long run. If it
reappears, the knobs to try are a shorter interval and
`build_config_packet(0xF3, ...)` — documentation for this panel mentions
`0xA1 0xF2/0xF3` together without saying what the second one is for.

Verify either way with no install:

```bash
python3 tools/ht32_standalone_probe.py --loop 30                 # expect no banner
python3 tools/ht32_standalone_probe.py --loop 30 --no-heartbeat  # expect the banner
```

### Not implemented

- **Brightness.** Upstream exposes no brightness or backlight command, and
  none is documented, so this package does not invent one.

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
