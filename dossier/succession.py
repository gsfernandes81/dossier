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

Pure, like :mod:`dossier.dedup`. :func:`propose` treats two documents as the same
credential when they share a real document number, or their type-*core* (generic
"certificate of …" words dropped) overlaps AND their issuer and holder are
compatible — gating on issuer + holder, not type alone, is what stops a shared
word like "Certificate" from chaining unrelated documents. Each credential's
versions sort by date; version *i* is proposed to supersede *i-1*. Review-only:
the reconcile view surfaces these and accepting sets the ``supersedes`` link a
user would otherwise pick by hand; already-linked pairs drop out.

Verified against a real 137-doc store: recovers the CoC-card, ENG-1 medical, and
BRP renewal chains. Genuinely ambiguous cases stay (a *series* like sea-service
testimonials looks like renewals; distinct-but-related courses can merge) — which
is why acceptance is always a human decision.
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


# Generic words a scanned type shares with unrelated documents ("Certificate of
# X"); dropping them keeps "competency" from matching "training" via the shell.
_TYPE_STOP = frozenset(
    {
        "of",
        "in",
        "for",
        "the",
        "and",
        "a",
        "an",
        "to",
        "card",
        "certificate",
        "cert",
        "form",
        "issued",
        "copy",
        "dated",
        "proficiency",
        "updated",
        "document",
    }
)


def propose(docs: list[Document], readings: dict[str, ScanReading]) -> list[Succession]:
    """Proposed successions from the scan readings, best-confidence first.

    Two documents are the *same credential* (and so cluster) when they share a real
    document number, OR their type-core (generic "certificate of …" words dropped)
    overlaps AND their issuer and holder are compatible. Requiring issuer + holder —
    not type alone — is what stops a shared word like "Certificate" from chaining
    unrelated documents together. Each cluster is sorted by date and proposes
    ``members[i]`` supersedes ``members[i-1]``; already-linked pairs drop out.
    """
    by_id = {doc.id: doc for doc in docs}
    entries: list[tuple[Document, ScanReading, date]] = []
    for doc in docs:
        reading = readings.get(doc.id)
        if reading is None:
            continue
        when = _parse(reading.issue_date_text) or _parse(reading.expiry_date_text)
        if when is not None and (_type_core(reading.document_type) or _number(reading)):
            entries.append((doc, reading, when))

    parent = list(range(len(entries)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            if _same_credential(entries[i][1], entries[j][1]):
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
            shared_no = bool(_number(older_r) and _number(older_r) == _number(newer_r))
            same_holder = _similar_holder(older_r.holder_name, newer_r.holder_name)
            confidence = min(older_r.confidence, newer_r.confidence)
            confidence *= 1.0 if shared_no else 0.9
            confidence *= 1.0 if same_holder else 0.6
            note = " (same no.)" if shared_no else ""
            note += "" if same_holder else " (holder differs)"
            out.append(
                Succession(
                    newer=newer.id,
                    older=older.id,
                    document_type=newer_r.document_type,
                    confidence=round(confidence, 3),
                    rationale=f"{older_d.isoformat()} -> {newer_d.isoformat()}{note}",
                )
            )
    out.sort(key=lambda s: s.confidence, reverse=True)
    return out


def _same_credential(a: ScanReading, b: ScanReading) -> bool:
    if _number(a) and _number(a) == _number(b):
        return True  # a shared document number is decisive; a *differing* one is
        # not (a renewal is usually issued a NEW number) — fall through to fields.
    ca, cb = _type_core(a.document_type), _type_core(b.document_type)
    if not ca or not cb:
        return False
    small, large = sorted((ca, cb), key=len)
    type_ok = small <= large or (bool(ca & cb) and len(ca & cb) / len(ca | cb) >= 0.5)
    return (
        type_ok
        and _compatible(a.issuer, b.issuer)
        and _similar_holder(a.holder_name, b.holder_name)
    )


def _number(reading: ScanReading) -> str:
    # A usable id has digits and length — "Annex B" / "NA" are form labels, not ids.
    raw = re.sub(r"[^a-z0-9]", "", (reading.document_number or "").lower())
    return raw if len(raw) >= 4 and any(c.isdigit() for c in raw) else ""


def _type_core(text: str | None) -> frozenset[str]:
    return frozenset(
        t for t in _norm_type(text).split() if t not in _TYPE_STOP and len(t) > 1
    )


def _compatible(a: str | None, b: str | None) -> bool:
    """Issuer compatibility — subset or ≥2 shared tokens; unknown never blocks."""
    ta = frozenset(_norm_type(a).split()) - _TYPE_STOP
    tb = frozenset(_norm_type(b).split()) - _TYPE_STOP
    if not ta or not tb:
        return True
    small, large = sorted((ta, tb), key=len)
    return small <= large or len(ta & tb) >= 2


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
