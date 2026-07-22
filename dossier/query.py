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

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from dossier.model import Bundle, Document, ExpiryStatus, FileStatus

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


# -- bundles -----------------------------------------------------------------


def bundle_sort_key(bundle: Bundle) -> tuple[bool, date, str, str]:
    """Sort chronologically: by ``date``, else ``created``, else title; empty last.

    The ``created`` tiebreak is compared as an ISO string to sidestep any
    aware/naive datetime mismatch from a hand-edited ``created``.
    """
    effective = bundle.date or (bundle.created.date() if bundle.created else None)
    created = bundle.created.isoformat() if bundle.created else ""
    return (effective is None, effective or date.min, created, bundle.title.casefold())


def sort_bundles(bundles: Iterable[Bundle]) -> list[Bundle]:
    return sorted(bundles, key=bundle_sort_key)


def group_bundles(bundles: Iterable[Bundle]) -> list[tuple[str | None, list[Bundle]]]:
    """Group chronologically-sorted bundles by top slug segment.

    ``travel/india-2024`` and ``travel/bali-2025`` share the ``"travel"`` group;
    a flat slug (``us-visa``) falls into the ``None`` group, ordered last so
    legacy flat bundles don't each spawn a one-row header.
    """
    groups: dict[str | None, list[Bundle]] = {}
    for bundle in sort_bundles(bundles):
        top = bundle.slug.split("/", 1)[0] if "/" in bundle.slug else None
        groups.setdefault(top, []).append(bundle)
    named: list[tuple[str | None, list[Bundle]]] = [
        (key, groups[key]) for key in sorted(k for k in groups if k is not None)
    ]
    if None in groups:
        named.append((None, groups[None]))
    return named


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
    return sorted(flagged, key=lambda d: _expiry_order(d, today))


def _expiry_order(doc: Document, today: date) -> tuple[bool, date]:
    """Sort key for the expiry lists: soonest date first, undated last."""
    return (doc.expiry_date is None, doc.expiry_date or today)


# -- supersession & the expiry watch -----------------------------------------


def superseded_ids(docs: list[Document]) -> set[str]:
    """Ids of documents that some *other* document supersedes (renewed away).

    A document is superseded iff another document's ``supersedes`` points at it.
    Membership does not require the target to exist, so a dangling ``supersedes``
    marks nothing here (``doctor`` reports that separately).
    """
    out: set[str] = set()
    for doc in docs:
        if doc.supersedes:
            out.add(doc.supersedes)
    return out


def tracked(docs: list[Document], *, today: date) -> list[Document]:
    """The expiry-watch list — tracked documents, soonest expiry first.

    Opt-out watch (see :meth:`Document.is_expiry_tracked`): a document is
    included iff it has an expiry date and is neither explicitly ignored nor
    superseded by a newer document. Ignored and superseded documents are hidden.
    Callers colour a row red only within the warn window; membership here is not
    gated on it.
    """
    superseded = superseded_ids(docs)
    watched = [
        doc for doc in docs if doc.is_expiry_tracked(superseded=doc.id in superseded)
    ]
    return sorted(watched, key=lambda d: _expiry_order(d, today))


def supersession_chain(docs: list[Document], doc: Document) -> list[Document]:
    """The documents ``doc`` supersedes, transitively — newest replaced first.

    Follows ``supersedes`` links (``doc`` -> the doc it replaced -> the one that
    replaced, ...), excluding ``doc`` itself. Cycle-safe (stops on a revisit) and
    stops at the first dangling link.
    """
    by_id = {d.id: d for d in docs}
    chain: list[Document] = []
    seen: set[str] = {doc.id}
    current = doc.supersedes
    while current is not None and current not in seen and current in by_id:
        seen.add(current)
        nxt = by_id[current]
        chain.append(nxt)
        current = nxt.supersedes
    return chain


# -- moves -------------------------------------------------------------------


def plan_move(
    docs: list[Document], moving: Document, location: str | None, slot: int | None
) -> list[Document]:
    """Move ``moving`` to ``location``/``slot``, shifting neighbours to insert.

    Mutates the affected documents in place and returns them (the shifted
    neighbours plus ``moving``) so the caller can persist each. When ``slot`` is
    given, every other doc already at that permanent slot or later in the same
    location is bumped by one to open the gap.
    """
    changed: list[Document] = []
    if slot is not None:
        for doc in docs:
            if (
                doc.id != moving.id
                and doc.perm_location == location
                and doc.perm_slot is not None
                and doc.perm_slot >= slot
            ):
                doc.perm_slot += 1
                changed.append(doc)
    moving.perm_location = location
    moving.perm_slot = slot
    moving.perm_subslot = None
    changed.append(moving)
    return changed


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
