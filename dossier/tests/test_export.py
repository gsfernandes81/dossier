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

"""Tests for the bundle export planner + applier."""

from pathlib import Path

from dossier import export
from dossier.model import Document, Rendition


def _docs(root: Path) -> list[Document]:
    (root / "Marine").mkdir(exist_ok=True)  # helper may run twice in a test
    (root / "Marine" / "coc.pdf").write_bytes(b"coc")
    return [
        Document(
            id="coc-card",
            name="CoC Card",
            bundles=["travel/india-2024"],
            files=[Rendition("d", "Marine/coc.pdf", primary=True)],
        ),
        Document(
            id="seamans-book",  # physical only, no digital file
            name="Seaman's Book",
            bundles=["travel/india-2024"],
        ),
        Document(
            id="gone",
            name="Gone Doc",
            bundles=["travel/india-2024"],
            files=[Rendition("d", "Marine/gone.pdf", primary=True)],  # missing on disk
        ),
        Document(id="other", name="Other", bundles=["visa/us-2025"]),  # not a member
    ]


def test_export_plan_flags_problems_and_names_by_id(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    dest = tmp_path / "out"
    plan = export.build_export_plan(
        _docs(root), "travel/india-2024", root=root, dest=dest
    )
    by_id = {item.doc_id: item for item in plan.items}
    assert set(by_id) == {"coc-card", "seamans-book", "gone"}  # only members
    assert by_id["coc-card"].problem is None
    assert by_id["coc-card"].dst == dest / "coc-card.pdf"  # named by doc id
    assert by_id["seamans-book"].problem == export.NO_FILE
    assert by_id["gone"].problem == export.MISSING


def test_export_apply_copies_ready_items(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    dest = tmp_path / "out"
    plan = export.build_export_plan(
        _docs(root), "travel/india-2024", root=root, dest=dest
    )
    exported, errors = export.apply_export_plan(plan)
    assert exported == 1
    assert errors == []
    assert (dest / "coc-card.pdf").read_bytes() == b"coc"


def test_export_skips_existing_without_force(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "coc-card.pdf").write_bytes(b"old")  # already there
    plan = export.build_export_plan(
        _docs(root), "travel/india-2024", root=root, dest=dest
    )
    assert {i.doc_id: i.problem for i in plan.items}["coc-card"] == export.EXISTS
    export.apply_export_plan(plan)
    assert (dest / "coc-card.pdf").read_bytes() == b"old"  # untouched

    forced = export.build_export_plan(
        _docs(root), "travel/india-2024", root=root, dest=dest, force=True
    )
    export.apply_export_plan(forced)
    assert (dest / "coc-card.pdf").read_bytes() == b"coc"  # overwritten
