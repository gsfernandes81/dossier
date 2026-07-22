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

"""Domain models: documents, renditions, locations, bundles.

These are pure data types — no I/O. Persistence lives in :mod:`dossier.store`.
"""

from __future__ import annotations

import datetime as dt  # `dt.date`/`dt.datetime` — the Bundle.date field shadows `date`
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum


class ExpiryStatus(StrEnum):
    """Where a document's expiry date sits relative to today."""

    EXPIRED = "expired"
    EXPIRING = "expiring"
    OK = "ok"
    NONE = "none"


class FileStatus(StrEnum):
    """Whether a document's linked soft-copy file resolves on disk."""

    OK = "ok"
    MISSING = "missing"
    NONE = "none"


@dataclass
class Rendition:
    """A digital version of a document, e.g. 'complete' vs 'front-and-back'.

    ``path`` is POSIX and relative to the device's Syncthing root.
    """

    label: str
    path: str
    primary: bool = False


@dataclass
class Location:
    """A physical storage location (folder / pouch / file), keyed by slug."""

    slug: str
    title: str
    notes: str = ""


@dataclass
class Bundle:
    """A named set of documents gathered for an application or trip.

    ``date`` is the trip / joining / application date (user-set); ``created`` is
    stamped by the store on first save. Bundles sort by ``date`` else ``created``,
    so a bundles surface reads chronologically.
    """

    slug: str
    title: str
    date: dt.date | None = None
    created: dt.datetime | None = None
    export_dir: str | None = None
    notes: str = ""


@dataclass
class ReconcileState:
    """Persisted reconcile decisions — the ``.dossier/reconcile.toml`` sidecar.

    Machine-owned (unlike the hand-editable ``config.toml``): the reconcile
    screen writes it to remember what the user has already judged, so decisions
    survive re-runs. Every field is a *suppression* — nothing here ever writes a
    document or touches a real file.

    * ``dismissed`` — orphan relpaths the user rejected as non-documents.
    * ``ignore`` — extra reconcile-scope globs added from the TUI, unioned with
      ``config.toml``'s ``ignore`` (kept apart so the human-owned config keeps
      its comments).
    * ``missing_ok`` — ``path → {doc ids}`` whose lost rendition is acknowledged.
    * ``folded`` — ``keep path → {confirmed duplicate/subset paths}``.
    """

    dismissed: set[str] = field(default_factory=set)
    ignore: list[str] = field(default_factory=list)
    missing_ok: dict[str, set[str]] = field(default_factory=dict)
    folded: dict[str, set[str]] = field(default_factory=dict)

    def suppressed_orphans(self) -> frozenset[str]:
        """Orphan paths to hide: the dismissed set plus every folded subset."""
        hidden = set(self.dismissed)
        for subsets in self.folded.values():
            hidden |= subsets
        return frozenset(hidden)

    def is_acked(self, doc_id: str, path: str) -> bool:
        """Whether this document's missing rendition at ``path`` was acknowledged."""
        return doc_id in self.missing_ok.get(path, frozenset())

    def covers(self, keep: str, subsets: Sequence[str]) -> bool:
        """Whether a scanned cluster is fully accounted for by a fold decision.

        Suppress iff the same ``keep`` was folded *and* every current subset was
        recorded — so a new copy (an unrecorded subset) resurfaces the whole
        cluster for a fresh decision.
        """
        recorded = self.folded.get(keep)
        return recorded is not None and set(subsets) <= recorded


class SuggestedField(StrEnum):
    """A document field a suggestion can propose a value for."""

    ISSUE = "issue_date"
    EXPIRY = "expiry_date"
    NOTES = "notes"


@dataclass(frozen=True)
class Suggestion:
    """A proposed value for a document field, to accept or dismiss (never auto-written).

    ``values`` are canonical ISO date strings; more than one means the source read
    an ambiguous token (e.g. ``21-08-23``) and the user picks which. ``source``
    labels where it came from (name parsing now; vision / folders later). The
    ``key`` identifies the suggestion for dismissal — it includes the values, so a
    later re-parse yielding *different* values reopens the question.
    """

    doc_id: str
    field: SuggestedField
    values: tuple[str, ...]
    source: str = "name"
    rationale: str = ""  # display-only; never part of the key

    @property
    def key(self) -> str:
        return f"{self.doc_id}:{self.field.value}:{self.source}:{'|'.join(self.values)}"


