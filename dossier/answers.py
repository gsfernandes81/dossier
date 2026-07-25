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

"""`ds ask` — retrieval-first answers over the document records.

Tier 0: **no model, ever.** A question is routed by intent (expiry / issue /
number / location) to the *authoritative* record field, and the target document is
found by ranking the question's residue words against a BM25 index of every
document's name, tags, notes, and scan reading (structured fields + transcript).
Questions that aren't a known intent fall back to ranked retrieval — the top
documents and why. Pure, offline, instant on any device; the optional Tier-1 text
model (`--compose`) is reserved for later and never sits in this path.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from dossier import fuzz
from dossier.model import Document, Location
from dossier.query import reading_text, superseded_ids

if TYPE_CHECKING:
    from dossier.scan import ScanReading

_TOKEN = re.compile(r"[a-z0-9]+")


def _wordset(words: str) -> frozenset[str]:
    return frozenset(words.split())


# Question words + articles that carry no retrieval signal.
_STOP = _wordset(
    "a an the my your our of for to in on at is are was were do does did have has "
    "i me we you it this that these those and or with about from as "
    "what when which who whom whose how why"
)
# Intent trigger words — also stripped before ranking the target document.
_TRIGGERS = _wordset(
    "expire expires expiry expired expiring valid until till renew renewal renews "
    "issue issued issuance issuing number no located location where"
)

_EXPIRY_RE = re.compile(r"\bexpir\w*|\bvalid (?:until|till|to)\b|\brenew")
_ISSUE_RE = re.compile(r"\bissued?\b|\bissuance\b|\bissue date\b")
_NUMBER_RE = re.compile(r"\bnumber\b|\bno\.?\b|#")
_LOCATION_RE = re.compile(r"\bwhere\b|\blocat")


def _tokens(text: str) -> list[str]:
    """Casefolded alphanumeric runs; single chars kept only if they're digits."""
    return [t for t in _TOKEN.findall(text.casefold()) if len(t) > 1 or t.isdigit()]


def _residue(question: str) -> list[str]:
    """The question's content words — stop + trigger words removed."""
    return [t for t in _tokens(question) if t not in _STOP and t not in _TRIGGERS]


@dataclass(frozen=True)
class Corpus:
    ids: list[str]
    tf: list[dict[str, int]]  # per-document term counts
    lengths: list[int]
    df: dict[str, int]  # document frequency
    avg_len: float


def build_corpus(docs: list[Document], readings: Mapping[str, ScanReading]) -> Corpus:
    """One BM25 document per record: name + tags + bundles + notes + reading text
    (structured fields *and* transcript — this is the answer corpus).

    Superseded documents are excluded — a renewed-away certificate must not be the
    answer to "when does my ENG-1 expire" (the current one is).
    """
    superseded = superseded_ids(docs)
    ids: list[str] = []
    tf: list[dict[str, int]] = []
    lengths: list[int] = []
    df: dict[str, int] = {}
    for doc in docs:
        if doc.id in superseded:
            continue
        text = " ".join([doc.name, *doc.tags, *doc.bundles, doc.notes])
        reading = readings.get(doc.id)
        if reading is not None:
            text += " " + reading_text(reading, include_content=True)
        counts: dict[str, int] = {}
        for token in _tokens(text):
            counts[token] = counts.get(token, 0) + 1
        for token in counts:
            df[token] = df.get(token, 0) + 1
        ids.append(doc.id)
        tf.append(counts)
        lengths.append(sum(counts.values()))
    avg = sum(lengths) / len(lengths) if lengths else 0.0
    return Corpus(ids, tf, lengths, df, avg)


def rank(
    corpus: Corpus,
    question_tokens: list[str],
    *,
    k: int = 5,
    k1: float = 1.5,
    b: float = 0.75,
) -> list[tuple[str, float]]:
    """Okapi BM25 — the top-``k`` (doc id, score), best first, zero-scores dropped.

    Typo-tolerant, precision-first: an in-vocabulary token scores as usual; an
    out-of-vocabulary one (a likely typo) expands to its nearest ≤3 vocabulary
    neighbours within an edit budget, contributing at a ``0.5 ** distance`` penalty —
    so an exact term always outweighs a fuzzy one and a correctly-spelled query is
    scored bit-for-bit as before.
    """
    n = len(corpus.ids)
    if n == 0:
        return []
    scores = [0.0] * n

    def add(token: str, weight: float) -> None:
        dfreq = corpus.df.get(token, 0)
        if not dfreq:
            return
        idf = math.log(1 + (n - dfreq + 0.5) / (dfreq + 0.5))
        for i, counts in enumerate(corpus.tf):
            freq = counts.get(token, 0)
            if not freq:
                continue
            denom = freq + k1 * (1 - b + b * corpus.lengths[i] / (corpus.avg_len or 1))
            scores[i] += weight * idf * (freq * (k1 + 1)) / denom

    for token in set(question_tokens):
        if corpus.df.get(token, 0):
            add(token, 1.0)
            continue
        budget = fuzz.budget(token)  # OOV → fuzzy-expand (a short token never does)
        if budget < 1:
            continue
        scored = [(fuzz.distance(token, cand, budget), cand) for cand in corpus.df]
        near = sorted((d, c) for d, c in scored if d <= budget)
        if not near:
            continue
        best = near[0][0]
        for dist, cand in near[:3]:
            if dist == best:  # only the closest neighbours, evenly penalised
                add(cand, 0.5**best)
    ranked = [(corpus.ids[i], scores[i]) for i in range(n) if scores[i] > 0]
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked[:k]


