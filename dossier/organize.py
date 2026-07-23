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

"""Give every *linked* file a canonical name (and optional category folder).

Pure plan / apply split, like :mod:`dossier.export`: :func:`build_organize_plan`
derives a canonical destination for each rendition from its document record
(``name`` + ``issue_date``) and flags problems — a file shared by two records, a
missing source, an occupied destination — **without touching disk**;
:func:`apply_organize_plan` renames the real file and rewrites the rendition path,
disk-first with rollback so a crash can never silently lose the file→record link.

This is the first surface that mutates the user's real documents, so it is
conservative by construction: it renames renditions **in place** by default
(``--to-folders`` opts into category-folder placement, derived from tags — see
below), never overwrites, never deletes, and never touches an unlinked file.

``canonical_stem`` is the per-document hook Phase 9 "intake" reuses to name a
freshly captured file on its review card.
"""

from __future__ import annotations

import errno
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from dossier.migrate import slugify
from dossier.model import Document
from dossier.query import resolve_path
from dossier.store import Store
from dossier.suggest import name_has_date

# Problem codes for an un-organizable rendition (none of these are moved).
SHARED = "shared-file"  # ≥2 records point at this one file — renaming orphans one
MISSING = "missing-file"  # the source rendition is gone on disk
EXISTS = "exists"  # a *different* file already occupies the destination
NO_LABEL = "no-label"  # a multi-rendition doc has an empty/duplicate rendition label

# Keep the basename (stem + a short extension) comfortably within the ~255-byte
# limits and clear of any path-length ceilings; back off to a hyphen boundary.
MAX_STEM = 96


@dataclass(frozen=True)
class OrganizeItem:
    """One rendition's proposed rename, or a flagged reason it can't be."""

    doc_id: str
    name: str
    label: str
    src_rel: str  # POSIX, root-relative — the current rendition path
    dst_rel: str  # POSIX, root-relative — the canonical destination
    problem: str | None  # None = ready or already canonical
    note: str = ""  # comma-joined hints: id-fallback/truncated/case-only/no-folder

    @property
    def already_canonical(self) -> bool:
        """Already exactly where it belongs — no rename, no problem."""
        return self.problem is None and self.src_rel == self.dst_rel


@dataclass(frozen=True)
class OrganizePlan:
    to_folders: bool
    items: tuple[OrganizeItem, ...]

    @property
    def ready(self) -> list[OrganizeItem]:
        """Items that will actually be renamed on apply."""
        return [i for i in self.items if i.problem is None and not i.already_canonical]

    @property
    def problems(self) -> list[OrganizeItem]:
        return [i for i in self.items if i.problem is not None]

    @property
    def already(self) -> list[OrganizeItem]:
        return [i for i in self.items if i.already_canonical]


def canonical_stem(doc: Document) -> tuple[str, str]:
    """The canonical filename stem (no extension, no folder) for ``doc``.

    ``slugify(name)``, prefixed with the ISO ``issue_date`` **only** when the name
    doesn't already embed a date (else ``2019-05-19-...-2019-05-19``). Returns
    ``(stem, note)`` — pure, so the Phase 9 intake card can render a proposed name
    without building a whole plan.
    """
    base = slugify(doc.name)
    if doc.issue_date is not None and not name_has_date(doc.name):
        base = f"{doc.issue_date.isoformat()}-{base}"
    return _truncate(base)


def _truncate(base: str) -> tuple[str, str]:
    if len(base) <= MAX_STEM:
        return base, ""
    cut = base[:MAX_STEM]
    if "-" in cut:  # back off to a hyphen boundary rather than mid-token
        cut = cut[: cut.rfind("-")]
    cut = cut.strip("-") or base[:MAX_STEM].strip("-") or "document"
    return cut, "truncated"


def _mapped_folder(
    doc: Document, folder_map: Mapping[str, str]
) -> PurePosixPath | None:
    """Longest-prefix match of the doc's primary tag in ``folder_map`` (or None)."""
    if not doc.tags:
        return None
    parts = doc.tags[0].split("/")
    for i in range(len(parts), 0, -1):
        key = "/".join(parts[:i])
        if key in folder_map:
            return PurePosixPath(folder_map[key])
    return None


