"""Tests for HT32 device discovery.

Discovery is the one place that must work identically whether hidapi is
installed or not, so the USB backend is substituted rather than mocked out: a
fake ``hid`` module returns enumeration dictionaries in the same shape the real
one does, including the fields platforms are known to omit.
"""

from __future__ import annotations

from typing import Any

import pytest

from tinydisplay.ht32 import DeviceNotFoundError, HT32DeviceInfo, enumerate_panels, find_panel
from tinydisplay.ht32 import device as device_module
from tinydisplay.ht32.device import is_hid_available, select_display_interface
from tinydisplay.ht32.protocol import LCD_INTERFACE, PRODUCT_ID, VENDOR_ID


def entry(interface: int, *, path: bytes = b"/dev/hidraw0", serial: str = "") -> dict[str, Any]:
    return {
        "path": path,
        "vendor_id": VENDOR_ID,
        "product_id": PRODUCT_ID,
        "interface_number": interface,
        "serial_number": serial,
        "product_string": "HT32 Panel",
    }


class FakeHid:
    """Stands in for the ``hid`` module during enumeration."""

    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self._entries = entries
        self.enumerate_calls: list[tuple[int, int]] = []

    def enumerate(self, vendor_id: int, product_id: int) -> list[dict[str, Any]]:
        self.enumerate_calls.append((vendor_id, product_id))
        return self._entries


@pytest.fixture
def fake_hid(monkeypatch: pytest.MonkeyPatch) -> FakeHid:
    hid = FakeHid([entry(0), entry(LCD_INTERFACE, path=b"/dev/hidraw1")])
    monkeypatch.setattr(device_module, "import_hid", lambda: hid)
    return hid


class TestDeviceInfo:
    def test_identifies_the_display_interface(self) -> None:
        info = HT32DeviceInfo(b"p", VENDOR_ID, PRODUCT_ID, LCD_INTERFACE)
        assert info.is_display_interface

    def test_other_interfaces_are_not_the_display(self) -> None:
        info = HT32DeviceInfo(b"p", VENDOR_ID, PRODUCT_ID, 0)
        assert not info.is_display_interface

    def test_str_is_readable_in_an_error_message(self) -> None:
        info = HT32DeviceInfo(b"p", VENDOR_ID, PRODUCT_ID, 1, product_string="HT32 Panel")
        assert str(info) == "HT32 Panel (04D9:FD01 if1)"

    def test_str_falls_back_when_the_product_is_unnamed(self) -> None:
        assert "HT32 panel" in str(HT32DeviceInfo(b"p", VENDOR_ID, PRODUCT_ID, 1))

    def test_is_hashable_and_immutable(self) -> None:
        info = HT32DeviceInfo(b"p", VENDOR_ID, PRODUCT_ID, 1)
        assert {info, info} == {info}
        with pytest.raises(AttributeError):
            info.path = b"other"  # type: ignore[misc]


class TestSelectDisplayInterface:
    def test_prefers_the_documented_interface(self) -> None:
        devices = (
            HT32DeviceInfo(b"a", VENDOR_ID, PRODUCT_ID, 0),
            HT32DeviceInfo(b"b", VENDOR_ID, PRODUCT_ID, LCD_INTERFACE),
        )
        assert select_display_interface(devices) is devices[1]

    def test_falls_back_to_the_first_when_interfaces_are_unreported(self) -> None:
        # macOS reports -1 for most devices; refusing to open anything there
        # would mean the driver simply does not work on a Mac.
        devices = (HT32DeviceInfo(b"a", VENDOR_ID, PRODUCT_ID, -1),)
        assert select_display_interface(devices) is devices[0]

    def test_nothing_attached_selects_nothing(self) -> None:
        assert select_display_interface(()) is None


class TestEnumerate:
    @pytest.mark.usefixtures("fake_hid")
    def test_returns_one_entry_per_interface(self) -> None:
        panels = enumerate_panels()
        assert len(panels) == 2
        assert [panel.interface_number for panel in panels] == [0, LCD_INTERFACE]

    def test_queries_the_documented_hardware_id(self, fake_hid: FakeHid) -> None:
        enumerate_panels()
        assert fake_hid.enumerate_calls == [(0x04D9, 0xFD01)]

    def test_missing_optional_fields_become_empty_strings(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # hidapi reports None rather than "" for an absent serial, and some
        # backends omit the key entirely.
        sparse = {"path": b"p", "interface_number": 1, "serial_number": None}
        monkeypatch.setattr(device_module, "import_hid", lambda: FakeHid([sparse]))

        panel = enumerate_panels()[0]
        assert panel.serial_number == ""
        assert panel.product_string == ""

    def test_nothing_attached_is_not_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(device_module, "import_hid", lambda: FakeHid([]))
        assert enumerate_panels() == ()


class TestFindPanel:
    @pytest.mark.usefixtures("fake_hid")
    def test_picks_the_display_interface(self) -> None:
        assert find_panel().path == b"/dev/hidraw1"

    def test_filters_by_serial_number(self, monkeypatch: pytest.MonkeyPatch) -> None:
        entries = [
            entry(LCD_INTERFACE, path=b"/dev/hidraw0", serial="AAA"),
            entry(LCD_INTERFACE, path=b"/dev/hidraw1", serial="BBB"),
        ]
        monkeypatch.setattr(device_module, "import_hid", lambda: FakeHid(entries))

        assert find_panel(serial_number="BBB").path == b"/dev/hidraw1"

    def test_an_unmatched_serial_reports_the_serial(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            device_module,
            "import_hid",
            lambda: FakeHid([entry(LCD_INTERFACE, serial="AAA")]),
        )
        with pytest.raises(DeviceNotFoundError, match="'ZZZ'"):
            find_panel(serial_number="ZZZ")

    def test_nothing_attached_mentions_permissions(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # On Linux an unplugged panel and an unreadable hidraw node look the
        # same from here, so the message has to raise both possibilities.
        monkeypatch.setattr(device_module, "import_hid", lambda: FakeHid([]))
        with pytest.raises(DeviceNotFoundError, match="udev"):
            find_panel()


class TestBackendAvailability:
    def test_reports_whether_a_backend_is_installed(self) -> None:
        assert isinstance(is_hid_available(), bool)

    def test_a_missing_backend_is_reported_not_raised(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def missing() -> Any:
            msg = "the hidapi package is required"
            raise DeviceNotFoundError(msg)

        monkeypatch.setattr(device_module, "import_hid", missing)
        assert is_hid_available() is False
