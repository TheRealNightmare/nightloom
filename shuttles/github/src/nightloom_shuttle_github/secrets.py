"""Finding likely secrets in the lines a commit added, without keeping them.

:func:`scan_patch` reads a file's unified diff (as GitHub returns it in a
commit's ``files[].patch``) and reports, for each **added** line that holds
a likely secret, the rule's kind and the line's number in the new file.
The secret itself never leaves this module: no value, prefix, suffix or
hash is returned, logged or kept. Deduplicating the same secret across
commits uses an in-memory fingerprint (:class:`SeenSecrets`) that exists
only during one run.

Rules are deliberately few and precise: formats with a fixed prefix or
structure that rarely match by chance (cloud and service keys, tokens,
unencrypted private keys). Generic patterns ("password = ...", high
entropy) are not used: they flag examples and test data more often than
real secrets. Values documentation uses as placeholders (``EXAMPLE``,
runs of one character, ``xxxx``) are skipped.
"""

import hashlib
import re
from collections.abc import Iterator
from dataclasses import dataclass

# (kind, pattern). The pattern's first group, if any, is the secret (for placeholder checks).
RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key ID", re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{16})\b")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,251})\b")),
    ("GitHub fine-grained token", re.compile(r"\b(github_pat_[A-Za-z0-9_]{82})\b")),
    ("GitLab token", re.compile(r"\b(glpat-[A-Za-z0-9_-]{20})\b")),
    ("Slack token", re.compile(r"\b(xox[baprs]-[0-9A-Za-z-]{10,72})\b")),
    ("Stripe live key", re.compile(r"\b((?:sk|rk)_live_[0-9A-Za-z]{24,99})\b")),
    ("Google API key", re.compile(r"\b(AIza[0-9A-Za-z_-]{35})(?![0-9A-Za-z_-])")),
    ("SendGrid API key", re.compile(r"\b(SG\.[0-9A-Za-z_-]{22}\.[0-9A-Za-z_-]{43})\b")),
    ("npm token", re.compile(r"\b(npm_[A-Za-z0-9]{36})\b")),
    (
        "OpenAI API key",
        re.compile(r"\b(sk-(?:proj-)?[A-Za-z0-9_-]{20,}T3BlbkFJ[A-Za-z0-9_-]{20,})"),
    ),
    ("Anthropic API key", re.compile(r"\b(sk-ant-(?:api|admin)[0-9]{2}-[A-Za-z0-9_-]{80,})")),
    (
        "Private key",
        re.compile(r"(-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----)"),
    ),
)

PLACEHOLDER = re.compile(r"EXAMPLE|example|XXXX|xxxx|(.)\1{7,}|0123456789|abcdef0123")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(frozen=True)
class Match:
    """A likely secret: the rule that matched and the line in the new file. Never the value."""

    kind: str
    line: int


def _is_placeholder(kind: str, value: str) -> bool:
    if kind == "Private key":
        return False  # the header itself is the match; a key block is never a placeholder
    return PLACEHOLDER.search(value) is not None


def scan_line(text: str) -> Iterator[tuple[str, str]]:
    """``(kind, secret)`` for each likely secret in one line. Internal: the secret stays here."""
    for kind, pattern in RULES:
        for found in pattern.finditer(text):
            value = found.group(1)
            if not _is_placeholder(kind, value):
                yield kind, value


class SeenSecrets:
    """Secrets already reported in this run, as salted in-memory fingerprints (never kept)."""

    def __init__(self) -> None:
        self._salt = hashlib.sha256(str(id(self)).encode()).digest()
        self._seen: set[bytes] = set()

    def first(self, kind: str, value: str) -> bool:
        """Whether this is the first time the run sees this secret."""
        digest = hashlib.sha256(self._salt + kind.encode() + b"\x00" + value.encode()).digest()
        if digest in self._seen:
            return False
        self._seen.add(digest)
        return True


def scan_patch(patch: str, seen: SeenSecrets) -> list[Match]:
    """The likely secrets in the lines ``patch`` adds that this run has not seen yet."""
    matches: list[Match] = []
    line = 0
    for raw in patch.splitlines():
        hunk = HUNK.match(raw)
        if hunk is not None:
            line = int(hunk.group(1))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            for kind, value in scan_line(raw[1:]):
                if seen.first(kind, value):
                    matches.append(Match(kind, line))
            line += 1
        elif raw.startswith(" "):
            line += 1
        # "-" lines are not in the new file; "\\ No newline" lines are not lines.
    return matches


__all__ = ["RULES", "Match", "SeenSecrets", "scan_line", "scan_patch"]
