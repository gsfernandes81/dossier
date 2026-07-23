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

"""Tests for `ds ask` — the retrieval-first answer engine (Tier 0, no model)."""

from datetime import date

from dossier import answers
from dossier.model import Document, Location
from dossier.scan import ScanReading

TODAY = date(2026, 7, 21)


def _reading(**fields: object) -> ScanReading:
    return ScanReading.from_payload(fields, model="m")


def _ans(question, docs, readings=None, locations=None) -> answers.Answer:
    return answers.answer(question, docs, readings or {}, locations or {}, today=TODAY)


# -- tokenizer / BM25 --------------------------------------------------------


def test_tokens_split_alphanumeric_and_drop_single_letters():
    assert answers._tokens("ENG-1 CoC #095") == ["eng", "1", "coc", "095"]
    # single non-digit letters carry no signal; lone digits are kept
    assert answers._tokens("a big X 7") == ["big", "7"]


def test_rank_selects_the_document_with_the_rare_term():
    docs = [
        Document(id="passport", name="British Passport"),
        Document(id="eng1", name="ENG-1 Medical Certificate"),
        Document(id="coc", name="Certificate of Competency"),
    ]
    corpus = answers.build_corpus(docs, {})
    ranked = answers.rank(corpus, answers._residue("my ENG-1"))
    assert ranked[0][0] == "eng1"


# -- intents -----------------------------------------------------------------


def test_expiry_intent_uses_the_authoritative_date():
    docs = [Document(id="eng1", name="ENG-1 Medical", expiry_date=date(2028, 5, 21))]
    ans = _ans("when does my ENG-1 expire", docs)
    assert ans.answered
    assert any("2028-05-21" in line for line in ans.lines)


def test_expiry_intent_falls_back_to_the_scan_text():
    docs = [Document(id="eng1", name="ENG-1 Medical")]  # no structured expiry_date
    readings = {"eng1": _reading(document_type="Med", expiry_date_text="21 May 2028")}
    ans = _ans("when does my ENG-1 expire", docs, readings)
    assert ans.answered
    assert any("21 May 2028" in line and "scan" in line for line in ans.lines)


def test_expiry_intent_reports_no_recorded_expiry():
    docs = [Document(id="pp", name="Passport")]
    ans = _ans("when does my passport expire", docs)
    assert ans.answered  # an honest "no recorded expiry" is still an answer
    assert any("no recorded expiry" in line for line in ans.lines)


def test_number_intent_returns_the_document_number():
    docs = [Document(id="eng1", name="ENG-1 Medical")]
    readings = {"eng1": _reading(document_type="Med", document_number="ENG10166083")}
    ans = _ans("what is my ENG-1 number", docs, readings)
    assert any("ENG10166083" in line for line in ans.lines)


def test_location_intent_resolves_title_and_slot():
    docs = [Document(id="pp", name="Passport", perm_location="cert-file", perm_slot=3)]
    locations = {"cert-file": Location(slug="cert-file", title="Cert File")}
    ans = _ans("where is my passport", docs, locations=locations)
    assert any("Cert File" in line and "slot 3" in line for line in ans.lines)


def test_unknown_question_falls_back_to_ranked_retrieval():
    docs = [
        Document(id="pp", name="British Passport"),
        Document(id="coc", name="Certificate of Competency"),
    ]
    ans = _ans("tell me about the passport", docs)
    assert ans.answered
    assert any("pp" in line for line in ans.lines)


def test_superseded_document_is_not_an_answer_target():
    docs = [
        Document(id="eng1-old", name="ENG-1 Medical", expiry_date=date(2023, 8, 21)),
        Document(
            id="eng1-new",
            name="ENG-1 Medical",
            expiry_date=date(2028, 5, 21),
            supersedes="eng1-old",
        ),
    ]
    ans = _ans("when does my ENG-1 expire", docs)
    assert any("2028-05-21" in line for line in ans.lines)  # the current one
    assert not any("eng1-old" in line for line in ans.lines)  # renewed away


def test_no_match_is_unanswered():
    docs = [Document(id="pp", name="Passport")]
    ans = _ans("airspeed velocity of an unladen swallow", docs)
    assert not ans.answered
