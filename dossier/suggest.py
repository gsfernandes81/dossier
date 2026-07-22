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

"""Suggestions engine: propose (never write) document fields from its name.

Pure, like :mod:`dossier.doctor` — :func:`for_document` turns a document's name
into candidate field values; :func:`live` is the seam the UI calls, dropping
suggestions the user has dismissed or already satisfied. Name parsing is only ever
a *suggestion* here: it never decides whether a document has an expiry, and a
date **range** in a name is treated as a period (its span goes to notes) unless
the authoritative expiry confirms it as a validity window.

This module owns all name-date intelligence: the migration stopped parsing dates
from names once its issue-date writing was demoted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath

from dateutil import parser as du_parser

from dossier.doctor import candidate_readings
from dossier.migrate import slugify
from dossier.model import (
    Bundle,
    Document,
    SuggestedField,
    Suggestion,
    SuggestionState,
)

_DATE_TOKEN = re.compile(
    r"("
    r"\d{1,2}[-/][A-Za-z]{3,9}[-/]\d{2,4}"  # 07-Jan-2026
    r"|\d{4}[-/][A-Za-z]{3,9}[-/]\d{1,2}"  # 2020-dec-11
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"  # 10-07-26
    r"|\d{4}[-/]\d{1,2}[-/]\d{1,2}"  # 2019-05-19
    r")"
)


def _parse_token(token: str) -> date | None:
    # ISO-style tokens lead with a 4-digit year and must NOT be read dayfirst, or
    # "2022-01-06" flips to 2022-06-01. Only DD-first numeric tokens are dayfirst.
    iso_like = bool(re.match(r"\d{4}[-/]", token))
    try:
        parsed = du_parser.parse(token, dayfirst=not iso_like, yearfirst=iso_like)
    except (ValueError, OverflowError, TypeError):
        return None
    return parsed.date()


def for_document(doc: Document) -> list[Suggestion]:
    """Name-derived suggestions for ``doc``, ignoring dismissals and current values.

    The decision table (deliberately conservative — the name never *decides*
    expiry on its own):

    * a ``… to …`` **range** whose second date equals the authoritative
      ``expiry_date`` → suggest the start as the *issue* date (a confirmed
      validity window); any other range → a *notes* period suggestion, and
      **no** issue/expiry (the range start never becomes the issue date);
    * a single date with an *expiry* keyword → suggest expiry;
    * a single date with an *issue* keyword, or a bare date with neither → suggest
      *issue* (never a guessed expiry).
    """
    name = doc.name
    valid = [
        (tok, dt)
        for tok, dt in ((t, _parse_token(t)) for t in _DATE_TOKEN.findall(name))
        if dt is not None
    ]
    if not valid:
        return []
    lower = f" {name.lower()} "
    ambig = {tok: readings for tok, readings in candidate_readings(name)}

    if len(valid) >= 2 and " to " in lower:
        (start_tok, start), (end_tok, end) = valid[0], valid[1]
        if doc.expiry_date is not None and doc.expiry_date == end:
            return [
                _date_suggestion(
                    doc,
                    SuggestedField.ISSUE,
                    start_tok,
                    start,
                    ambig,
                    f"start of the '{start_tok} to {end_tok}' validity range",
                )
            ]
        return [
            Suggestion(
                doc_id=doc.id,
                field=SuggestedField.NOTES,
                values=(start.isoformat(), end.isoformat()),
                rationale="date range in the name — record as a period, not an expiry",
            )
        ]

    if "expir" in lower or "exp " in lower:
        tok, dt = valid[-1]
        return [
            _date_suggestion(
                doc,
                SuggestedField.EXPIRY,
                tok,
                dt,
                ambig,
                f"'{tok}' with an expiry keyword",
            )
        ]
    if "issue" in lower:
        tok, dt = valid[0]
        return [
            _date_suggestion(
                doc,
                SuggestedField.ISSUE,
                tok,
                dt,
                ambig,
                f"'{tok}' with an issue keyword",
            )
        ]
    tok, dt = valid[0]
    return [
        _date_suggestion(
            doc,
            SuggestedField.ISSUE,
            tok,
            dt,
            ambig,
            f"'{tok}' in the name (no keyword)",
        )
    ]


def live(doc: Document, state: SuggestionState) -> list[Suggestion]:
    """The suggestions worth showing: not dismissed, not already satisfied."""
    return [
        s
        for s in for_document(doc)
        if not state.is_dismissed(s) and not _satisfied(doc, s)
    ]


def _date_suggestion(
    doc: Document,
    field: SuggestedField,
    token: str,
    parsed: date,
    ambig: dict[str, list[date]],
    rationale: str,
) -> Suggestion:
    # Ambiguous tokens offer every reading — but only for issue dates; an expiry
    # from a name is already a stretch, so it stays single-valued.
    readings = ambig.get(token)
    if field is SuggestedField.ISSUE and readings and len(readings) >= 2:
        values = tuple(d.isoformat() for d in readings)
    else:
        values = (parsed.isoformat(),)
    return Suggestion(doc_id=doc.id, field=field, values=values, rationale=rationale)


def _satisfied(doc: Document, suggestion: Suggestion) -> bool:
    """Whether the document already carries what the suggestion proposes."""
    if suggestion.field is SuggestedField.ISSUE:
        return doc.issue_date is not None
    if suggestion.field is SuggestedField.EXPIRY:
        return doc.expiry_date is not None
    return all(value in doc.notes for value in suggestion.values)  # NOTES span


# -- folder → bundle suggestions ---------------------------------------------

# A file-path keyword → the bundle category (top slug segment) it implies. Only
# these folders become bundles; a plain category folder (Certificates/) does not.
_FOLDER_HINTS: dict[str, str] = {
    "travel": "travel",
    "visa": "visa",
    "joining": "joining",
    "application": "application",
    "attempt": "application",
    "uploaded": "application",
    "submission": "application",
}


@dataclass(frozen=True)
class BundleSuggestion:
    """A proposed bundle over the documents sharing an application/trip folder."""

    slug: str  # hierarchical, e.g. "travel/india-2024"
    title: str  # the folder's leaf name, for display
    folder: str  # the shared POSIX relpath (the rationale)
    doc_ids: tuple[str, ...]  # sorted members, for a deterministic key

    @property
    def key(self) -> str:
        return f"bundle:folder:{self.slug}:{'|'.join(self.doc_ids)}"


def bundles_from_folders(
    docs: list[Document], *, min_docs: int = 2
) -> list[BundleSuggestion]:
    """Suggest a bundle for each application/trip folder holding ≥ ``min_docs``.

    A document's folder is its primary rendition's parent dir. A folder qualifies
    only if a path component matches a :data:`_FOLDER_HINTS` keyword — so category
    folders never become bundles.
    """
    by_folder: dict[str, list[str]] = {}
    for doc in docs:
        rendition = doc.primary_rendition()
        if rendition is None:
            continue
        folder = PurePosixPath(rendition.path).parent.as_posix()
        by_folder.setdefault(folder, []).append(doc.id)

    out: list[BundleSuggestion] = []
    for folder, ids in by_folder.items():
        if len(ids) < min_docs:
            continue
        category = _folder_category(folder)
        if category is None:
            continue
        leaf = folder.rsplit("/", 1)[-1]
        out.append(
            BundleSuggestion(
                slug=f"{category}/{slugify(leaf)}",
                title=leaf,
                folder=folder,
                doc_ids=tuple(sorted(ids)),
            )
        )
    return sorted(out, key=lambda s: s.slug)


def live_bundles(
    docs: list[Document], bundles: dict[str, Bundle], state: SuggestionState
) -> list[BundleSuggestion]:
    """Folder-bundle suggestions worth showing: not already a bundle, not dismissed."""
    return [
        suggestion
        for suggestion in bundles_from_folders(docs)
        if suggestion.slug not in bundles and not state.is_dismissed_key(suggestion.key)
    ]


def _folder_category(folder: str) -> str | None:
    lower = folder.lower()
    for keyword, category in _FOLDER_HINTS.items():
        if keyword in lower:
            return category
    return None
