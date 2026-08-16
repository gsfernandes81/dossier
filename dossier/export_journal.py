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

"""One-shot v2 store → v3 journal export, with a parity check (REWRITE.md §6 R2).

The cutover is big-bang (D4), so this converter and the parity harness beside it
are the only things standing between the user's ~948 real documents and a
silently lossy migration. Two rules follow from that:

* **Read-only w.r.t. the v2 store.** Everything here reads through the trusted
  `store.py`; nothing writes a document, and nothing touches the real file tree.
* **Idempotent.** Exporting twice from an unchanged store produces the same ops,
  so a rehearsal costs nothing and a re-run after a fix is safe.

The value mapping lives in one place — `doc_fields`, `location_fields`,
`bundle_fields`, `settings_fields` — used by *both* the exporter and the parity
check. That is deliberate: parity then tests the thing that can actually break
(a date turning into a datetime, a set losing its order, an int arriving as a
string through JSONL) rather than re-deriving the same mistake twice and
agreeing with itself.

Journals written by this module must live **outside the Syncthing tree** until
cutover (§6 R2, §7): anything inside it syncs by default, and a half-built
journal must never reach the phone early.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

from dossier.journal import FORMAT_VERSION, Fold, Op, OpKind

if TYPE_CHECKING:  # pragma: no cover - imports for typing only
    from dossier.config import Config
    from dossier.model import (
        Bundle,
        Document,
        Location,
        ReconcileState,
        SuggestionState,
    )
    from dossier.scan import ScanReading
    from dossier.store import Store

#: Entity kinds in the exported journal.
DOC = "doc"
LOCATION = "location"
BUNDLE = "bundle"
SETTINGS = "settings"
REVIEW = "review"
SUGGESTION = "suggestion"
READING = "reading"
PROPOSAL = "proposal"

#: The single settings entity id — synced settings are ops, not a whole-file LWW
#: special case (REWRITE.md §2).
SETTINGS_ID = "synced"


def _iso(value: dt.date | dt.datetime | None) -> str | None:
    """Dates go into the format as ISO strings, never as numbers (§3.2)."""
    return None if value is None else value.isoformat()


def doc_fields(doc: Document) -> dict[str, Any]:
    """A v2 document as journal fields — the one mapping, used by both sides.

    Only fields that carry information are emitted: a document that is not
    superseded has no `supersedes` field at all, rather than an explicit null.
    The fold's absence-of-a-field and v2's `None` mean the same thing, and
    keeping the journal sparse keeps ~948 documents' worth of ops small.
    """
    out: dict[str, Any] = {"name": doc.name}
    if doc.tags:
        out["tags"] = list(doc.tags)
    if doc.bundles:
        out["bundles"] = list(doc.bundles)
    if doc.issue_date is not None:
        out["issue_date"] = _iso(doc.issue_date)
    if doc.expiry_date is not None:
        out["expiry_date"] = _iso(doc.expiry_date)
    if doc.ignore_expiry:
        out["ignore_expiry"] = True
    if doc.supersedes is not None:
        out["supersedes"] = doc.supersedes
    if doc.has_physical:
        out["has_physical"] = True
    if doc.has_digital:
        out["has_digital"] = True
    if doc.files:
        # "Rendition" is dropped as a word (D9); the capability — several files
        # with one primary — is kept exactly as it was.
        out["files"] = [
            {"label": r.label, "path": r.path, "primary": r.primary} for r in doc.files
        ]
    for name in (
        "perm_location",
        "perm_slot",
        "perm_subslot",
        "temp_location",
        "temp_slot",
        "temp_subslot",
    ):
        value = getattr(doc, name)
        if value is not None:
            out[name] = value
    if doc.notes:
        out["notes"] = doc.notes
    return out


def location_fields(location: Location) -> dict[str, Any]:
    """A v2 location as journal fields."""
    out: dict[str, Any] = {"title": location.title}
    if location.notes:
        out["notes"] = location.notes
    return out


def bundle_fields(bundle: Bundle) -> dict[str, Any]:
    """A v2 bundle as journal fields.

    `template` is exported even though bundle templates are dropped from the
    feature set (§8): the export is a faithful record of the v2 store, and
    deciding what v3 *uses* is not the exporter's job. Dropping data here would
    make the parity check pass while losing information the user still has.
    """
    out: dict[str, Any] = {"title": bundle.title}
    if bundle.date is not None:
        out["date"] = _iso(bundle.date)
    if bundle.created is not None:
        out["created"] = _iso(bundle.created)
    if bundle.export_dir is not None:
        out["export_dir"] = bundle.export_dir
    if bundle.notes:
        out["notes"] = bundle.notes
    if bundle.template is not None:
        out["template"] = bundle.template
    return out


def settings_fields(config: Config, reconcile: ReconcileState) -> dict[str, Any]:
    """The **synced** settings as journal fields.

    REWRITE.md §6 R2 calls these out by name because losing them at cutover
    would silently reset scope and filing behaviour — the app would still run,
    and would quietly stop seeing half the tree. Per-device settings (syncthing
    address, API key, scan endpoint) are deliberately **not** here: they stay in
    the per-device TOML, and the key is a secret that must never be synced.

    `reconcile_ignore` is the TUI-added half of the ignore scope
    (`reconcile.toml`), kept separate from the hand-written `ignore` globs in
    `config.toml` exactly as v2 keeps them apart.
    """
    out: dict[str, Any] = {"expiry_threshold_days": config.expiry_threshold_days}
    if config.include:
        out["include"] = list(config.include)
    if config.ignore:
        out["ignore"] = list(config.ignore)
    if reconcile.ignore:
        out["reconcile_ignore"] = list(reconcile.ignore)
    if config.organize_folders:
        out["organize_folders"] = dict(config.organize_folders)
    if config.intake_inbox is not None:
        out["intake_inbox"] = config.intake_inbox
    out["intake_filed"] = config.intake_filed
    if config.intake_tags:
        out["intake_tags"] = dict(config.intake_tags)
    return out


def reading_payload(reading: ScanReading) -> dict[str, Any]:
    """A scan reading as an `enrich` payload.

    **`confidence` is a float and the format has none** (§3.2) — a float would
    make the canonical JSON comparison between the Rust and Python folds
    unimplementable. It is exported as `confidence_permille`, an integer 0–1000,
    renamed rather than rounded in place so nothing can read it as a fraction by
    accident.
    """
    out: dict[str, Any] = {
        "document_type": reading.document_type,
        "is_validity_period": reading.is_validity_period,
        "confidence_permille": round(reading.confidence * 1000),
    }
    for name in (
        "issuer",
        "holder_name",
        "issue_date_text",
        "expiry_date_text",
        "document_number",
        "evidence",
    ):
        value = getattr(reading, name)
        if value is not None:
            out[name] = value
    if reading.model:
        out["model"] = reading.model
    if reading.fingerprint:
        out["fingerprint"] = reading.fingerprint
    if reading.transcript:
        out["transcript"] = reading.transcript
    if reading.keywords:
        out["keywords"] = list(reading.keywords)
    return out


def review_states(reconcile: ReconcileState) -> dict[str, str]:
    """Reconcile decisions as `review` state keys → value.

    Every one is a *suppression* in v2, and each becomes one per-key LWW entry
    (§3.2) so the restore verbs keep working: `state` is newest-wins in both
    directions, which a monotone union could never express.

    Keys are namespaced by kind so the five review tabs can filter without
    parsing. Succession keys arrive from v2 as `"newer\\x00older"`; a NUL inside
    a JSON string is legal but hostile to read, so it becomes a `:` — safe
    because ids are slugs.
    """
    states: dict[str, str] = {}
    for path in sorted(reconcile.dismissed):
        states[f"orphan:{path}"] = "dismissed"
    for path, doc_ids in sorted(reconcile.missing_ok.items()):
        for doc_id in sorted(doc_ids):
            states[f"missing:{doc_id}:{path}"] = "acked"
    for keep, subsets in sorted(reconcile.folded.items()):
        for subset in sorted(subsets):
            states[f"dup:{keep}:{subset}"] = "folded"
    for keep, subsets in sorted(reconcile.dup_dismissed.items()):
        for subset in sorted(subsets):
            states[f"dup:{keep}:{subset}"] = "dismissed"
    for key in sorted(reconcile.succession_dismissed):
        newer, _, older = key.partition("\x00")
        states[f"succession:{newer}:{older}"] = "dismissed"
    return states


@dataclass
class Export:
    """The exported journal: `meta` ops and `enrich` ops, ready to write out."""

    meta: list[Op]
    enrich: list[Op]

    def body(self, ops: Iterable[Op]) -> str:
        """Serialize ops as a journal file body (one op per line, trailing \\n)."""
        return "".join(f"{op.to_line()}\n" for op in ops)

    @property
    def meta_body(self) -> str:
        """The `meta/<writer>.jsonl` body."""
        return self.body(self.meta)

    @property
    def enrich_body(self) -> str:
        """The `enrich/<writer>.jsonl` body."""
        return self.body(self.enrich)


class _Stamper:
    """Hands out strictly increasing timestamps for one writer.

    The exported ops all come from one writer at one moment, so their `ts`
    values only have to be unique and ordered — the fold's `(ts, w)` key does
    the rest. Starting from a caller-supplied base keeps the export
    deterministic, which is what makes it idempotent.
    """

    def __init__(self, writer: str, base_ts: int) -> None:
        self.writer = writer
        self._next = base_ts

    def op(
        self,
        kind: OpKind,
        ent: str,
        entity_id: str,
        *,
        field_name: str | None = None,
        val: Any = None,
    ) -> Op:
        ts = self._next
        self._next += 1
        return Op(
            v=FORMAT_VERSION,
            ts=ts,
            w=self.writer,
            op=kind,
            ent=ent,
            id=entity_id,
            f=field_name,
            val=val,
        )


def export(
    store: Store,
    *,
    writer: str = "desk-core",
    lab_writer: str = "desk-lab",
    base_ts: int = 1_700_000_000_000,
) -> Export:
    """Convert a whole v2 store into journal ops. Read-only, idempotent.

    `meta` ops are signed by the core writer and `enrich` ops by the lab writer,
    matching who owns each namespace in v3 (§5): the satellite proposes fields
    and never writes `meta`.
    """
    config = store.config
    meta = _Stamper(writer, base_ts)
    enrich = _Stamper(lab_writer, base_ts)

    meta_ops: list[Op] = []
    for slug, location in sorted(store.load_locations().items()):
        meta_ops.append(meta.op(OpKind.CREATE, LOCATION, slug))
        for name, value in location_fields(location).items():
            meta_ops.append(
                meta.op(OpKind.SET, LOCATION, slug, field_name=name, val=value)
            )

    for slug, bundle in sorted(store.load_bundles().items()):
        meta_ops.append(meta.op(OpKind.CREATE, BUNDLE, slug))
        for name, value in bundle_fields(bundle).items():
            meta_ops.append(
                meta.op(OpKind.SET, BUNDLE, slug, field_name=name, val=value)
            )

    for doc in sorted(store.load_all(), key=lambda d: d.id):
        meta_ops.append(meta.op(OpKind.CREATE, DOC, doc.id))
        for name, value in doc_fields(doc).items():
            meta_ops.append(
                meta.op(OpKind.SET, DOC, doc.id, field_name=name, val=value)
            )

    reconcile = store.load_reconcile()
    meta_ops.append(meta.op(OpKind.CREATE, SETTINGS, SETTINGS_ID))
    for name, value in settings_fields(config, reconcile).items():
        meta_ops.append(
            meta.op(OpKind.SET, SETTINGS, SETTINGS_ID, field_name=name, val=value)
        )

    for key, value in review_states(reconcile).items():
        meta_ops.append(meta.op(OpKind.STATE, REVIEW, key, val=value))

    suggestions: SuggestionState = store.load_suggestions()
    for key in sorted(suggestions.dismissed):
        meta_ops.append(meta.op(OpKind.STATE, SUGGESTION, key, val="dismissed"))

    enrich_ops: list[Op] = []
    for path, reading in sorted(store.load_scans().items()):
        enrich_ops.append(
            enrich.op(OpKind.READING, READING, path, val=reading_payload(reading))
        )
    for path, reading in sorted(store.load_intake_cache().items()):
        enrich_ops.append(
            enrich.op(OpKind.PROPOSAL, PROPOSAL, path, val=reading_payload(reading))
        )

    return Export(meta=meta_ops, enrich=enrich_ops)


@dataclass(frozen=True)
class Mismatch:
    """One field-level parity failure. Any of these is a hard stop (§7)."""

    kind: str
    key: str
    field: str
    expected: Any
    got: Any

    def __str__(self) -> str:
        return (
            f"{self.kind} {self.key}: {self.field} — "
            f"store has {self.expected!r}, journal has {self.got!r}"
        )


def _compare(
    kind: str,
    expected: Mapping[str, Mapping[str, Any]],
    got: Mapping[str, Mapping[str, Any]],
) -> list[Mismatch]:
    """Field-by-field comparison of one entity kind, in both directions."""
    problems: list[Mismatch] = []
    for key in sorted(set(expected) | set(got)):
        if key not in got:
            problems.append(Mismatch(kind, key, "*", "present", "missing"))
            continue
        if key not in expected:
            problems.append(Mismatch(kind, key, "*", "missing", "present"))
            continue
        want, have = expected[key], got[key]
        for field_name in sorted(set(want) | set(have)):
            if want.get(field_name) != have.get(field_name):
                problems.append(
                    Mismatch(
                        kind,
                        key,
                        field_name,
                        want.get(field_name),
                        have.get(field_name),
                    )
                )
    return problems


def check_parity(store: Store, folded: Fold) -> list[Mismatch]:
    """Compare a folded journal against the v2 store it came from.

    An empty list is the only acceptable result before cutover: §7 makes a
    parity failure on any field a hard stop. The comparison covers documents,
    locations, bundles, settings, review and suggestion state, and enrich
    payloads — in **both** directions, so an entity the journal invented is as
    much a failure as one it lost.
    """
    problems: list[Mismatch] = []

    def by_kind(kind: str) -> dict[str, dict[str, Any]]:
        return {
            entity_id: dict(fields_)
            for (ent, entity_id), fields_ in folded.entities.items()
            if ent == kind
        }

    problems += _compare(
        DOC,
        {doc.id: doc_fields(doc) for doc in store.load_all()},
        by_kind(DOC),
    )
    problems += _compare(
        LOCATION,
        {slug: location_fields(loc) for slug, loc in store.load_locations().items()},
        by_kind(LOCATION),
    )
    problems += _compare(
        BUNDLE,
        {slug: bundle_fields(b) for slug, b in store.load_bundles().items()},
        by_kind(BUNDLE),
    )

    reconcile = store.load_reconcile()
    problems += _compare(
        SETTINGS,
        {SETTINGS_ID: settings_fields(store.config, reconcile)},
        by_kind(SETTINGS),
    )

    def states(kind: str) -> dict[str, Any]:
        return {
            entity_id: value
            for (ent, entity_id), value in folded.states.items()
            if ent == kind
        }

    problems += _compare(
        REVIEW,
        {key: {"state": value} for key, value in review_states(reconcile).items()},
        {key: {"state": value} for key, value in states(REVIEW).items()},
    )
    problems += _compare(
        SUGGESTION,
        {key: {"state": "dismissed"} for key in store.load_suggestions().dismissed},
        {key: {"state": value} for key, value in states(SUGGESTION).items()},
    )

    def enrich(kind: str) -> dict[str, Any]:
        return {
            entity_id: value
            for (ent, entity_id), value in folded.enrich.items()
            if ent == kind
        }

    problems += _compare(
        READING,
        {path: reading_payload(r) for path, r in store.load_scans().items()},
        enrich(READING),
    )
    problems += _compare(
        PROPOSAL,
        {path: reading_payload(r) for path, r in store.load_intake_cache().items()},
        enrich(PROPOSAL),
    )
    return problems


def unexported_document_fields() -> set[str]:
    """v2 `Document` attributes the export deliberately drops.

    A guard for the day someone adds a field to the model: the test that calls
    this fails, and whoever added it has to decide whether it belongs in the
    journal rather than discovering at cutover that it does not survive.
    """
    from dossier.model import Document

    exported = {
        "name",
        "tags",
        "bundles",
        "issue_date",
        "expiry_date",
        "ignore_expiry",
        "supersedes",
        "has_physical",
        "has_digital",
        "files",
        "perm_location",
        "perm_slot",
        "perm_subslot",
        "temp_location",
        "temp_slot",
        "temp_subslot",
        "notes",
    }
    # `id` is the entity id, not a field; `source_hash` is store bookkeeping for
    # v2's optimistic-concurrency check, which journals make obsolete.
    bookkeeping = {"id", "source_hash"}
    return {f.name for f in fields(Document)} - exported - bookkeeping
