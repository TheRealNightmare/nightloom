"""Shrink PNG screenshots to a size limit (``make screenshots``).

Each file is first saved again losslessly with the best zlib settings; if it
is still over the limit, it is reduced to a palette of at most 256 colors
(the interface is mostly flat token colors, so this is not visible), then to
fewer colors until it fits. Fails when a file cannot be brought under the
limit.

Run with Pillow, which the project does not depend on:
    uv run --no-project --with pillow==13.0.0 python scripts/optimize-png.py --max-kb 300 FILE...
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image


def encode(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True, compress_level=9)
    return buffer.getvalue()


def shrink(path: Path, limit: int) -> tuple[int, int, str]:
    raw = path.read_bytes()
    original = len(raw)
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    best = min(raw, encode(image), key=len)
    how = "lossless"
    for colors in (256, 192, 128, 96, 64):
        if len(best) <= limit:
            break
        quantized = image.quantize(
            colors=colors, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
        )
        best = encode(quantized)
        how = f"{colors} colors"
    path.write_bytes(best)
    return original, len(best), how


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-kb", type=int, default=300)
    parser.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    limit = args.max_kb * 1024
    failed = False
    for path in args.files:
        before, after, how = shrink(path, limit)
        ok = after <= limit
        failed |= not ok
        sizes = f"{before / 1024:.0f} KiB -> {after / 1024:.0f} KiB"
        print(f"{'ok  ' if ok else 'FAIL'} {path}: {sizes} ({how})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
