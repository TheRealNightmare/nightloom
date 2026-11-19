"""scripts/check-versions.py and scripts/bump-version.py.

The repository itself must pass the check, so `make test` catches a manifest
left behind. The other tests run on small fake workspaces in a temporary
directory; bump-version runs with --no-lock there (no uv or pnpm needed).
"""

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cv = load("check_versions", "check-versions.py")
bump = load("bump_version", "bump-version.py")

PYPROJECT = """\
[project]
name = "{name}"
version = "{version}"
description = "A test package."
dependencies = ["httpx>=0.28"]

[tool.uv.sources]
other = {{ workspace = true }}
"""


def make_workspace(root: Path, python_version: str = "1.2.3", npm_version: str = "1.2.3") -> Path:
    for directory in ("packages/core", "apps/api", "apps/worker", "shuttles/dns"):
        path = root / directory / "pyproject.toml"
        path.parent.mkdir(parents=True)
        path.write_text(PYPROJECT.format(name=directory.replace("/", "-"), version=python_version))
    # The virtual workspace root has no [project] table and is not a manifest.
    (root / "pyproject.toml").write_text('[tool.uv.workspace]\nmembers = ["apps/*"]\n')
    web = root / "apps/web"
    web.mkdir(parents=True)
    package = {"name": "@nightloom/web", "version": npm_version, "private": True}
    (web / "package.json").write_text(json.dumps(package, indent=2) + "\n")
    (root / ".env.example").write_text(
        f"NIGHTLOOM_ENV=development\n# NIGHTLOOM_VERSION={npm_version}\n"
    )
    return root


# --- Version parsing --------------------------------------------------------


@pytest.mark.parametrize(
    ("semver", "pep440", "prerelease"),
    [("0.1.0", "0.1.0", False), ("1.20.3", "1.20.3", False), ("2.0.0-rc.1", "2.0.0rc1", True)],
)
def test_versions_have_a_semver_and_a_pep440_spelling(
    semver: str, pep440: str, prerelease: bool
) -> None:
    version = cv.Version.parse_semver(semver)
    assert version == cv.Version.parse_pep440(pep440)
    assert version == cv.Version.parse_tag(f"v{semver}")
    assert (version.semver, version.pep440, version.is_prerelease) == (semver, pep440, prerelease)
    assert str(version) == semver


@pytest.mark.parametrize(
    "text", ["1.0", "1.0.0.0", "01.0.0", "1.0.0-rc.01", "1.0.0-beta.1", "1.0.0rc1", "latest", ""]
)
def test_rejects_versions_that_are_not_releases(text: str) -> None:
    with pytest.raises(ValueError, match=r"release version|leading zero"):
        cv.Version.parse_semver(text)


@pytest.mark.parametrize("tag", ["0.1.0", "v0.1", "v0.1.0-beta.1", "release-0.1.0", "v0.1.0rc1"])
def test_rejects_tags_that_are_not_release_tags(tag: str) -> None:
    with pytest.raises(ValueError, match="release"):
        cv.Version.parse_tag(tag)


# --- check-versions -----------------------------------------------------------


def test_the_repository_versions_agree() -> None:
    version, problems = cv.check()
    assert problems == []
    assert version is not None
    names = {path.relative_to(ROOT).as_posix() for path in cv.python_manifests()}
    assert {"apps/api/pyproject.toml", "apps/worker/pyproject.toml"} <= names
    assert {"packages/core/pyproject.toml", "packages/sdk/pyproject.toml"} <= names
    assert {p.parent.as_posix() for p in (ROOT / "shuttles").glob("*/pyproject.toml")} <= {
        (ROOT / name).parent.as_posix() for name in names
    }
    assert "pyproject.toml" not in names
    assert cv.env_examples() == [ROOT / ".env.example"]


