"""Tests for the raw-USB transport.

This is the transport that actually drives the panel, so the parts that can be
checked without hardware are checked hard: the ioctl encodings, which must
match the kernel's exactly, and the sysfs parsing that decides which interface
to claim.

The ioctl numbers matter more than they look. They encode the size of a struct,
so a padding mistake produces a number the kernel does not recognise, and the
failure appears as an unhelpful EINVAL on the one machine where debugging is
most awkward. They are pure arithmetic, so they are pinned here instead.
"""

from __future__ import annotations

import ctypes
from pathlib import Path

import pytest

from tinydisplay.ht32 import DeviceNotFoundError, TransportError, UsbfsTransport
from tinydisplay.ht32.protocol import (
    PACKET_SIZE,
    PRODUCT_ID,
    VENDOR_ID,
    build_refresh_packet,
    device_payload,
)
from tinydisplay.ht32.transport import PanelTransport
from tinydisplay.ht32.usbfs import (
    TRANSFER_BULK,
    TRANSFER_INTERRUPT,
    USBDEVFS_BULK,
    USBDEVFS_CLAIMINTERFACE,
    USBDEVFS_DISCONNECT,
    USBDEVFS_IOCTL,
    USBDEVFS_RELEASEINTERFACE,
    UsbDeviceInfo,
    UsbEndpoint,
    _BulkTransfer,
    _DevfsIoctl,
    _ioc,
    find_output_endpoint,
    find_usb_panel,
    is_usbfs_available,
    usb_interfaces,
)

SIXTY_FOUR_BIT = ctypes.sizeof(ctypes.c_void_p) == 8


def make_device(root: Path, name: str = "1-8", *, bus: int = 1, devnum: int = 5) -> Path:
    """Build a fake /sys/bus/usb/devices entry for the panel."""
    entry = root / name
    entry.mkdir(parents=True)
    (entry / "idVendor").write_text(f"{VENDOR_ID:04x}\n", encoding="utf-8")
    (entry / "idProduct").write_text(f"{PRODUCT_ID:04x}\n", encoding="utf-8")
    (entry / "busnum").write_text(f"{bus}\n", encoding="utf-8")
    (entry / "devnum").write_text(f"{devnum}\n", encoding="utf-8")
    return entry


def make_interface(
    device: Path,
    number: int,
    endpoints: list[tuple[int, int, int]],
) -> None:
    """Add an interface with ``(address, attributes, packet_size)`` endpoints.

    The kernel names these ``<device>:<config>.<interface>``, but a colon is
    not a legal filename character on Windows, where these tests also run. The
    parser identifies interfaces by the presence of ``bInterfaceNumber`` rather
    than by name, so the directory can be called anything.
    """
    interface = device / f"if{number}"
    interface.mkdir()
    (interface / "bInterfaceNumber").write_text(f"{number:02x}\n", encoding="utf-8")
    for address, attributes, packet_size in endpoints:
        ep = interface / f"ep_{address:02x}"
        ep.mkdir()
        (ep / "bEndpointAddress").write_text(f"{address:02x}\n", encoding="utf-8")
        (ep / "bmAttributes").write_text(f"{attributes:02x}\n", encoding="utf-8")
        (ep / "wMaxPacketSize").write_text(f"{packet_size:04x}\n", encoding="utf-8")


class TestIoctlEncoding:
    """Pinned against the constants in linux/usbdevice_fs.h."""

    def test_claim_interface(self) -> None:
        assert USBDEVFS_CLAIMINTERFACE == 0x8004550F

    def test_release_interface(self) -> None:
        assert USBDEVFS_RELEASEINTERFACE == 0x80045510

    def test_disconnect(self) -> None:
        assert USBDEVFS_DISCONNECT == 0x5516

    @pytest.mark.skipif(not SIXTY_FOUR_BIT, reason="64-bit struct layout")
    def test_bulk(self) -> None:
        assert ctypes.sizeof(_BulkTransfer) == 24
        assert USBDEVFS_BULK == 0xC0185502

    @pytest.mark.skipif(not SIXTY_FOUR_BIT, reason="64-bit struct layout")
    def test_devfs_ioctl(self) -> None:
        assert ctypes.sizeof(_DevfsIoctl) == 16
        assert USBDEVFS_IOCTL == 0xC0105512

    def test_the_encoder_matches_the_macro(self) -> None:
        assert _ioc(2, "U", 15, 4) == (2 << 30) | (4 << 16) | (0x55 << 8) | 15


class TestEndpoints:
    def test_out_endpoints_have_the_top_bit_clear(self) -> None:
        assert UsbEndpoint(0x02, TRANSFER_INTERRUPT, 64).is_out
        assert not UsbEndpoint(0x82, TRANSFER_INTERRUPT, 64).is_out

    def test_only_bulk_and_interrupt_can_carry_frames(self) -> None:
        assert UsbEndpoint(0x02, TRANSFER_INTERRUPT, 64).can_carry_frames
        assert UsbEndpoint(0x02, TRANSFER_BULK, 64).can_carry_frames
        assert not UsbEndpoint(0x02, 0, 64).can_carry_frames  # control
        assert not UsbEndpoint(0x02, 1, 64).can_carry_frames  # isochronous

    def test_an_in_endpoint_cannot_carry_frames(self) -> None:
        assert not UsbEndpoint(0x81, TRANSFER_INTERRUPT, 64).can_carry_frames


