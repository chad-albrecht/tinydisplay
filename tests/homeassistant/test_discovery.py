"""Tests for the config flow's discovery helpers.

These are the only part of the custom component that can be imported without
Home Assistant: ``discovery.py`` deliberately touches nothing from it, so the
logic behind the setup form -- which dashboards to offer, which panel to
preselect -- is testable rather than assumed.

The behaviour worth protecting is that the form only ever offers paths that
will work. A dropdown listing every YAML file in a Home Assistant config is
worse than a text box, because most of them belong to somebody else.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tinydisplay.homeassistant import Dashboard
from tinydisplay.ht32 import usbfs

if TYPE_CHECKING:
    from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT = REPO_ROOT / "custom_components" / "tinydisplay"

VALID = """
theme: midnight
root:
  type: label
  text: "{{ sun.sun }}"
"""


@pytest.fixture(scope="module")
def discovery() -> ModuleType:
    """Import discovery.py by path, with const.py available as its sibling."""
    for name in ("const", "discovery"):
        spec = importlib.util.spec_from_file_location(
            f"_tinydisplay_{name}", COMPONENT / f"{name}.py"
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # discovery.py does `from .const import ...`, so it needs a package
        # context; giving it one by hand is cheaper than importing the whole
        # component, which would drag in Home Assistant.
        sys.modules[f"_tinydisplay_{name}"] = module
        if name == "discovery":
            source = (COMPONENT / "discovery.py").read_text(encoding="utf-8")
            source = source.replace("from .const import", "from _tinydisplay_const import")
            exec(compile(source, str(COMPONENT / "discovery.py"), "exec"), module.__dict__)
        else:
            spec.loader.exec_module(module)
    return sys.modules["_tinydisplay_discovery"]


class TestStarterDashboard:
    def test_the_shipped_starter_parses(self) -> None:
        # The starter is copied to a user's config on first setup. If it does
        # not parse, their first experience of the integration is an error.
        dashboard = Dashboard.load(COMPONENT / "starter_dashboard.yaml")
        assert dashboard.entity_ids == {"sun.sun"}

    def test_the_starter_reads_only_universal_entities(self) -> None:
        # sun.sun exists on every Home Assistant with no configuration, which
        # is what lets the starter show real moving data rather than dashes.
        assert Dashboard.load(COMPONENT / "starter_dashboard.yaml").entity_ids <= {"sun.sun"}

    def test_it_is_written_when_absent(self, discovery: ModuleType, tmp_path: Path) -> None:
        written = discovery.ensure_starter_dashboard(str(tmp_path))
        assert written is not None
        assert written.is_file()
        assert written == tmp_path / "tinydisplay" / "dashboard.yaml"

    def test_an_existing_file_is_never_overwritten(
        self, discovery: ModuleType, tmp_path: Path
    ) -> None:
        # Someone who has edited the starter has made it theirs.
        destination = tmp_path / "tinydisplay" / "dashboard.yaml"
        destination.parent.mkdir(parents=True)
        destination.write_text("mine", encoding="utf-8")

        discovery.ensure_starter_dashboard(str(tmp_path))
        assert destination.read_text(encoding="utf-8") == "mine"


class TestFindDashboards:
    def test_finds_a_valid_dashboard(self, discovery: ModuleType, tmp_path: Path) -> None:
        (tmp_path / "panel.yaml").write_text(VALID, encoding="utf-8")
        assert str(tmp_path / "panel.yaml") in discovery.find_dashboards(str(tmp_path))

    def test_skips_files_that_do_not_parse(self, discovery: ModuleType, tmp_path: Path) -> None:
        # The whole point of the dropdown: everything in it works.
        (tmp_path / "good.yaml").write_text(VALID, encoding="utf-8")
        (tmp_path / "bad.yaml").write_text("root:\n  type: nonsense\n", encoding="utf-8")

        found = discovery.find_dashboards(str(tmp_path))
        assert str(tmp_path / "good.yaml") in found
        assert str(tmp_path / "bad.yaml") not in found

    def test_skips_home_assistants_own_yaml(self, discovery: ModuleType, tmp_path: Path) -> None:
        # These never parse anyway; skipping by name avoids offering a
        # tempting-looking configuration.yaml in the list.
        (tmp_path / "configuration.yaml").write_text(VALID, encoding="utf-8")
        (tmp_path / "secrets.yaml").write_text(VALID, encoding="utf-8")
        assert discovery.find_dashboards(str(tmp_path)) == []

    def test_searches_the_tinydisplay_subdirectory(
        self, discovery: ModuleType, tmp_path: Path
    ) -> None:
        nested = tmp_path / "tinydisplay"
        nested.mkdir()
        (nested / "dashboard.yaml").write_text(VALID, encoding="utf-8")
        assert str(nested / "dashboard.yaml") in discovery.find_dashboards(str(tmp_path))

    def test_accepts_the_yml_spelling(self, discovery: ModuleType, tmp_path: Path) -> None:
        (tmp_path / "panel.yml").write_text(VALID, encoding="utf-8")
        assert str(tmp_path / "panel.yml") in discovery.find_dashboards(str(tmp_path))

    def test_a_missing_directory_is_not_an_error(
        self, discovery: ModuleType, tmp_path: Path
    ) -> None:
        assert discovery.find_dashboards(str(tmp_path / "nope")) == []


class TestDriverDetection:
    def test_falls_back_to_preview_without_a_panel(
        self, discovery: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Defaulting to the HT32 on a machine that has none makes the first
        # thing a new user does fail.
        monkeypatch.setattr(usbfs, "find_usb_panel", lambda **_: None)
        assert discovery.detect_default_driver() == "memory"

    def test_prefers_the_panel_when_one_is_attached(
        self, discovery: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        info = usbfs.UsbDeviceInfo(bus=1, device=6, sysfs=Path("/sys/bus/usb/devices/1-8"))
        monkeypatch.setattr(usbfs, "find_usb_panel", lambda **_: info)
        assert discovery.detect_default_driver() == "ht32"

    def test_an_unreadable_bus_falls_back(
        self, discovery: ModuleType, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(**_: object) -> None:
            message = "no sysfs here"
            raise OSError(message)

        monkeypatch.setattr(usbfs, "find_usb_panel", boom)
        assert discovery.detect_default_driver() == "memory"
