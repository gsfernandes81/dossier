# Copyright © 2026-present gsfernandes81
#
# This file is part of "dossier".
#
# dossier is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
#
# dossier is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License along with
# dossier. If not, see <https://www.gnu.org/licenses/>.

"""Perceptual page hashes for the dedup engine (optional ``[dedup]`` extra).

Rasterizes a PDF/image to a small greyscale and computes a 64-bit **difference
hash (dHash)** per page — robust to scaling / recompression, cheap, and free of
heavy numeric deps (just Pillow + a PDF rasterizer). The rasterizer is imported
lazily, so importing this module never requires the extra; only *using* it does:
``pip install 'dossier[dedup]'``.

The bit-packing (:func:`dhash_from_grey`) is pure and unit-tested; the Pillow /
pypdfium2 adapters are thin and desktop-only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from dossier.errors import DossierError

_MISSING = (
    "dedup needs the [dedup] extra — pip install 'dossier[dedup]' (pypdfium2 + Pillow)"
)

_PDF_SUFFIXES = {".pdf"}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
_W, _H = 9, 8  # a 9x8 grey grid → 8x8 left>right comparisons → 64 bits


class DedupError(DossierError):
    """A dedup page-hash could not be computed (usually the missing extra)."""


def page_hashes(path: Path) -> list[int]:
    """Per-page dHashes for a PDF/image; ``[]`` for a non-page-bearing file."""
    suffix = path.suffix.lower()
    if suffix in _PDF_SUFFIXES:
        return _pdf_hashes(path)
    if suffix in _IMAGE_SUFFIXES:
        return [_image_hash(path)]
    return []


def hashes_for_files(paths: Iterable[Path]) -> dict[str, list[int]]:
    """Map each readable page-bearing file to its per-page hashes.

    Unreadable / corrupt files are skipped so one bad file can't abort a batch;
    a missing ``[dedup]`` extra raises :class:`DedupError` (it's not per-file).
    """
    out: dict[str, list[int]] = {}
    for path in paths:
        try:
            hashes = page_hashes(path)
        except DedupError:
            raise
        except Exception:
            continue
        if hashes:
            out[path.as_posix()] = hashes
    return out


def dhash_from_grey(pixels: Sequence[int]) -> int:
    """A 64-bit dHash from a row-major ``9x8`` greyscale (``_W*_H`` values)."""
    bits = 0
    for row in range(_H):
        base = row * _W
        for col in range(_W - 1):
            bits = (bits << 1) | int(pixels[base + col] > pixels[base + col + 1])
    return bits


def _dhash_image(img: Any) -> int:
    grey = img.convert("L").resize((_W, _H))
    return dhash_from_grey(list(grey.getdata()))


def _image_hash(path: Path) -> int:
    image_mod = _load_pillow()
    with image_mod.open(path) as img:
        return _dhash_image(img)


def _pdf_hashes(path: Path) -> list[int]:
    pdfium = _load_pdfium()
    pdf = pdfium.PdfDocument(str(path))
    try:
        return [
            _dhash_image(pdf[i].render(scale=1.0).to_pil()) for i in range(len(pdf))
        ]
    finally:
        pdf.close()


def _load_pdfium() -> Any:
    try:
        import pypdfium2
    except ImportError as exc:
        raise DedupError(_MISSING) from exc
    return pypdfium2


def _load_pillow() -> Any:
    try:
        from PIL import Image
    except ImportError as exc:
        raise DedupError(_MISSING) from exc
    return Image
