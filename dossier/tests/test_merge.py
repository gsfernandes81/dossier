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

"""Tests for the pure field-merge engine (:mod:`dossier.merge`)."""

from dataclasses import replace
from datetime import date, datetime

from dossier import scan
from dossier.merge import (
    Side,
    merge_bundles,
    merge_documents,
    merge_locations,
    merge_readings,
    merge_reconcile,
    merge_suggestions,
)
from dossier.model import (
    Bundle,
    Document,
    Location,
    ReconcileState,
    Rendition,
    SuggestionState,
)


def _reading(document_type: str = "Passport", **over: object) -> scan.ScanReading:
    base = scan.ScanReading.from_payload({"document_type": document_type}, model="m")
    return replace(base, **over)  # type: ignore[arg-type]


# -- documents ---------------------------------------------------------------


def test_agreed_scalar_is_kept_and_not_contested():
    ours = Document(id="d", name="Passport")
    theirs = Document(id="d", name="Passport")
    result = merge_documents(ours, theirs, prefer=Side.THEIRS)
    assert result.merged.name == "Passport"
    assert result.clean  # nothing to review


def test_fill_takes_the_nonempty_side_either_direction():
    ours = Document(id="d", name="Passport")  # no expiry
    theirs = Document(id="d", name="Passport", expiry_date=date(2030, 1, 1))
    # theirs fills ours' empty expiry; ours fills theirs' empty issue.
    theirs2 = replace(theirs, issue_date=None)
    ours2 = replace(ours, issue_date=date(2020, 1, 1))
    result = merge_documents(ours2, theirs2, prefer=Side.OURS)
    assert result.merged.expiry_date == date(2030, 1, 1)  # filled from theirs
    assert result.merged.issue_date == date(2020, 1, 1)  # filled from ours
    assert result.clean  # a fill is never contested — nothing was overwritten


def test_contested_scalar_is_last_writer_wins_by_prefer():
    ours = Document(id="d", name="Passport")
    theirs = Document(id="d", name="Passport (renewed)")
    kept_theirs = merge_documents(ours, theirs, prefer=Side.THEIRS)
    kept_ours = merge_documents(ours, theirs, prefer=Side.OURS)
    assert kept_theirs.merged.name == "Passport (renewed)"
    assert kept_ours.merged.name == "Passport"
    assert [d.field for d in kept_theirs.contested] == ["name"]
    assert kept_theirs.contested[0].winner is Side.THEIRS


def test_tie_resolves_to_ours_and_is_labelled_tie():
    ours = Document(id="d", name="A")
    theirs = Document(id="d", name="B")
    result = merge_documents(ours, theirs, prefer=Side.THEIRS, tie=True)
    assert result.merged.name == "A"  # tie → ours, despite prefer=THEIRS
    assert result.contested[0].action == "tie"
    assert result.contested[0].winner is Side.OURS


def test_tags_and_bundles_union_ordered_and_deduped():
    ours = Document(id="d", tags=["gov", "id"], bundles=["visa"])
    theirs = Document(id="d", tags=["id", "travel"], bundles=["visa", "trip"])
    result = merge_documents(ours, theirs, prefer=Side.THEIRS)
    assert result.merged.tags == ["gov", "id", "travel"]  # ours first, new appended
    assert result.merged.bundles == ["visa", "trip"]
    assert result.clean  # a union is never a human-review item


def test_renditions_union_by_path():
    ours = Document(id="d", files=[Rendition("full", "a.pdf", primary=True)])
    theirs = Document(id="d", files=[Rendition("back", "b.pdf")])
    result = merge_documents(ours, theirs, prefer=Side.THEIRS)
    assert {r.path for r in result.merged.files} == {"a.pdf", "b.pdf"}


def test_same_path_rendition_is_lww_and_only_one_primary_survives():
    ours = Document(id="d", files=[Rendition("full", "a.pdf", primary=True)])
    theirs = Document(
        id="d",
        files=[
            Rendition("full-hd", "a.pdf", primary=True),  # same path, differs → LWW
            Rendition("back", "b.pdf", primary=True),  # second primary must collapse
        ],
    )
    result = merge_documents(ours, theirs, prefer=Side.THEIRS)
    by_path = {r.path: r for r in result.merged.files}
    assert by_path["a.pdf"].label == "full-hd"  # theirs won the same-path clash
    primaries = [r for r in result.merged.files if r.primary]
    assert len(primaries) == 1  # never two primaries after a merge


