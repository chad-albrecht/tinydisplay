# Home Assistant OS bring-up add-on

A local add-on that runs `tinydisplay-ht32` against the panel built into the
machine Home Assistant is running on — an AceMagic S1 or similar.

It exists because **Home Assistant OS gives you nowhere else to run code.**
There is no host shell with `pip`, and the panel cannot be moved to a
development machine because it is part of the computer. An add-on container is
the sanctioned way to get code onto the box with USB access.

This is a diagnostic, not a service. It runs one command, prints the result to
the add-on log, and exits.

## Install

1. **Get file access to the box.** Install either the *Samba share* add-on or
   the *Advanced SSH & Web Terminal* add-on from the Add-on Store.

2. **Copy this directory to `/addons`** on the Home Assistant machine, so that
   `config.yaml` lands at `/addons/tinydisplay_ht32/config.yaml`.

   Over Samba, the `addons` share is visible from Windows Explorer at
   `\\<ha-host>\addons`. Copy the `tinydisplay_ht32` folder into it.

3. **Settings → Add-ons → Add-on Store → ⋮ → Check for updates.** The add-on
   appears under *Local add-ons*. Open it and click **Install**; the first
   build takes a few minutes.

4. **Turn Protection Mode OFF** on the add-on's Info tab. The add-on needs raw
   USB access, which Home Assistant will not grant a protected add-on. Turn it
   back on, or uninstall the add-on, once bring-up is done.

## Use

Set `command` on the **Configuration** tab, then **Start**, then read the
**Log** tab. The add-on stops on its own; that is success, not a crash.

| `command` | What it does |
| --- | --- |
| `probe` | Enumerates the USB bus and tries to open the panel. **Start here.** |
| `frame` | Draws `pattern` on the panel. |
| `led` | Sets the LED strip to `theme` at `intensity` and `speed`. |

### Reading a `probe` result

```text
hidapi:  available
panel:   2 interface(s)
  if0    b'/dev/hidraw0'
  if1    b'/dev/hidraw1' <- display
chosen:  interface 1
open:    ok
```

That is the good case. The interesting failures:

- **`panel: NOT FOUND`** — the panel is not on the bus, or its nodes are not
  visible inside the container. Check that Protection Mode is off. The log's
  `ls -l /dev/hidraw*` line at the top says whether the container can see any
  HID devices at all.
- **`open: FAILED`** — enumeration worked, opening did not. That is a
  permissions problem, not a missing device.

### Then draw something

Set `command: frame` and `pattern: bars`, start, and look at the panel.

The bars are labelled. **If the bar labelled `red` is not red, the RGB565 byte
order is wrong** — which is the single most likely defect in this driver, since
the protocol was reconstructed from source rather than from a specification.
Report what you see and it is a one-line fix.

Other patterns: `gradient` (a diagonal shear means the row stride is wrong),
`chunks` (bands that identify which of the 27 HID packets went astray),
`solid`, and `black` to blank the panel when you are finished.

## Why Debian, not Alpine

Home Assistant OS is Alpine-based, and Alpine is musl. PyPI publishes
`manylinux` wheels for `hidapi` but no `musllinux` ones, so an Alpine base
would mean compiling a USB library inside the add-on build. The Debian base
image gets a prebuilt wheel instead. See `build.yaml`.

## What this is not

It does not integrate the panel with Home Assistant — no entities, no
dashboards, nothing in the UI. That is Phase 5, and it should be built on a
driver that is known to work, which is what this add-on is for establishing.
