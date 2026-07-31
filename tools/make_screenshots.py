"""Render the example dashboards to PNGs for the README.

Run it from the repository root::

    uv run python tools/make_screenshots.py

These are renders, not photographs. That distinction matters and the README
says so: what is *not* faked is the pipeline. Each image is produced by the
same `Dashboard.render` the appliance calls, at the HT32's exact 320x170, then
put through the panel's RGB565 colour depth, so the banding and the colour
shift are the ones a camera would find. What a photograph would add is the
bezel, the viewing angle and the backlight -- none of which this project
controls.

Two details are worth knowing before trusting the output:

Sparklines are fed a warm-up run rather than a single frame. A sparkline
samples on update, so one render draws one point and a screenshot taken that
way would show an empty box next to a filled one -- flattering to neither and
untrue to both.

`age` reads the clock, so a fixed boot timestamp renders a different uptime
every time this script runs and every image is a diff. The boot time is
therefore derived backwards from *now*, which is the opposite of pinning it and
is what actually pins the pixels.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from PIL import Image

from tinydisplay.core import Canvas
from tinydisplay.homeassistant import Dashboard, StaticStateSource

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"
OUTPUT = REPO_ROOT / "docs" / "screenshots"

#: The HT32's panel, exactly.
PANEL = (320, 170)

#: Nearest-neighbour, so a pixel stays a pixel. GitHub scales an image to the
#: column width regardless; at 1x a browser on a hi-dpi screen resamples it to
#: mush, and the point of these is that the panel is 320 pixels wide.
SCALE = 2

#: Enough updates to fill a sparkline across the panel.
WARMUP_FRAMES = 90

#: How long the System screen should say the machine has been up. `age`
#: subtracts the boot time from the clock, so this is what gets rendered only
#: if the boot time is computed from the clock too -- see ``_states_at``.
UPTIME = timedelta(days=4, hours=6, minutes=18)


def _states_at(seconds: float) -> StaticStateSource:
    """Fake entity state for both example dashboards, moving with ``seconds``.

    Every id here is one an example names. They drift rather than hold still so
    that the warm-up produces a sparkline with a shape instead of a flat line.
    """
    source = StaticStateSource()

    # -- examples/ha_dashboard.yaml -----------------------------------------
    source.set(
        "sensor.living_room_temperature",
        f"{21.0 + 1.5 * math.sin(seconds / 4):.2f}",
        friendly_name="Living Room",
        unit_of_measurement="C",
    )
    source.set(
        "sensor.living_room_humidity",
        f"{48 + 6 * math.sin(seconds / 3):.1f}",
        friendly_name="Humidity",
    )
    source.set("sensor.phone_battery", f"{int(72 + 14 * math.sin(seconds / 7))}")

    # -- examples/ha_five_screens.yaml --------------------------------------
    source.set(
        "weather.forecast_home",
        "cloudy",
        temperature=f"{14 + 3 * math.sin(seconds / 9):.1f}",
        humidity=f"{61 + 5 * math.sin(seconds / 6):.0f}",
    )
    source.set("lock.front_door", "locked")
    source.set(
        "sensor.h5075_living_room_temperature",
        f"{21.4 + 0.9 * math.sin(seconds / 5):.1f}",
        unit_of_measurement="C",
    )
    source.set(
        "sensor.h5075_living_room_humidity",
        f"{47 + 5 * math.sin(seconds / 3):.1f}",
    )
    source.set("sensor.processor_temperature", f"{52 + 8 * math.sin(seconds / 4):.1f}")
    source.set("sensor.memory_use_percent", f"{63 + 7 * math.sin(seconds / 8):.1f}")
    source.set("sensor.last_boot", (datetime.now(UTC) - UPTIME).isoformat())
    source.set("sensor.speedtest_download", f"{452 + 40 * math.sin(seconds / 5):.1f}")
    source.set("sensor.speedtest_upload", f"{41 + 6 * math.sin(seconds / 6):.1f}")
    source.set("sensor.speedtest_ping", f"{8 + 3 * math.sin(seconds / 7):.1f}")

    # -- Shared by both ------------------------------------------------------
    source.set("sensor.processor_use", f"{50 + 30 * math.sin(seconds / 5):.0f}")
    # Held closed. A screenshot of a door alarming is a screenshot of an
    # unusual moment, and the colour mapping is the interesting part either way.
    source.set("binary_sensor.front_door", "off")

    return source


def _as_panel_sees_it(canvas: Canvas) -> Image.Image:
    """Quantise to RGB565 and back, which is what the panel actually shows.

    Done on the array rather than through ``to_rgb565`` because the bytes that
    go down the wire are not an image; the round trip is. Low bits are
    replicated from the high ones, which is what the controller does when it
    expands 5 and 6 bit channels back to 8.
    """
    pixels = canvas.buffer.copy()
    red = pixels[..., 0] & 0xF8
    green = pixels[..., 1] & 0xFC
    blue = pixels[..., 2] & 0xF8
    quantised = np.stack(
        [red | (red >> 5), green | (green >> 6), blue | (blue >> 5)],
        axis=-1,
    ).astype(np.uint8)
    image = Image.fromarray(quantised)
    return image.resize((image.width * SCALE, image.height * SCALE), Image.Resampling.NEAREST)


def _check_every_entity_is_faked(dashboard: Dashboard, source: StaticStateSource) -> None:
    """Refuse to render a dashboard naming an entity the fake state lacks.

    A missing entity does not raise -- it renders as the `unavailable` string,
    which is the behaviour the dashboards want and a trap for a screenshot: a
    screen of `--` looks like a broken panel, and the first version of this
    script shipped one because `lock.front_door` was read off a screen whose
    other ids all began `sensor.`. The dashboard already knows what it reads.
    """
    missing = sorted(entity for entity in dashboard.entity_ids if source.get(entity) is None)
    if missing:
        where = dashboard.source_path.name if dashboard.source_path else "dashboard"
        msg = f"{where} reads entities with no fake state: {', '.join(missing)}"
        raise SystemExit(msg)


def _render(dashboard: Dashboard, screen: int) -> Image.Image:
    """One screen, warmed up so anything with history has some."""
    _check_every_entity_is_faked(dashboard, _states_at(0))
    dashboard.show_screen(screen)
    canvas = Canvas(*PANEL, background=dashboard.background)
    for frame in range(WARMUP_FRAMES):
        dashboard.update(_states_at(frame))
    dashboard.draw(canvas)
    return _as_panel_sees_it(canvas)


def _slug(text: str) -> str:
    return "".join(character if character.isalnum() else "-" for character in text.lower())


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    single = Dashboard.load(EXAMPLES / "ha_dashboard.yaml")
    path = OUTPUT / "ha-dashboard.png"
    _render(single, 0).save(path)
    written.append(path)

    rotating = Dashboard.load(EXAMPLES / "ha_five_screens.yaml")
    for index in range(rotating.screen_count):
        rotating.show_screen(index)
        name = rotating.screen_name or f"screen-{index + 1}"
        path = OUTPUT / f"five-screens-{index + 1}-{_slug(name)}.png"
        _render(rotating, index).save(path)
        written.append(path)

    for path in written:
        print(path.relative_to(REPO_ROOT).as_posix())


if __name__ == "__main__":
    main()
