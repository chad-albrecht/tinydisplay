# TinyDisplay — Home Assistant integration

Draws a Home Assistant dashboard onto a small hardware panel.

> **Pre-alpha, and not yet installable by the normal route.** Two things stand
> between this and a working panel, and the second one may not be solvable from
> inside an integration at all. Read [Before you start](#before-you-start)
> first — it is a five-minute check that decides whether the rest is worth
> doing.

## What it does

You write a dashboard as YAML, point the integration at the file, and the panel
repaints whenever one of the entities the dashboard names changes. Nothing is
polled: the integration subscribes to exactly the entities the document reads,
and the render loop sleeps in between.

The dashboard format, the templating and the render loop are documented in
[`packages/homeassistant`](../../packages/homeassistant/README.md). This file
covers only installation and setup.

## Before you start

The bring-up in Phase 3 reached the panel from an **add-on** container, which
asked for `usb: true`, `udev: true` and `full_access: true` and needed
Protection Mode turned off. An integration does not run there. It runs inside
the **Home Assistant Core** container, which has a different device mapping and
no way to request more.

The driver writes to `/dev/bus/usb/<bus>/<device>`, so the question is whether
that path is reachable **and writable** from Core.

**Core can see the bus.** Confirmed on Home Assistant OS 18.1 / Core 2026.7.4:
`docker exec homeassistant ls -l /dev/bus/usb/*/*` lists the device nodes, so
the mapping exists. That settles the part that would have been fatal.

It does not settle everything. The nodes are `crw-rw-r--  root root`, so
*others* have read access only — listing them proves nothing about writing to
them, and raw USB needs write access plus the ability to detach the kernel's
`usbhid` driver from the interface. Confirm the rest with the preflight, which
is standard library only and needs nothing installed:

Get the file onto the machine — via the Samba share, the File editor add-on, or
a paste into a heredoc — so that it lands at `/config/ht32_usbfs_preflight.py`.
The Advanced SSH & Web Terminal add-on and the Core container see the same
`/config`, so one copy serves both. Then, with Protection Mode off:

```bash
docker exec homeassistant python3 /config/ht32_usbfs_preflight.py
```

Once this repository is published, `wget` from
`raw.githubusercontent.com/chad-albrecht/tinydisplay/main/tools/ht32_usbfs_preflight.py`
is the shorter route. It does not work against an unpushed checkout.

It finds the panel by vendor and product id exactly as the driver does, prints
the interfaces and their bound drivers, and opens the node read-write before
closing it again. It writes nothing to the panel, so it is safe to run on a
live system. Its constants are pinned to the driver by
`tests/ht32/test_usbfs_preflight.py`, so a result from it is a result about
`tinydisplay-ht32`.

| It says | What to do |
| --- | --- |
| `READY` | Continue to [Installation](#installation). |
| `NOT READY` | The panel is present but Core cannot write to it. |
| `NOT FOUND` | The panel is not visible from this container. |

For either failure, the honest answer is that the panel would need driving from
something that *can* hold USB permissions — an add-on that renders frames and
talks to Home Assistant over its API, rather than an integration that renders
in-process. Everything below `custom_components/` survives that move unchanged:
the renderer reads state through a `StateSource`, and an add-on on the
websocket API implements that protocol exactly as `HassStateSource` does over
`hass.states`.

The `memory` driver needs none of this and is a useful way to confirm the rest
of the integration works — the config flow, the entity subscriptions, the
render loop — while the USB question is still open. It renders real frames and
throws them away.

## Installation

### Dependencies come first

Home Assistant installs a `manifest.json` requirement with pip, from PyPI.
**These packages are not published yet**, so that step fails and the
integration will not load. Until they are, build and install them by hand.

On a machine with the repository checked out:

```bash
uv build --all-packages          # wheels land in dist/
```

Copy `dist/tinydisplay_homeassistant-*.whl`, `dist/tinydisplay_core-*.whl`,
`dist/tinydisplay_widgets-*.whl` and `dist/tinydisplay_ht32-*.whl` to the Home
Assistant machine, then from the SSH add-on:

```bash
docker exec homeassistant pip install --target /config/deps /path/to/*.whl
```

`/config/deps` is added to Home Assistant's import path at startup and lives on
the config volume, so it survives a Core update — which a plain `pip install`
into the container does not. That is the right target for Home Assistant OS and
Container installs; a Supervised or Core-in-a-venv install resolves imports
from its virtualenv instead, so install into that.

Note that `tinydisplay-ht32` is pinned without its `[hid]` extra. That is
deliberate — the raw-USB transport needs nothing installed — but it means the
hidapi fallback is unavailable, so a machine where usbfs is not reachable
fails with a clear message rather than silently taking a path that does not
drive this panel anyway.

### HACS

1. Add `https://github.com/chad-albrecht/tinydisplay` as a custom repository,
   category **Integration**.
2. Install **TinyDisplay**.
3. Restart Home Assistant.
4. **Settings → Devices & Services → Add Integration → TinyDisplay.**

### Manually

Copy `custom_components/tinydisplay/` into your Home Assistant `config/`
directory and restart.

Either route still needs the wheels installed first, as above. HACS copies the
component; it does not solve the requirement.

## Setup

| Field | Meaning |
| --- | --- |
| **Panel** | `ht32` for the real 320×170 panel; `memory` renders frames without hardware, which is useful for checking that a dashboard loads. |
| **Dashboard file** | Absolute path to a YAML dashboard, readable by Home Assistant. |
| **USB serial number** | Only needed when more than one identical panel is attached. |

The dashboard is validated during setup, so a mistake in it appears in the
dialog — with the offending key's path — rather than as a blank screen later.
Entities the dashboard names that do not exist yet produce a log warning, not
an error: writing a dashboard before the sensor it watches is perfectly
reasonable.

Options (**Configure**, after setup):

| Option | Default | Meaning |
| --- | --- | --- |
| Minimum seconds between repaints | 0.2 | A floor, so a burst of state changes becomes one repaint rather than a dozen. |
| Maximum seconds between repaints | 30 | A ceiling, so the panel refreshes even when nothing has changed. |
| Landscape orientation | on | Turn off to draw the short way round. |

Changing an option reloads the entry, which also re-reads the dashboard file —
that is currently the way to pick up an edit to the YAML.

## Hardware notes

The HT32 panel is driven over **raw USB**, not `hidraw`. Its HID interface
declares 64-byte output reports, so a 4,104-byte frame chunk cannot travel that
path however it is framed: the kernel accepts every write and the device acts
on none. Home Assistant OS therefore needs the container to reach the USB
device directly. See [`deploy/hassio-addon`](../../deploy/hassio-addon) for the
bring-up add-on and what permissions it needs.

The panel's firmware paints *"Disconnection, content information display will
not be allowed!"* over the screen when the host stops checking in, so the
integration sends a keep-alive about once a second — before the first frame,
because the banner is up from the start of every session.

The panel is opened during setup rather than by the render loop, so an
unreachable one fails the config entry with a reason on the card and Home
Assistant retries. Building the driver only *selects* a transport and touches
no hardware, so without that the entry would report itself set up while the
render task died quietly behind it.

## How it is put together

```text
custom_components/tinydisplay/     <- imports homeassistant; adapter code only
        |
        v
tinydisplay.homeassistant          <- dashboards, templating, render loop
        |
        v
tinydisplay.widgets / .core        <- layout and rendering
        |
        v
tinydisplay.ht32                   <- the panel
```

This directory is deliberately thin. Everything that can be decided without
Home Assistant running is decided one layer down, where the test suite reaches
it; what is left here is reading `hass.states`, subscribing to changes, and
owning a task. If a module in here starts making decisions, they belong below.

That boundary is enforced rather than trusted: `tests/homeassistant/test_component.py`
asserts that no package under `packages/` imports `homeassistant`, that this
directory does, and that the manifest's pinned requirements still match the
versions in the workspace.

## Status

Working and covered by tests:

- Dashboard parsing, validation and error reporting.
- Templating, entity binding, and the change-driven render loop.
- The manifest, translations and HACS metadata.

Blocking a real test, in order:

1. **Partly answered: the Core container can see `/dev/bus/usb`.** Confirmed on
   Home Assistant OS 18.1. Whether it can *write* to the node and detach
   `usbhid` is what the preflight in [Before you start](#before-you-start)
   establishes; bring-up used an add-on with privileges an integration cannot
   request, so none of it can be assumed.
2. **The packages in `manifest.json` are not on PyPI**, so neither HACS nor a
   manual copy can satisfy the requirement. Build the wheels and install them
   into `/config/deps` by hand until they are published.
3. **This integration has never run inside a live Home Assistant.** Everything
   below it has. The config flow, the setup path and the `hass.states` adapter
   are structurally checked — the manifest, the translations and the import
   boundaries are asserted by the test suite — but none of them have been
   executed, because doing so needs Home Assistant installed and this
   workspace deliberately does not depend on it.

Also absent, but not blocking: no entities are published back to Home
Assistant, and there is no service to reload a dashboard without reloading the
entry.
