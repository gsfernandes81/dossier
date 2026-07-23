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

"""Field-level merge of two diverged copies — the heart of conflict resolution.

Pure, I/O-free, clock-free (mirrors :mod:`dossier.organize`/:mod:`dossier.answers`).
There is no common ancestor available, so this is a **2-way** merge:

* **agreed** — both sides equal → keep.
* **fill** — one side empty, the other has a value → take the value (nothing lost).
* **union** — both are collections (tags, sets, keyed tables) → type-aware union.
  Bias: union resurrects an item one side deleted (deletion is indistinguishable
  from never-added without a base) — chosen deliberately, keeping is recoverable.
* **contested** — both sides hold different non-empty scalars → **last-writer-wins**
  by the ``prefer`` side (computed from file mtimes by the caller), or ``tie`` →
  ours. LWW is a *policy*, not truth: the resolver archives the losing copy so a
  wrong verdict is a recoverable surprise, never a silent loss.

The suppression sidecars (:class:`ReconcileState`, :class:`SuggestionState`) are
union-only — every field is an append-only suppression, so a union honours both
users and never needs LWW or a human.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Generic, TypeVar

from dossier.model import (
    Bundle,
    Document,
    Location,
    ReconcileState,
    Rendition,
    SuggestionState,
)
from dossier.scan import ScanReading


class Side(StrEnum):
    OURS = "ours"
    THEIRS = "theirs"


@dataclass(frozen=True)
class FieldDecision:
    """How one field was resolved — for the CLI/doctor report and the TUI picker."""

    field: str
    action: str  # equal | fill | union | lww | tie
    ours: str  # display repr
    theirs: str
    winner: Side | None = None


T = TypeVar("T")
M = TypeVar("M")


@dataclass(frozen=True)
class MergeResult(Generic[M]):
    merged: M
    decisions: tuple[FieldDecision, ...]

    @property
    def contested(self) -> tuple[FieldDecision, ...]:
        """Fields decided by LWW/tie — the ones a human might want to review."""
        return tuple(d for d in self.decisions if d.action in ("lww", "tie"))

    @property
    def clean(self) -> bool:
        return not self.contested


# -- primitives --------------------------------------------------------------


def _empty(value: object) -> bool:
    return value is None or value == "" or value == () or value == []


def _ordered_union(ours: list[T], theirs: list[T]) -> list[T]:
    out = list(ours)
    for item in theirs:
        if item not in out:
            out.append(item)
    return out


class _Log:
    """Collects field decisions while a merge walks a shape."""

    def __init__(self, prefer: Side, tie: bool) -> None:
        self._prefer = prefer
        self._tie = tie
        self.decisions: list[FieldDecision] = []

    def scalar(self, field: str, ours: object, theirs: object) -> object:
        """Merge one scalar field: agreed / fill / contested (LWW)."""
        if ours == theirs:
            self.decisions.append(FieldDecision(field, "equal", str(ours), str(theirs)))
            return ours
        if _empty(ours):
            self.decisions.append(
                FieldDecision(field, "fill", str(ours), str(theirs), Side.THEIRS)
            )
            return theirs
        if _empty(theirs):
            self.decisions.append(
                FieldDecision(field, "fill", str(ours), str(theirs), Side.OURS)
            )
            return ours
        winner = Side.OURS if self._tie else self._prefer
        action = "tie" if self._tie else "lww"
        self.decisions.append(
            FieldDecision(field, action, str(ours), str(theirs), winner)
        )
        return theirs if winner is Side.THEIRS else ours

    def union(self, field: str, ours: list[T], theirs: list[T]) -> list[T]:
        merged = _ordered_union(ours, theirs)
        if merged != list(ours) or merged != list(theirs):
            self.decisions.append(
                FieldDecision(field, "union", str(len(ours)), str(len(theirs)))
            )
        return merged

    def pick(self) -> Side:
        return Side.OURS if self._tie else self._prefer

    @property
    def is_tie(self) -> bool:
        return self._tie


# -- documents ---------------------------------------------------------------


def merge_documents(
    ours: Document,
    theirs: Document,
    *,
    prefer: Side,
    tie: bool = False,
) -> MergeResult[Document]:
    """Field-merge two copies of one document (same id)."""
    log = _Log(prefer, tie)
    tags = log.union("tags", ours.tags, theirs.tags)
    bundles = log.union("bundles", ours.bundles, theirs.bundles)
    files = _merge_renditions(ours.files, theirs.files, log)
    merged = replace(
        ours,
        name=log.scalar("name", ours.name, theirs.name),
        tags=tags,
        bundles=bundles,
        issue_date=log.scalar("issue_date", ours.issue_date, theirs.issue_date),
        expiry_date=log.scalar("expiry_date", ours.expiry_date, theirs.expiry_date),
        ignore_expiry=log.scalar(
            "ignore_expiry", ours.ignore_expiry, theirs.ignore_expiry
        ),
        supersedes=log.scalar("supersedes", ours.supersedes, theirs.supersedes),
        has_physical=log.scalar("has_physical", ours.has_physical, theirs.has_physical),
        has_digital=log.scalar("has_digital", ours.has_digital, theirs.has_digital),
        files=files,
        perm_location=log.scalar(
            "perm_location", ours.perm_location, theirs.perm_location
        ),
        perm_slot=log.scalar("perm_slot", ours.perm_slot, theirs.perm_slot),
        perm_subslot=log.scalar("perm_subslot", ours.perm_subslot, theirs.perm_subslot),
        temp_location=log.scalar(
            "temp_location", ours.temp_location, theirs.temp_location
        ),
        temp_slot=log.scalar("temp_slot", ours.temp_slot, theirs.temp_slot),
        temp_subslot=log.scalar("temp_subslot", ours.temp_subslot, theirs.temp_subslot),
        notes=log.scalar("notes", ours.notes, theirs.notes),
        source_hash=None,  # a merged doc is new content; save() re-hashes
    )
    return MergeResult(merged, tuple(log.decisions))


def _merge_renditions(
    ours: list[Rendition], theirs: list[Rendition], log: _Log
) -> list[Rendition]:
    """Union renditions by path; a same-path contested rendition is LWW'd whole,
    then at most one stays primary."""
    by_path: dict[str, Rendition] = {r.path: r for r in ours}
    for r in theirs:
        if r.path not in by_path:
            by_path[r.path] = r
            log.decisions.append(
                FieldDecision(f"files[{r.path}]", "union", "-", r.label)
            )
        elif by_path[r.path] != r:
            ours_r = by_path[r.path]
            winner = log.pick()
            by_path[r.path] = r if winner is Side.THEIRS else ours_r
            log.decisions.append(
                FieldDecision(
                    f"files[{r.path}]",
                    "tie" if log.is_tie else "lww",
                    ours_r.label,
                    r.label,
                    winner,
                )
            )
    out = list(by_path.values())
    seen_primary = False
    for i, r in enumerate(out):
        if r.primary and seen_primary:
            out[i] = replace(r, primary=False)
        elif r.primary:
            seen_primary = True
    return out


# -- suppression sidecars (union-only, never contested) ----------------------


def merge_suggestions(
    ours: SuggestionState, theirs: SuggestionState
) -> MergeResult[SuggestionState]:
    """Union the dismissed sets — pure append-only suppression, always safe."""
    merged = SuggestionState(dismissed=ours.dismissed | theirs.dismissed)
    changed = merged.dismissed != ours.dismissed or merged.dismissed != theirs.dismissed
    decisions = (
        (
            FieldDecision(
                "dismissed",
                "union",
                str(len(ours.dismissed)),
                str(len(theirs.dismissed)),
            ),
        )
        if changed
        else ()
    )
    return MergeResult(merged, decisions)


def merge_reconcile(
    ours: ReconcileState, theirs: ReconcileState
) -> MergeResult[ReconcileState]:
    """Union every field — all are suppressions, so union honours both users."""
    decisions: list[FieldDecision] = []

    def note(field: str, a: object, b: object) -> None:
        if a != b:
            decisions.append(FieldDecision(field, "union", str(a), str(b)))

    note("dismissed", len(ours.dismissed), len(theirs.dismissed))
    note(
        "succession_dismissed",
        len(ours.succession_dismissed),
        len(theirs.succession_dismissed),
    )
    merged = ReconcileState(
        dismissed=ours.dismissed | theirs.dismissed,
        ignore=_ordered_union(ours.ignore, theirs.ignore),
        missing_ok=_merge_map_of_sets(ours.missing_ok, theirs.missing_ok),
        folded=_merge_map_of_sets(ours.folded, theirs.folded),
        succession_dismissed=ours.succession_dismissed | theirs.succession_dismissed,
    )
    return MergeResult(merged, tuple(decisions))


def _merge_map_of_sets(
    ours: dict[str, set[str]], theirs: dict[str, set[str]]
) -> dict[str, set[str]]:
    out = {key: set(values) for key, values in ours.items()}
    for key, values in theirs.items():
        out.setdefault(key, set()).update(values)
    return out


# -- keyed-table sidecars ----------------------------------------------------


def merge_readings(
    ours: Mapping[str, ScanReading],
    theirs: Mapping[str, ScanReading],
    *,
    prefer: Side,
) -> MergeResult[dict[str, ScanReading]]:
    """Union readings by id; a same-id clash prefers the side with a transcript
    (Phase 11's expensive part), else LWW. Readings are regenerable — never human."""
    decisions: list[FieldDecision] = []
    merged = dict(ours)
    for key, theirs_r in theirs.items():
        if key not in merged:
            merged[key] = theirs_r
            continue
        ours_r = merged[key]
        if ours_r == theirs_r:
            continue
        if theirs_r.transcript and not ours_r.transcript:
            merged[key] = theirs_r
            winner = Side.THEIRS
        elif ours_r.transcript and not theirs_r.transcript:
            winner = Side.OURS
        else:
            winner = prefer
            merged[key] = theirs_r if prefer is Side.THEIRS else ours_r
        decisions.append(
            FieldDecision(f"scans[{key}]", "lww", ours_r.model, theirs_r.model, winner)
        )
    return MergeResult(merged, tuple(decisions))


def merge_bundles(
    ours: Mapping[str, Bundle],
    theirs: Mapping[str, Bundle],
    *,
    prefer: Side,
    tie: bool = False,
) -> MergeResult[dict[str, Bundle]]:
    """Union bundles by slug; same slug field-merges (created = the earlier stamp)."""
    log = _Log(prefer, tie)
    merged = dict(ours)
    for slug, t in theirs.items():
        if slug not in merged:
            merged[slug] = t
            continue
        o = merged[slug]
        if o == t:
            continue
        created = _min_dt(o.created, t.created)
        merged[slug] = replace(
            o,
            title=log.scalar(f"bundles[{slug}].title", o.title, t.title),
            date=log.scalar(f"bundles[{slug}].date", o.date, t.date),
            notes=log.scalar(f"bundles[{slug}].notes", o.notes, t.notes),
            template=log.scalar(f"bundles[{slug}].template", o.template, t.template),
            export_dir=log.scalar(
                f"bundles[{slug}].export_dir", o.export_dir, t.export_dir
            ),
            created=created,
        )
    return MergeResult(merged, tuple(log.decisions))


def merge_locations(
    ours: Mapping[str, Location],
    theirs: Mapping[str, Location],
    *,
    prefer: Side,
    tie: bool = False,
) -> MergeResult[dict[str, Location]]:
    """Union locations by slug; same slug field-merges title/notes."""
    log = _Log(prefer, tie)
    merged = dict(ours)
    for slug, t in theirs.items():
        if slug not in merged:
            merged[slug] = t
            continue
        o = merged[slug]
        if o == t:
            continue
        merged[slug] = replace(
            o,
            title=log.scalar(f"locations[{slug}].title", o.title, t.title),
            notes=log.scalar(f"locations[{slug}].notes", o.notes, t.notes),
        )
    return MergeResult(merged, tuple(log.decisions))


def _min_dt(a: datetime | None, b: datetime | None) -> datetime | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)
