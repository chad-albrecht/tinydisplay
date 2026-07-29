"""Tests for the HT32 wire protocol.

The protocol module does no I/O, so these are the tests that can be exhaustive:
every chunk of a full frame is built and inspected byte by byte. Framing is the
part of a driver most likely to be subtly wrong, and the part a panel gives the
least feedback about -- a bad packet produces a blank screen, not an error.
"""

from __future__ import annotations

import pytest

from tinydisplay.core import Canvas, Color, PixelFormat
from tinydisplay.ht32 import ProtocolError
from tinydisplay.ht32.protocol import (
    CHUNK_COUNT,
    CHUNK_PIXEL_OFFSETS,
    CHUNK_SIZES,
    DATA_SIZE,
    FINAL_CHUNK_SIZE,
    FRAME_BYTES,
    HEADER_SIZE,
    PACKET_SIZE,
    PANEL_HEIGHT,
    PANEL_PIXEL_FORMAT,
    PANEL_WIDTH,
    PRODUCT_ID,
    REPORT_SIZE,
    SIGNATURE,
    VENDOR_ID,
    Command,
    RedrawPhase,
    SubCommand,
    build_config_packet,
    build_redraw_packet,
    build_refresh_packet,
    iter_redraw_packets,
)

DATA_START = REPORT_SIZE + HEADER_SIZE


def blank_frame() -> bytes:
    return bytes(FRAME_BYTES)


def counting_frame() -> bytes:
    """A frame whose every byte is a function of its offset.

    Makes a misplaced chunk obvious: any packet carrying the wrong slice shows
    up as a value that does not match its own position.
    """
    return bytes(offset % 251 for offset in range(FRAME_BYTES))


class TestConstants:
    def test_panel_geometry_matches_the_documented_device(self) -> None:
        assert (PANEL_WIDTH, PANEL_HEIGHT) == (320, 170)
        assert (VENDOR_ID, PRODUCT_ID) == (0x04D9, 0xFD01)

    def test_panel_takes_big_endian_rgb565(self) -> None:
        assert PANEL_PIXEL_FORMAT is PixelFormat.RGB565_BE

    def test_frame_is_two_bytes_per_pixel(self) -> None:
        assert FRAME_BYTES == PANEL_WIDTH * PANEL_HEIGHT * 2
        assert FRAME_BYTES == 108_800

    def test_chunk_geometry_reproduces_the_upstream_figures(self) -> None:
        # Derived here, hard-coded upstream. If the arithmetic and the
        # documented values ever disagree, that is worth knowing.
        assert CHUNK_COUNT == 27
        assert FINAL_CHUNK_SIZE == 2304
        assert PACKET_SIZE == 4105

    def test_chunk_sizes_sum_to_exactly_one_frame(self) -> None:
        assert sum(CHUNK_SIZES) == FRAME_BYTES

    def test_only_the_last_chunk_is_short(self) -> None:
        assert set(CHUNK_SIZES[:-1]) == {DATA_SIZE}
        assert CHUNK_SIZES[-1] == FINAL_CHUNK_SIZE

    def test_pixel_offsets_fit_the_sixteen_bit_field(self) -> None:
        # The reason the offset is counted in pixels rather than bytes: a byte
        # offset would overflow this field partway through the frame.
        assert max(CHUNK_PIXEL_OFFSETS) <= 0xFFFF
        assert (CHUNK_COUNT - 1) * DATA_SIZE > 0xFFFF


