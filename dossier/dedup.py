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

"""Duplicate / superset detection by per-page perceptual hashes.

A file (a PDF or image) is reduced to a list of per-page perceptual hashes — the
rasterize + hash step lives in :mod:`dossier.dedup_hash` (optional ``[dedup]``
extra); *this* module is the pure grouping algorithm, dependency-free and fully
testable with synthetic hashes.

Two files are compared by page **containment**, fuzzy within a Hamming distance
(re-scans of the same page differ by a few bits):

* equal page sets → **duplicates**;
* one file's pages a strict subset of another's → a **fewer-pages copy** of a
  **superset** (the more complete scan).

:func:`group_files` clusters related files and names the ``keep`` — the superset
(most pages; ties break toward a canonical, non-bundle path) — so the reconcile
view can present a duplicate cluster with the copy to keep and the copies to fold
in. Clusters with no single file containing all the others are flagged
``ambiguous`` (a partial overlap for a human to judge).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

# Path fragments that mark an application/trip copy — never the canonical keep.
_BUNDLE_HINTS = ("application", "attempt", "uploaded", "submission")

# Default max per-page Hamming distance to still call two pages "the same".
# A 64-bit pHash of a re-scan typically lands within a handful of bits.
DEFAULT_MAX_DISTANCE = 6


def hamming(a: int, b: int) -> int:
    """Bit distance between two integer perceptual hashes."""
    return (a ^ b).bit_count()


def contained(small: Sequence[int], large: Sequence[int], max_distance: int) -> bool:
    """Whether every page in ``small`` matches a *distinct* page in ``large``.

    Maximum bipartite matching (Kuhn's augmenting paths): a plain greedy first-fit
    can false-negatively reject a valid assignment when two pages hash ambiguously
    close to the same candidate (e.g. blank dividers / identical footers), so it
    would miss a real subset cluster. ``small ⊆ large``.
    """
    if len(small) > len(large):
        return False
    match = [-1] * len(large)  # large index → the small index assigned to it

    def augment(i: int, seen: list[bool]) -> bool:
        for j in range(len(large)):
            if not seen[j] and hamming(small[i], large[j]) <= max_distance:
                seen[j] = True
                if match[j] == -1 or augment(match[j], seen):
                    match[j] = i
                    return True
        return False

    return all(augment(i, [False] * len(large)) for i in range(len(small)))


@dataclass(frozen=True)
class DupGroup:
    """A cluster of files that are the same document (2+ members)."""

    files: list[str]  # every file in the cluster, sorted
    keep: str  # the superset / canonical copy to keep
    subsets: list[str]  # the other copies (duplicates or fewer-pages), sorted
    ambiguous: bool  # no single file contains all the others (partial overlap)


def group_files(
    pages_by_file: Mapping[str, Sequence[int]],
    *,
    max_distance: int = DEFAULT_MAX_DISTANCE,
) -> list[DupGroup]:
    """Cluster duplicate / subset-superset files by their per-page hashes.

    ``pages_by_file`` maps a file path to its list of per-page perceptual hashes
    (in page order). Files with no pages are ignored. Returns the multi-file
    clusters, each naming the ``keep`` (superset) and the ``subsets`` to fold in.
    """
    files = sorted(f for f, pages in pages_by_file.items() if pages)
    n = len(files)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    # contains[i][j] is True iff file j's pages are contained in file i's.
    contains = [[False] * n for _ in range(n)]
    for i, a in enumerate(files):
        for j, b in enumerate(files):
            if i != j and contained(pages_by_file[a], pages_by_file[b], max_distance):
                contains[j][i] = True  # a ⊆ b
                parent[find(i)] = find(j)  # union: a and b are related

    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    groups: list[DupGroup] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        supersets = [
            i for i in members if all(contains[i][k] for k in members if k != i)
        ]
        candidates = supersets or members
        keep_i = min(
            candidates,
            key=lambda i: (-len(pages_by_file[files[i]]), _rank(files[i])),
        )
        keep = files[keep_i]
        members_files = sorted(files[i] for i in members)
        groups.append(
            DupGroup(
                files=members_files,
                keep=keep,
                subsets=[f for f in members_files if f != keep],
                ambiguous=not supersets,
            )
        )
    groups.sort(key=lambda g: g.keep)
    return groups


def _rank(path: str) -> tuple[bool, int, str]:
    """Keep-preference key (smaller wins): a non-bundle, shallower, earlier path."""
    lower = path.lower()
    in_bundle = any(hint in lower for hint in _BUNDLE_HINTS)
    return (in_bundle, path.count("/"), path)
