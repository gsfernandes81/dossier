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

"""Intake: file a dropped document in one step.

The highest-friction moment is a new document arriving — today it means scan, drop
the file, reconcile, adopt, then hand-fill every field. This composes the pieces
that already exist into a single **proposal**: read the file with the VLM
(:mod:`dossier.scan`), name it, derive its dates (:mod:`dossier.suggest`), find a
succession link (:mod:`dossier.succession`), and pick its canonical destination
(:mod:`dossier.organize`). :func:`build_proposal` writes nothing; :func:`apply_proposal`
files it — like ``ds organize``, propose then explicitly apply, never automatic.

Apply order is chosen so every crash state is consistent: save the record (still
linked at the inbox path) → persist the reading → move the file via
:func:`dossier.organize.apply_organize_plan`'s rollback-safe rename. Ambiguous dates
are left unset and resurface in the detail pane after filing (the reading is synced).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path

from dossier import organize, scan, succession
from dossier.config import Config
from dossier.errors import IntakeError
from dossier.migrate import slugify
from dossier.model import Document, Rendition, SuggestedField, Suggestion
from dossier.query import resolve_path
from dossier.reconcile import scan_files
from dossier.scan import ScanReading, file_fingerprint
from dossier.store import Store, unique_id
from dossier.succession import Succession
from dossier.suggest import from_reading

_Extract = Callable[[Path, Config], ScanReading]


@dataclass(frozen=True)
class IntakeProposal:
    """A whole-record proposal for one dropped file — nothing saved yet."""

    src_rel: str  # POSIX, root-relative inbox file
    reading: ScanReading  # fingerprinted
    doc: Document  # the fully-built draft (NOT persisted)
    dst_rel: str  # canonical destination applied on accept
    notes: tuple[str, ...]  # organize hints: fallback-folder / truncated / …
    succession: Succession | None  # a proposed "this supersedes an older doc"
    open_questions: tuple[Suggestion, ...]  # ambiguous dates left unset for the pane

    @property
    def name(self) -> str:
        return self.doc.name

    @property
    def moves(self) -> bool:
        return self.dst_rel != self.src_rel


def pending_files(
    store: Store, config: Config, *, from_dir: str | None = None
) -> list[str]:
    """Unfiled files awaiting intake — the inbox (or ``from_dir``) minus what's
    already linked to a document or dismissed as not-a-document.

    Reuses the reconcile file scan (so ``.dossier``/Syncthing noise and ignore
    globs are handled once) and its ``dismissed`` suppression.
    """
    scope = from_dir if from_dir is not None else config.intake_inbox
    if not scope:  # no inbox configured and no --from
        return []
    state = store.load_reconcile()
    linked = {r.path for doc in store.load_all() for r in doc.files if r.path}
    suppressed = state.suppressed_orphans()
    prefix = scope.rstrip("/") + "/"
    return [
        rel
        for rel in scan_files(config, state.ignore)
        if rel.startswith(prefix) and rel not in linked and rel not in suppressed
    ]


def build_proposal(
    src_rel: str,
    store: Store,
    config: Config,
    *,
    docs: list[Document],
    readings: dict[str, ScanReading],
    extract: _Extract | None = None,
    in_place: bool = False,
) -> IntakeProposal:
    """Propose the whole record for ``src_rel`` (no disk writes).

    ``docs``/``readings`` are the existing store (loaded once by the caller, as
    :func:`dossier.succession.propose` expects) — used only to detect a succession
    link. ``extract`` is injectable so tests need no VLM (default resolves
    :func:`dossier.scan.extract` at call time, so it's monkeypatchable too).
    ``in_place`` files where the document sits (a bulk import) rather than moving it
    to a category/fallback folder (an inbox drop).
    """
    read = extract if extract is not None else scan.extract
    abs_path = resolve_path(config.syncthing_root, src_rel)
    reading = replace(read(abs_path, config), fingerprint=file_fingerprint(abs_path))

    name = _name_from_reading(reading, src_rel)
    doc = Document(
        id=unique_id(store, slugify(name)),
        name=name,
        tags=_tags_from_reading(reading, config.intake_tags),
        has_digital=True,
        files=[Rendition(label="default", path=src_rel, primary=True)],
    )

    open_questions: list[Suggestion] = []
    for suggestion in from_reading(doc, reading):
        if _is_ambiguous_date(suggestion):
            open_questions.append(suggestion)
        else:
            _apply_suggestion(doc, suggestion)

    link = _best_succession(docs, readings, doc, reading)
    if link is not None:
        doc.supersedes = link.older

    plan = organize.build_organize_plan(
        [doc],
        root=config.syncthing_root,
        to_folders=not in_place,
        folder_map=config.organize_folders,
        fallback_folder=None if in_place else config.intake_filed,
    )
    item = plan.items[0]
    notes = tuple(t for t in item.note.split(",") if t)
    return IntakeProposal(
        src_rel=src_rel,
        reading=reading,
        doc=doc,
        dst_rel=item.dst_rel,
        notes=notes,
        succession=link,
        open_questions=tuple(open_questions),
    )


def apply_proposal(
    proposal: IntakeProposal, store: Store, config: Config
) -> tuple[Document, list[str]]:
    """File ``proposal``: create the record, persist the reading, move the file.

    Returns ``(saved_doc, errors)``. Raises :class:`IntakeError` for a hard failure
    (source vanished, already filed) before anything is written. Non-fatal issues —
    a reading that didn't persist, a destination that got occupied — come back in
    ``errors`` with the document still filed (recoverable via ``ds organize``).
    """
    root = config.syncthing_root
    if not resolve_path(root, proposal.src_rel).exists():
        raise IntakeError(f"source vanished: {proposal.src_rel}")
    linked = {r.path for d in store.load_all() for r in d.files if r.path}
    if proposal.src_rel in linked:
        raise IntakeError(f"already filed: {proposal.src_rel}")

    # Fresh id: a bulk session can propose two identical names before either saves.
    doc = copy.deepcopy(proposal.doc)
    doc.id = unique_id(store, slugify(doc.name))
    doc.source_hash = None  # a new document — save() must not expect a prior file

    errors: list[str] = []
    store.save(doc)  # record now links the still-in-inbox file — a consistent state

    try:
        readings = store.load_scans()
        readings[doc.id] = proposal.reading
        store.save_scans(readings)
    except OSError as exc:  # best-effort; the doc is filed regardless
        errors.append(f"reading not saved: {exc}")

    if proposal.moves:
        item = organize.OrganizeItem(
            doc_id=doc.id,
            name=doc.name,
            label="default",
            src_rel=proposal.src_rel,
            dst_rel=proposal.dst_rel,
            problem=None,
        )
        move_plan = organize.OrganizePlan(to_folders=False, items=(item,))
        _, move_errors = organize.apply_organize_plan(move_plan, store, root=root)
        errors.extend(move_errors)
    return doc, errors


# -- field derivations -------------------------------------------------------


def _name_from_reading(reading: ScanReading, src_rel: str) -> str:
    """The VLM's document type, whitespace-collapsed; else the prettified stem."""
    name = " ".join(reading.document_type.split())
    if name:
        return name
    stem = src_rel.rsplit("/", 1)[-1]
    stem = stem.rsplit(".", 1)[0] if "." in stem else stem
    return stem.replace("_", " ").strip() or stem


def _tags_from_reading(reading: ScanReading, tag_map: dict[str, str]) -> list[str]:
    """The tag whose keyword matches the reading (longest keyword wins), or none.

    A user-configurable ``[intake.tags]`` keyword→tag map is the *only* tag source:
    the store has none today, and inventing tags from raw type strings would pollute
    it. Intake is the first surface that sets tags, which then feed organize folders.
    """
    if not tag_map:
        return []
    haystack = f"{reading.document_type} {reading.issuer or ''}".casefold()
    matches = [(kw, tag) for kw, tag in tag_map.items() if kw.casefold() in haystack]
    if not matches:
        return []
    _, tag = max(matches, key=lambda kt: len(kt[0]))
    return [tag]


def _is_ambiguous_date(suggestion: Suggestion) -> bool:
    """A date the VLM couldn't disambiguate (DD/MM) — leave it for the pane."""
    return (
        suggestion.field in (SuggestedField.ISSUE, SuggestedField.EXPIRY)
        and len(suggestion.values) > 1
    )


def _apply_suggestion(doc: Document, suggestion: Suggestion) -> None:
    # Mirrors dossier.tui.detail_pane._apply_suggestion_to_doc (single-valued only).
    if suggestion.field is SuggestedField.ISSUE:
        doc.issue_date = date.fromisoformat(suggestion.values[0])
    elif suggestion.field is SuggestedField.EXPIRY:
        doc.expiry_date = date.fromisoformat(suggestion.values[0])
    else:  # a NOTES period span (values are start, end)
        span = f"Period: {suggestion.values[0]} to {suggestion.values[1]}"
        doc.notes = f"{doc.notes}\n{span}" if doc.notes else span


def _best_succession(
    docs: list[Document],
    readings: dict[str, ScanReading],
    draft: Document,
    reading: ScanReading,
) -> Succession | None:
    """The highest-confidence proposal where the draft supersedes an existing doc."""
    proposals = succession.propose(docs + [draft], {**readings, draft.id: reading})
    mine = [s for s in proposals if s.newer == draft.id]
    return max(mine, key=lambda s: s.confidence) if mine else None
