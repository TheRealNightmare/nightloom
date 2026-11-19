#!/usr/bin/env python3
"""Print one version's section of CHANGELOG.md (the release notes).

    scripts/changelog-section.py 0.1.0
    scripts/changelog-section.py 0.2.0-rc.1 --file CHANGELOG.md

CHANGELOG.md follows Keep a Changelog: each release is a heading
``## [X.Y.Z] - YYYY-MM-DD``. This prints the body of that section, from the
line after its heading up to the next ``## `` heading or the link reference
definitions at the end of the file, with surrounding blank lines removed.

Exits 1 if the section is missing or empty. Standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_HEADING = re.compile(r"^## \[(?P<version>[^\]]+)\](?P<rest>.*)$")
_LINK_DEFINITION = re.compile(r"^\[[^\]]+\]:\s")


class SectionNotFoundError(LookupError):
    """The changelog has no (non-empty) section for the version."""


def extract_section(text: str, version: str) -> str:
    """Return the body of the ``## [version]`` section of a Keep a Changelog file."""
    version = version.removeprefix("v")
    lines = text.splitlines()
    start: int | None = None
    for index, line in enumerate(lines):
        match = _HEADING.match(line)
        if match and match.group("version") == version:
            start = index + 1
            break
    if start is None:
        raise SectionNotFoundError(f"no '## [{version}]' section in the changelog")

    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("## ") or _LINK_DEFINITION.match(line):
            break
        body.append(line.rstrip())
    section = "\n".join(body).strip("\n")
    if not section.strip():
        raise SectionNotFoundError(f"the '## [{version}]' section of the changelog is empty")
    return section + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("version", help="the version, with or without a leading v")
    parser.add_argument("--file", type=Path, default=ROOT / "CHANGELOG.md")
    args = parser.parse_args(argv)
    try:
        section = extract_section(args.file.read_text(encoding="utf-8"), args.version)
    except (OSError, SectionNotFoundError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(section)
    return 0


if __name__ == "__main__":
    sys.exit(main())