def test_merged_document_drops_source_hash():
    ours = Document(id="d", name="A", source_hash="abc")
    theirs = Document(id="d", name="A", source_hash="def")
    result = merge_documents(ours, theirs, prefer=Side.THEIRS)
    assert result.merged.source_hash is None  # new content; the store re-hashes


# -- readings ----------------------------------------------------------------


def test_readings_union_new_ids():
    ours = {"a": _reading("Passport")}
    theirs = {"b": _reading("Licence")}
    result = merge_readings(ours, theirs, prefer=Side.OURS)
    assert set(result.merged) == {"a", "b"}


def test_reading_clash_prefers_the_side_with_a_transcript():
    ours = {"a": _reading("Passport", model="cheap")}
    theirs = {"a": _reading("Passport", model="cheap", transcript="full text")}
    # prefer=OURS, but theirs has the (expensive) transcript, so theirs wins.
    result = merge_readings(ours, theirs, prefer=Side.OURS)
    assert result.merged["a"].transcript == "full text"


def test_reading_clash_without_transcripts_is_lww():
    ours = {"a": _reading("Passport", model="old")}
    theirs = {"a": _reading("Passport", model="new")}
    result = merge_readings(ours, theirs, prefer=Side.THEIRS)
    assert result.merged["a"].model == "new"


# -- bundles & locations -----------------------------------------------------


def test_bundles_union_and_created_is_the_earlier_stamp():
    early = datetime(2024, 1, 1)
    late = datetime(2025, 1, 1)
    ours = {"trip": Bundle(slug="trip", title="Trip", created=late)}
    theirs = {
        "trip": Bundle(slug="trip", title="Trip", created=early, notes="pack early"),
        "visa": Bundle(slug="visa", title="Visa"),
    }
    result = merge_bundles(ours, theirs, prefer=Side.THEIRS)
    assert set(result.merged) == {"trip", "visa"}
    assert result.merged["trip"].created == early  # min of the two
    assert result.merged["trip"].notes == "pack early"  # filled from theirs


def test_locations_union_and_field_merge():
    ours = {"safe": Location(slug="safe", title="Home safe")}
    theirs = {
        "safe": Location(slug="safe", title="Home safe", notes="top shelf"),
        "office": Location(slug="office", title="Office"),
    }
    result = merge_locations(ours, theirs, prefer=Side.THEIRS)
    assert set(result.merged) == {"safe", "office"}
    assert result.merged["safe"].notes == "top shelf"


# -- suppression sidecars (union-only) ---------------------------------------


def test_suggestions_union_never_loses_a_dismissal():
    ours = SuggestionState(dismissed={"k1"})
    theirs = SuggestionState(dismissed={"k2"})
    result = merge_suggestions(ours, theirs)
    assert result.merged.dismissed == {"k1", "k2"}
    assert result.clean  # union is never contested


def test_reconcile_unions_every_field_including_maps_of_sets():
    ours = ReconcileState(
        dismissed={"o1"},
        ignore=["*.tmp"],
        missing_ok={"p": {"d1"}},
        folded={"keep": {"sub1"}},
        succession_dismissed={"a\x00b"},
    )
    theirs = ReconcileState(
        dismissed={"o2"},
        ignore=["*.bak"],
        missing_ok={"p": {"d2"}, "q": {"d3"}},
        folded={"keep": {"sub2"}},
        succession_dismissed={"c\x00d"},
    )
    merged = merge_reconcile(ours, theirs).merged
    assert merged.dismissed == {"o1", "o2"}
    assert set(merged.ignore) == {"*.tmp", "*.bak"}
    assert merged.missing_ok == {"p": {"d1", "d2"}, "q": {"d3"}}  # sets unioned per key
    assert merged.folded == {"keep": {"sub1", "sub2"}}
    assert merged.succession_dismissed == {"a\x00b", "c\x00d"}
