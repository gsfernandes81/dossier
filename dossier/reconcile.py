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

"""Reconcile the Syncthing folder against the store.

Surfaces what doesn't line up so a messy folder becomes legible:

* **orphans** — files under the root not linked to any document (each annotated
  with the document it best fuzzy-matches, if any);
* **missing** — documents whose linked file no longer resolves on disk;
* **duplicate/superset clusters** — when per-page hashes are supplied (see
  :mod:`dossier.dedup`), the same document scanned more than once.

Pure engine (like :mod:`dossier.doctor`): takes a :class:`Store` + :class:`Config`
and returns a :class:`ReconcileReport`. Nothing is written. The file walk is
scoped by the synced ``include``/``ignore`` globs and always skips ``.dossier/``,
Syncthing's own dirs, and conflict files.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch
from typing import TYPE_CHECKING

from dossier import dedup, migrate, query
from dossier.config import Config
from dossier.model import Document, ReconcileState
from dossier.store import CONFLICT_MARKER, Store

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Syncthing's own bookkeeping dirs — never documents.
_SYNC_DIRS = frozenset({".stfolder", ".stversions"})


@dataclass(frozen=True)
class Orphan:
    """A file under the root that no document links to."""

    path: str  # POSIX, relative to the root
    suggestion: str | None = None  # id of the document it best matches, if any
    score: float = 0.0


@dataclass(frozen=True)
class MissingFile:
    """A document whose linked rendition no longer resolves on disk."""

    doc_id: str
    path: str


@dataclass
class ReconcileReport:
    orphans: list[Orphan] = field(default_factory=list)
    missing: list[MissingFile] = field(default_factory=list)
    groups: list[dedup.DupGroup] | None = None  # None = dedup not run
    linked: dict[str, list[str]] = field(default_factory=dict)  # path -> doc ids


def scan_files(config: Config, extra_ignore: Sequence[str] = ()) -> list[str]:
    """Every file under the root (POSIX-relative), scoped by include/ignore globs.

    Always excludes ``.dossier/``, Syncthing dirs, and ``*.sync-conflict-*``. An
    empty ``include`` means the whole root; the ``ignore`` globs (the synced
    config's, plus any ``extra_ignore`` from the reconcile sidecar) then drop
    matches. ``fnmatch`` ``*`` crosses ``/`` here, so ``"Wallpapers/*"`` scopes a
    whole subtree.
    """
    root = config.syncthing_root
    meta = config.meta_dir
    include = config.include
    ignore = [*config.ignore, *extra_ignore]
    out: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or meta in path.parents:
            continue
        rel = path.relative_to(root).as_posix()
        if _is_sync_noise(rel):
            continue
        if include and not any(fnmatch(rel, pattern) for pattern in include):
            continue
        if any(fnmatch(rel, pattern) for pattern in ignore):
            continue
        out.append(rel)
    return sorted(out)


def run(
    store: Store,
    config: Config,
    pages_by_file: Mapping[str, Sequence[int]] | None = None,
    state: ReconcileState | None = None,
) -> ReconcileReport:
    """Build the reconcile report, filtered by the sidecar ``state`` if given.

    Stays pure — no new I/O. The caller loads ``state`` once (from
    :meth:`Store.load_reconcile`) so dismissed orphans, acknowledged-missing
    renditions, folded clusters, and sidecar ignore-globs drop out at the source.
    ``pages_by_file`` (if given) adds dup clusters.
    """
    state = state or ReconcileState()
    docs = store.load_all()
    linked: dict[str, list[str]] = {}
    for doc in docs:
        for rendition in doc.files:
            linked.setdefault(rendition.path, []).append(doc.id)

    suppressed = state.suppressed_orphans()
    orphan_paths = [
        path
        for path in scan_files(config, state.ignore)
        if path not in linked and path not in suppressed
    ]
    orphans = _with_suggestions(orphan_paths, docs)

    missing = [
        MissingFile(doc.id, rendition.path)
        for doc in docs
        for rendition in doc.files
        if not query.resolve_path(config.syncthing_root, rendition.path).exists()
        and not state.is_acked(doc.id, rendition.path)
    ]

    groups = None
    if pages_by_file is not None:
        groups = [
            group
            for group in dedup.group_files(pages_by_file)
            if not state.covers(group.keep, group.subsets)
        ]
    return ReconcileReport(
        orphans=orphans, missing=missing, groups=groups, linked=linked
    )


def _is_sync_noise(rel: str) -> bool:
    parts = rel.split("/")
    return CONFLICT_MARKER in parts[-1] or any(p in _SYNC_DIRS for p in parts[:-1])


def _with_suggestions(orphan_paths: list[str], docs: list[Document]) -> list[Orphan]:
    """Attach the best-matching document to each orphan (fuzzy name match)."""
    index = migrate.FileIndex(orphan_paths)
    best: dict[str, tuple[str, float]] = {}  # orphan path -> (doc id, score)
    for doc in docs:
        match = index.fuzzy_best(doc.name, exclude=set())
        if match is None:
            continue
        path, score = match
        current = best.get(path)
        if current is None or score > current[1]:
            best[path] = (doc.id, score)
    return [
        Orphan(path, best[path][0], best[path][1]) if path in best else Orphan(path)
        for path in orphan_paths
    ]
