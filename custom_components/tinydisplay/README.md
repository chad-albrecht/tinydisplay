# TinyDisplay — Home Assistant integration

Draws a Home Assistant dashboard onto a small hardware panel.

> **Pre-alpha.** Installation is now the ordinary HACS one — download, restart,
> add the integration — but one thing still stands between that and a working
> panel, and it may not be solvable from inside an integration at all. Read
> [Before you start](#before-you-start) first: it is a five-minute check that
> decides whether the rest is worth doing.

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

From the Advanced SSH & Web Terminal add-on, with Protection Mode off:

```bash
wget -O /config/ht32_usbfs_preflight.py \
  https://raw.githubusercontent.com/chad-albrecht/tinydisplay/main/tools/ht32_usbfs_preflight.py
docker exec homeassistant python3 /config/ht32_usbfs_preflight.py
```

The add-on and the Core container see the same `/config`, so the file only
needs fetching once. If the machine cannot reach `raw.githubusercontent.com`,
get the file there by any other route — the Samba share, the File editor
add-on, a paste into a heredoc — and run the second line as it stands.

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

**One step.** HACS copies the integration; Home Assistant installs the Python
packages itself, from this repository's own release tarball, because the
manifest asks for them by URL rather than by name. There is nothing to install
by hand and nothing to keep in sync: an update through HACS brings the
libraries that version needs, because the URLs point at the tag that shipped
it.

1. **HACS → ⋮ → Custom repositories**
2. Repository `https://github.com/chad-albrecht/tinydisplay`, type
   **Integration** → **Add**
3. Find **TinyDisplay** in HACS → **Download**
4. **Restart Home Assistant**

The first start after a download takes roughly ten seconds longer than usual,
while Home Assistant fetches the tarball and builds four small pure-Python
packages. Later starts cost nothing measurable — uv recognises the URLs it has
already installed and does no work.

**If you installed the packages by hand for an earlier release, delete them**
— they do not get replaced, they get *shadowed*, and the symptom is a dashboard
key being rejected as unknown while the release notes say it exists:

```bash
docker exec homeassistant sh -c 'rm -rf /config/deps/lib/python*/site-packages/tinydisplay /config/deps/lib/python*/site-packages/tinydisplay_*.dist-info'
```

Home Assistant installs into the container's own `site-packages`, the hand
install put them in `<config>/deps`, and Python reads the second one first. See
[If a key the document clearly supports is
rejected](#if-a-key-the-document-clearly-supports-is-rejected).

### Why the requirements are URLs

Worth knowing, because it is unusual and it explains the shape of the manifest.

None of these packages are on PyPI, so a requirement naming one sends Home
Assistant to an index that has never heard of it and setup fails with
`RequirementsNotFound`. A [PEP 508 direct
reference](https://peps.python.org/pep-0508/) needs no index — and no account,
no publishing step, and no second place for the version to drift out of.

Home Assistant handles direct references deliberately well:

```python
if req.url:
    # If requirement is a URL, we cannot verify versions, so let
    # the package manager handle it
    return False
```

Because it never assumes a URL requirement is already satisfied, a changed URL
is always installed — which is exactly the update behaviour publishing would
have bought, without the publishing.

Two consequences are load-bearing, and both are asserted in
`tests/homeassistant/test_component.py`:

**Every package is listed, including ones the integration never imports.**
Home Assistant installs requirements one at a time, each its own `uv` run, so
`tinydisplay-homeassistant` on its own would resolve `tinydisplay-widgets`
against PyPI and fail. The whole dependency closure has to come from URLs.

**They are listed in dependency order.** Each install may only rely on packages
an earlier line already put in place.

The tag in every URL matches the integration's own version, so the libraries
Home Assistant installs come from the same commit as the component asking for
them. That replaced pinning a version, and it is stricter: a pin can go stale
silently, while a mismatched tag cannot exist — the release workflow installs
the manifest's URLs from the published tag before anyone else does.

**What it costs: the HACS default store.** `script/hassfest/requirements.py`
rejects any requirement containing a space, with no allowlist and no exemption,
and a PEP 508 direct reference needs the ` @ ` separator. So the manifest
cannot pass hassfest — and hacs/default's own pull request CI runs hassfest
against the repository being submitted and fails on it. This is not a gap to be
closed later by tidying something up; the two designs are mutually exclusive.

Install as a custom repository, which is what the steps above do. Everything
else HACS validates does pass, and CI keeps it that way.

Publishing the four packages to PyPI would satisfy both — named `==`-pinned
requirements pass hassfest, and Home Assistant installs them itself — and it
would solve the original problem too, since a version the appliance lacks would
have somewhere to come from. It needs an account and a publishing step, which
is what the URL scheme was chosen to avoid. That is the way back if the store
ever matters more than it does now.

### Configure

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
directory and restart. The requirements install the same way; that part is
Home Assistant's doing, not HACS's.

### If setup says "Requirements for tinydisplay not found"

The install of one of the four packages failed, and the log line above it says
which and why. The likely causes, in order:

**No route to github.com.** The tarball is fetched at setup. An appliance on a
restricted network needs to reach `github.com` and `codeload.github.com`, and
`pypi.org` for the build backend and for numpy, Pillow and PyYAML.

**The tag does not exist.** Fetch the URL from the manifest by hand; a 404
means the release was cut without its workflow finishing, which is a bug worth
reporting.

```bash
docker exec homeassistant python3 -c "
import json, urllib.request
reqs = json.load(open('/config/custom_components/tinydisplay/manifest.json'))['requirements']
for r in reqs:
    url = r.split(' @ ', 1)[1].split('#', 1)[0]
    with urllib.request.urlopen(url) as response:
        print(response.status, url)
"
```

To see what actually landed, ask Home Assistant where it looks rather than
guessing — and with **no `PYTHONPATH`**, which makes packages importable for
one command and proves nothing about what Home Assistant can see:

```bash
docker exec -e PYTHONUSERBASE=/config/deps homeassistant python3 -c "
from importlib.metadata import version
for n in ('tinydisplay-core','tinydisplay-widgets','tinydisplay-ht32','tinydisplay-homeassistant'):
    print(' ', n, version(n))
"
```

Four versions means the requirement check will pass. `<config>/deps` lives on
the config volume, so what lands there survives a Core update. A Supervised or
Core-in-a-venv install resolves imports from its virtualenv instead and ignores
`deps` entirely; check with
`python3 -c "import sys; print(sys.prefix != sys.base_prefix)"`.

`tinydisplay-ht32` is required without its `[hid]` extra, deliberately — the
raw-USB transport needs nothing installed. The consequence is that the hidapi
fallback is unavailable, so a machine where usbfs is unreachable fails with a
clear message rather than silently taking a path that cannot drive this panel
anyway.

### If a key the document clearly supports is rejected

A dashboard erroring with something like

```text
rotate_every: unknown key; this node accepts background, pixel_format, root, theme, unavailable
```

is a **stale copy of the library shadowing the one Home Assistant installed**.
The key is not unknown; it is unknown to the version being imported.

This is a leftover of installing the packages by hand, which every release
before v0.2.1 required. Home Assistant installs its requirements into the
container's own `site-packages`, while the hand install put them in
`<config>/deps/lib/python3.X/site-packages` — and Python adds the user site to
`sys.path` *ahead* of the global one. So Home Assistant installs the new
version, satisfies its own requirement check against it, and then imports the
old one. `tinydisplay` is a namespace package, so the two directories merge
rather than one winning outright, which is why nothing looks obviously broken.

Find every copy:

```bash
docker exec homeassistant sh -c 'find / -name "tinydisplay_homeassistant-*.dist-info" -maxdepth 9 2>/dev/null'
```

Two paths and two versions is the diagnosis. Delete the one under `deps`:

```bash
docker exec homeassistant sh -c 'rm -rf /config/deps/lib/python*/site-packages/tinydisplay /config/deps/lib/python*/site-packages/tinydisplay_*.dist-info'
```

Restart, and confirm — with **no `PYTHONUSERBASE`**, which is the variable that
points at the stale copy and will keep reporting it:

```bash
docker exec homeassistant python3 -c "
import tinydisplay.homeassistant as m; print(m.__version__, m.__file__)"
```

The path should be the container's `site-packages`, not `deps`. That location
is inside the container and a Core update wipes it, which is correct and needs
no action: Home Assistant reinstalls from the manifest's URLs on the next
start.

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

### If the panel loses the host

**Reload the entry.** Settings → Devices & Services → TinyDisplay → ⋮ →
**Reload**. That is the recovery path, and it is the whole of it.

The render loop does not reconnect on its own, and that is deliberate rather
than missing. A dashboard error skips one frame and keeps the loop running,
because a template bug must not take the panel down. A *driver* error is
different: it means the panel itself is gone, so the loop stops and logs one
`TinyDisplay render loop stopped` with a traceback. A task that relaunched
itself would hide a dead panel behind a busy log, which is worse than a panel
that is visibly dead.

Verified by pulling the panel out from under a running instance
(2026-08-01, HA OS 18.1 / Core 2026.7.4):

| What happened | What the panel did |
| --- | --- |
| USB device de-authorised | Disconnection banner, within about a second |
| Device re-authorised | Banner stays — the device is back, nothing is driving it |
| Entry reloaded | Banner clears, rotation resumes |

Exactly one error was logged, not a flood, and the panel came back on the same
`/sys` path it left from.

The banner is therefore a reliable "nothing is driving me" indicator, not just
a startup nuisance: the firmware raises it within about a second of the
keep-alive stopping, whatever the reason. If you see it and it stays, the loop
has stopped and the log will say why.

Forcing a disconnect on Home Assistant OS is harder than it sounds, and three
routes do not work. The Terminal add-on has `/sys` mounted read-only;
`docker exec hassio_supervisor` is refused with `Permission denied`. What works
is a privileged throwaway container:

```bash
docker run --rm --privileged -v /sys:/sys:rw alpine \
  sh -c 'echo 0 > /sys/bus/usb/devices/1-8/authorized'   # 1 to restore
```

Find the path first rather than assuming `1-8`, which is this machine's:

```bash
for d in /sys/bus/usb/devices/*/; do
  [ "$(cat "$d/idVendor" 2>/dev/null)" = "04d9" ] &&
  [ "$(cat "$d/idProduct" 2>/dev/null)" = "fd01" ] &&
  echo "${d%/}"
done
```

`04d9` is Holtek, whose controllers are in a great many USB keyboards. The
product id is what makes this specific. If that loop prints more than one path,
do not guess — de-authorising the wrong device disconnects it.

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

**The keep-alive holds.** Over 24 hours continuous on that machine, with the
disconnection banner never appearing — which is what the once-a-second
heartbeat exists to prevent, and the question Phase 3 could not answer.

The evidence is that the panel was still *changing*, not merely still lit: the
dashboard rotates screens every ten seconds and was still doing it at the end.
That distinction is the whole check. A render loop that died leaves its last
frame on the glass, and a still frame of plausible numbers is indistinguishable
from a working panel — which is the same shape of mistake as the bring-up that
"verified" Phase 3 without noticing the picture was upside down.

**Losing the panel is handled, and reload brings it back.** The panel was
de-authorised out from under a running instance: the loop stopped with one
logged error, the firmware raised its banner, and reloading the entry restored
it. That also exercises entry unload and setup against hardware for the first
time, since a reload is both. See
[If the panel loses the host](#if-the-panel-loses-the-host).

**Not established.**

- The options flow itself. Changing an option reloads the entry, and the reload
  half is now proven, but nobody has submitted that form against a live panel.
- Whether any of this holds on a second machine. One appliance has run it, and
  that is the largest unknown by some distance.

**Rough edges.**

- No entities or device are published back to Home Assistant, so nothing in the
  UI tells you whether the panel is being drawn to. The logs are currently the
  only answer, and the disconnect test above is the argument for fixing it: the
  panel sat dead with the entry still reporting itself healthy, and the single
  line in the log was the only thing that knew. A binary sensor fed by
  `on_frame` would say it on the dashboard.
- No service to reload a dashboard without reloading the entry.