@dataclass(frozen=True)
class Answer:
    lines: tuple[str, ...]
    answered: bool


def answer(
    question: str,
    docs: list[Document],
    readings: Mapping[str, ScanReading],
    locations: Mapping[str, Location],
    *,
    today: date,
    k: int = 3,
) -> Answer:
    """Answer ``question`` from the records — intent lookup, else ranked retrieval."""
    corpus = build_corpus(docs, readings)
    by_id = {doc.id: doc for doc in docs}
    ranked = rank(corpus, _residue(question), k=max(k, 8))  # headroom for intents
    q = question.casefold()
    if _EXPIRY_RE.search(q):
        return _date_answer(ranked, by_id, readings, today, kind="expiry")
    if _ISSUE_RE.search(q):
        return _date_answer(ranked, by_id, readings, today, kind="issue")
    if _NUMBER_RE.search(q):
        return _number_answer(ranked, by_id, readings)
    if _LOCATION_RE.search(q):
        return _location_answer(ranked, by_id, locations)
    return _retrieval_answer(ranked[:k], by_id)


def _cluster(
    ranked: list[tuple[str, float]], by_id: dict[str, Document]
) -> list[Document]:
    """The clearly-matching documents — those within 80% of the top score."""
    if not ranked:
        return []
    top = ranked[0][1]
    return [by_id[i] for i, s in ranked if i in by_id and s >= 0.8 * top]


def _top(
    ranked: list[tuple[str, float]], by_id: dict[str, Document]
) -> Document | None:
    for doc_id, _score in ranked:
        if doc_id in by_id:
            return by_id[doc_id]
    return None


def _no_match() -> Answer:
    return Answer(("no match — nothing in the records answers that.",), False)


def _header(doc: Document) -> str:
    return f"{doc.id}  {doc.name or doc.id}"


def _date_answer(
    ranked: list[tuple[str, float]],
    by_id: dict[str, Document],
    readings: Mapping[str, ScanReading],
    today: date,
    *,
    kind: str,
) -> Answer:
    cluster = _cluster(ranked, by_id)
    if not cluster:
        return _no_match()
    # Prefer a record that actually carries the date, latest first (the current
    # certificate, not a duplicate/older one that only has scan text).
    if kind == "expiry":
        dated = sorted(
            (d for d in cluster if d.expiry_date is not None),
            key=lambda d: d.expiry_date or date.min,
            reverse=True,
        )
    else:
        dated = sorted(
            (d for d in cluster if d.issue_date is not None),
            key=lambda d: d.issue_date or date.min,
            reverse=True,
        )
    doc = dated[0] if dated else cluster[0]
    reading = readings.get(doc.id)
    if kind == "expiry":
        value, word = doc.expiry_date, "expires"
        text = reading.expiry_date_text if reading else None
    else:
        value, word = doc.issue_date, "issued"
        text = reading.issue_date_text if reading else None
    if value is not None:
        rel = (value - today).days
        when = f"in {rel} days" if rel >= 0 else f"{-rel} days ago"
        return Answer((_header(doc), f"  {word} {value.isoformat()} ({when})"), True)
    if text:
        return Answer(
            (_header(doc), f'  {word} "{text}" (as printed on the scan)'), True
        )
    return Answer((_header(doc), f"  no recorded {kind} date"), True)


def _number_answer(
    ranked: list[tuple[str, float]],
    by_id: dict[str, Document],
    readings: Mapping[str, ScanReading],
) -> Answer:
    doc = _top(ranked, by_id)
    if doc is None:
        return _no_match()
    reading = readings.get(doc.id)
    if reading is not None and reading.document_number:
        return Answer((_header(doc), f"  number {reading.document_number}"), True)
    return Answer((_header(doc), "  no recorded document number"), True)


def _location_answer(
    ranked: list[tuple[str, float]],
    by_id: dict[str, Document],
    locations: Mapping[str, Location],
) -> Answer:
    doc = _top(ranked, by_id)
    if doc is None:
        return _no_match()
    slug = doc.effective_location
    if slug is None:
        return Answer((_header(doc), "  no recorded location"), True)
    loc = locations.get(slug)
    where = loc.title if loc is not None else slug
    slot = f", slot {doc.effective_slot}" if doc.effective_slot is not None else ""
    carried = " (carried)" if doc.is_temp_located else ""
    return Answer((_header(doc), f"  {where}{slot}{carried}"), True)


def _retrieval_answer(
    ranked: list[tuple[str, float]], by_id: dict[str, Document]
) -> Answer:
    if not ranked:
        return _no_match()
    lines = ["top matches:"]
    for doc_id, score in ranked:
        doc = by_id.get(doc_id)
        if doc is not None:
            lines.append(f"  {doc.id}  {doc.name or doc.id}  ({score:.1f})")
    return Answer(tuple(lines), True)
