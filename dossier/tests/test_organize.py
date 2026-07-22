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

"""Tests for the canonical-rename planner + applier (`ds organize`)."""

from __future__ import annotations

import errno
from datetime import date
from pathlib import Path

import pytest

from dossier import organize
from dossier.config import Config
from dossier.model import Document, Rendition
from dossier.store import Store


def _note(item: organize.OrganizeItem) -> set[str]:
    return set(item.note.split(",")) - {""}


# -- canonical_stem (pure) ---------------------------------------------------


def test_canonical_stem_slugifies_the_name():
    stem, note = organize.canonical_stem(Document(id="p", name="Passport"))
    assert (stem, note) == ("passport", "")


def test_canonical_stem_prefixes_issue_date_when_name_has_none():
    doc = Document(id="p", name="Passport", issue_date=date(2024, 1, 3))
    assert organize.canonical_stem(doc) == ("2024-01-03-passport", "")


def test_canonical_stem_skips_prefix_when_the_name_already_has_a_date():
    # The gate that stops "2020-08-06-brp-...-06-08-2020" stutter on the real store.
    doc = Document(id="b", name="BRP Expires 06-08-2020", issue_date=date(2019, 8, 22))
    stem, note = organize.canonical_stem(doc)
    assert stem == "brp-expires-06-08-2020"
    assert note == ""


def test_canonical_stem_truncates_a_very_long_name_at_a_hyphen():
    doc = Document(id="x", name="word " * 60)  # ~300 chars of "word-word-..."
    stem, note = organize.canonical_stem(doc)
    assert note == "truncated"
    assert len(stem) <= organize.MAX_STEM
    assert not stem.endswith("-")  # backed off to a boundary, no dangling hyphen


# -- build_organize_plan: in-place renames -----------------------------------


def _root(tmp_path: Path, *rel: str) -> Path:
    root = tmp_path / "root"
    for r in rel:
        p = root / r
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    root.mkdir(exist_ok=True)
    return root


def test_plan_renames_in_place_and_lowercases_ext(tmp_path: Path):
    root = _root(tmp_path, "Marine/Scan.PDF")
    docs = [
        Document(
            id="coc", name="CoC Card", files=[Rendition("d", "Marine/Scan.PDF", True)]
        )
    ]
    plan = organize.build_organize_plan(docs, root=root)
    (item,) = plan.items
    assert item.problem is None
    assert item.dst_rel == "Marine/coc-card.pdf"  # same dir, kebab, lowercase ext
    assert plan.ready == [item]


def test_plan_marks_already_canonical(tmp_path: Path):
    root = _root(tmp_path, "Marine/coc-card.pdf")
    docs = [
        Document(
            id="coc",
            name="CoC Card",
            files=[Rendition("d", "Marine/coc-card.pdf", True)],
        )
    ]
    plan = organize.build_organize_plan(docs, root=root)
    assert plan.ready == []
    assert plan.already == list(plan.items)
    assert plan.items[0].already_canonical


def test_plan_flags_a_file_shared_by_two_records(tmp_path: Path):
    root = _root(tmp_path, "Marine/med.pdf")
    docs = [
        Document(
            id="med-a", name="Med A", files=[Rendition("d", "Marine/med.pdf", True)]
        ),
        Document(
            id="med-b", name="Med B", files=[Rendition("d", "Marine/med.pdf", True)]
        ),
    ]
    plan = organize.build_organize_plan(docs, root=root)
    assert {i.doc_id: i.problem for i in plan.items} == {
        "med-a": organize.SHARED,
        "med-b": organize.SHARED,
    }
    assert plan.ready == []  # neither is moved


def test_plan_flags_missing_source(tmp_path: Path):
    root = _root(tmp_path)  # no files on disk
    docs = [
        Document(id="g", name="Gone", files=[Rendition("d", "Marine/gone.pdf", True)])
    ]
    plan = organize.build_organize_plan(docs, root=root)
    assert plan.items[0].problem == organize.MISSING


