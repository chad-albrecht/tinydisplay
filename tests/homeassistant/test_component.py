"""Tests for the Home Assistant custom component's packaging and boundaries.

The component itself cannot be imported here -- doing that needs Home
Assistant, which is deliberately not a dependency of this workspace. What *can*
be checked without it is everything that fails silently at runtime: a manifest
Home Assistant will refuse to load, translations that have drifted from the
strings they mirror, a requirement pinned to a version that no longer exists,
and -- most importantly -- a library package that has quietly started importing
``homeassistant``.

That last one is the rule the whole repository is arranged around, so it is
asserted rather than assumed.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPONENT = REPO_ROOT / "custom_components" / "tinydisplay"
PACKAGES = REPO_ROOT / "packages"

#: Home Assistant's own name. ``tinydisplay.homeassistant`` is ours and is not
#: this, which is why the check is on the root module rather than a substring.
HA_ROOT = "homeassistant"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def imported_roots(source: Path) -> set[str]:
    """Every top-level module name ``source`` imports."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    data = load_json(COMPONENT / "manifest.json")
    assert isinstance(data, dict)
    return data


@pytest.fixture(scope="module")
def const() -> Any:
    """Load ``const.py`` directly, bypassing the package's Home Assistant imports."""
    spec = importlib.util.spec_from_file_location("_tinydisplay_const", COMPONENT / "const.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestLayout:
    def test_the_component_lives_where_home_assistant_looks(self) -> None:
        # HACS offers no path override for integrations: the directory must be
        # custom_components/<domain>/ at the repository root, or it will not
        # install at all.
        assert COMPONENT.is_dir()

    @pytest.mark.parametrize(
        "name",
        [
            "__init__.py",
            "const.py",
            "config_flow.py",
            "runtime.py",
            "discovery.py",
            "image.py",
            "manifest.json",
            "strings.json",
            "starter_dashboard.yaml",
        ],
    )
    def test_required_files_exist(self, name: str) -> None:
        assert (COMPONENT / name).is_file()

    def test_every_module_parses(self) -> None:
        # The suite cannot import these, so this is the cheapest guard against
        # committing a file that will not even compile inside Home Assistant.
        for source in COMPONENT.glob("*.py"):
            ast.parse(source.read_text(encoding="utf-8"), filename=str(source))


class TestManifest:
    @pytest.mark.parametrize(
        "key",
        [
            "domain",
            "name",
            "codeowners",
            "config_flow",
            "documentation",
            "iot_class",
            "requirements",
            "version",
        ],
    )
    def test_required_key_is_present(self, manifest: dict[str, Any], key: str) -> None:
        assert key in manifest

    def test_domain_matches_the_directory(self, manifest: dict[str, Any]) -> None:
        assert manifest["domain"] == COMPONENT.name

    def test_domain_matches_the_constant(self, manifest: dict[str, Any], const: Any) -> None:
        assert manifest["domain"] == const.DOMAIN

    def test_declares_a_config_flow(self, manifest: dict[str, Any]) -> None:
        # config_flow: true is what makes the integration appear in the "Add
        # integration" list; without it the config_flow module is never loaded.
        assert manifest["config_flow"] is True

    def test_version_is_a_release_number(self, manifest: dict[str, Any]) -> None:
        # Custom integrations must carry a version, and Home Assistant refuses
        # to load ones it cannot parse.
        assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])

    def test_iot_class_is_one_home_assistant_knows(self, manifest: dict[str, Any]) -> None:
        assert manifest["iot_class"] in {
            "assumed_state",
            "cloud_polling",
            "cloud_push",
            "local_polling",
            "local_push",
            "calculated",
        }

    def test_requirements_are_pinned(self, manifest: dict[str, Any]) -> None:
        assert manifest["requirements"]
        for requirement in manifest["requirements"]:
            assert "==" in requirement, f"{requirement} is not pinned"

    @pytest.mark.parametrize(
        ("distribution", "package"),
        [
            ("tinydisplay-homeassistant", "homeassistant"),
            ("tinydisplay-ht32", "ht32"),
        ],
    )
    def test_requirement_versions_match_the_workspace(
        self,
        manifest: dict[str, Any],
        distribution: str,
        package: str,
    ) -> None:
        # A pin that has drifted past the version actually in this repository
        # would install something that does not exist, and would do it only on
        # a user's machine.
        pyproject = tomllib.loads(
            (PACKAGES / package / "pyproject.toml").read_text(encoding="utf-8")
        )
        expected = f"{distribution}=={pyproject['project']['version']}"
        assert expected in manifest["requirements"]


