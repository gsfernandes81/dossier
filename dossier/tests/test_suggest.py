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

from dossier import suggest
from dossier.config import Config
from dossier.model import Document, SuggestedField, Suggestion, SuggestionState
from dossier.store import Store


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
