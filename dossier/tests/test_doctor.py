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

"""Tests for the doctor diagnostics."""

from datetime import date
from pathlib import Path

import pytest

from dossier import doctor
from dossier.config import Config
from dossier.model import Document, Location, Rendition
from dossier.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_history")
    st = Store(config)
    st.ensure_layout()
    return st


def _kinds(report: doctor.Report) -> dict[str, int]:
    return {check: len(items) for check, items in report.by_check().items()}


def test_clean_store_has_no_findings(store: Store):
    store.save_locations({"file": Location(slug="file", title="File")})
    store.save(Document(id="ok", name="Passport", perm_location="file"))
    assert doctor.run(store, store.config).findings == []


def test_location_ref_and_missing_file(store: Store):
    store.save(
        Document(
            id="d",
            name="Doc",
            perm_location="ghost",  # not in locations.toml
            files=[Rendition(label="x", path="nope.pdf", primary=True)],
        )
    )
    kinds = _kinds(doctor.run(store, store.config))
    assert kinds.get("location-ref") == 1
    assert kinds.get("missing-file") == 1


def test_round_trip_flags_hand_edit(store: Store):
    store.save(Document(id="h", name="Hand"))
    path = store.document_path("h")
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert _kinds(doctor.run(store, store.config)).get("round-trip") == 1


def test_ambiguous_dates(store: Store):
    # single ambiguous 2-digit-year date -> flagged
    store.save(
        Document(
            id="single",
            name="ENG-1 Med Cert Expires 10-07-26",
            expiry_date=date(2026, 7, 10),
        )
    )
    # a range where issue < expiry -> order self-consistent, not flagged
    store.save(
        Document(
            id="range",
            name="CoC 10-02-25 to 28-09-26",
            issue_date=date(2025, 2, 10),
            expiry_date=date(2026, 9, 28),
        )
    )
    # a 4-digit-year date -> unambiguous, not flagged
    store.save(
        Document(id="iso", name="Issued 24-06-2024", issue_date=date(2024, 6, 24))
    )
    # day > 12 fixes day/month order, but DD-MM-YY (2023-08-21) vs YY-MM-DD
    # (2021-08-23) is still ambiguous -> flagged
    store.save(
        Document(id="yearpos", name="Cert 21-08-23", expiry_date=date(2023, 8, 21))
    )

    flagged = {
        f.subject
        for f in doctor.run(store, store.config).by_check().get("ambiguous-date", [])
    }
    assert flagged == {"single", "yearpos"}


def test_candidate_readings():
    tokens = doctor.candidate_readings("Cert 21-08-23")
    assert len(tokens) == 1
    token, readings = tokens[0]
    assert token == "21-08-23"
    assert date(2023, 8, 21) in readings  # DD-MM-YY
    assert date(2021, 8, 23) in readings  # YY-MM-DD


def test_supersession_integrity(store: Store):
    store.save(Document(id="v1", name="Old"))
    store.save(Document(id="v2", name="New", supersedes="v1"))  # clean chain
    store.save(Document(id="dangling", name="D", supersedes="ghost"))
    store.save(Document(id="selfie", name="S", supersedes="selfie"))
    store.save(Document(id="a", name="A", supersedes="b"))  # a <-> b cycle
    store.save(Document(id="b", name="B", supersedes="a"))

    findings = doctor.run(store, store.config).by_check().get("supersession", [])
    by_subject = {f.subject: f.detail for f in findings}
    assert "not a known document" in by_subject["dangling"]
    assert "supersedes itself" in by_subject["selfie"]
    cycle = {f.subject for f in findings if "cycle" in f.detail}
    assert cycle and cycle <= {"a", "b"}  # reported once, on one member
    assert "v1" not in by_subject and "v2" not in by_subject  # clean chain is silent
    assert len(findings) == 3  # dangling + self + one cycle


def test_date_order_violation(store: Store):
    store.save(
        Document(
            id="bad",
            name="Weird cert",
            issue_date=date(2020, 1, 1),
            expiry_date=date(2019, 1, 1),
        )
    )
    assert _kinds(doctor.run(store, store.config)).get("date-order") == 1
