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


class TestInitDelay:
    def test_matches_the_package_default(self, probe: ModuleType) -> None:
        # The standalone copy waiting a different amount than the driver would
        # make a bring-up result meaningless.
        assert probe.DEFAULT_INIT_DELAY == DEFAULT_INIT_DELAY
