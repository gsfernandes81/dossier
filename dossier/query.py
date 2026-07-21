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

"""Search, filter, sort, grouping, and derived status over a set of documents.

Pure functions over in-memory ``Document`` lists (137 docs load instantly), plus
``file_status`` which resolves a document's rendition paths against the Syncthing
root. The TUI drives everything here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from dossier.model import Document, ExpiryStatus, FileStatus

# -- file resolution ---------------------------------------------------------


def resolve_path(root: Path, relative: str) -> Path:
    """Resolve a stored POSIX-relative rendition path against the local root."""
    return root.joinpath(*PurePosixPath(relative).parts)


def file_status(doc: Document, root: Path) -> FileStatus:
    """Whether the document's rendition files resolve on disk.

    ``NONE`` if it has no renditions, ``OK`` if every rendition exists, else
    ``MISSING`` (at least one linked file is absent).
    """
    if not doc.files:
        return FileStatus.NONE
    for rendition in doc.files:
        if not resolve_path(root, rendition.path).exists():
            return FileStatus.MISSING
    return FileStatus.OK


# -- filtering ---------------------------------------------------------------


@dataclass(frozen=True)
class Filter:
    """A conjunction of predicates over a document.

    ``text`` is a case-insensitive substring over name/notes/tags/bundles.
    ``tags`` match hierarchically (``marine`` also matches ``marine/coc``) and
    are AND-ed; ``bundles`` are AND-ed; ``locations`` and ``expiry`` are OR-ed
    within themselves (effective location / status must be one of the given).
    """

    text: str = ""
    tags: tuple[str, ...] = ()
    bundles: tuple[str, ...] = ()
    locations: tuple[str | None, ...] = ()
    expiry: tuple[ExpiryStatus, ...] = ()


def matches(doc: Document, flt: Filter, *, today: date, threshold_days: int) -> bool:
    return (
        (not flt.text or _text_matches(doc, flt.text))
        and all(_has_tag(doc.tags, tag) for tag in flt.tags)
        and all(bundle in doc.bundles for bundle in flt.bundles)
        and (not flt.locations or doc.effective_location in flt.locations)
        and (not flt.expiry or doc.expiry_status(today, threshold_days) in flt.expiry)
    )


def search(
    docs: list[Document], flt: Filter, *, today: date, threshold_days: int
) -> list[Document]:
    return [
        doc
        for doc in docs
        if matches(doc, flt, today=today, threshold_days=threshold_days)
    ]


def _text_matches(doc: Document, text: str) -> bool:
    needle = text.casefold()
    hay = " ".join([doc.name, doc.notes, *doc.tags, *doc.bundles]).casefold()
    return needle in hay


def _has_tag(tags: list[str], wanted: str) -> bool:
    prefix = f"{wanted}/"
    return any(tag == wanted or tag.startswith(prefix) for tag in tags)


# -- sorting & grouping ------------------------------------------------------


def sort_key(doc: Document) -> tuple[bool, str, bool, int, bool, int, str]:
    """Sort by effective location -> slot -> subslot -> name; empties sort last.

    Each ``... is None`` flag sorts real values before empty ones (so slot 0 —
    a real position — precedes an unset slot).
    """
    loc = doc.effective_location
    slot = doc.effective_slot
    subslot = doc.effective_subslot
    return (
        loc is None,
        loc or "",
        slot is None,
        slot or 0,
        subslot is None,
        subslot or 0,
        doc.name.casefold(),
    )


def sort_documents(docs: list[Document]) -> list[Document]:
    return sorted(docs, key=sort_key)


def group_by_location(
    docs: list[Document],
) -> list[tuple[str | None, list[Document]]]:
    """Group sorted documents by effective location (unlocated group last)."""
    groups: dict[str | None, list[Document]] = {}
    for doc in sort_documents(docs):
        groups.setdefault(doc.effective_location, []).append(doc)
    return list(groups.items())


# -- expiry ------------------------------------------------------------------


def expiring(
    docs: list[Document], *, today: date, threshold_days: int
) -> list[Document]:
    """Expired + expiring-soon documents, most urgent (soonest date) first."""
    flagged = [
        doc
        for doc in docs
        if doc.expiry_status(today, threshold_days)
        in (ExpiryStatus.EXPIRED, ExpiryStatus.EXPIRING)
    ]
    return sorted(
        flagged, key=lambda d: (d.expiry_date is None, d.expiry_date or today)
    )


# -- display views -----------------------------------------------------------


@dataclass(frozen=True)
class DocumentView:
    """A document paired with its runtime-derived statuses, for display."""

    document: Document
    expiry: ExpiryStatus
    file: FileStatus


def view(
    doc: Document, *, root: Path, today: date, threshold_days: int
) -> DocumentView:
    return DocumentView(
        document=doc,
        expiry=doc.expiry_status(today, threshold_days),
        file=file_status(doc, root),
    )


def views(
    docs: list[Document], *, root: Path, today: date, threshold_days: int
) -> list[DocumentView]:
    return [
        view(doc, root=root, today=today, threshold_days=threshold_days) for doc in docs
    ]
