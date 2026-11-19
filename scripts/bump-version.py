#!/usr/bin/env python3
"""Set the Nightloom version in every manifest and refresh the lockfiles.

    scripts/bump-version.py 0.2.0          # or: make bump-version V=0.2.0
    scripts/bump-version.py 0.2.0-rc.1     # a release candidate
    scripts/bump-version.py 0.2.0 --no-lock

Edits the same files that scripts/check-versions.py checks (every workspace
pyproject.toml, apps/web/package.json and the NIGHTLOOM_VERSION example in
.env.example), changing only their version line, then runs `uv lock` and
`pnpm install --lockfile-only` so uv.lock and pnpm-lock.yaml agree. Python
manifests get the PEP 440 spelling of a release candidate (0.2.0rc1), the
others the SemVer one (0.2.0-rc.1).
Shuttle versions (ShuttleMeta.version) are not touched. Does not commit.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent


def _load_check_versions() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_versions", HERE / "check-versions.py")
    if spec is None or spec.loader is None:  # pragma: no cover - the file is next to this one
        raise ImportError("scripts/check-versions.py not found")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_versions", module)
    spec.loader.exec_module(module)
    return module


cv = _load_check_versions()

# The first `version = "..."` line, which is the [project] table's in every manifest.
_PY_VERSION_LINE = re.compile(r'^version = "[^"]*"$', re.MULTILINE)
# The top-level "version" of package.json (two-space indentation, as written by pnpm).
_NPM_VERSION_LINE = re.compile(r'^  "version": "[^"]*",$', re.MULTILINE)
# The commented example in .env.example.
_ENV_VERSION_LINE = re.compile(r"^# NIGHTLOOM_VERSION=\S*$", re.MULTILINE)


def _replace_once(pattern: re.Pattern[str], text: str, line: str, path: Path) -> str:
    new_text, count = pattern.subn(line, text, count=1)
    if count != 1:
        raise ValueError(f"{path}: no version line to replace")
    return new_text


def set_version(version: Any, root: Path) -> list[Path]:
    """Write ``version`` (a check-versions ``Version``) into every manifest.

    Returns the manifests that changed.
    """
    changed: list[Path] = []
    edits = [
        (path, _PY_VERSION_LINE, f'version = "{version.pep440}"')
        for path in cv.python_manifests(root)
    ]
    edits += [
        (path, _NPM_VERSION_LINE, f'  "version": "{version.semver}",')
        for path in cv.npm_manifests(root)
    ]
    edits += [
        (path, _ENV_VERSION_LINE, f"# NIGHTLOOM_VERSION={version.semver}")
        for path in cv.env_examples(root)
    ]
    for path, pattern, line in edits:
        text = path.read_text(encoding="utf-8")
        new_text = _replace_once(pattern, text, line, path)
        if new_text != text:
            path.write_text(new_text, encoding="utf-8")
            changed.append(path)
    return changed


def refresh_lockfiles(root: Path) -> None:
    for command in (["uv", "lock"], ["pnpm", "install", "--lockfile-only"]):
        executable = shutil.which(command[0])
        if executable is None:
            raise FileNotFoundError(f"{command[0]} is not installed; rerun with --no-lock")
        print(f"$ {' '.join(command)}", flush=True)
        subprocess.run([executable, *command[1:]], cwd=root, check=True)  # noqa: S603


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("version", help="the new version: X.Y.Z or X.Y.Z-rc.N")
    parser.add_argument("--no-lock", action="store_true", help="do not refresh the lockfiles")
    parser.add_argument("--root", type=Path, default=cv.ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        version = cv.Version.parse_semver(args.version.removeprefix("v"))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    try:
        changed = set_version(version, args.root)
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    for path in changed:
        print(f"set {version} in {path.relative_to(args.root).as_posix()}")
    if not changed:
        print(f"every manifest already has version {version}")

    if not args.no_lock:
        try:
            refresh_lockfiles(args.root)
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            print(f"error: {error}", file=sys.stderr)
            return 1

    _, problems = cv.check(args.root, f"v{version.semver}")
    if problems:  # pragma: no cover - set_version wrote every manifest checked
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    print(f"Version is now {version}. Next: update CHANGELOG.md (docs/RELEASING.md).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