def test_plan_flags_an_occupied_destination_never_overwrites(tmp_path: Path):
    # src exists; a *different* file already sits at the canonical name.
    root = _root(tmp_path, "Marine/raw.pdf", "Marine/passport.pdf")
    docs = [
        Document(
            id="p", name="Passport", files=[Rendition("d", "Marine/raw.pdf", True)]
        )
    ]
    plan = organize.build_organize_plan(docs, root=root)
    assert plan.items[0].problem == organize.EXISTS


def test_plan_resolves_within_plan_collision_via_id_fallback(tmp_path: Path):
    root = _root(tmp_path, "Docs/a.pdf", "Docs/b.pdf")
    docs = [
        Document(
            id="report-a", name="Report", files=[Rendition("d", "Docs/a.pdf", True)]
        ),
        Document(
            id="report-b", name="Report", files=[Rendition("d", "Docs/b.pdf", True)]
        ),
    ]
    plan = organize.build_organize_plan(docs, root=root)
    by_id = {i.doc_id: i for i in plan.items}
    assert by_id["report-a"].dst_rel == "Docs/report-a.pdf"  # fell back to the id
    assert by_id["report-b"].dst_rel == "Docs/report-b.pdf"
    assert "id-fallback" in _note(by_id["report-a"])
    assert len({i.dst_rel for i in plan.items}) == 2  # no longer collide


def test_plan_treats_a_case_only_rename_as_ready(tmp_path: Path):
    root = _root(tmp_path, "Marine/CoC-Card.pdf")
    docs = [
        Document(
            id="coc",
            name="CoC Card",
            files=[Rendition("d", "Marine/CoC-Card.pdf", True)],
        )
    ]
    plan = organize.build_organize_plan(docs, root=root)
    (item,) = plan.items
    assert item.problem is None  # not flagged EXISTS against its own file
    assert item.dst_rel == "Marine/coc-card.pdf"
    assert "case-only" in _note(item)
    assert item in plan.ready


# -- build_organize_plan: --to-folders ---------------------------------------


def test_to_folders_moves_into_the_mapped_folder(tmp_path: Path):
    root = _root(tmp_path, "Inbox/raw.pdf")
    docs = [
        Document(
            id="coc",
            name="CoC",
            tags=["marine"],
            files=[Rendition("d", "Inbox/raw.pdf", True)],
        )
    ]
    plan = organize.build_organize_plan(
        docs, root=root, to_folders=True, folder_map={"marine": "Marine"}
    )
    assert plan.items[0].dst_rel == "Marine/coc.pdf"


def test_to_folders_keeps_current_dir_when_untagged(tmp_path: Path):
    root = _root(tmp_path, "Inbox/raw.pdf")
    docs = [
        Document(id="coc", name="CoC", files=[Rendition("d", "Inbox/raw.pdf", True)])
    ]
    plan = organize.build_organize_plan(docs, root=root, to_folders=True)
    (item,) = plan.items
    assert item.dst_rel == "Inbox/coc.pdf"  # degrades to in-place
    assert "no-folder" in _note(item)


def test_to_folders_longest_prefix_wins(tmp_path: Path):
    root = _root(tmp_path, "Inbox/raw.pdf")
    docs = [
        Document(
            id="cert",
            name="Cert",
            tags=["marine/safety"],
            files=[Rendition("d", "Inbox/raw.pdf", True)],
        )
    ]
    fmap = {"marine": "Marine", "marine/safety": "Marine/Safety Course Certs"}
    plan = organize.build_organize_plan(
        docs, root=root, to_folders=True, folder_map=fmap
    )
    assert plan.items[0].dst_rel == "Marine/Safety Course Certs/cert.pdf"


# -- multi-rendition ---------------------------------------------------------


def test_multi_rendition_labels_the_non_primary(tmp_path: Path):
    root = _root(tmp_path, "P/a.pdf", "P/b.pdf")
    docs = [
        Document(
            id="pp",
            name="Passport",
            files=[Rendition("front", "P/a.pdf", True), Rendition("back", "P/b.pdf")],
        )
    ]
    plan = organize.build_organize_plan(docs, root=root)
    dsts = {i.label: i.dst_rel for i in plan.items}
    assert dsts == {"front": "P/passport.pdf", "back": "P/passport--back.pdf"}


