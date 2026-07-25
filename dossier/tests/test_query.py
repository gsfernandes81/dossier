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
from dossier.model import Bundle, Document, ExpiryStatus, FileStatus, Rendition

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
    ignore_expiry: bool = False,
    supersedes: str | None = None,
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
        ignore_expiry=ignore_expiry,
        supersedes=supersedes,
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


def _reading(**fields: object):
    from dossier.scan import ScanReading

    return ScanReading.from_payload(fields, model="m")


def test_content_search_matches_a_readings_field_not_in_the_name():
    # "bernhard" is nowhere in the name/tags/notes — only in the scan's issuer.
    docs = [
        _doc("sea", name="2025-07-01 testimonial"),
        _doc("other", name="Passport"),
    ]
    readings = {"sea": _reading(issuer="Bernhard Schulte", document_type="Testimonial")}
    flt = query.Filter(text="bernhard")
    plain = {d.id for d in query.search(docs, flt, today=TODAY, threshold_days=90)}
    assert plain == set()  # without readings: no match
    withr = {
        d.id
        for d in query.search(
            docs, flt, today=TODAY, threshold_days=90, readings=readings
        )
    }
    assert withr == {"sea"}  # content search finds it via the reading


def test_transcript_is_opt_in_not_in_the_default_search():
    doc = _doc("d", name="scan001")
    reading = _reading(document_type="X", transcript="the INDoS number is 09MU1234")
    flt = query.Filter(text="indos")
    # The default `/` filter does NOT match transcript body text (noisy, opt-in)...
    got = query.search(
        [doc], flt, today=TODAY, threshold_days=90, readings={"d": reading}
    )
    assert got == []
    # ...but the transcript is in the content-inclusive text `ds ask` uses.
    assert "indos" in query.reading_text(reading, include_content=True).casefold()
    assert "indos" not in query.reading_text(reading).casefold()


def test_reading_text_joins_present_fields_only():
    reading = _reading(document_type="Passport", issuer="HMPO", holder_name=None)
    text = query.reading_text(reading)
    assert "Passport" in text and "HMPO" in text
    assert "None" not in text  # None fields skipped


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


def test_superseded_ids_and_watch_is_opt_out():
    docs = [
        _doc("passport-2016", expiry_date=date(2026, 1, 1)),
        _doc("passport-2026", expiry_date=date(2036, 1, 1), supersedes="passport-2016"),
        _doc("coc", expiry_date=date(2027, 3, 1)),
        _doc("old-cdc", expiry_date=date(2025, 1, 1), ignore_expiry=True),
        _doc("no-expiry"),
    ]
    assert query.superseded_ids(docs) == {"passport-2016"}

    watch = [d.id for d in query.tracked(docs, today=TODAY)]
    # superseded old passport, opted-out CDC, and the undated doc are all hidden;
    # order is soonest expiry first.
    assert watch == ["coc", "passport-2026"]


def test_supersession_chain_follows_links_and_is_cycle_safe():
    docs = [
        _doc("v1"),
        _doc("v2", supersedes="v1"),
        _doc("v3", supersedes="v2"),
    ]
    by_id = {d.id: d for d in docs}
    chain = query.supersession_chain(docs, by_id["v3"])
    assert [d.id for d in chain] == ["v2", "v1"]  # newest replaced first

    assert query.supersession_chain(docs, by_id["v1"]) == []

    # A cycle must not loop forever.
    a = _doc("a", supersedes="b")
    b = _doc("b", supersedes="a")
    assert {d.id for d in query.supersession_chain([a, b], a)} == {"b"}


def test_plan_move_inserts_and_shifts():
    docs = [
        _doc("a", perm_location="file", perm_slot=1),
        _doc("b", perm_location="file", perm_slot=2),
        _doc("c", perm_location="file", perm_slot=3),
        _doc("x", perm_location="pouch", perm_slot=1),
    ]
    changed = query.plan_move(docs, docs[3], "file", 2)  # x -> file slot 2

    slots = {d.id: (d.perm_location, d.perm_slot) for d in docs}
    assert slots["a"] == ("file", 1)  # before the gap, untouched
    assert slots["b"] == ("file", 3)  # shifted 2 -> 3
    assert slots["c"] == ("file", 4)  # shifted 3 -> 4
    assert slots["x"] == ("file", 2)  # inserted
    assert {d.id for d in changed} == {"b", "c", "x"}


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


def test_bundle_sort_and_group():
    india = Bundle(slug="travel/india-2024", title="India 2024", date=date(2024, 3, 11))
    bali = Bundle(slug="travel/bali-2025", title="Bali 2025", date=date(2025, 6, 2))
    ship = Bundle(slug="joining/ship-2024", title="Ship 2024", date=date(2024, 8, 1))
    visa = Bundle(slug="us-visa", title="US Visa")  # flat, no date

    groups = query.group_bundles([visa, bali, india, ship])
    assert [key for key, _ in groups] == ["joining", "travel", None]  # flat last
    travel = dict(groups)["travel"]
    assert [b.slug for b in travel] == [  # chronological within the group
        "travel/india-2024",
        "travel/bali-2025",
    ]


def _search(docs, text, **kw):
    return query.search(
        docs, query.Filter(text=text), today=TODAY, threshold_days=90, **kw
    )


def test_search_exact_suppresses_fuzzy():
    docs = [_doc("passport", name="Passport"), _doc("policy", name="Policy")]
    # An exact substring hit is returned alone — the fuzzy pass never runs.
    assert [d.id for d in _search(docs, "passport")] == ["passport"]


def test_search_fuzzy_fires_only_on_zero_exact():
    docs = [_doc("passport", name="Passport"), _doc("visa", name="US Visa")]
    assert [d.id for d in _search(docs, "pasport")] == ["passport"]  # typo forgiven
    assert [d.id for d in _search(docs, "passport")] == ["passport"]  # exact, unchanged


def test_search_short_query_never_fuzzes():
    docs = [_doc("cat", name="cat"), _doc("car", name="car")]
    assert [d.id for d in _search(docs, "cat")] == ["cat"]  # not "car"
    assert _search(docs, "xyz") == []  # a short miss stays a miss (no match-everything)


def test_search_fuzzy_ands_its_terms():
    docs = [_doc("a", name="Marine Certificate"), _doc("b", name="Marine Passport")]
    # Both terms must land — "cerificate" (typo) picks only the certificate doc.
    assert [d.id for d in _search(docs, "marine cerificate")] == ["a"]


def test_search_fuzzy_matches_a_reading_field():
    from dossier import scan as scan_mod

    docs = [_doc("d1", name="Untitled scan")]
    reading = scan_mod.ScanReading.from_payload(
        {"document_type": "Certificate", "confidence": 0.9}, model="m"
    )
    # A typo of a scan's structured field is found once readings are supplied.
    assert _search(docs, "cerificate") == []  # not in the name
    hits = _search(docs, "cerificate", readings={"d1": reading})
    assert [d.id for d in hits] == ["d1"]