def _renditions(docs: list[Document]) -> list[tuple[Document, int, str]]:
    """Every (doc, rendition index, path) with a linked file, in a stable order."""
    out: list[tuple[Document, int, str]] = []
    for doc in docs:
        for idx, rendition in enumerate(doc.files):
            if rendition.path:
                out.append((doc, idx, rendition.path))
    return out


@dataclass
class _Draft:
    """A rendition's in-progress destination, mutated across the planner's passes."""

    doc: Document
    label: str
    src_rel: str
    stem: str
    ext: str
    parent: PurePosixPath
    notes: list[str]
    problem: str | None

    @property
    def dst_rel(self) -> str:
        return (self.parent / f"{self.stem}{self.ext}").as_posix()


def build_organize_plan(
    docs: list[Document],
    *,
    root: Path,
    to_folders: bool = False,
    folder_map: Mapping[str, str] | None = None,
    fallback_folder: str | None = None,
) -> OrganizePlan:
    """Plan the canonical rename of every linked rendition (no disk writes).

    ``docs`` is the already-scoped set (the caller filters by id / bundle, as
    ``ds export`` does). With ``to_folders`` each file also moves into the folder
    its primary tag maps to via ``folder_map``; an unmapped or untagged doc goes to
    ``fallback_folder`` if given (intake files an untagged inbox scan into ``Filed/``),
    else keeps its current directory (a ``no-folder`` note, not a problem).
    """
    folder_map = folder_map or {}
    renditions = _renditions(docs)

    # A source referenced by more than one rendition (any doc) is SHARED: renaming
    # it for one record silently breaks the other. Compare casefolded — the same
    # file reached via different-cased paths is still the same file.
    src_counts: dict[str, int] = {}
    for _doc, _idx, path in renditions:
        src_counts[path.casefold()] = src_counts.get(path.casefold(), 0) + 1

    # First pass: ideal destination per rendition (pre-collision-resolution).
    drafts: list[_Draft] = []
    for doc, idx, src_rel in renditions:
        rendition = doc.files[idx]
        multi = len([r for r in doc.files if r.path]) > 1
        stem, note = canonical_stem(doc)
        problem: str | None = None
        if multi and not rendition.primary:
            label_slug = slugify(rendition.label) if rendition.label else ""
            if not label_slug:
                problem = NO_LABEL
            stem = f"{stem}--{label_slug}" if label_slug else stem
        notes = [note] if note else []
        parent = PurePosixPath(src_rel).parent
        if to_folders:
            folder = _mapped_folder(doc, folder_map)
            if folder is not None:
                parent = folder
            elif fallback_folder is not None:
                parent = PurePosixPath(fallback_folder)
                notes.append("fallback-folder")
            else:
                notes.append("no-folder")
        drafts.append(
            _Draft(
                doc=doc,
                label=rendition.label,
                src_rel=src_rel,
                stem=stem,
                ext=PurePosixPath(src_rel).suffix.lower(),
                parent=parent,
                notes=notes,
                problem=problem,
            )
        )

    # Second pass: within-plan destination collisions (casefolded). Every colliding
    # item falls back to its doc id (unique by store construction) — all of them, so
    # the outcome is independent of iteration order. SHARED/NO_LABEL don't move, so
    # they're excluded from the collision set.
    movable = [
        d
        for d in drafts
        if d.problem is None and src_counts[d.src_rel.casefold()] == 1  # not SHARED
    ]
    dst_counts: dict[str, int] = {}
    for d in movable:
        dst_counts[d.dst_rel.casefold()] = dst_counts.get(d.dst_rel.casefold(), 0) + 1
    for d in movable:
        if dst_counts[d.dst_rel.casefold()] > 1:
            d.stem = d.doc.id
            d.notes.append("id-fallback")

    # Final pass: materialize items, flag SHARED / MISSING / EXISTS.
    items: list[OrganizeItem] = []
    for d in drafts:
        src_rel, dst_rel = d.src_rel, d.dst_rel
        case_only = src_rel != dst_rel and src_rel.casefold() == dst_rel.casefold()
        if case_only:
            d.notes.append("case-only")

        problem = d.problem
        if problem is None:
            if src_counts[src_rel.casefold()] > 1:
                problem = SHARED
            elif not resolve_path(root, src_rel).exists():
                problem = MISSING
            elif src_rel != dst_rel and not case_only and _occupied(root, dst_rel):
                problem = EXISTS
        items.append(
            OrganizeItem(
                doc_id=d.doc.id,
                name=d.doc.name,
                label=d.label,
                src_rel=src_rel,
                dst_rel=dst_rel,
                problem=problem,
                note=",".join(d.notes),
            )
        )
    return OrganizePlan(to_folders=to_folders, items=tuple(items))