def test_multi_rendition_empty_label_is_flagged(tmp_path: Path):
    root = _root(tmp_path, "P/a.pdf", "P/b.pdf")
    docs = [
        Document(
            id="pp",
            name="Passport",
            files=[Rendition("front", "P/a.pdf", True), Rendition("", "P/b.pdf")],
        )
    ]
    plan = organize.build_organize_plan(docs, root=root)
    assert {i.label: i.problem for i in plan.items}[""] == organize.NO_LABEL


# -- apply -------------------------------------------------------------------


def _store(tmp_path: Path) -> tuple[Store, Path]:
    root = tmp_path / "root"
    root.mkdir()
    config = Config(syncthing_root=root, history_dir=tmp_path / "_history")
    store = Store(config)
    store.ensure_layout()
    return store, root


def _add(store: Store, root: Path, doc: Document, content: bytes = b"x") -> None:
    for r in doc.files:
        p = root / r.path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    store.save(doc)


def test_apply_renames_the_file_and_rewrites_the_rendition_path(tmp_path: Path):
    store, root = _store(tmp_path)
    _add(
        store,
        root,
        Document(
            id="coc", name="CoC Card", files=[Rendition("d", "Marine/scan.pdf", True)]
        ),
    )
    plan = organize.build_organize_plan(store.load_all(), root=root)
    renamed, errors = organize.apply_organize_plan(plan, store, root=root)
    assert (renamed, errors) == (1, [])
    assert (root / "Marine/coc-card.pdf").exists()
    assert not (root / "Marine/scan.pdf").exists()
    assert store.load("coc").files[0].path == "Marine/coc-card.pdf"


def test_apply_is_idempotent(tmp_path: Path):
    store, root = _store(tmp_path)
    _add(
        store,
        root,
        Document(
            id="coc", name="CoC Card", files=[Rendition("d", "Marine/scan.pdf", True)]
        ),
    )
    first = organize.build_organize_plan(store.load_all(), root=root)
    organize.apply_organize_plan(first, store, root=root)
    second = organize.build_organize_plan(store.load_all(), root=root)
    assert second.ready == []  # nothing left to do
    renamed, errors = organize.apply_organize_plan(second, store, root=root)
    assert (renamed, errors) == (0, [])


def test_apply_rolls_back_the_move_when_the_save_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, root = _store(tmp_path)
    _add(
        store,
        root,
        Document(
            id="coc", name="CoC Card", files=[Rendition("d", "Marine/scan.pdf", True)]
        ),
    )
    plan = organize.build_organize_plan(store.load_all(), root=root)

    def boom(_doc: Document) -> Document:
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "save", boom)
    renamed, errors = organize.apply_organize_plan(plan, store, root=root)
    assert renamed == 0
    assert "rolled back" in errors[0]
    assert (root / "Marine/scan.pdf").exists()  # file is back where it started
    assert not (root / "Marine/coc-card.pdf").exists()


def test_apply_uses_shutil_move_on_cross_device_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, root = _store(tmp_path)
    _add(
        store,
        root,
        Document(
            id="coc", name="CoC Card", files=[Rendition("d", "Marine/scan.pdf", True)]
        ),
    )
    plan = organize.build_organize_plan(store.load_all(), root=root)

    def exdev(_src: str, _dst: str) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(organize.os, "rename", exdev)  # force the fallback path
    renamed, errors = organize.apply_organize_plan(plan, store, root=root)
    assert (renamed, errors) == (1, [])
    assert (root / "Marine/coc-card.pdf").exists()


def test_apply_reports_a_stale_plan_without_touching_disk(tmp_path: Path):
    store, root = _store(tmp_path)
    _add(
        store,
        root,
        Document(
            id="coc", name="CoC Card", files=[Rendition("d", "Marine/scan.pdf", True)]
        ),
    )
    plan = organize.build_organize_plan(store.load_all(), root=root)
    # The rendition moves out from under the plan (e.g. an earlier run).
    doc = store.load("coc")
    doc.files[0].path = "Marine/elsewhere.pdf"
    store.save(doc)
    renamed, errors = organize.apply_organize_plan(plan, store, root=root)
    assert renamed == 0
    assert "stale plan" in errors[0]
