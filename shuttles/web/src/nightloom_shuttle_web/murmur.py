"""MurmurHash3 (x86, 32-bit) and the favicon hash Shodan and FOFA search for.

Pure Python, so the Shuttle needs no compiled extension; favicons are small
(the Shuttle reads at most a few hundred kilobytes), so speed does not matter.
"""

import base64

_C1 = 0xCC9E2D51
_C2 = 0x1B873593
_MASK = 0xFFFFFFFF


def _rotl(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (32 - shift))) & _MASK


def _fmix(value: int) -> int:
    value ^= value >> 16
    value = (value * 0x85EBCA6B) & _MASK
    value ^= value >> 13
    value = (value * 0xC2B2AE35) & _MASK
    value ^= value >> 16
    return value


def murmur3_32(data: bytes, seed: int = 0) -> int:
    """The signed 32-bit MurmurHash3 of ``data`` (what ``mmh3.hash`` returns)."""
    length = len(data)
    h = seed & _MASK
    end = length - length % 4
    for start in range(0, end, 4):
        k = int.from_bytes(data[start : start + 4], "little")
        k = (k * _C1) & _MASK
        k = _rotl(k, 15)
        k = (k * _C2) & _MASK
        h ^= k
        h = _rotl(h, 13)
        h = (h * 5 + 0xE6546B64) & _MASK
    tail = data[end:]
    if tail:
        k = int.from_bytes(tail, "little")
        k = (k * _C1) & _MASK
        k = _rotl(k, 15)
        k = (k * _C2) & _MASK
        h ^= k
    h ^= length
    h = _fmix(h)
    return h - (1 << 32) if h & 0x80000000 else h


def favicon_hash(icon: bytes) -> int:
    """Shodan's ``http.favicon.hash``: MurmurHash3 of the icon's base64, in 76-character lines.

    The line breaks (and the final one) are part of what is hashed, as in
    ``mmh3.hash(codecs.encode(icon, "base64"))``.
    """
    return murmur3_32(base64.encodebytes(icon))


__all__ = ["favicon_hash", "murmur3_32"]
