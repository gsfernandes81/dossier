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

"""Infer succession (renewals) from :mod:`dossier.scan` readings.

Pure, like :mod:`dossier.dedup`: :func:`propose` groups documents by what the VLM
read — the same document *type* and *holder* — sorts each group by date, and
proposes that each version supersedes the one before it. Review-only: the caller
surfaces these in the reconcile view, and accepting sets the ``supersedes`` link a
user would otherwise pick by hand. A pair whose link is already set drops out.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from dateutil import parser as du_parser

from dossier.model import Document
from dossier.scan import ScanReading


@dataclass(frozen=True)
class Succession:
    """A proposed "``newer`` supersedes ``older``" link between two documents."""

    newer: str  # document id of the renewal
    older: str  # document id it replaces
    document_type: str
    confidence: float
    rationale: str

    @property
    def key(self) -> str:
        return f"{self.newer}\x00{self.older}"


def propose(docs: list[Document], readings: dict[str, ScanReading]) -> list[Succession]:
    """Proposed successions from the scan readings, best-confidence first.

    Clusters documents whose read *type* is similar (token subset or high overlap —
    "Certificate of Competency" and "…(CoC) Card" belong together), sorts each
    cluster by date, and proposes ``members[i]`` supersedes ``members[i-1]``. A
    mismatched holder lowers confidence but never splits a cluster (the store is one
    owner). Pairs whose ``supersedes`` link is already set are omitted.
    """
    by_id = {doc.id: doc for doc in docs}
    entries: list[tuple[Document, ScanReading, date]] = []
    for doc in docs:
        reading = readings.get(doc.id)
        if reading is None:
            continue
        when = _parse(reading.issue_date_text) or _parse(reading.expiry_date_text)
        if when is not None and _norm_type(reading.document_type):
            entries.append((doc, reading, when))

    parent = list(range(len(entries)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            if _similar_type(entries[i][1].document_type, entries[j][1].document_type):
                parent[find(i)] = find(j)

    clusters: dict[int, list[tuple[Document, ScanReading, date]]] = defaultdict(list)
    for i, entry in enumerate(entries):
        clusters[find(i)].append(entry)

    out: list[Succession] = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: m[2])
        for (older, older_r, older_d), (newer, newer_r, newer_d) in zip(
            members, members[1:], strict=False
        ):
            if newer_d <= older_d or by_id[newer.id].supersedes == older.id:
                continue  # not strictly newer, or already linked
            same_holder = _similar_holder(older_r.holder_name, newer_r.holder_name)
            confidence = min(older_r.confidence, newer_r.confidence) * (
                1.0 if same_holder else 0.6
            )
            note = "" if same_holder else "  (holder differs)"
            out.append(
                Succession(
                    newer=newer.id,
                    older=older.id,
                    document_type=newer_r.document_type,
                    confidence=round(confidence, 3),
                    rationale=(f"{older_d.isoformat()} -> {newer_d.isoformat()}{note}"),
                )
            )
    out.sort(key=lambda s: s.confidence, reverse=True)
    return out


def _tokens(text: str | None) -> set[str]:
    return set(_norm_type(text).split())


def _similar_type(a: str | None, b: str | None) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    small, large = sorted((ta, tb), key=len)
    return small <= large or len(ta & tb) / len(ta | tb) >= 0.6


def _similar_holder(a: str | None, b: str | None) -> bool:
    ta = set(_norm_holder(a).split())
    tb = set(_norm_holder(b).split())
    if not ta or not tb:
        return True  # unknown holder — don't penalise (the store is one owner)
    small, large = sorted((ta, tb), key=len)
    return small <= large or len(ta & tb) >= 2


def _parse(text: str | None) -> date | None:
    if not text:
        return None
    try:  # dayfirst — the store's docs are UK/marine (DD/MM); fuzzy for "27TH MAR"
        return du_parser.parse(text, dayfirst=True, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _norm_type(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _norm_holder(text: str | None) -> str:
    # Order-insensitive: a card printing "Fernandes Gavin" must match "Gavin Fernandes".
    tokens = re.sub(r"[^a-z ]+", " ", (text or "").lower()).split()
    return " ".join(sorted(tokens))
