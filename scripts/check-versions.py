#!/usr/bin/env python3
"""Check that every Nightloom package has the same version.

    scripts/check-versions.py                 # all manifests agree
    scripts/check-versions.py --tag v0.1.0    # ... and match this release tag
    scripts/check-versions.py --print         # print the version (SemVer form)

The application version lives in every Python package of the uv workspace
(packages/*, apps/api, apps/worker, shuttles/*) and in apps/web/package.json.
They are released together, so they must always be equal. The commented
``# NIGHTLOOM_VERSION=`` line of .env.example (the release production pulls)
is checked too. A Shuttle's own
version (``ShuttleMeta.version`` in its code) versions the Shuttle's output,
not the application, and is not checked here.

Release versions are ``X.Y.Z`` or release candidates ``X.Y.Z-rc.N``. Python
manifests spell a release candidate the PEP 440 way (``X.Y.ZrcN``), npm the
SemVer way (``X.Y.Z-rc.N``); both name the same version. Release tags are
``vX.Y.Z`` / ``vX.Y.Z-rc.N``.

Exits 0 when everything agrees, 1 when it does not, 2 on a usage error.
Standard library only (runs before any dependency is installed).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PYTHON_MANIFEST_GLOBS = (
    "packages/*/pyproject.toml",
    "apps/*/pyproject.toml",
    "shuttles/*/pyproject.toml",
)
NPM_MANIFESTS = ("apps/web/package.json",)
# The release production runs, as documented in the env template (a comment).
ENV_EXAMPLE = ".env.example"
ENV_VERSION_LINE = re.compile(r"^# NIGHTLOOM_VERSION=(?P<version>\S*)$", re.MULTILINE)

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-rc\.(\d+))?$")
_PEP440 = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:rc(\d+))?$")
_TAG = re.compile(r"^v(\d+\.\d+\.\d+(?:-rc\.\d+)?)$")


@dataclass(frozen=True)
class Version:
    """A release version: X.Y.Z with an optional release-candidate number."""

    major: int
    minor: int
    patch: int
    rc: int | None = None

    @classmethod
    def parse_semver(cls, text: str) -> Version:
        """Parse ``X.Y.Z`` or ``X.Y.Z-rc.N``."""
        return cls._parse(_SEMVER, text, "X.Y.Z or X.Y.Z-rc.N")

    @classmethod
    def parse_pep440(cls, text: str) -> Version:
        """Parse ``X.Y.Z`` or ``X.Y.ZrcN``."""
        return cls._parse(_PEP440, text, "X.Y.Z or X.Y.ZrcN")

    @classmethod
    def parse_tag(cls, text: str) -> Version:
        """Parse a release tag, ``vX.Y.Z`` or ``vX.Y.Z-rc.N``."""
        match = _TAG.match(text)
        if not match:
            raise ValueError(f"{text!r} is not a release tag (vX.Y.Z or vX.Y.Z-rc.N)")
        return cls.parse_semver(match.group(1))

    @classmethod
    def _parse(cls, pattern: re.Pattern[str], text: str, expected: str) -> Version:
        match = pattern.match(text)
        if not match:
            raise ValueError(f"{text!r} is not a release version ({expected})")
        major, minor, patch, rc = match.groups()
        # No leading zeros (SemVer 2.0.0, and what PEP 440 normalises away).
        for part in (major, minor, patch, rc):
            if part is not None and len(part) > 1 and part.startswith("0"):
                raise ValueError(f"{text!r} has a number with a leading zero")
        return cls(int(major), int(minor), int(patch), None if rc is None else int(rc))

    @property
    def semver(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if self.rc is None else f"{base}-rc.{self.rc}"

    @property
    def pep440(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        return base if self.rc is None else f"{base}rc{self.rc}"

    @property
    def is_prerelease(self) -> bool:
        return self.rc is not None

    def __str__(self) -> str:
        return self.semver


def python_manifests(root: Path = ROOT) -> list[Path]:
    """Every pyproject.toml of a workspace package (not the virtual root)."""
    found: set[Path] = set()
    for pattern in PYTHON_MANIFEST_GLOBS:
        found.update(root.glob(pattern))
    return sorted(found)


def npm_manifests(root: Path = ROOT) -> list[Path]:
    return [root / name for name in NPM_MANIFESTS]


def env_examples(root: Path = ROOT) -> list[Path]:
    """The env template, when the tree has one (it names the release to run)."""
    path = root / ENV_EXAMPLE
    return [path] if path.exists() else []


def read_env_example_version(path: Path) -> str:
    match = ENV_VERSION_LINE.search(path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError("no '# NIGHTLOOM_VERSION=' line")
    return match.group("version")


def all_manifests(root: Path = ROOT) -> list[Path]:
    return [*python_manifests(root), *npm_manifests(root), *env_examples(root)]


def read_python_version(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str):
        raise ValueError("no [project] version")
    return version


def read_npm_version(path: Path) -> str:
    version = json.loads(path.read_text(encoding="utf-8")).get("version")
    if not isinstance(version, str):
        raise ValueError('no "version"')
    return version


def collect(root: Path = ROOT) -> tuple[dict[str, Version], list[str]]:
    """Return {manifest: version} and the problems found while reading them."""
    versions: dict[str, Version] = {}
    problems: list[str] = []
    readers = [(path, read_python_version, Version.parse_pep440) for path in python_manifests(root)]
    readers += [(path, read_npm_version, Version.parse_semver) for path in npm_manifests(root)]
    readers += [
        (path, read_env_example_version, Version.parse_semver) for path in env_examples(root)
    ]
    if not readers:
        problems.append(f"no manifests found under {root}")
    for path, read, parse in readers:
        name = path.relative_to(root).as_posix()
        try:
            versions[name] = parse(read(path))
        except (OSError, ValueError) as error:
            problems.append(f"{name}: {error}")
    return versions, problems


def check(root: Path = ROOT, tag: str | None = None) -> tuple[Version | None, list[str]]:
    """Return the common version (if there is one) and every problem found."""
    versions, problems = collect(root)
    distinct = set(versions.values())
    common = next(iter(distinct)) if len(distinct) == 1 else None
    if len(distinct) > 1:
        lines = [f"  {name}: {version}" for name, version in sorted(versions.items())]
        problems.append("the manifests disagree:\n" + "\n".join(lines))
    if tag is not None:
        try:
            wanted = Version.parse_tag(tag)
        except ValueError as error:
            problems.append(str(error))
        else:
            wrong = {name: v for name, v in versions.items() if v != wanted}
            if wrong:
                lines = [f"  {name}: {version}" for name, version in sorted(wrong.items())]
                problems.append(f"these manifests do not match the tag {tag}:\n" + "\n".join(lines))
    return (common if not problems else None), problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--tag", help="also require this release tag's version (vX.Y.Z[-rc.N])")
    parser.add_argument(
        "--print", action="store_true", help="print only the common version (SemVer form)"
    )
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    version, problems = check(args.root, args.tag)
    if problems:
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    assert version is not None  # noqa: S101 - no problems means one common version
    if args.print:
        print(version.semver)
        return 0
    count = len(all_manifests(args.root))
    suffix = f", matching the tag {args.tag}" if args.tag else ""
    print(f"All {count} manifests have version {version}{suffix}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