class TestRedrawPacket:
    def test_packet_is_always_the_full_report_size(self) -> None:
        for index in range(CHUNK_COUNT):
            packet = build_redraw_packet(blank_frame(), index)
            assert len(packet) == PACKET_SIZE

    def test_header_fields(self) -> None:
        packet = build_redraw_packet(blank_frame(), 0)
        assert packet[0] == 0x00  # HID report ID
        assert packet[1] == SIGNATURE
        assert packet[2] == Command.REDRAW
        assert packet[5] == 0x00  # reserved

    def test_phases_bracket_the_frame(self) -> None:
        packets = iter_redraw_packets(blank_frame())
        assert packets[0][3] == RedrawPhase.START
        assert packets[-1][3] == RedrawPhase.END
        assert {packet[3] for packet in packets[1:-1]} == {RedrawPhase.CONTINUE}

    def test_sequence_numbers_are_one_based_and_contiguous(self) -> None:
        packets = iter_redraw_packets(blank_frame())
        assert [packet[4] for packet in packets] == list(range(1, CHUNK_COUNT + 1))

    def test_sequence_number_still_fits_a_byte(self) -> None:
        assert CHUNK_COUNT <= 0xFF

    def test_pixel_offset_is_big_endian(self) -> None:
        for index in range(CHUNK_COUNT):
            packet = build_redraw_packet(blank_frame(), index)
            offset = (packet[6] << 8) | packet[7]
            assert offset == CHUNK_PIXEL_OFFSETS[index]

    def test_chunk_size_high_byte(self) -> None:
        packets = iter_redraw_packets(blank_frame())
        assert packets[0][8] == DATA_SIZE >> 8
        assert packets[-1][8] == FINAL_CHUNK_SIZE >> 8

    def test_both_chunk_sizes_have_a_zero_low_byte(self) -> None:
        # This is what makes it safe to let pixel data own byte 9, and the
        # reason upstream's overwritten low-byte store is harmless.
        assert DATA_SIZE % 256 == 0
        assert FINAL_CHUNK_SIZE % 256 == 0

    def test_payload_is_the_matching_slice_of_the_frame(self) -> None:
        frame = counting_frame()
        for index in range(CHUNK_COUNT):
            packet = build_redraw_packet(frame, index)
            size = CHUNK_SIZES[index]
            start = index * DATA_SIZE
            assert packet[DATA_START : DATA_START + size] == frame[start : start + size]

    def test_every_frame_byte_is_transmitted_exactly_once(self) -> None:
        frame = counting_frame()
        rebuilt = bytearray()
        for index, packet in enumerate(iter_redraw_packets(frame)):
            rebuilt += packet[DATA_START : DATA_START + CHUNK_SIZES[index]]
        assert bytes(rebuilt) == frame

    def test_final_chunk_is_zero_padded(self) -> None:
        packet = build_redraw_packet(counting_frame(), CHUNK_COUNT - 1)
        assert packet[DATA_START + FINAL_CHUNK_SIZE :] == bytes(DATA_SIZE - FINAL_CHUNK_SIZE)

    def test_pixel_bytes_are_not_swapped(self) -> None:
        # Core packs RGB565_BE; the chunker copies slices verbatim. A byte swap
        # anywhere in that path would show up as a wrong first pixel here.
        canvas = Canvas(PANEL_WIDTH, PANEL_HEIGHT)
        canvas.clear(Color.from_hex("#ff0000"))
        frame = canvas.to_rgb565(byte_order="big")
        packet = build_redraw_packet(frame, 0)
        assert packet[DATA_START : DATA_START + 2] == frame[:2]
        assert packet[DATA_START] == 0xF8  # red's high byte in RGB565

    def test_wrong_frame_size_is_rejected(self) -> None:
        with pytest.raises(ProtocolError, match="expects a 108800-byte"):
            build_redraw_packet(bytes(FRAME_BYTES - 1), 0)

    @pytest.mark.parametrize("index", [-1, CHUNK_COUNT, CHUNK_COUNT + 1])
    def test_out_of_range_chunk_index_is_rejected(self, index: int) -> None:
        with pytest.raises(ProtocolError, match="chunk index must be"):
            build_redraw_packet(blank_frame(), index)


class TestIterRedrawPackets:
    def test_builds_one_packet_per_chunk(self) -> None:
        assert len(iter_redraw_packets(blank_frame())) == CHUNK_COUNT

    def test_returns_a_materialised_sequence_not_a_generator(self) -> None:
        # A frame is validated in full before any of it is written, so a bad
        # frame cannot leave the panel waiting for an end phase.
        packets = iter_redraw_packets(blank_frame())
        assert isinstance(packets, tuple)

    def test_a_bad_frame_fails_before_producing_anything(self) -> None:
        with pytest.raises(ProtocolError):
            iter_redraw_packets(bytes(10))


class TestOtherPackets:
    def test_refresh_packet(self) -> None:
        packet = build_refresh_packet()
        assert len(packet) == PACKET_SIZE
        assert packet[1] == SIGNATURE
        assert packet[2] == Command.REFRESH

    def test_config_packet_carries_its_parameters(self) -> None:
        packet = build_config_packet(SubCommand.ORIENTATION, bytes([0x01, 0x02]))
        assert packet[2] == Command.CONFIG
        assert packet[3] == SubCommand.ORIENTATION
        assert packet[4:6] == bytes([0x01, 0x02])

    def test_config_packet_accepts_a_raw_sub_command(self) -> None:
        # Bring-up work needs to probe undocumented sub-commands.
        packet = build_config_packet(0xF9)
        assert packet[3] == 0xF9

    def test_config_parameters_must_fit(self) -> None:
        with pytest.raises(ProtocolError, match="must fit"):
            build_config_packet(SubCommand.SET_TIME, bytes(PACKET_SIZE))
