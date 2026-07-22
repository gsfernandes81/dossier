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

"""A per-device cache of page hashes, so reconcile needn't re-rasterize each run.

Keyed by ``(relpath, size, mtime_ns)`` under the platform **cache** dir — not the
synced ``.dossier/``: a hash cache is disposable, churns on every file touch, and
would just add Syncthing traffic + conflict surface. First run over a big folder
is minutes; every later run is a cache read. Writes are flushed incrementally so
an interrupted first run keeps its progress.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import cast

import platformdirs

from dossier import dedup_hash
from dossier.store import atomic_write_bytes

_VERSION = 1
_FLUSH_EVERY = 25  # persist the cache every N newly-hashed files


def cached_page_hashes(
    paths: Iterable[Path],
    root: Path,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, list[int]]:
    """Page hashes for ``paths`` (POSIX-relative keys), reusing a per-device cache.

    A cached entry is reused when the file's ``size`` + ``mtime_ns`` are unchanged;
    otherwise the file is re-hashed. Non-page files cache as ``[]`` and are omitted
    from the result. ``progress(done, total)`` is called per file if given.
    """
    cache_file = _cache_path(root)
    cache = _load(cache_file)
    paths = list(paths)
    total = len(paths)
    out: dict[str, list[int]] = {}
    dirty = 0
    for done, path in enumerate(paths, start=1):
        try:
            rel = path.relative_to(root).as_posix()
            stat = path.stat()
        except (OSError, ValueError):
            if progress is not None:
                progress(done, total)
            continue
        entry = cache.get(rel)
        cached = entry.get("pages") if isinstance(entry, dict) else None
        if (
            isinstance(entry, dict)
            and entry.get("size") == stat.st_size
            and entry.get("mtime_ns") == stat.st_mtime_ns
            and isinstance(cached, list)
        ):
            pages = cast("list[int]", cached)
        else:
            try:
                pages = dedup_hash.page_hashes(path)
            except dedup_hash.DedupError:
                raise  # missing extra — not a per-file problem, abort
            except Exception:
                pages = []  # corrupt / unreadable — cache empty so we skip it next time
            cache[rel] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "pages": pages,
            }
            dirty += 1
            if dirty % _FLUSH_EVERY == 0:
                _save(cache_file, cache)
        if pages:
            out[rel] = pages
        if progress is not None:
            progress(done, total)
    if dirty:
        _save(cache_file, cache)
    return out


def _cache_path(root: Path) -> Path:
    digest = hashlib.sha1(str(root).encode("utf-8")).hexdigest()
    base = Path(platformdirs.user_cache_dir("dossier", appauthor=False))
    return base / "page-hashes" / f"{digest}.json"


def _load(path: Path) -> dict[str, dict[str, object]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("version") != _VERSION:
        return {}
    files = data.get("files")
    return files if isinstance(files, dict) else {}


def _save(path: Path, files: dict[str, dict[str, object]]) -> None:
    payload = json.dumps({"version": _VERSION, "files": files}).encode("utf-8")
    atomic_write_bytes(path, payload)
