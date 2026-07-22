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

"""Tests for succession inference over scan readings."""

from dossier import scan, succession
from dossier.model import Document


def _reading(
    doc_type: str,
    holder: str,
    issue: str,
    *,
    issuer: str | None = None,
    number: str | None = None,
) -> scan.ScanReading:
    return scan.ScanReading.from_payload(
        {
            "document_type": doc_type,
            "holder_name": holder,
            "issuer": issuer,
            "document_number": number,
            "issue_date_text": issue,
            "confidence": 0.9,
        },
        model="m",
    )


def test_different_issuer_blocks_a_same_type_merge():
    # Two "Certificate of Competency"-typed docs from different authorities are NOT
    # the same credential (a real-store trap: an EOOW/OTCTO reads as "CoC" too).
    docs = [Document(id="mca"), Document(id="asha")]
    readings = {
        "mca": _reading(
            "Certificate of Competency",
            "X Y",
            "01/01/2020",
            issuer="Maritime & Coastguard Agency",
        ),
        "asha": _reading(
            "Certificate of Competency",
            "X Y",
            "01/01/2024",
            issuer="Asha International Institute",
        ),
    }
    assert succession.propose(docs, readings) == []


def test_renewals_with_new_numbers_still_cluster():
    # Medicals get a fresh number each renewal — a *differing* number must not veto
    # a match that type + issuer + holder otherwise support.
    docs = [Document(id="m1"), Document(id="m2")]
    readings = {
        "m1": _reading(
            "Seafarer Medical Certificate",
            "X Y",
            "20/08/2021",
            issuer="MCA",
            number="ENG10063801",
        ),
        "m2": _reading(
            "Seafarer Medical Certificate",
            "X Y",
            "11/07/2024",
            issuer="MCA",
            number="ENG10166083",
        ),
    }
    assert [(s.newer, s.older) for s in succession.propose(docs, readings)] == [
        ("m2", "m1")
    ]


def test_shared_number_matches_across_phrasing():
    docs = [Document(id="a"), Document(id="b")]
    readings = {
        "a": _reading(
            "Certificate of Competency", "X Y", "14/10/2021", number="CoC0095036"
        ),
        "b": _reading("CoC Card", "Y X", "29/05/2026", number="CoC 009 5036"),
    }
    proposals = succession.propose(docs, readings)
    assert [(s.newer, s.older) for s in proposals] == [("b", "a")]
    assert "same no." in proposals[0].rationale


def test_links_a_renewal_chain_despite_phrasing_and_name_order():
    docs = [Document(id=x) for x in ("a", "b", "c", "visa")]
    readings = {
        # Same certificate, three reissues — noisy type + reordered/partial names.
        "a": _reading(
            "Certificate of Competency", "Fernandes Gavin Shawn", "06/09/2020"
        ),
        "b": _reading(
            "Certificate of Competency (CoC) Card", "Gavin Fernandes", "06/09/2024"
        ),
        "c": _reading("Certificate of Competency", "Fernandes Gavin", "06/09/2026"),
        "visa": _reading("US Visa", "Gavin Fernandes", "01/01/2023"),
    }
    links = {(s.newer, s.older) for s in succession.propose(docs, readings)}
    assert links == {("b", "a"), ("c", "b")}  # a chain; the visa is a different type


def test_skips_pairs_already_linked():
    docs = [Document(id="old"), Document(id="new", supersedes="old")]
    readings = {
        "old": _reading("Passport", "X Y", "01/01/2015"),
        "new": _reading("Passport", "X Y", "01/01/2025"),
    }
    assert succession.propose(docs, readings) == []


def test_needs_two_documents_of_a_similar_type():
    docs = [Document(id="a"), Document(id="b")]
    readings = {
        "a": _reading("Passport", "X Y", "01/01/2015"),
        "b": _reading("Driving Licence", "X Y", "01/01/2025"),
    }
    assert succession.propose(docs, readings) == []


def test_dayfirst_parsing_orders_uk_dates():
    # 06/09/2024 (6 Sep) is newer than 12/03/2024 (12 Mar) under day-first parsing.
    docs = [Document(id="a"), Document(id="b")]
    readings = {
        "a": _reading("Seaman Book", "X Y", "12/03/2024"),
        "b": _reading("Seaman Book", "X Y", "06/09/2024"),
    }
    proposals = succession.propose(docs, readings)
    assert [(s.newer, s.older) for s in proposals] == [("b", "a")]
