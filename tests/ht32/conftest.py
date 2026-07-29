"""Fixtures for the HT32 tests.

Everything except :mod:`tests.ht32.test_hardware` runs against the recording
transports and needs nothing attached. The fixtures here exist for the
hardware tests, and their job is to turn "no panel" into a skip with a reason
rather than a failure -- running the suite on a laptop with nothing plugged in
should be quiet, not red.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tinydisplay.ht32 import (
    DeviceNotFoundError,
    HidTransport,
    HT32DeviceInfo,
    HT32Driver,
    find_panel,
    is_hid_available,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture(scope="session")
def panel() -> HT32DeviceInfo:
    """The attached panel, or a skip explaining why there is not one."""
    if not is_hid_available():
        pytest.skip("hidapi is not installed; install tinydisplay-ht32[hid]")
    try:
        return find_panel()
    except DeviceNotFoundError as exc:
        pytest.skip(str(exc))


@pytest.fixture
async def connected_panel(panel: HT32DeviceInfo) -> AsyncIterator[HT32Driver]:
    """A connected driver for the attached panel, closed afterwards."""
    driver = HT32Driver(transport=HidTransport(device=panel))
    await driver.connect()
    try:
        yield driver
    finally:
        await driver.disconnect()
