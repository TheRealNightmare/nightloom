"""Lookalike names of a registrable domain: the typos and tricks typosquatters register.

:func:`lookalikes` turns ``example.org`` into candidates such as
``exmple.org`` (omission), ``exaample.org`` (repetition), ``exmaple.org``
(transposition), ``exanple.org`` (a neighbouring key), ``examp1e.org``
(a lookalike character), ``example.net`` (another suffix) and so on, in the
spirit of dnstwist. Every candidate is a valid, normalized, registrable
domain different from the input, and appears once, under the first kind that
produced it.

The kinds take turns (one candidate of each, then the next of each, ...), so
a cap keeps a few of every kind instead of only the first kinds' long lists.
Everything is deterministic: the same input always gives the same list.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from nightloom_core.normalize import NormalizationError, normalize_domain, registrable_domain

MAX_CANDIDATES = 500

# What each kind means, for the relationship's note.
KINDS: dict[str, str] = {
    "omission": "a letter left out",
    "repetition": "a letter typed twice",
    "transposition": "two letters swapped",
    "replacement": "a neighbouring key instead of a letter",
    "insertion": "a neighbouring key typed next to a letter",
    "addition": "a character added at the end",
    "hyphenation": "a hyphen added",
    "vowel-swap": "a vowel changed",
    "homoglyph": "a lookalike character (such as 1 for l, rn for m)",
    "idn-homoglyph": "a lookalike letter from another alphabet",
    "bitsquatting": "one bit of a character flipped",
    "suffix": "the same name under another suffix",
}

# Neighbouring keys on a QWERTY keyboard (letters and digits).
_QWERTY_ROWS = ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm")


def _neighbours() -> dict[str, str]:
    """Each key's neighbours: left and right, and the two keys touching it above and below.

    Rows are staggered: the keys above ``(row, col)`` are at ``col`` and
    ``col + 1`` of the row above, the keys below at ``col - 1`` and ``col``.
    """
    found: dict[str, str] = {}
    for row, keys in enumerate(_QWERTY_ROWS):
        for col, key in enumerate(keys):
            spots = [(row, col - 1), (row, col + 1)]
            spots += [(row - 1, col), (row - 1, col + 1), (row + 1, col - 1), (row + 1, col)]
            near = {
                _QWERTY_ROWS[r][c]
                for r, c in spots
                if 0 <= r < len(_QWERTY_ROWS) and 0 <= c < len(_QWERTY_ROWS[r])
            }
            found[key] = "".join(sorted(near))
    return found


KEYBOARD = _neighbours()
VOWELS = "aeiou"
ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

# Lookalike ASCII spellings (both ways where it makes sense).
ASCII_HOMOGLYPHS: tuple[tuple[str, str], ...] = (
    ("o", "0"),
    ("0", "o"),
    ("l", "1"),
    ("1", "l"),
    ("l", "i"),
    ("i", "l"),
    ("i", "1"),
    ("m", "rn"),
    ("rn", "m"),
    ("w", "vv"),
    ("vv", "w"),
    ("d", "cl"),
    ("cl", "d"),
    ("g", "q"),
    ("q", "g"),
    ("s", "5"),
    ("e", "3"),
    ("a", "4"),
    ("b", "8"),
)
# Latin letters and the Cyrillic letters that look the same.
IDN_HOMOGLYPHS: dict[str, str] = {
    "a": chr(0x0430),  # CYRILLIC SMALL LETTER A
    "c": chr(0x0441),  # CYRILLIC SMALL LETTER ES
    "e": chr(0x0435),  # CYRILLIC SMALL LETTER IE
    "i": chr(0x0456),  # CYRILLIC SMALL LETTER BYELORUSSIAN-UKRAINIAN I
    "j": chr(0x0458),  # CYRILLIC SMALL LETTER JE
    "o": chr(0x043E),  # CYRILLIC SMALL LETTER O
    "p": chr(0x0440),  # CYRILLIC SMALL LETTER ER
    "s": chr(0x0455),  # CYRILLIC SMALL LETTER DZE
    "x": chr(0x0445),  # CYRILLIC SMALL LETTER HA
    "y": chr(0x0443),  # CYRILLIC SMALL LETTER U
}
# Suffixes typosquatters favour: cheap, popular or confusable with common ones.
SUFFIXES: tuple[str, ...] = (
    "com",
    "net",
    "org",
    "co",
    "io",
    "info",
    "biz",
    "xyz",
    "online",
    "site",
    "app",
    "dev",
    "me",
    "top",
    "shop",
    "store",
    "cc",
    "us",
    "uk",
    "co.uk",
    "de",
    "in",
    "bd",
    "com.bd",
)

_LDH = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")


@dataclass(frozen=True)
class Candidate:
    """One lookalike name and the kind of change that made it."""

    name: str
    kind: str


def _omission(label: str) -> Iterator[str]:
    for i in range(len(label)):
        yield label[:i] + label[i + 1 :]


def _repetition(label: str) -> Iterator[str]:
    for i, ch in enumerate(label):
        if ch != "-":
            yield label[: i + 1] + ch + label[i + 1 :]


def _transposition(label: str) -> Iterator[str]:
    for i in range(len(label) - 1):
        if label[i] != label[i + 1]:
            yield label[:i] + label[i + 1] + label[i] + label[i + 2 :]


def _replacement(label: str) -> Iterator[str]:
    for i, ch in enumerate(label):
        for key in KEYBOARD.get(ch, ""):
            yield label[:i] + key + label[i + 1 :]


def _insertion(label: str) -> Iterator[str]:
    for i, ch in enumerate(label):
        for key in KEYBOARD.get(ch, ""):
            yield label[:i] + key + label[i:]
            yield label[: i + 1] + key + label[i + 1 :]


def _addition(label: str) -> Iterator[str]:
    for ch in ALPHABET:
        yield label + ch


def _hyphenation(label: str) -> Iterator[str]:
    for i in range(1, len(label)):
        yield label[:i] + "-" + label[i:]


def _vowel_swap(label: str) -> Iterator[str]:
    for i, ch in enumerate(label):
        if ch in VOWELS:
            for other in VOWELS:
                if other != ch:
                    yield label[:i] + other + label[i + 1 :]


def _homoglyph(label: str) -> Iterator[str]:
    for old, new in ASCII_HOMOGLYPHS:
        start = label.find(old)
        while start != -1:
            yield label[:start] + new + label[start + len(old) :]
            start = label.find(old, start + 1)


def _idn_homoglyph(label: str) -> Iterator[str]:
    for i, ch in enumerate(label):
        lookalike = IDN_HOMOGLYPHS.get(ch)
        if lookalike is not None:
            yield label[:i] + lookalike + label[i + 1 :]


def _bitsquatting(label: str) -> Iterator[str]:
    for i, ch in enumerate(label):
        code = ord(ch)
        for bit in range(8):
            flipped = chr(code ^ (1 << bit)).lower()
            if flipped in _LDH and flipped != ch:
                yield label[:i] + flipped + label[i + 1 :]


_LABEL_KINDS: tuple[tuple[str, Callable[[str], Iterator[str]]], ...] = (
    ("omission", _omission),
    ("repetition", _repetition),
    ("transposition", _transposition),
    ("replacement", _replacement),
    ("insertion", _insertion),
    ("addition", _addition),
    ("hyphenation", _hyphenation),
    ("vowel-swap", _vowel_swap),
    ("homoglyph", _homoglyph),
    ("idn-homoglyph", _idn_homoglyph),
    ("bitsquatting", _bitsquatting),
)


def _valid_label(label: str) -> bool:
    if not 1 <= len(label) <= 63 or label[0] == "-" or label[-1] == "-":
        return False
    ascii_only = label.isascii()
    if ascii_only and not set(label) <= _LDH:
        return False
    # "ab--cd" is reserved for encodings; only xn-- (IDNA) is in use.
    return not (
        ascii_only and len(label) >= 4 and label[2:4] == "--" and not label.startswith("xn--")
    )


def _domain(label: str, suffix: str) -> str | None:
    """``label.suffix`` normalized, or ``None`` if it is not a registrable domain."""
    if not _valid_label(label):
        return None
    try:
        name = normalize_domain(f"{label}.{suffix}")
        return name if registrable_domain(name) == name else None
    except NormalizationError:
        return None


def lookalikes(domain: str, *, limit: int = MAX_CANDIDATES) -> list[Candidate]:
    """At most ``limit`` lookalike names of the registrable ``domain``, kinds taking turns.

    ``domain`` must be a normalized registrable domain (``example.org``,
    ``example.co.uk``). For an internationalized name (``xn--`` label), only
    other suffixes are tried: changing its encoded form gives noise.
    """
    label, _, suffix = domain.partition(".")
    seen = {domain}
    lists: list[list[Candidate]] = []
    kinds = () if label.startswith("xn--") else _LABEL_KINDS
    for kind, make in kinds:
        found: list[Candidate] = []
        for variant in make(label):
            name = _domain(variant, suffix)
            if name is not None and name not in seen:
                seen.add(name)
                found.append(Candidate(name, kind))
        lists.append(found)
    other_suffixes: list[Candidate] = []
    for other in SUFFIXES:
        if other == suffix:
            continue
        name = _domain(label, other)
        if name is not None and name not in seen:
            seen.add(name)
            other_suffixes.append(Candidate(name, "suffix"))
    # Other suffixes first in each turn: they are the most often registered.
    lists.insert(0, other_suffixes)
    result: list[Candidate] = []
    turn = 0
    while len(result) < limit and any(turn < len(found) for found in lists):
        for found in lists:
            if turn < len(found):
                result.append(found[turn])
                if len(result) == limit:
                    break
        turn += 1
    return result


__all__ = ["KINDS", "MAX_CANDIDATES", "SUFFIXES", "Candidate", "lookalikes"]
