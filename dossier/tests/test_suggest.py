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

"""Tests for the suggestions engine and its dismissal sidecar."""

from datetime import date
from pathlib import Path

from dossier import scan, suggest
from dossier.config import Config
from dossier.model import Bundle, Document, SuggestedField, Suggestion, SuggestionState
from dossier.store import Store


def _reading(**fields: object) -> scan.ScanReading:
    return scan.ScanReading.from_payload(
        {"document_type": "X", "confidence": 0.9, **fields}, model="m"
    )


def _only(doc: Document) -> Suggestion:
    out = suggest.for_document(doc)
    assert len(out) == 1, out
    return out[0]


def test_validity_range_confirmed_by_expiry_suggests_issue():
    doc = Document(
        id="coc",
        name="CoC 2025-02-10 to 2026-09-28",
        expiry_date=date(2026, 9, 28),  # authoritative; matches the range end
    )
    s = _only(doc)
    assert s.field is SuggestedField.ISSUE
    assert s.values == ("2025-02-10",)  # the range start


def test_unconfirmed_range_is_a_period_note_not_issue_or_expiry():
    doc = Document(id="sst", name="Sea Service 2022-01-06 to 2022-03-27")
    s = _only(doc)
    assert s.field is SuggestedField.NOTES
    assert s.values == ("2022-01-06", "2022-03-27")
    # crucially: no issue/expiry suggestion — the range start never becomes issue
    assert all(x.field is SuggestedField.NOTES for x in suggest.for_document(doc))


def test_expiry_keyword_suggests_expiry():
    doc = Document(id="cbt", name="Motorcycle CBT expires 07-Jan-2026")
    s = _only(doc)
    assert s.field is SuggestedField.EXPIRY
    assert s.values == ("2026-01-07",)


def test_issue_keyword_suggests_issue():
    doc = Document(id="gas", name="Basic Gas issued 2024-06-24")
    s = _only(doc)
    assert s.field is SuggestedField.ISSUE
    assert s.values == ("2024-06-24",)


def test_bare_date_suggests_issue_never_expiry():
    doc = Document(id="misc", name="Some Doc 2023-08-15")
    s = _only(doc)
    assert s.field is SuggestedField.ISSUE  # not a guessed expiry
    assert s.values == ("2023-08-15",)


def test_ambiguous_numeric_date_offers_multiple_readings():
    doc = Document(id="amb", name="Cert 21-08-23")
    s = _only(doc)
    assert s.field is SuggestedField.ISSUE
    assert len(s.values) >= 2  # the reader can pick which reading


def test_no_date_no_suggestion():
    assert suggest.for_document(Document(id="x", name="Passport")) == []


def test_live_drops_dismissed():
    doc = Document(id="misc", name="Some Doc 2023-08-15")
    s = _only(doc)
    state = SuggestionState()
    state.dismiss(s)
    assert suggest.live(doc, state) == []


def test_live_drops_already_satisfied():
    # Same name, but the issue date is already set → nothing to suggest.
    doc = Document(id="misc", name="Some Doc 2023-08-15", issue_date=date(2000, 1, 1))
    assert suggest.live(doc, SuggestionState()) == []


def test_live_span_satisfied_when_both_dates_in_notes():
    doc = Document(
        id="sst",
        name="Sea Service 2022-01-06 to 2022-03-27",
        notes="Period: 2022-01-06 to 2022-03-27",
    )
    assert suggest.live(doc, SuggestionState()) == []


def test_dismissal_key_reopens_on_changed_values():
    a = Document(id="misc", name="Some Doc 2023-08-15")
    key_a = _only(a).key
    b = Document(id="misc", name="Some Doc 2024-08-15")  # different parsed value
    assert _only(b).key != key_a  # a new value is a new decision


def _folder_doc(doc_id: str, path: str) -> Document:
    from dossier.model import Rendition

    return Document(id=doc_id, name=doc_id, files=[Rendition("d", path, primary=True)])


def test_bundles_from_folders_only_hint_folders_with_enough_docs():
    docs = [
        _folder_doc("a", "Travel Documents/India 2024/passport.pdf"),
        _folder_doc("b", "Travel Documents/India 2024/visa.pdf"),
        _folder_doc("c", "Certificates/coc.pdf"),  # not a hint folder
        _folder_doc("d", "Travel Documents/Bali 2025/lonely.pdf"),  # only 1 doc
    ]
    out = suggest.bundles_from_folders(docs)
    assert len(out) == 1
    s = out[0]
    assert s.slug == "travel/india-2024"
    assert s.title == "India 2024"
    assert s.doc_ids == ("a", "b")