@dataclass
class SuggestionState:
    """Persisted suggestion dismissals — the ``.dossier/suggestions.toml`` sidecar.

    Machine-owned and synced. Everything here is a *suppression*: a dismissed key
    hides a suggestion forever, but nothing here ever writes a document. Acceptance
    needs no record — a suggestion whose field is already filled simply drops out.
    A Syncthing conflict is self-healing: a lost dismissal only resurfaces a
    suggestion, never changes a document.
    """

    dismissed: set[str] = field(default_factory=set)

    def is_dismissed(self, suggestion: Suggestion) -> bool:
        return self.is_dismissed_key(suggestion.key)

    def dismiss(self, suggestion: Suggestion) -> None:
        self.dismiss_key(suggestion.key)

    # Bundle suggestions (a different shape) share this sidecar via namespaced
    # string keys — see dossier.suggest.BundleSuggestion.key ("bundle:...").
    def is_dismissed_key(self, key: str) -> bool:
        return key in self.dismissed

    def dismiss_key(self, key: str) -> None:
        self.dismissed.add(key)


@dataclass
class Document:
    """A tracked document — one logical thing, with 0+ digital renditions.

    A document has a permanent storage location and an optional temporary one;
    the ``effective_*`` properties resolve to the temporary location when set,
    otherwise the permanent one. Locations and bundles are referenced by slug.
    """

    # Store-managed identity: id is the filename stem, not written to the
    # frontmatter. Set by the store on load.
    id: str = ""
    name: str = ""
    tags: list[str] = field(default_factory=list)
    bundles: list[str] = field(default_factory=list)
    issue_date: date | None = None
    expiry_date: date | None = None
    # Opt out of the expiry watch (residual noise: old CDCs no longer in use).
    ignore_expiry: bool = False
    # Id of the document this one replaces, set when filing a renewal. The
    # superseded document is kept but excluded from the expiry watch. Whether
    # *this* document is itself superseded is a collection-level fact (some
    # other document's ``supersedes`` points here), not stored here.
    supersedes: str | None = None
    has_physical: bool = False
    has_digital: bool = False
    files: list[Rendition] = field(default_factory=list)
    perm_location: str | None = None
    perm_slot: int | None = None
    perm_subslot: int | None = None
    temp_location: str | None = None
    temp_slot: int | None = None
    temp_subslot: int | None = None
    notes: str = ""

    # Store-managed: content hash captured at load, used for optimistic-
    # concurrency checks on save. Not part of the document's value/identity.
    source_hash: str | None = field(default=None, compare=False, repr=False)

    @property
    def is_temp_located(self) -> bool:
        return self.temp_location is not None

    @property
    def effective_location(self) -> str | None:
        return self.temp_location if self.is_temp_located else self.perm_location

    @property
    def effective_slot(self) -> int | None:
        return self.temp_slot if self.is_temp_located else self.perm_slot

    @property
    def effective_subslot(self) -> int | None:
        return self.temp_subslot if self.is_temp_located else self.perm_subslot

    def primary_rendition(self) -> Rendition | None:
        """The rendition to open/export by default (primary, else the first)."""
        if not self.files:
            return None
        for rendition in self.files:
            if rendition.primary:
                return rendition
        return self.files[0]

    def expiry_status(self, today: date, threshold_days: int) -> ExpiryStatus:
        """Classify the expiry date relative to ``today`` and a warn window."""
        if self.expiry_date is None:
            return ExpiryStatus.NONE
        if self.expiry_date < today:
            return ExpiryStatus.EXPIRED
        if (self.expiry_date - today).days <= threshold_days:
            return ExpiryStatus.EXPIRING
        return ExpiryStatus.OK

    def is_expiry_tracked(self, *, superseded: bool) -> bool:
        """Whether this document takes part in the expiry watch.

        Tracking is *opt-out*: on by default for any document that has an
        expiry date and is neither explicitly ignored nor superseded by a newer
        document. ``superseded`` is a collection-level fact the caller supplies
        (some other document's ``supersedes`` points at this one).
        """
        return (
            self.expiry_date is not None and not self.ignore_expiry and not superseded
        )