def test_passes_when_every_manifest_agrees(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    assert cv.main(["--root", str(root)]) == 0
    assert capsys.readouterr().out == "All 6 manifests have version 1.2.3.\n"
    assert cv.main(["--root", str(root), "--tag", "v1.2.3"]) == 0
    assert "matching the tag v1.2.3" in capsys.readouterr().out
    assert cv.main(["--root", str(root), "--print"]) == 0
    assert capsys.readouterr().out == "1.2.3\n"


def test_a_release_candidate_matches_in_both_spellings(tmp_path: Path) -> None:
    root = make_workspace(tmp_path, python_version="1.2.3rc2", npm_version="1.2.3-rc.2")
    version, problems = cv.check(root, "v1.2.3-rc.2")
    assert problems == []
    assert version == cv.Version(1, 2, 3, 2)


def test_fails_when_a_manifest_differs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = make_workspace(tmp_path)
    shuttle = root / "shuttles/dns/pyproject.toml"
    shuttle.write_text(shuttle.read_text().replace('version = "1.2.3"', 'version = "1.2.4"'))
    assert cv.main(["--root", str(root)]) == 1
    err = capsys.readouterr().err
    assert "the manifests disagree" in err
    assert "shuttles/dns/pyproject.toml: 1.2.4" in err


def test_checks_the_env_example_when_there_is_one(tmp_path: Path) -> None:
    root = make_workspace(tmp_path)
    env = root / ".env.example"
    env.write_text("# NIGHTLOOM_VERSION=1.2.2\n")
    assert "  .env.example: 1.2.2" in cv.check(root)[1][0]
    env.write_text("NIGHTLOOM_ENV=development\n")
    assert cv.check(root)[1] == [".env.example: no '# NIGHTLOOM_VERSION=' line"]
    env.unlink()
    assert cv.check(root)[1] == []


def test_fails_when_the_tag_does_not_match(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    assert cv.main(["--root", str(root), "--tag", "v1.3.0"]) == 1
    assert "do not match the tag v1.3.0" in capsys.readouterr().err
    assert cv.main(["--root", str(root), "--tag", "1.2.3"]) == 1
    assert "is not a release tag" in capsys.readouterr().err


def test_fails_on_a_development_or_missing_version(tmp_path: Path) -> None:
    root = make_workspace(tmp_path, python_version="1.2.3.dev0")
    _, problems = cv.check(root)
    assert any("is not a release version" in problem for problem in problems)

    root = make_workspace(tmp_path / "missing")
    (root / "apps/web/package.json").write_text('{"name": "@nightloom/web"}\n')
    _, problems = cv.check(root)
    assert problems == ['apps/web/package.json: no "version"']


# --- bump-version -------------------------------------------------------------


def test_bump_sets_every_manifest_and_nothing_else(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    before = {path: path.read_text() for path in root.rglob("*") if path.is_file()}

    assert bump.main(["2.0.0-rc.1", "--no-lock", "--root", str(root)]) == 0

    out = capsys.readouterr().out
    assert "set 2.0.0-rc.1 in apps/web/package.json" in out
    assert "Version is now 2.0.0-rc.1." in out
    for path, text in before.items():
        new = path.read_text()
        if path.name == ".env.example":
            assert new == text.replace("VERSION=1.2.3", "VERSION=2.0.0-rc.1")
        elif path.name == "package.json":
            assert json.loads(new)["version"] == "2.0.0-rc.1"
            assert new == text.replace('"version": "1.2.3"', '"version": "2.0.0-rc.1"')
        elif path.parent == root:
            assert new == text  # the workspace root has no version
        else:
            assert new == text.replace('version = "1.2.3"', 'version = "2.0.0rc1"')
    assert cv.check(root, "v2.0.0-rc.1")[1] == []


def test_bump_accepts_a_leading_v_and_is_idempotent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    assert bump.main(["v1.2.3", "--no-lock", "--root", str(root)]) == 0
    assert "every manifest already has version 1.2.3" in capsys.readouterr().out


@pytest.mark.parametrize("version", ["1.2", "1.2.3.dev0", "1.2.3rc1", "next"])
def test_bump_refuses_a_version_that_is_not_a_release(
    tmp_path: Path, version: str, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    before = (root / "apps/api/pyproject.toml").read_text()
    assert bump.main([version, "--no-lock", "--root", str(root)]) == 2
    assert "is not a release version" in capsys.readouterr().err
    assert (root / "apps/api/pyproject.toml").read_text() == before


def test_bump_fails_without_a_version_line(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    (root / "apps/web/package.json").write_text('{"name": "@nightloom/web", "version": "1.2.3"}\n')
    assert bump.main(["1.3.0", "--no-lock", "--root", str(root)]) == 1
    assert "no version line to replace" in capsys.readouterr().err


def test_bump_refreshes_the_lockfiles_with_uv_and_pnpm(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = make_workspace(tmp_path)
    calls: list[tuple[list[str], Path]] = []

    def fake_run(command: list[str], *, cwd: Path, check: bool) -> None:
        assert check
        calls.append((command, cwd))

    monkeypatch.setattr(bump.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(bump.subprocess, "run", fake_run)
    assert bump.main(["1.3.0", "--root", str(root)]) == 0
    assert calls == [
        (["/usr/bin/uv", "lock"], root),
        (["/usr/bin/pnpm", "install", "--lockfile-only"], root),
    ]

    monkeypatch.setattr(bump.shutil, "which", lambda name: None)
    assert bump.main(["1.4.0", "--root", str(root)]) == 1
    assert "uv is not installed" in capsys.readouterr().err


def test_bump_works_on_a_copy_of_the_repository_manifests(tmp_path: Path) -> None:
    for path in cv.all_manifests():
        target = tmp_path / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(path, target)
    assert bump.main(["9.8.7", "--no-lock", "--root", str(tmp_path)]) == 0
    version, problems = cv.check(tmp_path, "v9.8.7")
    assert problems == []
    assert version == cv.Version(9, 8, 7)
