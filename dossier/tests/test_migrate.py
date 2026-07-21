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

"""Tests for the Notion migration engine."""

from datetime import date
from pathlib import Path

from dossier import migrate
from dossier.config import Config
from dossier.store import Store


def test_slugify_and_reserved_names():
    assert (
        migrate.slugify("Certificate of Competency (CoC) Card")
        == "certificate-of-competency-coc-card"
    )
    assert migrate.slugify("   ") == "document"
    assert migrate.slugify("AUX") == "aux-doc"


def test_decode_slot():
    assert migrate.decode_slot(1.3) == (1, 3)
    assert migrate.decode_slot(2) == (2, None)
    assert migrate.decode_slot(0) == (0, None)
    assert migrate.decode_slot(1.1) == (1, 1)
    assert migrate.decode_slot(None) == (None, None)


def test_parse_dates_range_keywords_and_flags():
    rng = migrate.parse_dates("CoC Card 10-02-25 to 28-09-26")
    assert rng.issue == date(2025, 2, 10)
    assert rng.expiry == date(2026, 9, 28)
    assert rng.note  # two-digit numeric -> flagged for review

    exp = migrate.parse_dates("ENG-1 Med Cert Expires 10-07-26")
    assert exp.expiry == date(2026, 7, 10)
    assert exp.issue is None

    iss = migrate.parse_dates("LGTF Basic Gas Issued 24-06-2024")
    assert iss.issue == date(2024, 6, 24)
    assert iss.note == ""  # 4-digit year + keyword -> confident

    named = migrate.parse_dates("Motorcycle CBT expires 07-Jan-2026")
    assert named.expiry == date(2026, 1, 7)
    assert named.note == ""

    # ISO (YYYY-MM-DD) dates must NOT be read dayfirst.
    iso = migrate.parse_dates("03 - 2022-01-06 to 2022-03-27 Sea Service Testimonial")
    assert iso.issue == date(2022, 1, 6)
    assert iso.expiry == date(2022, 3, 27)
    assert iso.note == ""  # 4-digit year -> confident

    assert migrate.parse_dates("Home office BRP Letter") == migrate.DateParse()


def test_derive_flags():
    physical = migrate.derive_flags("Cert File #2048", None)
    assert physical.has_physical and physical.has_digital
    assert physical.location == "Cert File #2048"

    softcopy = migrate.derive_flags("Softcopy Only", None)
    assert not softcopy.has_physical and softcopy.has_digital
    assert softcopy.location is None

    destroyed = migrate.derive_flags("Destroyed", None)
    assert not destroyed.has_physical and destroyed.location is None

    no_soft = migrate.derive_flags("Blue Pouch", "No soft copy made")
    assert no_soft.has_physical and not no_soft.has_digital


def test_file_index_ranks_category_over_bundle():
    index = migrate.FileIndex(
        [
            "Official Documents/Marine/CoC Card.pdf",
            "Official Documents/Visas/US Visa Application/CoC Card.pdf",
        ]
    )
    result = index.match("CoC Card")
    assert result.status == "ambiguous"
    assert result.path == "Official Documents/Marine/CoC Card.pdf"

    assert index.match("nothing here").status == "no-match"


def _export() -> dict[str, object]:
    return {
        "locations": [
            {"name": "Cert File #2048"},
            {"name": "Softcopy Only"},  # state pseudo-location -> dropped
        ],
        "documents": [
            {
                "name": "ENG-1 Med Cert Expires 10-07-26",
                "permanent_storage": "Cert File #2048",
                "permanent_slot": 2.0,
                "carried_to_india": True,
            },
            {
                "name": "Old Scan",
                "permanent_storage": "Softcopy Only",
                "permanent_slot": None,
            },
        ],
    }


def test_build_plan():
    index = migrate.FileIndex(
        ["Official Documents/Marine/ENG-1 Med Cert Expires 10-07-26.pdf"]
    )
    plan = migrate.build_plan(_export(), index)

    assert set(plan.locations) == {"cert-file-2048"}  # state location dropped

    docs = {d.id: d for d in plan.documents}
    eng1 = docs["eng-1-med-cert-expires-10-07-26"]
    assert eng1.expiry_date == date(2026, 7, 10)
    assert eng1.perm_location == "cert-file-2048"
    assert (eng1.perm_slot, eng1.perm_subslot) == (2, None)
    assert eng1.has_physical and eng1.has_digital
    assert eng1.files and eng1.files[0].primary

    scan = docs["old-scan"]
    assert not scan.has_physical and scan.perm_location is None

    assert "carried-to-india" in plan.bundle_suggestions
    assert any(issue.kind == "no-file-match" for issue in plan.issues)


def test_apply_plan_writes_and_is_reentrant(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_history")
    store = Store(config)

    written = migrate.apply_plan(
        store, migrate.build_plan(_export(), migrate.FileIndex([]))
    )
    assert written == 2
    assert {d.id for d in store.load_all()} == {
        "eng-1-med-cert-expires-10-07-26",
        "old-scan",
    }
    assert "cert-file-2048" in store.load_locations()

    # A fresh migration run must not clobber already-written documents.
    again = migrate.apply_plan(
        store, migrate.build_plan(_export(), migrate.FileIndex([]))
    )
    assert again == 0
