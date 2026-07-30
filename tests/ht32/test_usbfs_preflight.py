"""Tests pinning the usbfs preflight to the real discovery implementation.

``tools/ht32_usbfs_preflight.py`` duplicates the discovery constants and the
sysfs walk in ``tinydisplay.ht32.usbfs`` on purpose: it runs inside a Home
Assistant Core container where nothing is installed, so it cannot import the
package. Duplication that nobody checks is duplication that drifts, and a
preflight that looks for the wrong device would send somebody hunting for a
permissions problem that is really a stale constant.

The discovery logic itself is exercised against a fake sysfs tree, so the walk
is tested rather than merely compared -- and the fake is built to the same
shape the kernel produces, which is what makes the comparison meaningful.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tinydisplay.ht32 import usbfs
from tinydisplay.ht32.protocol import PRODUCT_ID, VENDOR_ID

if TYPE_CHECKING:
    from types import ModuleType

PREFLIGHT_PATH = Path(__file__).resolve().parents[2] / "tools" / "ht32_usbfs_preflight.py"


@pytest.fixture(scope="module")
def preflight() -> ModuleType:
    """Import the standalone script by path, as a module."""
    spec = importlib.util.spec_from_file_location("ht32_usbfs_preflight", PREFLIGHT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_device(
    root: Path,
    name: str,
    *,
    vendor: str,
    product: str,
    bus: int,
    devnum: int,
) -> Path:
    """Build a sysfs device directory shaped like the kernel's."""
    entry = root / name
    entry.mkdir(parents=True)
    (entry / "idVendor").write_text(vendor, encoding="ascii")
    (entry / "idProduct").write_text(product, encoding="ascii")
    (entry / "busnum").write_text(f"{bus}\n", encoding="ascii")
    (entry / "devnum").write_text(f"{devnum}\n", encoding="ascii")
    return entry


def make_interface(
    device: Path,
    number: int,
    *,
    endpoints: list[tuple[str, str, str]],
) -> Path:
    """Add an interface with endpoints, as sysfs presents them.

    The kernel names these ``<device>:<config>.<interface>``, which contains a
    colon that Windows cannot create. Both implementations match on the
    ``bInterfaceNumber`` attribute rather than the directory name, so a
    portable name exercises the same path.
    """
    entry = device / f"iface{number}"
    entry.mkdir(parents=True)
    (entry / "bInterfaceNumber").write_text(f"{number:02x}", encoding="ascii")
    (entry / "bInterfaceClass").write_text("03", encoding="ascii")
    for index, (address, attributes, packet) in enumerate(endpoints):
        ep = entry / f"ep_{index:02d}"
        ep.mkdir()
        (ep / "bEndpointAddress").write_text(address, encoding="ascii")
        (ep / "bmAttributes").write_text(attributes, encoding="ascii")
        (ep / "wMaxPacketSize").write_text(packet, encoding="ascii")
    return entry


def make_panel_like_the_s1(root: Path) -> Path:
    """The interface layout the AceMagic S1 actually reports.

    Three HID interfaces: an IN-only one, a bare OUT one with no kernel driver
    bound, and one with both. Taken from a real ``ht32_usbfs_preflight`` run on
    Home Assistant OS 18.1, which is what makes it worth pinning.
    """
    device = make_device(root, "1-8", vendor="04d9", product="fd01", bus=1, devnum=6)
    make_interface(device, 0, endpoints=[("81", "03", "0040")])
    make_interface(device, 1, endpoints=[("02", "03", "0040")])
    make_interface(device, 2, endpoints=[("04", "03", "0040"), ("83", "03", "0040")])
    return device


class TestConstantsAgree:
    def test_the_file_exists_where_the_docs_say(self) -> None:
        assert PREFLIGHT_PATH.is_file()

    def test_vendor_id_matches_the_driver(self, preflight: ModuleType) -> None:
        assert preflight.VENDOR_ID == VENDOR_ID

    def test_product_id_matches_the_driver(self, preflight: ModuleType) -> None:
        assert preflight.PRODUCT_ID == PRODUCT_ID

    def test_sysfs_root_matches_the_driver(self, preflight: ModuleType) -> None:
        assert preflight.USB_DEVICES == usbfs.USB_DEVICES

    def test_node_path_matches_the_driver(self, preflight: ModuleType) -> None:
        # The driver formats this inside UsbDeviceInfo.node; the preflight has
        # to reach the same path from the same bus and device numbers.
        info = usbfs.UsbDeviceInfo(bus=1, device=7, sysfs=Path("/sys/bus/usb/devices/1-3"))
        expected = preflight.USB_NODES / "001" / "007"
        assert info.node == expected


