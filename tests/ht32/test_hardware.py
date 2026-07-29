"""Integration tests that need a real HT32 panel.

These are the only tests in the repository that cannot be trusted to a
recorder, because they check the one thing a recorder cannot: that the panel
accepts what we send it. CI deselects them with ``-m "not hardware"``, and the
fixtures skip them when nothing is attached, so they cost nothing until
somebody plugs a panel in and runs::

    uv run pytest -m hardware -v

They are deliberately shallow. A test suite cannot see what the panel is
displaying, so asserting "the write was accepted" is the honest limit; the
visual check is the operator's, which is what the simulator is for.
"""

from __future__ import annotations

import pytest

from tinydisplay.core import Color
from tinydisplay.ht32 import HidTransport, HT32DeviceInfo, HT32Driver, enumerate_panels
from tinydisplay.ht32.protocol import LCD_INTERFACE

pytestmark = pytest.mark.hardware


class TestDiscovery:
    @pytest.mark.usefixtures("panel")
    def test_the_panel_publishes_the_display_interface(self) -> None:
        interfaces = {device.interface_number for device in enumerate_panels()}
        assert LCD_INTERFACE in interfaces or interfaces == {-1}


class TestConnection:
    async def test_connect_and_disconnect(self, panel: HT32DeviceInfo) -> None:
        driver = HT32Driver(transport=HidTransport(device=panel))
        await driver.connect()
        assert driver.is_connected
        await driver.disconnect()
        assert not driver.is_connected


class TestFrames:
    async def test_a_solid_frame_is_accepted(self, connected_panel: HT32Driver) -> None:
        canvas = connected_panel.create_canvas()
        canvas.clear(Color.from_hex("#101010"))

        await connected_panel.show(canvas)
        assert connected_panel.frame_count == 1
        assert connected_panel.failure_count == 0

    async def test_consecutive_frames_are_accepted(
        self,
        connected_panel: HT32Driver,
    ) -> None:
        # A panel that takes one frame and then wedges is a real failure mode,
        # and it only shows up on the second frame.
        for shade in ("#200000", "#002000", "#000020"):
            canvas = connected_panel.create_canvas()
            canvas.clear(Color.from_hex(shade))
            await connected_panel.show(canvas)

        assert connected_panel.frame_count == 3
        assert connected_panel.failure_count == 0

    async def test_refresh_is_accepted(self, connected_panel: HT32Driver) -> None:
        await connected_panel.show(connected_panel.create_canvas())
        await connected_panel.refresh()
        assert connected_panel.failure_count == 0

    async def test_the_panel_is_left_dark(self, connected_panel: HT32Driver) -> None:
        # Not an assertion so much as courtesy: the last test to run should not
        # leave a bright rectangle on somebody's desk.
        canvas = connected_panel.create_canvas()
        canvas.clear(Color.BLACK)
        await connected_panel.show(canvas)
        assert connected_panel.frame_count == 1
