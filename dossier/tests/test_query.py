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

"""Tests for the query layer."""

from datetime import date
from pathlib import Path

from dossier import query
from dossier.model import Document, ExpiryStatus, FileStatus, Rendition

TODAY = date(2026, 7, 21)


def _doc(
    id_: str,
    *,
    name: str = "",
    tags: list[str] | None = None,
    bundles: list[str] | None = None,
    notes: str = "",
    files: list[Rendition] | None = None,
    perm_location: str | None = None,
    perm_slot: int | None = None,
    perm_subslot: int | None = None,
    temp_location: str | None = None,
    expiry_date: date | None = None,
) -> Document:
    return Document(
        id=id_,
        name=name or id_,
        tags=tags or [],
        bundles=bundles or [],
        notes=notes,
        files=files or [],
        perm_location=perm_location,
        perm_slot=perm_slot,
        perm_subslot=perm_subslot,
        temp_location=temp_location,
        expiry_date=expiry_date,
    )


def test_file_status(tmp_path: Path):
    assert query.file_status(Document(), tmp_path) is FileStatus.NONE

    present = _doc("a", files=[Rendition(label="d", path="sub/here.pdf")])
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "here.pdf").write_bytes(b"x")
    assert query.file_status(present, tmp_path) is FileStatus.OK

    absent = _doc("b", files=[Rendition(label="d", path="sub/gone.pdf")])
    assert query.file_status(absent, tmp_path) is FileStatus.MISSING


def test_resolve_path_splits_posix(tmp_path: Path):
    resolved = query.resolve_path(tmp_path, "Marine/CoC Card.pdf")
    assert resolved == tmp_path / "Marine" / "CoC Card.pdf"


def test_text_search_covers_name_tags_notes():
    docs = [
        _doc("a", name="Passport", tags=["identity"]),
        _doc("b", name="CoC", notes="a marine certificate"),
        _doc("c", name="Olympiad", tags=["academic"]),
    ]
    flt = query.Filter(text="marine")
    got = {d.id for d in query.search(docs, flt, today=TODAY, threshold_days=90)}
    assert got == {"b"}


def test_tag_filter_is_hierarchical():
    docs = [
        _doc("a", tags=["marine/coc"]),
        _doc("b", tags=["marine"]),
        _doc("c", tags=["medical"]),
    ]
    flt = query.Filter(tags=("marine",))
    got = {d.id for d in query.search(docs, flt, today=TODAY, threshold_days=90)}
    assert got == {"a", "b"}


def test_bundle_and_location_filters():
    docs = [
        _doc("a", bundles=["us-visa"], perm_location="file"),
        _doc("b", bundles=["india"], perm_location="file"),
        _doc("c", bundles=["us-visa"], perm_location="pouch"),
    ]
    flt = query.Filter(bundles=("us-visa",), locations=("file",))
    got = {d.id for d in query.search(docs, flt, today=TODAY, threshold_days=90)}
    assert got == {"a"}


def test_expiry_filter_and_expiring_ordering():
    docs = [
        _doc("expired", expiry_date=date(2026, 1, 1)),
        _doc("soon", expiry_date=date(2026, 8, 1)),
        _doc("far", expiry_date=date(2030, 1, 1)),
        _doc("none"),
    ]
    flt = query.Filter(expiry=(ExpiryStatus.EXPIRED, ExpiryStatus.EXPIRING))
    got = {d.id for d in query.search(docs, flt, today=TODAY, threshold_days=90)}
    assert got == {"expired", "soon"}

    ordered = query.expiring(docs, today=TODAY, threshold_days=90)
    assert [d.id for d in ordered] == ["expired", "soon"]  # soonest date first


def test_sort_and_group_by_effective_location():
    docs = [
        _doc("z", perm_location="file", perm_slot=2),
        _doc("a", perm_location="file", perm_slot=1, perm_subslot=3),
        _doc("b", perm_location="file", perm_slot=1, perm_subslot=1),
        _doc("t", perm_location="file", perm_slot=5, temp_location="pouch"),
        _doc("loose"),  # no location -> last group
    ]
    ordered = [d.id for d in query.sort_documents(docs)]
    # within "file": slot1/subslot1, slot1/subslot3, slot2; "pouch" then unlocated
    assert ordered == ["b", "a", "z", "t", "loose"]

    grouped = query.group_by_location(docs)
    assert [loc for loc, _ in grouped] == ["file", "pouch", None]
    assert [d.id for d in grouped[0][1]] == ["b", "a", "z"]
