"""scripts/changelog-section.py: one version's release notes from CHANGELOG.md."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "changelog_section", ROOT / "scripts" / "changelog-section.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["changelog_section"] = module
    spec.loader.exec_module(module)
    return module


cs = load()

CHANGELOG = """\
# Changelog

Intro text.

## [Unreleased]

## [1.1.0-rc.1] - 2027-02-01

### Fixed

- A release candidate fix.

## [1.0.0] - 2027-01-15

### Added

- Thing one.
  Continued line.

#### Sub-area

- Thing two.

### Known limitations

- None worth noting.


## [0.9.0] - 2026-12-01

- Old.

[Unreleased]: https://example.org/compare/v1.0.0...HEAD
[1.0.0]: https://example.org/releases/tag/v1.0.0
[0.9.0]: https://example.org/releases/tag/v0.9.0
"""


def test_extracts_the_section_up_to_the_next_release() -> None:
    assert cs.extract_section(CHANGELOG, "1.0.0") == (
        "### Added\n\n- Thing one.\n  Continued line.\n\n#### Sub-area\n\n- Thing two.\n\n"
        "### Known limitations\n\n- None worth noting.\n"
    )


def test_the_last_section_stops_at_the_link_definitions() -> None:
    assert cs.extract_section(CHANGELOG, "0.9.0") == "- Old.\n"


def test_accepts_a_tag_and_release_candidates() -> None:
    assert cs.extract_section(CHANGELOG, "v1.0.0") == cs.extract_section(CHANGELOG, "1.0.0")
    assert (
        cs.extract_section(CHANGELOG, "v1.1.0-rc.1") == "### Fixed\n\n- A release candidate fix.\n"
    )


@pytest.mark.parametrize(
    ("version", "message"),
    [
        ("2.0.0", r"no '## \[2.0.0\]' section"),
        ("1.0", r"no '## \[1.0\]' section"),  # no prefix matching
        ("Unreleased", "is empty"),
    ],
)
def test_missing_or_empty_sections_are_errors(version: str, message: str) -> None:
    with pytest.raises(cs.SectionNotFoundError, match=message):
        cs.extract_section(CHANGELOG, version)


def test_main_prints_the_section_or_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG)
    assert cs.main(["0.9.0", "--file", str(changelog)]) == 0
    assert capsys.readouterr().out == "- Old.\n"
    assert cs.main(["3.0.0", "--file", str(changelog)]) == 1
    assert "no '## [3.0.0]' section" in capsys.readouterr().err
    assert cs.main(["0.9.0", "--file", str(tmp_path / "missing.md")]) == 1
    assert "No such file" in capsys.readouterr().err


def test_the_repository_changelog_has_the_current_version() -> None:
    cv_spec = importlib.util.spec_from_file_location(
        "check_versions_for_changelog", ROOT / "scripts" / "check-versions.py"
    )
    assert cv_spec is not None
    assert cv_spec.loader is not None
    cv = importlib.util.module_from_spec(cv_spec)
    sys.modules["check_versions_for_changelog"] = cv
    cv_spec.loader.exec_module(cv)
    version, problems = cv.check()
    assert problems == []

    text = (ROOT / "CHANGELOG.md").read_text()
    section = cs.extract_section(text, version.semver)
    assert section.startswith(("### ", "First release"))
    assert "## [Unreleased]" in text
    assert f"[{version.semver}]: https://github.com/TheRealNightmare/nightloom/" in text