def _occupied(root: Path, dst_rel: str) -> bool:
    """Whether ``dst_rel`` is taken by a real file, casefold-aware.

    ``Path.exists`` misses a case-different twin on a case-sensitive filesystem, so
    also scan the destination directory and compare casefolded — never clobber a
    ``Foo.pdf`` when writing ``foo.pdf``.
    """
    dst = resolve_path(root, dst_rel)
    if dst.exists():
        return True
    parent = dst.parent
    if not parent.is_dir():
        return False
    target = dst.name.casefold()
    return any(child.name.casefold() == target for child in parent.iterdir())


def apply_organize_plan(
    plan: OrganizePlan, store: Store, *, root: Path
) -> tuple[int, list[str]]:
    """Rename every ready item on disk and rewrite its rendition path.

    Per item, disk-first then metadata, with rollback: reload the doc fresh (never
    trust a plan-age copy), move the file, then set the new path and ``store.save``;
    if the save fails, move the file back. Returns ``(renamed_count, errors)``.
    Nothing is deleted or overwritten; a stale plan (the rendition no longer sits at
    ``src_rel``) is reported, never forced.
    """
    renamed = 0
    errors: list[str] = []
    for item in plan.ready:
        try:
            doc = store.load(item.doc_id)
        except Exception as exc:  # missing / unreadable doc — skip, don't crash
            errors.append(f"{item.name}: could not load ({exc})")
            continue
        rendition = next((r for r in doc.files if r.path == item.src_rel), None)
        if rendition is None:
            errors.append(f"{item.name}: stale plan — re-run ds organize")
            continue
        src = resolve_path(root, item.src_rel)
        dst = resolve_path(root, item.dst_rel)
        case_only = "case-only" in item.note.split(",")
        if not src.exists():
            errors.append(f"{item.name}: source vanished ({item.src_rel})")
            continue
        if dst.exists() and not case_only:
            errors.append(f"{item.name}: destination now occupied ({item.dst_rel})")
            continue
        try:
            _move(src, dst)
        except OSError as exc:
            errors.append(f"{item.name}: move failed ({exc})")
            continue
        rendition.path = item.dst_rel
        try:
            store.save(doc)
        except Exception as exc:  # roll the file back so record & file stay in sync
            try:
                _move(dst, src)
                errors.append(f"{item.name}: save failed, rolled back ({exc})")
            except OSError as rb:
                errors.append(
                    f"{item.name}: save failed AND rollback failed — file is at "
                    f"{item.dst_rel}, record says {item.src_rel} ({exc}; {rb})"
                )
            continue
        renamed += 1
    return renamed, errors


def _move(src: Path, dst: Path) -> None:
    """Rename ``src`` → ``dst``, creating parents; cross-device falls back to move.

    ``os.rename`` (not ``os.replace``) so an unexpected existing destination raises
    instead of being silently clobbered — the caller has already guarded the
    destination, this is the last line of defence.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(src, dst)
    except OSError as exc:
        if exc.errno == errno.EXDEV:  # different filesystem: copy-then-unlink
            shutil.move(str(src), str(dst))
        else:
            raise
