"""Tests for the Linux hidraw transport.

Both halves are testable anywhere, which is the point of doing discovery
through sysfs and writing through a file descriptor:

- Discovery reads text files, so a fake ``/sys/class/hidraw`` tree in tmp_path
  exercises it on any platform.
- Writing is ``os.write``, so a plain temporary file stands in for the device
  node -- the transport cannot tell the difference, and neither could a panel.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tinydisplay.ht32 import DeviceNotFoundError, TransportError
from tinydisplay.ht32.hidraw import (
    DEFAULT_INIT_DELAY,
    UNKNOWN_INTERFACE,
    HidrawDeviceInfo,
    HidrawTransport,
    enumerate_hidraw,
    find_hidraw_panel,
    is_hidraw_available,
    parse_hid_id,
    select_display_node,
)
from tinydisplay.ht32.protocol import LCD_INTERFACE, PACKET_SIZE, PRODUCT_ID, VENDOR_ID
from tinydisplay.ht32.transport import HidTransport, PanelTransport, create_panel_transport

PANEL_HID_ID = "HID_ID=0003:000004D9:0000FD01"

# The real transport waits a second after opening for the panel to wake. These
# tests open temporary files, which are awake already.
NO_DELAY = 0.0


def make_node(
    root: Path,
    name: str,
    *,
    hid_id: str = PANEL_HID_ID,
    interface: int | None = None,
    hid_name: str = "HT32 Panel",
) -> None:
    """Build a fake ``/sys/class/hidraw/<name>`` tree.

    On a real system ``device`` is a symlink into the USB tree, and
    ``device/..`` therefore resolves to the *target's* parent -- the USB
    interface holding ``bInterfaceNumber``. Here it is a plain directory, so
    ``device/..`` is the hidraw directory and the interface number goes there
    instead. The reads under test are byte-identical either way, and avoiding
    symlinks keeps this runnable on Windows, where creating one needs
    elevation.
    """
    device = root / name / "device"
    device.mkdir(parents=True)
    (device / "uevent").write_text(f"{hid_id}\nHID_NAME={hid_name}\n", encoding="utf-8")
    if interface is not None:
        (root / name / "bInterfaceNumber").write_text(f"{interface:02d}\n", encoding="utf-8")


@pytest.fixture
def sysfs(tmp_path: Path) -> Path:
    """A fake sysfs with two panel interfaces, the second being the display."""
    root = tmp_path / "hidraw"
    root.mkdir()
    make_node(root, "hidraw0", interface=0)
    make_node(root, "hidraw1", interface=LCD_INTERFACE)
    return root


class TestParseHidId:
    def test_parses_the_documented_hardware_id(self) -> None:
        assert parse_hid_id(f"{PANEL_HID_ID}\nHID_NAME=Panel\n") == (3, VENDOR_ID, PRODUCT_ID)

    def test_ignores_other_lines(self) -> None:
        text = f"DRIVER=hid-generic\n{PANEL_HID_ID}\nMODALIAS=hid:xyz\n"
        assert parse_hid_id(text) is not None

    def test_a_missing_hid_id_is_none_not_a_guess(self) -> None:
        assert parse_hid_id("HID_NAME=Panel\n") is None

    @pytest.mark.parametrize(
        "line",
        ["HID_ID=0003:000004D9", "HID_ID=", "HID_ID=zz:yy:xx", "HID_ID=0003:04D9:FD01:extra"],
    )
    def test_malformed_ids_are_none(self, line: str) -> None:
        # Returning None rather than partial data: "unreadable" must not turn
        # into "wrong device".
        assert parse_hid_id(f"{line}\n") is None

    def test_empty_input(self) -> None:
        assert parse_hid_id("") is None


class TestEnumerate:
    def test_finds_both_panel_interfaces(self, sysfs: Path) -> None:
        nodes = enumerate_hidraw(root=sysfs)
        assert len(nodes) == 2
        assert [node.interface_number for node in nodes] == [0, LCD_INTERFACE]

    def test_maps_nodes_to_dev_paths(self, sysfs: Path) -> None:
        assert enumerate_hidraw(root=sysfs)[0].path == Path("/dev/hidraw0")

    def test_reads_the_reported_name(self, sysfs: Path) -> None:
        assert enumerate_hidraw(root=sysfs)[0].name == "HT32 Panel"

    def test_skips_other_hardware(self, tmp_path: Path) -> None:
        root = tmp_path / "hidraw"
        root.mkdir()
        make_node(root, "hidraw0", hid_id="HID_ID=0003:00001234:00005678", interface=0)
        assert enumerate_hidraw(root=root) == ()

    def test_a_node_without_an_interface_number_is_still_found(self, tmp_path: Path) -> None:
        # Containers and non-USB HID transports do not publish it; refusing to
        # return the node would mean refusing to work at all.
        root = tmp_path / "hidraw"
        root.mkdir()
        make_node(root, "hidraw0", interface=None)

        nodes = enumerate_hidraw(root=root)
        assert len(nodes) == 1
        assert nodes[0].interface_number == UNKNOWN_INTERFACE

    def test_a_node_with_no_uevent_is_skipped(self, tmp_path: Path) -> None:
        root = tmp_path / "hidraw"
        (root / "hidraw0" / "device").mkdir(parents=True)
        assert enumerate_hidraw(root=root) == ()

    def test_a_missing_sysfs_is_not_an_error(self, tmp_path: Path) -> None:
        assert enumerate_hidraw(root=tmp_path / "absent") == ()


class TestSelection:
    def test_prefers_the_display_interface(self, sysfs: Path) -> None:
        assert find_hidraw_panel(root=sysfs).interface_number == LCD_INTERFACE

    def test_falls_back_to_the_first_node(self, tmp_path: Path) -> None:
        root = tmp_path / "hidraw"
        root.mkdir()
        make_node(root, "hidraw0", interface=None)
        assert find_hidraw_panel(root=root).path == Path("/dev/hidraw0")

    def test_nothing_selects_nothing(self) -> None:
        assert select_display_node(()) is None

    def test_no_panel_mentions_udev_and_containers(self, tmp_path: Path) -> None:
        root = tmp_path / "hidraw"
        root.mkdir()
        with pytest.raises(DeviceNotFoundError, match="udev"):
            find_hidraw_panel(root=root)

    def test_absent_sysfs_says_so_distinctly(self, tmp_path: Path) -> None:
        with pytest.raises(DeviceNotFoundError, match="no hidraw devices are exposed"):
            find_hidraw_panel(root=tmp_path / "absent")


class TestDeviceInfo:
    def test_identifies_the_display_interface(self) -> None:
        info = HidrawDeviceInfo(Path("/dev/hidraw1"), VENDOR_ID, PRODUCT_ID, LCD_INTERFACE)
        assert info.is_display_interface

    def test_str_is_readable_in_an_error(self) -> None:
        info = HidrawDeviceInfo(Path("/dev/hidraw1"), VENDOR_ID, PRODUCT_ID, 1, name="HT32")
        assert "04D9:FD01" in str(info)
        assert "hidraw1" in str(info)


class TestTransport:
    def test_satisfies_the_panel_transport_protocol(self) -> None:
        assert isinstance(HidrawTransport(path="/dev/null", init_delay=NO_DELAY), PanelTransport)

    def test_writes_land_in_the_node_verbatim(self, tmp_path: Path) -> None:
        # A regular file is indistinguishable from a device node here, which
        # is what makes the write path testable off Linux.
        node = tmp_path / "hidraw1"
        node.touch()
        packet = bytes(range(256)) * (PACKET_SIZE // 256) + bytes(PACKET_SIZE % 256)

        transport = HidrawTransport(path=node, init_delay=NO_DELAY)
        transport.open()
        transport.write(packet)
        transport.close()

        assert node.read_bytes() == packet

    def test_open_is_idempotent(self, tmp_path: Path) -> None:
        node = tmp_path / "hidraw1"
        node.touch()
        transport = HidrawTransport(path=node, init_delay=NO_DELAY)
        transport.open()
        assert transport.is_open
        transport.open()
        assert transport.is_open
        transport.close()

    def test_writing_while_closed_fails(self) -> None:
        with pytest.raises(TransportError, match="not open"):
            HidrawTransport(path="/dev/null", init_delay=NO_DELAY).write(bytes(PACKET_SIZE))

    def test_a_wrong_sized_packet_is_refused(self, tmp_path: Path) -> None:
        node = tmp_path / "hidraw1"
        node.touch()
        transport = HidrawTransport(path=node, init_delay=NO_DELAY)
        transport.open()
        try:
            with pytest.raises(TransportError, match=f"{PACKET_SIZE} bytes"):
                transport.write(bytes(10))
        finally:
            transport.close()

    def test_opening_a_missing_node_reports_it(self, tmp_path: Path) -> None:
        with pytest.raises(DeviceNotFoundError):
            HidrawTransport(path=tmp_path / "absent", init_delay=NO_DELAY).open()

    def test_the_init_delay_defaults_to_the_documented_wait(self) -> None:
        # Writing before the panel has woken produces ETIMEDOUT, which reads
        # like a protocol fault. Regressing this default would be expensive to
        # diagnose, so it is pinned.
        assert DEFAULT_INIT_DELAY == 1.0

    def test_closing_an_unopened_transport_is_safe(self) -> None:
        transport = HidrawTransport(path="/dev/null", init_delay=NO_DELAY)
        transport.close()
        assert not transport.is_open

    def test_close_releases_the_descriptor(self, tmp_path: Path) -> None:
        node = tmp_path / "hidraw1"
        node.touch()
        transport = HidrawTransport(path=node, init_delay=NO_DELAY)
        transport.open()
        fd = transport._fd
        transport.close()

        assert fd is not None
        with pytest.raises(OSError):  # noqa: PT011 - any OSError means it is closed
            os.fstat(fd)

    def test_a_device_takes_precedence_over_discovery(self) -> None:
        info = HidrawDeviceInfo(Path("/dev/hidraw9"), VENDOR_ID, PRODUCT_ID, LCD_INTERFACE)
        assert HidrawTransport(device=info).path == Path("/dev/hidraw9")


class TestTransportSelection:
    def test_falls_back_to_hidapi_when_hidraw_is_absent(self) -> None:
        # This test machine is Windows or a Mac in CI, and a Linux runner has
        # no HT32 attached, so either way the fallback is what should happen.
        transport = create_panel_transport()
        assert isinstance(transport, PanelTransport)

    def test_a_serial_number_forces_the_hidapi_path(self) -> None:
        # hidraw identifies nodes by hardware ID and cannot filter by serial,
        # so asking for one must not silently select a different panel.
        assert isinstance(create_panel_transport(serial_number="ABC"), HidTransport)

    def test_hidraw_can_be_declined(self) -> None:
        assert isinstance(create_panel_transport(prefer_hidraw=False), HidTransport)


class TestAvailability:
    def test_reports_a_bool(self) -> None:
        assert isinstance(is_hidraw_available(), bool)

    def test_a_missing_root_is_unavailable(self, tmp_path: Path) -> None:
        assert is_hidraw_available(root=tmp_path / "absent") is False