class TestTranslations:
    def test_english_translations_exist(self) -> None:
        assert (COMPONENT / "translations" / "en.json").is_file()

    def test_english_translations_match_the_strings(self) -> None:
        # strings.json is the source; translations/en.json is what Home
        # Assistant actually shows to an English user. They drift silently.
        assert load_json(COMPONENT / "strings.json") == load_json(
            COMPONENT / "translations" / "en.json"
        )

    def test_the_config_step_documents_every_field(self, const: Any) -> None:
        fields = load_json(COMPONENT / "strings.json")["config"]["step"]["user"]["data"]
        assert set(fields) == {const.CONF_DRIVER, const.CONF_DASHBOARD, const.CONF_SERIAL_NUMBER}

    def test_the_options_step_documents_every_field(self, const: Any) -> None:
        fields = load_json(COMPONENT / "strings.json")["options"]["step"]["init"]["data"]
        assert set(fields) == {
            const.CONF_MIN_INTERVAL,
            const.CONF_MAX_INTERVAL,
            const.CONF_LANDSCAPE,
        }

    def test_every_entity_has_a_name(self) -> None:
        # An entity with no translated name shows up as its object id, which
        # looks like a bug in a UI someone is meeting for the first time.
        entities = load_json(COMPONENT / "strings.json")["entity"]
        for platform, keys in entities.items():
            for key, fields in keys.items():
                assert fields.get("name"), f"{platform}.{key} has no name"

    def test_the_dashboard_error_is_translated(self) -> None:
        # The config flow raises this key by name; an untranslated key shows up
        # as raw text in the UI.
        errors = load_json(COMPONENT / "strings.json")["config"]["error"]
        assert "invalid_dashboard" in errors
        assert "{error}" in errors["invalid_dashboard"]


class TestBrandImages:
    """Home Assistant 2026.3 and later prefer an icon shipped with the component.

    Before that, a custom integration could only get one by opening a pull
    request against home-assistant/brands. Bundling is both simpler and
    version-locked to the integration, so these check the files are where the
    UI looks for them and shaped the way it expects.
    """

    @pytest.mark.parametrize(
        ("name", "size"),
        [
            ("icon.png", 256),
            ("icon@2x.png", 512),
            ("dark_icon.png", 256),
            ("dark_icon@2x.png", 512),
        ],
    )
    def test_brand_image_exists_at_the_right_size(self, name: str, size: int) -> None:
        from PIL import Image  # noqa: PLC0415 - only this test needs Pillow

        path = COMPONENT / "brand" / name
        assert path.is_file()
        with Image.open(path) as image:
            assert image.size == (size, size)
            # Square, and transparent at the corners so the rounded shape reads
            # against whatever surface Home Assistant puts behind it.
            assert image.mode == "RGBA"
            corner = image.getpixel((0, 0))
            assert isinstance(corner, tuple)
            assert corner[3] == 0

    def test_the_brand_images_are_committed(self) -> None:
        """A file on disk is not a file in the release.

        These PNGs were built, tested, tagged and shipped twice without ever
        being committed. `*.png` is gitignored so that example renders stay out
        of the tree, `git add -A` skipped them without a word, and the size
        check above passed the whole time because it asks the filesystem. The
        release tarball is built from git, so git is what has to be asked.
        """
        result = subprocess.run(
            ["git", "ls-files", "--", "custom_components/tinydisplay/brand"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            check=False,
        )
        if result.returncode != 0:
            pytest.skip("not a git checkout")

        tracked = {Path(line).name for line in result.stdout.split() if line}
        assert tracked >= {"icon.png", "icon@2x.png", "dark_icon.png", "dark_icon@2x.png"}

    def test_the_generator_is_kept(self) -> None:
        # The icons are drawn with the project's own canvas rather than exported
        # from a design tool, so the source of truth is a script, not a binary.
        assert (REPO_ROOT / "tools" / "make_brand_icon.py").is_file()


class TestHacsMetadata:
    def test_hacs_manifest_exists(self) -> None:
        assert (REPO_ROOT / "hacs.json").is_file()

    def test_hacs_manifest_is_well_formed(self) -> None:
        hacs = load_json(REPO_ROOT / "hacs.json")
        assert hacs["name"]
        # content_in_root must be false: this repository is a workspace with the
        # integration in custom_components/, not a bare integration.
        assert hacs["content_in_root"] is False

    def test_hacs_declares_a_minimum_home_assistant(self) -> None:
        hacs = load_json(REPO_ROOT / "hacs.json")
        assert re.fullmatch(r"\d+\.\d+\.\d+", hacs["homeassistant"])


class TestDependencyRule:
    """The layering rule, asserted rather than trusted.

    ``custom_components/tinydisplay`` is the only code permitted to import
    Home Assistant. Everything under ``packages/`` must stay usable, and
    testable, by someone who has never installed it.
    """

    def test_no_library_package_imports_home_assistant(self) -> None:
        offenders = [
            source.relative_to(REPO_ROOT).as_posix()
            for source in PACKAGES.rglob("*.py")
            if HA_ROOT in imported_roots(source)
        ]
        assert offenders == []

    def test_the_component_does_import_home_assistant(self) -> None:
        # The negative test above would also pass if the component had quietly
        # stopped being an integration, so assert the other half too.
        roots = imported_roots(COMPONENT / "__init__.py")
        assert HA_ROOT in roots

    def test_the_component_uses_the_library(self) -> None:
        roots: set[str] = set()
        for source in COMPONENT.glob("*.py"):
            roots |= imported_roots(source)
        assert "tinydisplay" in roots

    def test_the_component_imports_nothing_unexpected(self) -> None:
        # Anything outside this set is a new runtime dependency, and a new
        # runtime dependency has to be declared in the manifest to be installed.
        allowed = {
            "__future__",
            "asyncio",
            "collections",
            "contextlib",
            "dataclasses",
            "datetime",
            "pathlib",
            "shutil",
            "homeassistant",
            "logging",
            "tinydisplay",
            "typing",
            "voluptuous",
        }
        for source in COMPONENT.glob("*.py"):
            unexpected = imported_roots(source) - allowed
            assert unexpected == set(), f"{source.name} imports {sorted(unexpected)}"
