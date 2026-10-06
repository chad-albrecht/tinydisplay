## Add portrait orientation for the HT32 / AceMagic S1

### Problem
- Turning off "Landscape orientation" did nothing on the AceMagic S1.
- The panel's orientation command does not rotate frames sent by the host.

### Changes
- `HT32Driver(portrait=True)`: reports 170x320 and rotates each frame 90° in software into the panel's 320x170 buffer.
- Works together with the existing `rotate_180`, so all four orientations are covered.
- The integration builds the driver from the options (Landscape off = portrait).
- New option: **Upside down**, for units standing the other way round.
- The orientation command (`0x02`) is still sent in portrait, because it rotates the firmware's own disconnection banner.
- Updated the protocol docs: the command rotates the banner, not the frames.
- Approach matches [ananthb/ht32-panel](https://github.com/ananthb/ht32-panel) (software rotation).

### Testing
- New driver tests check the rotation against Pillow's `rotate(±90)`.
- Full suite passes (1398 passed), ruff and mypy clean.
- Tested on a real AceMagic S1: dashboard upright in portrait, disconnection banner in portrait.
