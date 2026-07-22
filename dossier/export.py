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

"""Export a bundle's files to an external folder — the "gather the files" goal.

Pure plan / apply split, like :mod:`dossier.migrate`: :func:`build_export_plan`
resolves a bundle's member documents to (source, destination) pairs and flags
problems (no digital file, a missing source, an existing destination) without
touching disk; :func:`apply_export_plan` does the copy (or symlink). Destinations
are named by document id (unique + slug-safe), so the exported folder reads like
the document list rather than a pile of ``scan.pdf``\\ s.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from dossier import query
from dossier.model import Document

# Problem codes for an un-exportable member.
NO_FILE = "no-digital-file"
MISSING = "missing-file"
EXISTS = "exists"


@dataclass(frozen=True)
class ExportItem:
    doc_id: str
    name: str
    src: Path | None
    dst: Path | None
    problem: str | None  # None = ready; else NO_FILE / MISSING / EXISTS


@dataclass(frozen=True)
class ExportPlan:
    slug: str
    dest: Path
    items: tuple[ExportItem, ...]

    @property
    def ready(self) -> list[ExportItem]:
        return [item for item in self.items if item.problem is None]

    @property
    def problems(self) -> list[ExportItem]:
        return [item for item in self.items if item.problem is not None]


def build_export_plan(
    docs: list[Document], slug: str, *, root: Path, dest: Path, force: bool = False
) -> ExportPlan:
    """Plan the export of bundle ``slug``'s members into ``dest`` (no disk writes).

    Members are the documents carrying ``slug``, sorted by name. Each resolves to
    ``dest / f"{doc_id}{suffix}"``; a member with no primary rendition, a missing
    source file, or (unless ``force``) an existing destination is flagged instead.
    """
    members = sorted(
        (doc for doc in docs if slug in doc.bundles), key=lambda d: d.name.casefold()
    )
    items: list[ExportItem] = []
    for doc in members:
        rendition = doc.primary_rendition()
        if rendition is None:
            items.append(ExportItem(doc.id, doc.name, None, None, NO_FILE))
            continue
        src = query.resolve_path(root, rendition.path)
        dst = dest / f"{doc.id}{Path(rendition.path).suffix}"
        if not src.exists():
            problem: str | None = MISSING
        elif dst.exists() and not force:
            problem = EXISTS
        else:
            problem = None
        items.append(ExportItem(doc.id, doc.name, src, dst, problem))
    return ExportPlan(slug=slug, dest=dest, items=tuple(items))


def apply_export_plan(
    plan: ExportPlan, *, symlink: bool = False
) -> tuple[int, list[str]]:
    """Copy (or symlink) every ready item into the destination.

    Returns ``(exported_count, errors)``. Copying is non-destructive by default —
    only ``force``-marked (ready-despite-existing) destinations are overwritten.
    On Windows, symlinks need Developer Mode / an elevated shell; the ``OSError``
    is caught per item and surfaced, never silently downgraded to a copy.
    """
    plan.dest.mkdir(parents=True, exist_ok=True)
    exported = 0
    errors: list[str] = []
    for item in plan.ready:
        assert item.src is not None and item.dst is not None
        try:
            if item.dst.exists() or item.dst.is_symlink():
                item.dst.unlink()
            if symlink:
                item.dst.symlink_to(item.src)
            else:
                shutil.copy2(item.src, item.dst)
            exported += 1
        except OSError as exc:
            errors.append(f"{item.name}: {exc}")
    return exported, errors
