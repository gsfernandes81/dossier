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
    """A named set of documents gathered for an application or trip."""

    slug: str
    title: str
    export_dir: str | None = None
    notes: str = ""


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
