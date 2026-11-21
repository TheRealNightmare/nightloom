"""The lookalike names the typosquat Shuttle tries."""

from collections import Counter

import pytest

from nightloom_core.normalize import normalize_domain, registrable_domain
from nightloom_shuttle_dns.permutations import KEYBOARD, KINDS, MAX_CANDIDATES, lookalikes


def names(domain: str, kind: str) -> set[str]:
    return {c.name for c in lookalikes(domain, limit=10_000) if c.kind == kind}


def test_keyboard_neighbours() -> None:
    assert KEYBOARD["a"] == "qswz"
    assert KEYBOARD["g"] == "bfhtvy"
    assert KEYBOARD["q"] == "12aw"
    assert KEYBOARD["m"] == "jkn"


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("omission", {"exmple.org", "xample.org", "exampe.org"}),
        ("repetition", {"exaample.org", "examplee.org"}),
        ("transposition", {"exmaple.org", "xeample.org", "exampel.org"}),
        ("replacement", {"exanple.org", "wxample.org", "examplw.org"}),
        ("insertion", {"exsample.org", "examplre.org"}),
        ("addition", {"example0.org", "example1.org"}),
        ("hyphenation", {"ex-ample.org", "exampl-e.org"}),
        ("vowel-swap", {"exomple.org", "examplu.org"}),
        ("homoglyph", {"examp1e.org", "exampie.org", "exarnple.org"}),
        ("bitsquatting", {"ehample.org", "examplg.org"}),
        ("suffix", {"example.com", "example.net", "example.co.uk", "example.com.bd"}),
    ],
)
def test_each_kind(kind: str, expected: set[str]) -> None:
    # A name is listed under the first kind that makes it (3xample is a replacement too).
    assert expected <= names("example.org", kind)


def test_idn_homoglyphs_are_punycode() -> None:
    found = names("example.org", "idn-homoglyph")
    # CYRILLIC SMALL LETTER A for the a of "example".
    assert normalize_domain("ex" + chr(0x0430) + "mple.org") in found
    assert all(name.startswith("xn--") for name in found)


def test_every_candidate_is_a_valid_registrable_domain_once() -> None:
    candidates = lookalikes("monerbondhu.com", limit=10_000)
    found = [c.name for c in candidates]
    assert len(found) == len(set(found))
    assert "monerbondhu.com" not in found
    for name in found:
        assert normalize_domain(name) == name
        assert registrable_domain(name) == name
        label = name.split(".")[0]
        assert not label.startswith("-") and not label.endswith("-")
    assert {c.kind for c in candidates} <= set(KINDS)


def test_the_cap_keeps_every_kind() -> None:
    capped = lookalikes("averyveryverylongcompanyname.com")
    assert len(capped) == MAX_CANDIDATES
    kinds = Counter(c.kind for c in capped)
    assert set(kinds) == set(KINDS)
    # Kinds take turns, other suffixes first.
    assert capped[0].kind == "suffix"
    assert [c.kind for c in lookalikes("example.org", limit=3)] == [
        "suffix",
        "omission",
        "repetition",
    ]


def test_deterministic() -> None:
    assert lookalikes("example.org") == lookalikes("example.org")


def test_other_suffixes_keep_the_name() -> None:
    assert "example.org" in {c.name for c in lookalikes("example.co.uk", limit=10_000)}
    assert names("example.co.uk", "omission") >= {"exmple.co.uk"}


def test_an_idn_only_gets_other_suffixes() -> None:
    candidates = lookalikes("xn--bcher-kva.de")
    assert candidates
    assert {c.kind for c in candidates} == {"suffix"}
    assert "xn--bcher-kva.com" in {c.name for c in candidates}