class TestDiscovery:
    def test_finds_the_panel(
        self, preflight: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "devices"
        make_device(root, "1-1", vendor="8087", product="0024", bus=1, devnum=2)
        make_device(root, "1-3", vendor="04d9", product="fd01", bus=1, devnum=7)
        monkeypatch.setattr(preflight, "USB_DEVICES", root)

        found = preflight.find_panel()
        assert found is not None
        bus, device, sysfs = found
        assert (bus, device) == (1, 7)
        assert sysfs.name == "1-3"

    def test_reports_absence_rather_than_raising(
        self, preflight: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Absence is an ordinary answer here: it is the result the operator is
        # running this to find out.
        root = tmp_path / "devices"
        make_device(root, "1-1", vendor="8087", product="0024", bus=1, devnum=2)
        monkeypatch.setattr(preflight, "USB_DEVICES", root)

        assert preflight.find_panel() is None

    def test_ignores_entries_with_unreadable_attributes(
        self, preflight: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # sysfs is full of directories that are not devices -- interfaces,
        # buses, power domains. Skipping them must not abort the walk.
        #
        # The real interface directories are named `1-0:1.0`, which Windows
        # cannot create; what matters to the walk is the absent attributes
        # rather than the name, so the fake uses a portable one.
        root = tmp_path / "devices"
        (root / "usb1").mkdir(parents=True)
        (root / "1-0-interface").mkdir(parents=True)
        make_device(root, "1-3", vendor="04d9", product="fd01", bus=1, devnum=7)
        monkeypatch.setattr(preflight, "USB_DEVICES", root)

        found = preflight.find_panel()
        assert found is not None
        assert found[0:2] == (1, 7)

    def test_matches_the_drivers_own_discovery(
        self, preflight: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The two walks agree on the same tree, which is the point of the file."""
        root = tmp_path / "devices"
        make_device(root, "1-1", vendor="8087", product="0024", bus=1, devnum=2)
        make_device(root, "1-3", vendor="04d9", product="fd01", bus=1, devnum=7)
        monkeypatch.setattr(preflight, "USB_DEVICES", root)

        theirs = usbfs.find_usb_panel(root=root)
        ours = preflight.find_panel()

        assert theirs is not None
        assert ours is not None
        assert (theirs.bus, theirs.device, theirs.sysfs) == ours

    def test_missing_root_is_absence_not_an_error(
        self, preflight: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(preflight, "USB_DEVICES", tmp_path / "not-there")
        assert preflight.find_panel() is None


class TestEndpointSelection:
    """The preflight has to predict the interface the driver will claim.

    Choosing the wrong one is a failure this panel has already produced, and it
    presents as a blank screen rather than an error -- so a preflight that said
    READY while the driver went on to claim a keypad would be worse than no
    preflight at all.
    """

    def test_reads_endpoints_the_way_the_driver_does(
        self, preflight: ModuleType, tmp_path: Path
    ) -> None:
        device = make_panel_like_the_s1(tmp_path / "devices")

        theirs = usbfs.usb_interfaces(device)
        ours = preflight.read_interfaces(device)

        assert set(theirs) == set(ours)
        for number, endpoints in theirs.items():
            assert [(e.address, e.kind, e.packet_size) for e in endpoints] == ours[number]

    def test_picks_the_same_endpoint_as_the_driver(
        self, preflight: ModuleType, tmp_path: Path
    ) -> None:
        device = make_panel_like_the_s1(tmp_path / "devices")

        theirs = usbfs.find_output_endpoint(usbfs.usb_interfaces(device))
        ours = preflight.find_output_endpoint(preflight.read_interfaces(device))

        assert theirs == ours

    def test_picks_the_bare_out_interface_on_the_s1(
        self, preflight: ModuleType, tmp_path: Path
    ) -> None:
        # if01 carries the only OUT endpoint that is not sharing an interface
        # with an IN one, and ties on packet size keep the lowest interface.
        device = make_panel_like_the_s1(tmp_path / "devices")
        assert preflight.find_output_endpoint(preflight.read_interfaces(device)) == (1, 0x02)

    def test_in_endpoints_cannot_carry_frames(self, preflight: ModuleType) -> None:
        assert not preflight.can_carry_frames(0x81, preflight.TRANSFER_INTERRUPT)
        assert preflight.can_carry_frames(0x02, preflight.TRANSFER_INTERRUPT)

    def test_control_endpoints_cannot_carry_frames(self, preflight: ModuleType) -> None:
        # Control (0) and isochronous (1) endpoints exist on plenty of devices
        # and cannot take a frame.
        assert not preflight.can_carry_frames(0x02, 0)
        assert not preflight.can_carry_frames(0x02, 1)
        assert preflight.can_carry_frames(0x02, preflight.TRANSFER_BULK)

    def test_the_largest_packet_wins(self, preflight: ModuleType, tmp_path: Path) -> None:
        device = make_device(
            tmp_path / "devices", "1-8", vendor="04d9", product="fd01", bus=1, devnum=6
        )
        make_interface(device, 0, endpoints=[("02", "03", "0008")])
        make_interface(device, 1, endpoints=[("04", "03", "0040")])

        chosen = preflight.find_output_endpoint(preflight.read_interfaces(device))
        assert chosen == (1, 0x04)
        assert chosen == usbfs.find_output_endpoint(usbfs.usb_interfaces(device))

    def test_a_device_with_no_out_endpoint_selects_nothing(
        self, preflight: ModuleType, tmp_path: Path
    ) -> None:
        device = make_device(
            tmp_path / "devices", "1-8", vendor="04d9", product="fd01", bus=1, devnum=6
        )
        make_interface(device, 0, endpoints=[("81", "03", "0040")])

        assert preflight.find_output_endpoint(preflight.read_interfaces(device)) is None
