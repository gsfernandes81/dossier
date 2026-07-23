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

"""Tests for the intake proposal engine (`ds intake`)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from dossier import intake
from dossier.config import Config
from dossier.errors import IntakeError
from dossier.model import Document, Rendition, SuggestedField
from dossier.scan import ScanReading
from dossier.store import Store


def _store(tmp_path: Path) -> tuple[Store, Config, Path]:
    root = tmp_path / "root"
    root.mkdir()
    config = Config(syncthing_root=root, history_dir=tmp_path / "_hist")
    config.intake_inbox = "Inbox"
    store = Store(config)
    store.ensure_layout()
    return store, config, root


def _drop(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")


def _reading(**fields: object) -> ScanReading:
    return ScanReading.from_payload({"confidence": 0.9, **fields}, model="fake")


def _fixed(reading: ScanReading):
    return lambda _path, _config: reading


# -- build_proposal: derivations ---------------------------------------------


def test_proposal_names_and_dates_from_the_reading(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/scan.pdf")
    reading = _reading(
        document_type="Passport",
        is_validity_period=True,
        issue_date_text="03 Sep 2021",
        expiry_date_text="02 Sep 2031",
    )
    p = intake.build_proposal(
        "Inbox/scan.pdf", store, config, docs=[], readings={}, extract=_fixed(reading)
    )
    assert p.doc.name == "Passport"
    assert p.doc.id == "passport"
    assert p.doc.issue_date == date(2021, 9, 3)  # unambiguous → applied
    assert p.doc.expiry_date == date(2031, 9, 2)
    # untagged → fallback folder; canonical stem date-prefixed (name has no date).
    assert p.dst_rel == "Filed/2021-09-03-passport.pdf"
    assert "fallback-folder" in p.notes


def test_proposal_name_falls_back_to_the_filename(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/my_passport.pdf")
    p = intake.build_proposal(
        "Inbox/my_passport.pdf",
        store,
        config,
        docs=[],
        readings={},
        extract=_fixed(_reading(document_type="")),
    )
    assert p.doc.name == "my passport"


def test_proposal_tags_from_the_keyword_map_longest_wins(tmp_path: Path):
    store, config, root = _store(tmp_path)
    config.intake_tags = {"cert": "misc", "competency": "marine/coc"}
    _drop(root, "Inbox/coc.pdf")
    p = intake.build_proposal(
        "Inbox/coc.pdf",
        store,
        config,
        docs=[],
        readings={},
        extract=_fixed(_reading(document_type="Certificate of Competency")),
    )
    assert p.doc.tags == ["marine/coc"]  # both matched; longer keyword won


def test_ambiguous_issue_date_becomes_an_open_question(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/scan.pdf")
    # A 2-digit-year numeric date is genuinely DD/MM ambiguous (a 4-digit one is
    # resolved day-first and applied); intake leaves the ambiguous one unset.
    reading = _reading(
        document_type="Medical", is_validity_period=True, issue_date_text="06/09/24"
    )
    p = intake.build_proposal(
        "Inbox/scan.pdf", store, config, docs=[], readings={}, extract=_fixed(reading)
    )
    assert p.doc.issue_date is None  # left for the pane to disambiguate
    assert len(p.open_questions) == 1
    q = p.open_questions[0]
    assert q.field is SuggestedField.ISSUE
    assert len(q.values) >= 2  # multiple readings offered — the pane picks


def test_period_reading_becomes_a_notes_span(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/service.pdf")
    reading = _reading(
        document_type="Sea Service",
        is_validity_period=False,
        issue_date_text="01 Jan 2020",
        expiry_date_text="01 Jun 2020",
    )
    p = intake.build_proposal(
        "Inbox/service.pdf",
        store,
        config,
        docs=[],
        readings={},
        extract=_fixed(reading),
    )
    assert (
        p.doc.issue_date is None and p.doc.expiry_date is None
    )  # not a validity window
    assert "Period: 2020-01-01 to 2020-06-01" in p.doc.notes


def test_in_place_keeps_the_source_directory(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Marine/raw.pdf")
    p = intake.build_proposal(
        "Marine/raw.pdf",
        store,
        config,
        docs=[],
        readings={},
        extract=_fixed(_reading(document_type="CoC")),
        in_place=True,
    )
    assert p.dst_rel == "Marine/coc.pdf"  # renamed where it sits, no fallback folder


def test_proposal_finds_a_succession_link(tmp_path: Path):
    store, config, root = _store(tmp_path)
    # An existing scanned medical the new one renews.
    old = Document(
        id="med-old", name="Medical", files=[Rendition("d", "Marine/old.pdf", True)]
    )
    store.save(old)
    old_reading = _reading(
        document_type="Medical Certificate",
        issuer="MCA",
        holder_name="John Smith",
        issue_date_text="01 Jan 2020",
    )
    store.save_scans({"med-old": old_reading})
    _drop(root, "Inbox/new.pdf")
    new_reading = _reading(
        document_type="Medical Certificate",
        issuer="MCA",
        holder_name="John Smith",
        issue_date_text="01 Jan 2023",
    )
    p = intake.build_proposal(
        "Inbox/new.pdf",
        store,
        config,
        docs=store.load_all(),
        readings=store.load_scans(),
        extract=_fixed(new_reading),
    )
    assert p.succession is not None
    assert p.succession.older == "med-old"
    assert p.doc.supersedes == "med-old"


def test_with_name_reslugs_the_id_and_destination(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/scan.pdf")
    p = intake.build_proposal(
        "Inbox/scan.pdf",
        store,
        config,
        docs=[],
        readings={},
        extract=_fixed(_reading(document_type="Passport")),
    )
    renamed = intake.with_name(p, "UK Passport", store, config)
    assert renamed.doc.name == "UK Passport"
    assert renamed.doc.id == "uk-passport"
    assert renamed.dst_rel == "Filed/uk-passport.pdf"
    assert p.doc.name == "Passport"  # original proposal untouched (deep-copied)


# -- pending_files -----------------------------------------------------------


def test_pending_files_scopes_to_inbox_minus_linked_and_dismissed(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/a.pdf")
    _drop(root, "Inbox/b.pdf")
    _drop(root, "Inbox/rejected.pdf")
    _drop(root, "Other/c.pdf")  # outside the inbox
    store.save(Document(id="x", name="X", files=[Rendition("d", "Inbox/b.pdf", True)]))
    state = store.load_reconcile()
    state.dismissed.add("Inbox/rejected.pdf")
    store.save_reconcile(state)

    assert intake.pending_files(store, config) == ["Inbox/a.pdf"]


def test_pending_files_from_root_scans_the_whole_tree(tmp_path: Path):
    store, config, root = _store(tmp_path)
    config.intake_inbox = None  # no inbox; `--from .` (the root) drives it
    _drop(root, "Marine/a.pdf")
    _drop(root, "Identity/b.pdf")
    store.save(Document(id="x", name="X", files=[Rendition("d", "Marine/a.pdf", True)]))
    assert intake.pending_files(store, config, from_dir=".") == ["Identity/b.pdf"]


def test_pending_files_empty_when_no_inbox_configured(tmp_path: Path):
    store, config, root = _store(tmp_path)
    config.intake_inbox = None
    _drop(root, "Inbox/a.pdf")
    assert intake.pending_files(store, config) == []


# -- apply_proposal ----------------------------------------------------------


def test_apply_files_the_record_reading_and_moves_the_file(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/scan.pdf")
    reading = _reading(
        document_type="Passport",
        is_validity_period=True,
        issue_date_text="03 Sep 2021",
        expiry_date_text="02 Sep 2031",
    )
    p = intake.build_proposal(
        "Inbox/scan.pdf", store, config, docs=[], readings={}, extract=_fixed(reading)
    )
    doc, errors = intake.apply_proposal(p, store, config)
    assert errors == []

    saved = store.load(doc.id)
    assert saved.name == "Passport"
    assert saved.files[0].path == p.dst_rel  # rendition points at the moved file
    assert (root / p.dst_rel).exists()
    assert not (root / "Inbox/scan.pdf").exists()  # gone from the inbox
    scans = store.load_scans()
    assert doc.id in scans and scans[doc.id].fingerprint  # reading persisted + stamped


def test_apply_twice_raises_because_the_source_is_gone(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/scan.pdf")
    p = intake.build_proposal(
        "Inbox/scan.pdf",
        store,
        config,
        docs=[],
        readings={},
        extract=_fixed(_reading(document_type="Passport")),
    )
    intake.apply_proposal(p, store, config)
    with pytest.raises(IntakeError):
        intake.apply_proposal(p, store, config)