def test_live_bundles_drops_existing_and_dismissed():
    docs = [
        _folder_doc("a", "Travel Documents/India 2024/p.pdf"),
        _folder_doc("b", "Travel Documents/India 2024/v.pdf"),
    ]
    sug = suggest.bundles_from_folders(docs)[0]
    existing = {sug.slug: Bundle(slug=sug.slug, title="India")}
    assert (
        suggest.live_bundles(docs, existing, SuggestionState()) == []
    )  # already a bundle
    # dismissed by key → not suggested
    state = SuggestionState()
    state.dismiss_key(sug.key)
    assert suggest.live_bundles(docs, {}, state) == []
    # otherwise it is live
    assert suggest.live_bundles(docs, {}, SuggestionState()) == [sug]


def test_suggestions_sidecar_round_trip(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    assert store.load_suggestions() == SuggestionState()  # absent → empty
    state = SuggestionState(dismissed={"b:issue_date:name:x", "a:expiry_date:name:y"})
    store.save_suggestions(state)
    assert store.load_suggestions().dismissed == state.dismissed
    first = config.suggestions_path.read_bytes()
    store.save_suggestions(store.load_suggestions())
    assert config.suggestions_path.read_bytes() == first  # sorted, deterministic


# -- scan-reading suggestions (Phase 7 #3) -----------------------------------


def test_scan_validity_window_suggests_issue_and_expiry():
    doc = Document(id="coc", name="CoC card")  # no dates on the doc yet
    reading = _reading(
        issue_date_text="14 Oct 2021",
        expiry_date_text="28 Sep 2026",
        is_validity_period=True,
    )
    live = suggest.live(doc, SuggestionState(), reading)
    fields = {s.field for s in live}
    assert {SuggestedField.ISSUE, SuggestedField.EXPIRY} <= fields
    expiry = next(s for s in live if s.field is SuggestedField.EXPIRY)
    assert expiry.values == ("2026-09-28",) and expiry.source == "scan"


def test_scan_non_window_two_dates_becomes_a_notes_period():
    doc = Document(id="ss", name="Sea Service Testimonial")
    reading = _reading(  # a service period the VLM did NOT flag as a validity window
        issue_date_text="04-May 2025",
        expiry_date_text="13-Mar-2025",
        is_validity_period=False,
    )
    live = suggest.live(doc, SuggestionState(), reading)
    assert [s.field for s in live] == [SuggestedField.NOTES]
    assert live[0].values == ("2025-03-13", "2025-05-04")  # sorted low..high
    assert live[0].source == "scan"


def test_scan_expiry_dropped_when_the_doc_already_has_one():
    doc = Document(id="m", name="Medical", expiry_date=date(2026, 7, 10))
    reading = _reading(
        issue_date_text="11 July 2024",
        expiry_date_text="10 July 2026",
        is_validity_period=True,
    )
    live = suggest.live(doc, SuggestionState(), reading)
    assert all(s.field is not SuggestedField.EXPIRY for s in live)  # satisfied
    assert any(s.field is SuggestedField.ISSUE for s in live)


def test_name_and_scan_agreeing_dedup_to_one_row():
    doc = Document(id="v", name="US Visa 12-Jan-2026")  # name → issue 2026-01-12
    reading = _reading(issue_date_text="12-Jan-2026", is_validity_period=False)
    issues = [
        s
        for s in suggest.live(doc, SuggestionState(), reading)
        if s.field is SuggestedField.ISSUE
    ]
    assert len(issues) == 1  # name and scan agree → shown once


def test_scan_numeric_date_reads_day_first_with_verbatim_rationale():
    doc = Document(id="c", name="Cert")
    reading = _reading(issue_date_text="06/09/2024", is_validity_period=False)
    issue = next(
        s
        for s in suggest.live(doc, SuggestionState(), reading)
        if s.field is SuggestedField.ISSUE
    )
    assert issue.values == ("2024-09-06",)  # UK day-first: 6 Sep, not 9 Jun
    assert "06/09/2024" in issue.rationale  # verbatim source shown, for the user
