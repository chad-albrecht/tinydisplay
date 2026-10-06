"""The S1's RGB light bar, as state Home Assistant can own.

The bar is a separate device from the panel: a CH340 serial bridge into a
microcontroller that runs its own effects. It takes one five-byte command --
effect, intensity, speed -- and answers nothing, so there is no state to read
back. Home Assistant therefore holds the state, and this object is where it is
held: the light and the speed number both read and change it, and every change
is sent as one complete command, because the protocol has no partial update.

The library's :class:`~tinydisplay.ht32.LedController` does the talking. What
is left here is the part that needs the integration: discovering the bridge
without failing setup when there is none, and telling the entities when
something changed.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from tinydisplay.core import TinyDisplayError
from tinydisplay.ht32 import LedController, LedTheme, find_led_port
from tinydisplay.ht32.led import LEVEL_MAX, LEVEL_MIN

if TYPE_CHECKING:
    from collections.abc import Callable

_LOGGER = logging.getLogger(__name__)

#: The effects offered on the light, by the name Home Assistant shows. ``OFF``
#: is the light being off, not an effect.
EFFECTS: dict[str, LedTheme] = {
    "Rainbow": LedTheme.RAINBOW,
    "Breathing": LedTheme.BREATHING,
    "Colors": LedTheme.COLORS,
    "Auto": LedTheme.AUTO,
}

DEFAULT_EFFECT = "Rainbow"
DEFAULT_LEVEL = 3

#: Home Assistant brightness per bar level, so level 5 is full brightness.
_STEP = 255 / LEVEL_MAX


def brightness_to_level(brightness: int) -> int:
    """The bar level nearest to a Home Assistant brightness of 1..255."""
    return max(LEVEL_MIN, min(LEVEL_MAX, round(brightness / _STEP)))


def level_to_brightness(level: int) -> int:
    """The Home Assistant brightness that a bar level is shown as."""
    return round(level * _STEP)


class TinyDisplayLeds:
    """The light bar's last commanded state, and the means to change it."""

    def __init__(self, controller: LedController) -> None:
        self._controller = controller
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self.is_on = True
        self.effect = DEFAULT_EFFECT
        self.intensity = DEFAULT_LEVEL
        self.speed = DEFAULT_LEVEL

    @property
    def port(self) -> str | None:
        """The serial port of the bridge."""
        return getattr(self._controller.transport, "port", None)

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Call ``listener`` after every change; returns the unsubscriber."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    def restore(
        self,
        *,
        is_on: bool,
        effect: str | None,
        intensity: int | None,
        speed: int | None,
    ) -> None:
        """Adopt remembered state without sending it; out-of-range is ignored."""
        self.is_on = is_on
        if effect in EFFECTS:
            self.effect = effect
        if intensity is not None and LEVEL_MIN <= intensity <= LEVEL_MAX:
            self.intensity = intensity
        if speed is not None and LEVEL_MIN <= speed <= LEVEL_MAX:
            self.speed = speed

    async def async_update(
        self,
        *,
        is_on: bool | None = None,
        effect: str | None = None,
        intensity: int | None = None,
        speed: int | None = None,
    ) -> None:
        """Change any part of the state and send the whole of it.

        Raises:
            TinyDisplayError: If the bridge could not be written.
        """
        async with self._lock:
            if is_on is not None:
                self.is_on = is_on
            if effect is not None:
                self.effect = effect
            if intensity is not None:
                self.intensity = intensity
            if speed is not None:
                self.speed = speed
            await self._send()
        for listener in list(self._listeners):
            listener()

    async def async_apply(self) -> None:
        """Send the current state as it stands, e.g. after a restore."""
        await self.async_update()

    async def _send(self) -> None:
        if not self.is_on:
            await self._controller.off()
            return
        await self._controller.set_theme(
            EFFECTS[self.effect],
            intensity=self.intensity,
            speed=self.speed,
        )

    async def async_close(self) -> None:
        """Release the serial port."""
        await self._controller.disconnect()


async def async_open_leds(port: str | None = None) -> TinyDisplayLeds | None:
    """Open the light bar's bridge, or ``None`` if this machine has none.

    Absence is ordinary -- the preview driver, a panel on another machine, a
    Home Assistant without pyserial -- so it is logged once and setup carries
    on. The screen does not depend on the lights.
    """
    try:
        found = port or await asyncio.to_thread(find_led_port)
        controller = LedController(port=found)
        await controller.connect()
    except (TinyDisplayError, OSError) as exc:
        _LOGGER.info("no light bar control: %s", exc)
        return None
    _LOGGER.debug("light bar bridge opened on %s", found)
    return TinyDisplayLeds(controller)
