"""Reading a screenshot: its size and a perceptual hash, so look-alike pages can be found.

The hash is a 64-bit **dHash**: the picture in greys, shrunk to 9 x 8, and
one bit per pair of neighbouring pixels in a row (is the left one brighter?).
Pages that look alike get hashes a few bits apart, whatever their size,
compression or small changes (a date, a counter); :func:`distance` counts the
differing bits. As 16 hexadecimal digits, the form the URL's
``screenshot_phash`` property takes.
"""

import io
from dataclasses import dataclass

from PIL import Image

HASH_WIDTH, HASH_HEIGHT = 9, 8
# Pages at most this many bits apart are shown as looking alike.
SIMILAR_BITS = 10


@dataclass(frozen=True)
class Picture:
    width: int
    height: int
    phash: str


def read_picture(data: bytes) -> Picture:
    """The size and dHash of an image (PNG or JPEG). Raises ``ValueError`` if it is not one."""
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ("PNG", "JPEG"):
                raise ValueError("Not a PNG or JPEG image.")
            width, height = image.size
            small = image.convert("L").resize((HASH_WIDTH, HASH_HEIGHT), Image.Resampling.LANCZOS)
            pixels = small.tobytes()  # one byte per grey pixel, row by row
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("Not a readable image.") from error
    bits = 0
    for row in range(HASH_HEIGHT):
        for col in range(HASH_WIDTH - 1):
            left = pixels[row * HASH_WIDTH + col]
            right = pixels[row * HASH_WIDTH + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return Picture(width=width, height=height, phash=f"{bits:016x}")


def distance(a: str, b: str) -> int:
    """How many of the 64 bits two hashes differ in."""
    return (int(a, 16) ^ int(b, 16)).bit_count()


def to_jpeg(data: bytes, *, quality: int = 80) -> bytes:
    """A PNG as a smaller JPEG (for screenshots over the evidence size limit)."""
    with Image.open(io.BytesIO(data)) as image:
        out = io.BytesIO()
        image.convert("RGB").save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()


__all__ = ["SIMILAR_BITS", "Picture", "distance", "read_picture", "to_jpeg"]
