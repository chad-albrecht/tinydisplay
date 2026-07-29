"""Tests for the HT32 transports.

The recorder is exercised as a transport in its own right rather than as test
scaffolding, because the driver tests lean on it entirely: if the recorder's
lifecycle or failure behaviour is wrong, every reconnection test above it is
testing the wrong thing.
"""

from __future__ import annotations

import pytest

from tinydisplay.ht32 import (
    DeviceNotFoundError,
    HidTransport,
    PanelTransport,
    RecordingHidTransport,
    TransportError,
)
from tinydisplay.ht32.protocol import FRAME_BYTES, PACKET_SIZE, iter_redraw_packets
from tinydisplay.ht32.transport import packet_summary


class TestProtocolConformance:
    def test_recorder_satisfies_the_transport_protocol(self) -> None:
        assert isinstance(RecordingHidTransport(), PanelTransport)

    def test_hid_transport_satisfies_the_transport_protocol(self) -> None:
        # Constructing does not touch USB -- discovery is deferred to open().
        assert isinstance(HidTransport(), PanelTransport)


class TestRecordingTransport:
    def test_starts_closed_and_empty(self) -> None:
        transport = RecordingHidTransport()
        assert not transport.is_open
        assert transport.packets == ()
        assert transport.last_packet is None
        assert transport.write_count == 0

    def test_open_is_idempotent(self) -> None:
        transport = RecordingHidTransport()
        transport.open()
        transport.open()
        assert transport.open_count == 1

    def test_reopening_after_close_counts_again(self) -> None:
        transport = RecordingHidTransport()
        transport.open()
        transport.close()
        transport.open()
        assert transport.open_count == 2

    def test_records_packets_in_order(self) -> None:
        transport = RecordingHidTransport()
        transport.open()
        transport.write(b"first")
        transport.write(b"second")
        assert transport.packets == (b"first", b"second")
        assert transport.last_packet == b"second"

    def test_writing_while_closed_fails(self) -> None:
        transport = RecordingHidTransport()
        with pytest.raises(TransportError, match="not open"):
            transport.write(b"anything")

    def test_max_packets_retains_the_most_recent(self) -> None:
        transport = RecordingHidTransport(max_packets=2)
        transport.open()
        for value in (b"a", b"b", b"c"):
            transport.write(value)
        assert transport.packets == (b"b", b"c")
        assert transport.write_count == 3

    def test_fail_after_stops_accepting_and_closes(self) -> None:
        transport = RecordingHidTransport(fail_after=1)
        transport.open()
        transport.write(b"ok")

        with pytest.raises(TransportError, match="simulated write failure"):
            transport.write(b"dropped")
        # An unplugged panel does not stay open, and neither does this.
        assert not transport.is_open

    def test_fail_on_open_looks_like_an_absent_panel(self) -> None:
        transport = RecordingHidTransport(fail_on_open=True)
        with pytest.raises(DeviceNotFoundError):
            transport.open()

    def test_reset_keeps_lifecycle_counters(self) -> None:
        transport = RecordingHidTransport()
        transport.open()
        transport.write(b"a")
        transport.reset()
        assert transport.packets == ()
        assert transport.write_count == 1
        assert transport.open_count == 1


class TestHidTransport:
    def test_writing_while_closed_fails(self) -> None:
        transport = HidTransport()
        with pytest.raises(TransportError, match="not open"):
            transport.write(bytes(PACKET_SIZE))

    def test_closing_an_unopened_transport_is_safe(self) -> None:
        transport = HidTransport()
        transport.close()
        assert not transport.is_open

    def test_device_is_unknown_until_opened(self) -> None:
        assert HidTransport().device is None


class TestPacketSummary:
    def test_summarises_a_frame(self) -> None:
        packets = iter_redraw_packets(bytes(FRAME_BYTES))
        summary = packet_summary(packets)
        assert "27 packets" in summary
        assert "f0..f2" in summary

    def test_handles_no_packets(self) -> None:
        assert packet_summary([]) == "0 packets"
