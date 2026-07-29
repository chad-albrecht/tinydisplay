"""Tests pinning the standalone probe to the real protocol implementation.

``tools/ht32_standalone_probe.py`` duplicates the framing in
``tinydisplay.ht32.protocol`` on purpose: it has to run on an appliance with
nothing installed, so it cannot import the package. Duplication that nobody
checks is duplication that drifts, and a bring-up tool that disagrees with the
driver is worse than no bring-up tool -- it would send somebody hunting for a
hardware fault that is really a copy-paste error.

So these tests compare the two byte for byte. If the protocol changes and the
probe does not, the suite fails here.
"""

from __future__ import annotations

import ctypes
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tinydisplay.core import Color
from tinydisplay.ht32 import protocol
from tinydisplay.ht32.hidraw import DEFAULT_INIT_DELAY

if TYPE_CHECKING:
    from types import ModuleType

PROBE_PATH = Path(__file__).resolve().parents[2] / "tools" / "ht32_standalone_probe.py"


@pytest.fixture(scope="module")
def probe() -> ModuleType:
    """Import the standalone script by path, as a module."""
    spec = importlib.util.spec_from_file_location("ht32_standalone_probe", PROBE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestConstantsAgree:
    def test_the_file_exists_where_the_docs_say(self) -> None:
        assert PROBE_PATH.is_file()

    @pytest.mark.parametrize(
        "name",
        [
            "PANEL_WIDTH",
            "PANEL_HEIGHT",
            "VENDOR_ID",
            "PRODUCT_ID",
            "SIGNATURE",
            "REPORT_SIZE",
            "HEADER_SIZE",
            "DATA_SIZE",
            "PACKET_SIZE",
            "FRAME_BYTES",
            "CHUNK_COUNT",
            "FINAL_CHUNK_SIZE",
            "PIXELS_PER_CHUNK",
            "LCD_INTERFACE",
        ],
    )
    def test_constant_matches_the_package(self, probe: ModuleType, name: str) -> None:
        assert getattr(probe, name) == getattr(protocol, name), name

    def test_phase_codes_match(self, probe: ModuleType) -> None:
        assert probe.PHASE_START == protocol.RedrawPhase.START
        assert probe.PHASE_CONTINUE == protocol.RedrawPhase.CONTINUE
        assert probe.PHASE_END == protocol.RedrawPhase.END

    def test_redraw_command_matches(self, probe: ModuleType) -> None:
        assert probe.COMMAND_REDRAW == protocol.Command.REDRAW


class TestFramingAgrees:
    def test_every_packet_is_byte_identical(self, probe: ModuleType) -> None:
        # The assertion that actually matters: given the same frame, both
        # implementations must produce the same 27 packets.
        frame = bytes((offset * 7) % 251 for offset in range(protocol.FRAME_BYTES))

        theirs = probe.build_packets(frame)
        ours = protocol.iter_redraw_packets(frame)

        assert len(theirs) == len(ours)
        for index, (mine, other) in enumerate(zip(ours, theirs, strict=True)):
            assert other == mine, f"packet {index} differs"

    def test_phase_selection_agrees(self, probe: ModuleType) -> None:
        for index in range(protocol.CHUNK_COUNT):
            expected = protocol.build_redraw_packet(bytes(protocol.FRAME_BYTES), index)[3]
            assert probe.chunk_phase(index) == expected


class TestPixelPacking:
    def test_rgb565_matches_the_core_conversion(self, probe: ModuleType) -> None:
        for hexcode in ("#ff0000", "#00ff00", "#0000ff", "#ffffff", "#000000", "#3366cc"):
            colour = Color.from_hex(hexcode)
            assert probe.rgb565(colour.r, colour.g, colour.b) == colour.to_rgb565(), hexcode

    def test_red_packs_to_the_expected_value(self, probe: ModuleType) -> None:
        # Spelled out so a byte-order regression is legible in the failure.
        assert probe.rgb565(255, 0, 0) == 0xF800


class TestFrameBuilding:
    @pytest.mark.parametrize("pattern", ["bars", "white", "gradient", "black"])
    def test_frames_are_exactly_one_panel(self, probe: ModuleType, pattern: str) -> None:
        assert len(probe.build_frame(pattern)) == protocol.FRAME_BYTES

    def test_the_leftmost_bar_is_red(self, probe: ModuleType) -> None:
        # This is the claim the printed instructions make to the operator, so
        # it had better be true of the bytes actually sent.
        frame = probe.build_frame("bars")
        assert frame[0:2] == bytes([0xF8, 0x00])

    def test_black_is_black(self, probe: ModuleType) -> None:
        assert probe.build_frame("black") == bytes(protocol.FRAME_BYTES)


class TestNodeSelection:
    def test_prefers_the_display_interface(self, probe: ModuleType) -> None:
        nodes = [(Path("/dev/hidraw0"), 0), (Path("/dev/hidraw1"), protocol.LCD_INTERFACE)]
        assert probe.choose_node(nodes) == Path("/dev/hidraw1")

    def test_falls_back_to_the_first(self, probe: ModuleType) -> None:
        assert probe.choose_node([(Path("/dev/hidraw3"), -1)]) == Path("/dev/hidraw3")

    def test_nothing_selects_nothing(self, probe: ModuleType) -> None:
        assert probe.choose_node([]) is None

    def test_every_node_is_a_candidate(self, probe: ModuleType) -> None:
        # Upstream's interface number does not match real hardware -- an
        # AceMagic S1 publishes interfaces 0 and 2, and no interface 1 -- so
        # every node the panel owns must be tried rather than one guessed at.
        nodes = [(Path("/dev/hidraw0"), 0), (Path("/dev/hidraw1"), 2)]
        assert probe.candidate_order(nodes) == [Path("/dev/hidraw0"), Path("/dev/hidraw1")]

    def test_the_preferred_interface_is_tried_first(self, probe: ModuleType) -> None:
        nodes = [(Path("/dev/hidraw0"), 0), (Path("/dev/hidraw1"), protocol.LCD_INTERFACE)]
        assert probe.candidate_order(nodes)[0] == Path("/dev/hidraw1")

    def test_no_nodes_means_no_candidates(self, probe: ModuleType) -> None:
        assert probe.candidate_order([]) == []


class TestInitCommands:
    """The commands the panel may need before it will accept a frame.

    These are pinned to the documented layout rather than to our driver,
    because the driver does not send them yet -- that is the open question
    these probes exist to answer.
    """

    def test_command_packets_are_full_size(self, probe: ModuleType) -> None:
        assert len(probe.build_orientation()) == protocol.PACKET_SIZE
        assert len(probe.build_heartbeat()) == protocol.PACKET_SIZE

    def test_orientation_matches_the_documented_bytes(self, probe: ModuleType) -> None:
        # Documented as 55 a1 f1 01, after the report ID the kernel strips.
        assert list(probe.build_orientation()[:5]) == [0x00, 0x55, 0xA1, 0xF1, 0x01]

    def test_heartbeat_is_a_set_time_command(self, probe: ModuleType) -> None:
        packet = probe.build_heartbeat()
        assert list(packet[:4]) == [0x00, 0x55, 0xA1, 0xF2]

    def test_heartbeat_carries_a_plausible_clock(self, probe: ModuleType) -> None:
        hour, minute, second = probe.build_heartbeat()[4:7]
        assert 0 <= hour <= 23
        assert 0 <= minute <= 59
        assert 0 <= second <= 60

    def test_command_signature_survives_the_report_id(self, probe: ModuleType) -> None:
        # Byte 0 is stripped by the kernel, so the firmware sees 0x55 first.
        packet = probe.build_command(0xA1, 0xF1)
        assert packet[0] == 0x00
        assert packet[1] == probe.SIGNATURE


class TestHeaderVariant:
    def test_swapping_moves_only_two_bytes(self, probe: ModuleType) -> None:
        frame = bytes(protocol.FRAME_BYTES)
        original = probe.build_packet(frame, 5)
        swapped = probe.build_packet_seq_first(frame, 5)

        assert swapped[3] == original[4]
        assert swapped[4] == original[3]
        assert swapped[:3] == original[:3]
        assert swapped[5:] == original[5:]

    def test_the_variant_is_not_the_default(self, probe: ModuleType) -> None:
        # If these ever agree, the sweep is testing one hypothesis twice.
        frame = bytes(protocol.FRAME_BYTES)
        assert probe.build_packet(frame, 0) != probe.build_packet_seq_first(frame, 0)


class TestSweep:
    def test_every_variant_paints_a_distinct_colour(self, probe: ModuleType) -> None:
        # The whole method depends on the colour identifying the variant.
        colours = [entry[-1] for entry in probe.SWEEP]
        assert len(colours) == len(set(colours))

    def test_every_sweep_colour_is_a_real_pattern(self, probe: ModuleType) -> None:
        for entry in probe.SWEEP:
            assert entry[-1] in probe.SOLIDS

    def test_solid_patterns_fill_the_panel(self, probe: ModuleType) -> None:
        frame = probe.build_frame("red")
        assert len(frame) == protocol.FRAME_BYTES
        assert frame[0:2] == frame[2:4] == bytes([0xF8, 0x00])

    def test_the_leading_hypothesis_is_first(self, probe: ModuleType) -> None:
        # Ordering is the point: the best-supported explanation goes first, so
        # a success needs no further reading. The descriptor says this
        # interface speaks 64-byte reports, so that is the one to try.
        assert probe.SWEEP[0][1] == "reports"

    def test_the_known_failure_is_last(self, probe: ModuleType) -> None:
        # Keeping the already-disproved framing in the sweep is deliberate --
        # it is the control -- but it must not be tried first.
        assert probe.SWEEP[-1][1] == "whole"

    def test_every_framing_is_real(self, probe: ModuleType) -> None:
        for entry in probe.SWEEP:
            assert entry[1] in probe.FRAMINGS


class TestFramings:
    """How a logical packet is split into HID writes.

    The panel's interface declares 64-byte output reports, and every attempt
    before this one sent a single 4104-byte write. These are the readings of
    that mismatch worth offering to the hardware.
    """

    def test_whole_is_a_single_untouched_write(self, probe: ModuleType) -> None:
        packet = probe.build_packet(bytes(protocol.FRAME_BYTES), 0)
        assert probe.frame_whole(packet) == [packet]

    def test_removing_the_report_id_drops_exactly_one_byte(self, probe: ModuleType) -> None:
        packet = probe.build_packet(bytes(protocol.FRAME_BYTES), 0)
        reports = probe.frame_without_report_id(packet)

        assert len(reports) == 1
        assert len(reports[0]) == protocol.PACKET_SIZE - 1
        # The signature must land on the device's byte 0 either way.
        assert reports[0][0] == probe.SIGNATURE

    def test_reports_are_sixty_five_bytes_each(self, probe: ModuleType) -> None:
        packet = probe.build_packet(bytes(protocol.FRAME_BYTES), 0)
        reports = probe.frame_reports(packet)
        assert {len(report) for report in reports} == {probe.HID_REPORT_BYTES + 1}

    def test_bare_reports_are_sixty_four_bytes_each(self, probe: ModuleType) -> None:
        packet = probe.build_packet(bytes(protocol.FRAME_BYTES), 0)
        reports = probe.frame_reports_bare(packet)
        assert {len(report) for report in reports} == {probe.HID_REPORT_BYTES}

    def test_reports_carry_the_whole_packet(self, probe: ModuleType) -> None:
        # Splitting must lose nothing: the payload has to survive reassembly,
        # trailing padding aside.
        frame = bytes((offset * 11) % 251 for offset in range(protocol.FRAME_BYTES))
        packet = probe.build_packet(frame, 0)
        rebuilt = b"".join(report[1:] for report in probe.frame_reports(packet))
        assert rebuilt[: len(packet) - 1] == packet[1:]

    def test_the_last_report_is_padded_not_truncated(self, probe: ModuleType) -> None:
        # 4104 is not a multiple of 64, so the tail needs padding; a short
        # final report would be a different-sized report than declared.
        packet = probe.build_packet(bytes(protocol.FRAME_BYTES), 0)
        reports = probe.frame_reports(packet)
        payload = protocol.PACKET_SIZE - 1
        assert len(reports) == -(-payload // probe.HID_REPORT_BYTES)

    def test_every_framing_preserves_the_signature_position(self, probe: ModuleType) -> None:
        packet = probe.build_packet(bytes(protocol.FRAME_BYTES), 0)
        for name, split in probe.FRAMINGS.items():
            first = split(packet)[0]
            # Either the report ID is present and the signature follows it, or
            # it is absent and the signature leads.
            assert probe.SIGNATURE in (first[0], first[1]), name


class TestReportDescriptorParsing:
    """The parser is a diagnostic, so it must not invent findings.

    A wrong answer here would send somebody rewriting a protocol that was
    already correct, so the cases below are the ones that decide which
    interface gets written to.
    """

    def test_an_output_report_is_measured_in_bytes(self, probe: ModuleType) -> None:
        # Report Size 8 bits, Report Count 4104, Output.
        descriptor = bytes([0x75, 0x08, 0x96, 0x08, 0x10, 0x91, 0x02])
        reports = probe.parse_report_descriptor(descriptor)
        assert reports["output"] == [(0, 4104)]

    def test_report_ids_are_kept_apart(self, probe: ModuleType) -> None:
        descriptor = bytes(
            [
                0x85,
                0x01,  # Report ID 1
                0x75,
                0x08,
                0x95,
                0x40,
                0x91,
                0x02,  # 64 bytes output
                0x85,
                0x02,  # Report ID 2
                0x75,
                0x08,
                0x95,
                0x08,
                0x91,
                0x02,  # 8 bytes output
            ]
        )
        assert probe.parse_report_descriptor(descriptor)["output"] == [(1, 64), (2, 8)]

    def test_input_and_output_are_not_confused(self, probe: ModuleType) -> None:
        # A consumer-control interface has inputs and no outputs -- writing to
        # it is accepted and ignored, which is the failure being diagnosed.
        descriptor = bytes([0x75, 0x08, 0x95, 0x03, 0x81, 0x02])
        reports = probe.parse_report_descriptor(descriptor)
        assert reports["input"] == [(0, 3)]
        assert reports["output"] == []

    def test_repeated_output_items_accumulate(self, probe: ModuleType) -> None:
        descriptor = bytes([0x75, 0x08, 0x95, 0x04, 0x91, 0x02, 0x91, 0x02])
        assert probe.parse_report_descriptor(descriptor)["output"] == [(0, 8)]

    def test_bit_sized_reports_round_up_to_whole_bytes(self, probe: ModuleType) -> None:
        descriptor = bytes([0x75, 0x01, 0x95, 0x03, 0x91, 0x02])
        assert probe.parse_report_descriptor(descriptor)["output"] == [(0, 1)]

    def test_a_four_byte_item_is_read(self, probe: ModuleType) -> None:
        # bSize 3 means four data bytes, not three.
        descriptor = bytes([0x75, 0x08, 0x97, 0x08, 0x10, 0x00, 0x00, 0x91, 0x02])
        assert probe.parse_report_descriptor(descriptor)["output"] == [(0, 4104)]

    def test_an_empty_descriptor_yields_nothing(self, probe: ModuleType) -> None:
        assert probe.parse_report_descriptor(b"") == {
            "input": [],
            "output": [],
            "feature": [],
        }

    def test_a_truncated_descriptor_does_not_hang_or_raise(self, probe: ModuleType) -> None:
        # Reading sysfs can hand back anything; a diagnostic that crashes on
        # bad input is a diagnostic that stops the investigation.
        for cut in range(1, 8):
            probe.parse_report_descriptor(bytes([0x75, 0x08, 0x95, 0x40, 0x91, 0x02])[:cut])

    def test_a_long_item_is_skipped(self, probe: ModuleType) -> None:
        descriptor = bytes([0xFE, 0x02, 0x00, 0xAA, 0xBB, 0x75, 0x08, 0x95, 0x02, 0x91, 0x02])
        assert probe.parse_report_descriptor(descriptor)["output"] == [(0, 2)]


class TestUsbfsIoctls:
    """The ioctl numbers must match the kernel's, or nothing works.

    These are pure arithmetic, so they can be checked anywhere -- which
    matters, because the machine that can actually exercise them is the one
    machine where a mistake is expensive to debug. The expected values are the
    published constants from linux/usbdevice_fs.h on 64-bit.
    """

    def test_claim_interface(self, probe: ModuleType) -> None:
        assert probe.USBDEVFS_CLAIMINTERFACE == 0x8004550F

    def test_release_interface(self, probe: ModuleType) -> None:
        assert probe.USBDEVFS_RELEASEINTERFACE == 0x80045510

    def test_disconnect(self, probe: ModuleType) -> None:
        assert probe.USBDEVFS_DISCONNECT == 0x5516

    @pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="64-bit layout")
    def test_bulk_transfer_struct_is_the_kernel_layout(self, probe: ModuleType) -> None:
        # 4 + 4 + 4 + padding + 8. Getting the padding wrong changes the ioctl
        # number and the kernel rejects the call outright.
        assert ctypes.sizeof(probe._BulkTransfer) == 24
        assert probe.USBDEVFS_BULK == 0xC0185502

    @pytest.mark.skipif(ctypes.sizeof(ctypes.c_void_p) != 8, reason="64-bit layout")
    def test_devfs_ioctl_struct_is_the_kernel_layout(self, probe: ModuleType) -> None:
        assert ctypes.sizeof(probe._DevfsIoctl) == 16
        assert probe.USBDEVFS_IOCTL == 0xC0105512

    def test_the_encoder_matches_the_macro(self, probe: ModuleType) -> None:
        # _IOR('U', 15, unsigned int), spelled out.
        assert probe._ioc(2, "U", 15, 4) == (2 << 30) | (4 << 16) | (0x55 << 8) | 15


class TestEndpointSelection:
    def test_picks_an_interrupt_out_endpoint(self, probe: ModuleType) -> None:
        interfaces = [
            {
                "number": 0,
                "endpoints": [{"address": 0x81, "kind": 3, "packet_size": 8, "direction": "in"}],
            },
            {
                "number": 1,
                "endpoints": [{"address": 0x02, "kind": 3, "packet_size": 64, "direction": "out"}],
            },
        ]
        assert probe.find_output_endpoint(interfaces) == (1, 0x02)

    def test_ignores_in_endpoints(self, probe: ModuleType) -> None:
        interfaces = [
            {
                "number": 0,
                "endpoints": [{"address": 0x81, "kind": 3, "packet_size": 64, "direction": "in"}],
            }
        ]
        assert probe.find_output_endpoint(interfaces) is None

    def test_prefers_the_larger_endpoint(self, probe: ModuleType) -> None:
        # A keypad's OUT endpoint is small; the display's moves real data.
        interfaces = [
            {
                "number": 0,
                "endpoints": [{"address": 0x01, "kind": 3, "packet_size": 8, "direction": "out"}],
            },
            {
                "number": 2,
                "endpoints": [{"address": 0x03, "kind": 2, "packet_size": 512, "direction": "out"}],
            },
        ]
        assert probe.find_output_endpoint(interfaces) == (2, 0x03)

    def test_ignores_control_and_isochronous(self, probe: ModuleType) -> None:
        interfaces = [
            {
                "number": 0,
                "endpoints": [{"address": 0x01, "kind": 1, "packet_size": 64, "direction": "out"}],
            }
        ]
        assert probe.find_output_endpoint(interfaces) is None

    def test_no_interfaces_selects_nothing(self, probe: ModuleType) -> None:
        assert probe.find_output_endpoint([]) is None


class TestInitDelay:
    def test_matches_the_package_default(self, probe: ModuleType) -> None:
        # The standalone copy waiting a different amount than the driver would
        # make a bring-up result meaningless.
        assert probe.DEFAULT_INIT_DELAY == DEFAULT_INIT_DELAY
