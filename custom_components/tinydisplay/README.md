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

> **Do step 1 before step 2.** HACS copies the integration; it does **not**
> install Python requirements — Home Assistant does that itself, from PyPI, and
> the packages this integration needs are not published there yet. Installing
> HACS-first is recoverable (do step 1, then restart) but it fails once with a
> confusing error on the way.

Commands below are for **Home Assistant OS or Container**, run from the
*Advanced SSH & Web Terminal* add-on with Protection Mode off. Substitute the
current release for `v0.1.0`.

### Step 1 — the Python packages

```bash
cd /config
wget -qO td.tar.gz https://github.com/chad-albrecht/tinydisplay/archive/refs/tags/v0.1.0.tar.gz
tar xzf td.tar.gz

docker exec -e PYTHONUSERBASE=/config/deps homeassistant pip install --user --no-deps \
  /config/tinydisplay-0.1.0/packages/core \
  /config/tinydisplay-0.1.0/packages/widgets \
  /config/tinydisplay-0.1.0/packages/ht32 \
  /config/tinydisplay-0.1.0/packages/homeassistant
```

**Use `PYTHONUSERBASE` and `--user`, not `--target`.** This is not a style
preference and getting it wrong wastes an evening. Home Assistant installs its
own requirements by setting `PYTHONUSERBASE` to `<config>/deps` and running
`pip install --user`, which lands packages in
`<config>/deps/lib/python3.X/site-packages` — and *that* directory, not
`<config>/deps` itself, is what it adds to `sys.path`. A `--target` install puts
everything one level too high, somewhere Home Assistant never reads.

The failure is thoroughly misleading when it happens: pip reports success, the
`.dist-info` directories are visibly present, the modules import perfectly if
you set `PYTHONPATH` by hand — and setup still fails with
`RequirementsNotFound`. Every check short of the one Home Assistant actually
performs will tell you it worked.

Installing from source rather than copying files is deliberate for a related
reason: pip builds each package and writes real `.dist-info` metadata, which is
what the requirement check reads. A bare file copy imports fine and still fails.

`--no-deps` skips numpy, Pillow and PyYAML, which Home Assistant already ships,
and stops pip hunting PyPI for `tinydisplay-core`, which is not there. Check
first if you are unsure:

```bash
docker exec homeassistant python3 -c "import numpy, PIL, yaml; print(numpy.__version__, PIL.__version__, yaml.__version__)"
```

Confirm the install the way Home Assistant will, with **no `PYTHONPATH`** — the
whole point is that it resolves without one:

```bash
docker exec -e PYTHONUSERBASE=/config/deps homeassistant python3 -c "
from importlib.metadata import version
for n in ('tinydisplay-core','tinydisplay-widgets','tinydisplay-ht32','tinydisplay-homeassistant'):
    print(' ', n, version(n))
"
```

Four versions means the requirement check will pass.

**Restart Home Assistant after this step, before installing the integration.**
Home Assistant works out the user-site path and adds it to `sys.path` once, at
startup. Packages installed while it is running are not picked up, and the
symptom is `No module named 'tinydisplay'` in the log with
`Config flow could not be loaded: Invalid handler specified` in the UI — which
looks nothing like a missing restart.

Then check the library itself loads and parses a dashboard:

```bash
docker exec -e PYTHONUSERBASE=/config/deps homeassistant python3 -c "
from tinydisplay.homeassistant import Dashboard
d = Dashboard.load('/config/tinydisplay-0.1.0/examples/ha_dashboard.yaml')
print('parsed OK:', d); print('entities:', sorted(d.entity_ids))
"
```

**Where this applies.** `<config>/deps` lives on the config volume, so it
survives a Core update — which a plain `pip install` into the container does
not. A Supervised or Core-in-a-venv install resolves imports from its
virtualenv instead and ignores `deps` entirely, so install into that. Check
with `python3 -c "import sys; print(sys.prefix != sys.base_prefix)"`.

### Step 2 — the integration, via HACS

1. **HACS → ⋮ → Custom repositories**
2. Repository `https://github.com/chad-albrecht/tinydisplay`, type
   **Integration** → **Add**
3. Find **TinyDisplay** in HACS → **Download**
4. Restart Home Assistant

If you previously copied `custom_components/tinydisplay/` by hand, delete it
first so HACS owns the directory.

### Step 3 — configure

**Settings → Devices & Services → Add Integration → TinyDisplay.**

Nothing needs preparing. The flow writes a starter dashboard to
`<config>/tinydisplay/dashboard.yaml` if you have none, preselects it, and
preselects the panel by checking whether an HT32 is actually on the USB bus —
so accepting the defaults and pressing **Submit** works.

The starter reads only `sun.sun`, which every Home Assistant has, so the panel
shows real moving data immediately rather than a screen of `--` placeholders.
Edit it, or point the integration at your own file, once you have seen it work.

The dashboard field lists only files that parse as a dashboard, so anything you
can pick will load. Type a path if yours lives elsewhere.

### Without HACS

Copy `custom_components/tinydisplay/` into your Home Assistant `config/`
directory and restart. Step 1 is still required.

### If setup says "Requirements for tinydisplay not found"

Step 1 did not take, and the overwhelmingly likely cause is a `--target`
install putting the packages where Home Assistant does not look. Ask where it
*does* look, then look there:

```bash
docker exec homeassistant sh -c 'PYTHONUSERBASE=/config/deps python3 -m site --user-site'
docker exec homeassistant ls "$(docker exec homeassistant sh -c 'PYTHONUSERBASE=/config/deps python3 -m site --user-site')" | grep -i tinydisplay
```

Four `tinydisplay_*-0.1.0.dist-info` directories should be listed *there*. If
they are sitting directly in `/config/deps` instead, remove them and redo
step 1:

```bash
docker exec homeassistant sh -c 'rm -rf /config/deps/tinydisplay /config/deps/tinydisplay_*.dist-info'
```

Do not diagnose this with `PYTHONPATH=/config/deps`. It makes the packages
importable for that one command and proves nothing about what Home Assistant
can see.

`tinydisplay-ht32` is pinned without its `[hid]` extra, deliberately — the
raw-USB transport needs nothing installed. The consequence is that the hidapi
fallback is unavailable, so a machine where usbfs is unreachable fails with a
clear message rather than silently taking a path that cannot drive this panel
anyway.

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

**Confirmed on hardware.** Home Assistant OS 18.1, Core 2026.7.4, an AceMagic
S1's built-in panel. Installed through HACS, configured through the config
flow, drawing a dashboard from live entity state onto the panel the right way
up, driven from inside the Core container.

That establishes the chain end to end: the config flow and its validation,
`async_setup_entry`, driver construction and connection, the `hass.states`
adapter, the entity subscriptions, the change-driven render loop, the
keep-alive, and the 180° rotation the panel's mounting requires.

**Not established.** Everything about the long run:

- Uptime beyond minutes. Whether the keep-alive holds the disconnection banner
  off for hours is still the open question it was in Phase 3.
- Reconnection after the panel is replugged, and the driver's retry path.
- The options flow, entry reload and entry unload. All unit-tested, none run
  against hardware.

**Rough edges.**

- The packages in `manifest.json` are not on PyPI, so they must be installed by
  hand — and in a specific way; see [Installation](#installation).
- No entities or device are published back to Home Assistant, so nothing in the
  UI tells you whether the panel is being drawn to. The logs are currently the
  only answer.
- No service to reload a dashboard without reloading the entry.
