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

"""Resolve Syncthing conflict files by field-merging them back into the live copy.

Syncthing never overwrites divergent edits — it drops a ``…​.sync-conflict-<stamp>-
<device>.<ext>`` copy beside the file it kept. :meth:`Store.list_conflicts` finds
them; this module *closes* them:

1. **discover** — map each conflict file to its live counterpart and classify it
   (document, one of the sidecars, or ``config``);
2. **plan** — read both copies, pick a last-writer side from file mtimes, and run
   the shape's merger (:mod:`dossier.merge`) into merged bytes;
3. **apply** — crash-safely: archive the losing conflict, back up the live copy,
   write the merge, and only then delete the conflict.

Every step is recoverable. The losing conflict copy and the pre-merge live copy
are both stashed to the local (non-synced) history, and the destructive delete
happens last — a crash at any point leaves the conflict file on disk for the next
`ds resolve` to re-plan. A compare-and-swap on the live copy's hash means a
concurrent Syncthing write is detected (:class:`ResolveBusyError`) and retried,
never clobbered.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dossier.errors import ResolveBusyError
from dossier.merge import (
    FieldDecision,
    Side,
    merge_bundles,
    merge_documents,
    merge_locations,
    merge_readings,
    merge_reconcile,
    merge_suggestions,
)
from dossier.model import Document, ReconcileState, SuggestionState
from dossier.store import CONFLICT_MARKER, atomic_write_bytes

if TYPE_CHECKING:
    from dossier.store import Store

# Two copies whose mtimes fall within this window are treated as a tie (→ ours):
# Syncthing preserves mtimes across devices, but clock skew and 1–2 s filesystem
# granularity make sub-second ordering meaningless.
_MTIME_TOLERANCE = 2.0

# Model-merged sidecars: the field mergers know how to union/LWW them. ``config``
# and ``templates`` fall through to a whole-file last-writer-wins (see _plan_bytes).
_DOCUMENT = "document"


@dataclass(frozen=True)
class ConflictItem:
    """A discovered conflict file and where it belongs."""

    conflict_path: Path
    live_path: Path
    kind: str  # document | scans | intake | bundles | locations | reconcile | ...
    doc_id: str  # the live document id (only meaningful when kind == document)

    @property
    def name(self) -> str:
        return self.live_path.name


@dataclass(frozen=True)
class Resolution:
    """A planned merge: the bytes to write, plus what to archive and why."""

    item: ConflictItem
    merged_bytes: bytes
    live_hash: str  # of the live copy the merge was planned against (CAS token)
    conflict_bytes: bytes
    decisions: tuple[FieldDecision, ...]
    loud: bool  # whole-file LWW (config/templates) — surfaced prominently

    @property
    def kind(self) -> str:
        return self.item.kind

    @property
    def name(self) -> str:
        return self.item.name

    @property
    def contested(self) -> tuple[FieldDecision, ...]:
        return tuple(d for d in self.decisions if d.action in ("lww", "tie"))

    @property
    def changed(self) -> bool:
        """Whether the merge actually differs from the live copy."""
        return _hash(self.merged_bytes) != self.live_hash


@dataclass(frozen=True)
class ResolveReport:
    resolutions: tuple[Resolution, ...]  # planned (and applied, unless dry/skipped)
    skipped: tuple[Resolution, ...]  # busy — left in place for a retry
    applied: bool  # whether the writes actually happened

    @property
    def contested(self) -> tuple[Resolution, ...]:
        return tuple(r for r in self.resolutions if r.contested or r.loud)


# -- discovery ---------------------------------------------------------------


def find_conflicts(store: Store) -> list[ConflictItem]:
    """Every Syncthing conflict file under ``.dossier`` we know how to resolve.

    Files we can't classify (an unexpected name/extension) are left untouched —
    never guessed at.
    """
    items: list[ConflictItem] = []
    for conflict in store.list_conflicts():
        live = _live_path(conflict)
        kind = _classify(store, live)
        if kind is None:
            continue
        items.append(
            ConflictItem(
                conflict_path=conflict,
                live_path=live,
                kind=kind,
                doc_id=live.stem,
            )
        )
    return items


def _live_path(conflict: Path) -> Path:
    """Strip the ``.sync-conflict-<stamp>-<device>`` segment to get the live name.

    ``eng-1.sync-conflict-20260722-120000-ABCDEFG.md`` → ``eng-1.md``;
    ``scans.sync-conflict-…​.toml`` → ``scans.toml``.
    """
    name = conflict.name
    marker = name.find(CONFLICT_MARKER)
    base = name[:marker]
    rest = name[marker + len(CONFLICT_MARKER) :]
    dot = rest.find(".")
    ext = rest[dot:] if dot != -1 else ""
    return conflict.with_name(base + ext)


def _classify(store: Store, live: Path) -> str | None:
    cfg = store.config
    if live.parent == cfg.documents_dir and live.suffix == ".md":
        return _DOCUMENT
    known = {
        cfg.scans_path: "scans",
        cfg.intake_cache_path: "intake",
        cfg.bundles_path: "bundles",
        cfg.locations_path: "locations",
        cfg.reconcile_path: "reconcile",
        cfg.suggestions_path: "suggestions",
        cfg.templates_path: "templates",
        cfg.synced_config_path: "config",
    }
    return known.get(live)


# -- planning ----------------------------------------------------------------


def plan(store: Store, item: ConflictItem) -> Resolution:
    """Read both copies and compute the merged bytes (no writes)."""
    live = item.live_path
    live_bytes = live.read_bytes() if live.exists() else b""
    conflict_bytes = item.conflict_path.read_bytes()
    prefer, tie = _prefer(live, item.conflict_path)
    merged_bytes, decisions, loud = _plan_bytes(
        store, item, prefer=prefer, tie=tie, live_bytes=live_bytes
    )
    return Resolution(
        item=item,
        merged_bytes=merged_bytes,
        live_hash=_hash(live_bytes),
        conflict_bytes=conflict_bytes,
        decisions=decisions,
        loud=loud,
    )


def _plan_bytes(
    store: Store,
    item: ConflictItem,
    *,
    prefer: Side,
    tie: bool,
    live_bytes: bytes,
) -> tuple[bytes, tuple[FieldDecision, ...], bool]:
    live, conflict = item.live_path, item.conflict_path
    exists = bool(live_bytes)
    kind = item.kind

    if kind == _DOCUMENT:
        ours = store.read_document(live) if exists else Document(id=item.doc_id)
        theirs = store.read_document(conflict)
        result = merge_documents(ours, theirs, prefer=prefer, tie=tie)
        return store.serialize(result.merged).encode("utf-8"), result.decisions, False

    if kind in ("scans", "intake"):
        load = store.load_scans if kind == "scans" else store.load_intake_cache
        ours_r = load(live) if exists else {}
        result = merge_readings(ours_r, load(conflict), prefer=prefer)
        return store.serialize_readings(result.merged), result.decisions, False

    if kind == "bundles":
        ours_b = store.load_bundles(live) if exists else {}
        result = merge_bundles(
            ours_b, store.load_bundles(conflict), prefer=prefer, tie=tie
        )
        return store.serialize_bundles(result.merged), result.decisions, False

    if kind == "locations":
        ours_l = store.load_locations(live) if exists else {}
        result = merge_locations(
            ours_l, store.load_locations(conflict), prefer=prefer, tie=tie
        )
        return store.serialize_locations(result.merged), result.decisions, False

    if kind == "reconcile":
        ours_rc = store.load_reconcile(live) if exists else ReconcileState()
        result = merge_reconcile(ours_rc, store.load_reconcile(conflict))
        return store.serialize_reconcile(result.merged), result.decisions, False

    if kind == "suggestions":
        ours_sg = store.load_suggestions(live) if exists else SuggestionState()
        result = merge_suggestions(ours_sg, store.load_suggestions(conflict))
        return store.serialize_suggestions(result.merged), result.decisions, False

    # config / templates: no field structure worth a partial merge — whole-file
    # last-writer-wins, surfaced loudly so a clobbered hand-edit is never silent.
    winner = Side.OURS if tie else prefer
    merged = item.conflict_path.read_bytes() if winner is Side.THEIRS else live_bytes
    decision = FieldDecision(
        kind, "tie" if tie else "lww", "<live copy>", "<conflict copy>", winner
    )
    return merged, (decision,), True


def _prefer(live: Path, conflict: Path) -> tuple[Side, bool]:
    """Pick the last-writer side from file mtimes. Returns ``(side, is_tie)``.

    A missing/unstattable live copy → the conflict wins (it's the only content).
    """
    try:
        conflict_mtime = conflict.stat().st_mtime
    except OSError:
        return Side.THEIRS, False
    if not live.exists():
        return Side.THEIRS, False
    live_mtime = live.stat().st_mtime
    if conflict_mtime > live_mtime + _MTIME_TOLERANCE:
        return Side.THEIRS, False
    if live_mtime > conflict_mtime + _MTIME_TOLERANCE:
        return Side.OURS, False
    return Side.OURS, True  # within tolerance → tie → ours


# -- applying ----------------------------------------------------------------


def apply_resolution(store: Store, resolution: Resolution) -> None:
    """Write a planned merge crash-safely, or raise :class:`ResolveBusyError`.

    Order matters — every prefix of these steps is safe to crash after:
    1. compare-and-swap on the live copy (bail if it changed since planning);
    2. archive the losing conflict copy to local history;
    3. back up the pre-merge live copy to local history;
    4. atomically write the merge to the live path;
    5. delete the conflict file — last, so a crash before here just retries.
    """
    live = resolution.item.live_path
    current = live.read_bytes() if live.exists() else b""
    if _hash(current) != resolution.live_hash:
        raise ResolveBusyError(live.name)

    store.stash(
        "conflicts", resolution.item.conflict_path.name, resolution.conflict_bytes
    )
    if current:
        store.stash("superseded", live.name, current)
    atomic_write_bytes(live, resolution.merged_bytes)
    resolution.item.conflict_path.unlink(missing_ok=True)


def resolve_all(store: Store, *, apply: bool) -> ResolveReport:
    """Plan every conflict and, unless ``apply`` is false, apply each in turn.

    Conflicts are planned-then-applied one at a time so that several conflict
    copies of the *same* live file fold in sequence (each merge sees the prior
    one's result). A busy live copy is skipped and left for a later run.
    """
    resolved: list[Resolution] = []
    skipped: list[Resolution] = []
    for item in find_conflicts(store):
        resolution = plan(store, item)
        if not apply:
            resolved.append(resolution)
            continue
        try:
            apply_resolution(store, resolution)
        except ResolveBusyError:
            skipped.append(resolution)
        else:
            resolved.append(resolution)
    return ResolveReport(
        resolutions=tuple(resolved), skipped=tuple(skipped), applied=apply
    )


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
