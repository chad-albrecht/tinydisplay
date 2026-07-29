"""Tests for the HT32 bring-up command line.

The CLI is the only tool available on a headless box with the panel soldered
into it, so its failure paths matter more than its success path: a wrong exit
status or a swallowed message costs somebody a debugging session they cannot
easily instrument.
"""

from __future__ import annotations

import pytest

from tinydisplay.core import Canvas, Color
from tinydisplay.ht32 import DeviceNotFoundError, HT32DeviceInfo
from tinydisplay.ht32 import __main__ as cli
from tinydisplay.ht32.__main__ import build_parser, main
from tinydisplay.ht32.patterns import PATTERNS, draw_pattern
from tinydisplay.ht32.protocol import (
    CHUNK_COUNT,
    LCD_INTERFACE,
    PANEL_HEIGHT,
    PANEL_WIDTH,
    PIXELS_PER_CHUNK,
)


def panel_info(interface: int = LCD_INTERFACE) -> HT32DeviceInfo:
    return HT32DeviceInfo(b"/dev/hidraw1", 0x04D9, 0xFD01, interface, product_string="HT32")


@pytest.fixture
def no_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing on the bus, but a working USB backend.

    Patched in the CLI's namespace, not the device module's: ``__main__``
    imports these by name, so rebinding them anywhere else has no effect.
    """
    monkeypatch.setattr(cli, "enumerate_panels", lambda **_: ())
    monkeypatch.setattr(cli, "is_hid_available", lambda: True)


class TestParser:
    def test_a_subcommand_is_required(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args([])

    def test_frame_defaults_to_the_byte_order_pattern(self) -> None:
        # 'bars' is the pattern that catches the failure we most expect.
        assert build_parser().parse_args(["frame"]).pattern == "bars"

    def test_every_pattern_is_selectable(self) -> None:
        for name in PATTERNS:
            assert build_parser().parse_args(["frame", "--pattern", name]).pattern == name

    def test_an_unknown_pattern_is_refused(self) -> None:
        with pytest.raises(SystemExit):
            build_parser().parse_args(["frame", "--pattern", "nonsense"])


class TestProbe:
    def test_reports_a_missing_backend_distinctly(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # "no driver" and "no device" are different problems with different
        # fixes, so they must not share an exit status.
        monkeypatch.setattr(cli, "is_hid_available", lambda: False)

        assert main(["probe"]) == 2
        assert "NOT INSTALLED" in capsys.readouterr().out

    @pytest.mark.usefixtures("no_panel")
    def test_nothing_attached_exits_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["probe"]) == 1
        assert "NOT FOUND" in capsys.readouterr().out

    @pytest.mark.usefixtures("no_panel")
    def test_nothing_attached_mentions_permissions(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        main(["probe"])
        assert "udev" in capsys.readouterr().out

    def test_lists_interfaces_and_marks_the_display(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(cli, "is_hid_available", lambda: True)
        monkeypatch.setattr(
            cli,
            "enumerate_panels",
            lambda **_: (panel_info(0), panel_info(LCD_INTERFACE)),
        )

        assert main(["probe"]) == 0
        out = capsys.readouterr().out
        assert "2 interface(s)" in out
        assert "<- display" in out

    def test_an_enumeration_failure_is_reported(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def boom(**_: object) -> tuple[HT32DeviceInfo, ...]:
            msg = "hidapi exploded"
            raise DeviceNotFoundError(msg)

        monkeypatch.setattr(cli, "is_hid_available", lambda: True)
        monkeypatch.setattr(cli, "enumerate_panels", boom)

        assert main(["probe"]) == 2
        assert "hidapi exploded" in capsys.readouterr().out


class TestFrame:
    def test_dry_run_needs_no_hardware(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["frame", "--dry-run"]) == 0
        assert "nothing sent" in capsys.readouterr().out

    def test_dry_run_reports_the_packet_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        main(["frame", "--dry-run"])
        assert f"{CHUNK_COUNT} packets" in capsys.readouterr().out

    def test_repeat_sends_several_frames(self, capsys: pytest.CaptureFixture[str]) -> None:
        main(["frame", "--dry-run", "--repeat", "3"])
        assert "3 frame(s)" in capsys.readouterr().out

    @pytest.mark.parametrize("pattern", sorted(PATTERNS))
    def test_every_pattern_renders(self, pattern: str) -> None:
        assert main(["frame", "--dry-run", "--pattern", pattern]) == 0

    @pytest.mark.usefixtures("no_panel")
    def test_an_absent_panel_exits_nonzero(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["frame"]) == 1
        assert "error:" in capsys.readouterr().out


class TestLed:
    def test_dry_run_prints_the_packet(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["led", "--dry-run", "--theme", "off"]) == 0
        # 0xFA signature, 0x04 off, inverted levels, checksum.
        assert "fa 04" in capsys.readouterr().out

    def test_an_out_of_range_level_is_reported(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["led", "--dry-run", "--intensity", "9"]) == 1
        assert "intensity must be between" in capsys.readouterr().out


class TestPatterns:
    def test_colour_bars_put_red_where_the_label_says(self) -> None:
        # The whole point of the pattern: this pixel is the byte-order check.
        canvas = Canvas(PANEL_WIDTH, PANEL_HEIGHT)
        draw_pattern(canvas, "bars")
        assert canvas.get_pixel(4, 4) == Color.from_hex("#ff0000")

    def test_chunk_marks_change_shade_at_a_chunk_boundary(self) -> None:
        canvas = Canvas(PANEL_WIDTH, PANEL_HEIGHT)
        draw_pattern(canvas, "chunks")

        last_of_first = divmod(PIXELS_PER_CHUNK - 1, canvas.width)
        first_of_second = divmod(PIXELS_PER_CHUNK, canvas.width)
        assert canvas.get_pixel(last_of_first[1], last_of_first[0]) != canvas.get_pixel(
            first_of_second[1], first_of_second[0]
        )

    def test_solid_fills_the_whole_panel(self) -> None:
        canvas = Canvas(PANEL_WIDTH, PANEL_HEIGHT)
        draw_pattern(canvas, "solid")
        assert canvas.get_pixel(0, 0) == canvas.get_pixel(PANEL_WIDTH - 1, PANEL_HEIGHT - 1)

    def test_an_unknown_pattern_raises(self) -> None:
        with pytest.raises(KeyError):
            draw_pattern(Canvas(8, 8), "nonsense")