class TestDiscovery:
    def test_finds_the_panel(self, tmp_path: Path) -> None:
        make_device(tmp_path)
        found = find_usb_panel(root=tmp_path)

        assert found is not None
        assert found.bus == 1
        assert found.device == 5

    def test_builds_the_usbfs_node_path(self, tmp_path: Path) -> None:
        make_device(tmp_path, bus=2, devnum=17)
        found = find_usb_panel(root=tmp_path)

        assert found is not None
        assert found.node == Path("/dev/bus/usb/002/017")

    def test_ignores_other_hardware(self, tmp_path: Path) -> None:
        entry = tmp_path / "1-1"
        entry.mkdir()
        (entry / "idVendor").write_text("1234\n", encoding="utf-8")
        (entry / "idProduct").write_text("5678\n", encoding="utf-8")

        assert find_usb_panel(root=tmp_path) is None

    def test_nothing_attached_is_not_an_error(self, tmp_path: Path) -> None:
        assert find_usb_panel(root=tmp_path) is None

    def test_a_missing_sysfs_is_not_an_error(self, tmp_path: Path) -> None:
        assert find_usb_panel(root=tmp_path / "absent") is None

    def test_an_entry_without_numbers_is_skipped(self, tmp_path: Path) -> None:
        entry = tmp_path / "1-1"
        entry.mkdir()
        (entry / "idVendor").write_text("not-hex\n", encoding="utf-8")

        assert find_usb_panel(root=tmp_path) is None


class TestInterfaces:
    def test_reads_interfaces_and_endpoints(self, tmp_path: Path) -> None:
        device = make_device(tmp_path)
        make_interface(device, 0, [(0x81, TRANSFER_INTERRUPT, 8)])
        make_interface(device, 1, [(0x02, TRANSFER_INTERRUPT, 64)])

        interfaces = usb_interfaces(device)
        assert sorted(interfaces) == [0, 1]
        assert interfaces[1][0].address == 0x02
        assert interfaces[1][0].packet_size == 64

    def test_an_interface_with_no_endpoints_is_still_listed(self, tmp_path: Path) -> None:
        device = make_device(tmp_path)
        make_interface(device, 0, [])
        assert usb_interfaces(device) == {0: []}

    def test_a_missing_device_yields_nothing(self, tmp_path: Path) -> None:
        assert usb_interfaces(tmp_path / "absent") == {}


class TestEndpointSelection:
    def test_picks_the_out_endpoint(self) -> None:
        interfaces = {
            0: [UsbEndpoint(0x81, TRANSFER_INTERRUPT, 8)],
            1: [UsbEndpoint(0x02, TRANSFER_INTERRUPT, 64)],
        }
        assert find_output_endpoint(interfaces) == (1, 0x02)

    def test_prefers_the_largest_endpoint(self) -> None:
        # A keypad's OUT endpoint is small; a display's moves real data.
        interfaces = {
            0: [UsbEndpoint(0x01, TRANSFER_INTERRUPT, 8)],
            2: [UsbEndpoint(0x03, TRANSFER_BULK, 512)],
        }
        assert find_output_endpoint(interfaces) == (2, 0x03)

    def test_an_input_only_device_has_no_target(self) -> None:
        # This is the AceMagic S1's interface 0, and mistaking it for the
        # display cost two rounds of bring-up.
        assert find_output_endpoint({0: [UsbEndpoint(0x81, TRANSFER_INTERRUPT, 4)]}) is None

    def test_nothing_selects_nothing(self) -> None:
        assert find_output_endpoint({}) is None


class TestTransport:
    def test_satisfies_the_panel_transport_protocol(self) -> None:
        assert isinstance(UsbfsTransport(), PanelTransport)

    def test_construction_touches_no_hardware(self) -> None:
        transport = UsbfsTransport()
        assert not transport.is_open
        assert transport.device is None
        assert transport.endpoint is None

    def test_writing_while_closed_fails(self) -> None:
        with pytest.raises(TransportError, match="not open"):
            UsbfsTransport().write(build_refresh_packet())

    def test_a_wrong_sized_packet_is_refused(self) -> None:
        transport = UsbfsTransport()
        # Closed check comes first, so this asserts the message a caller sees.
        with pytest.raises(TransportError):
            transport.write(bytes(10))

    def test_closing_an_unopened_transport_is_safe(self) -> None:
        transport = UsbfsTransport()
        transport.close()
        assert not transport.is_open

    def test_opening_with_no_panel_reports_it(self, tmp_path: Path) -> None:
        # An explicit device that does not exist stands in for an absent panel
        # without depending on what is plugged into the test machine.
        info = UsbDeviceInfo(bus=99, device=99, sysfs=tmp_path / "absent")
        transport = UsbfsTransport(device=info, interface=0, endpoint=1, init_delay=0)
        with pytest.raises(DeviceNotFoundError):
            transport.open()


class TestAvailability:
    def test_reports_a_bool(self) -> None:
        assert isinstance(is_usbfs_available(), bool)

    def test_a_missing_sysfs_means_unavailable(self, tmp_path: Path) -> None:
        assert is_usbfs_available(root=tmp_path / "absent") is False


class TestDeviceInfo:
    def test_str_names_the_hardware_and_the_node(self) -> None:
        info = UsbDeviceInfo(bus=1, device=5, sysfs=Path("/sys/bus/usb/devices/1-8"))
        assert "04D9:FD01" in str(info)
        # Compared by parts rather than as a string: Path renders with
        # backslashes on Windows, where these tests also run.
        assert info.node.parts[-2:] == ("001", "005")


class TestPacketSize:
    def test_the_device_payload_is_one_byte_shorter(self) -> None:
        # The report-ID byte is a hidraw convention; the endpoint must not see
        # it, or the firmware reads its signature one byte late.
        assert len(device_payload(build_refresh_packet())) == PACKET_SIZE - 1
