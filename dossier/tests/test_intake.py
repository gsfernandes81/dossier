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


# -- reading cache -----------------------------------------------------------


def test_build_proposal_reuses_a_cached_reading(tmp_path: Path):
    from dataclasses import replace

    from dossier.scan import file_fingerprint

    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/scan.pdf")
    fp = file_fingerprint(root / "Inbox" / "scan.pdf")
    hit = replace(_reading(document_type="Cached"), fingerprint=fp)
    cache = {"Inbox/scan.pdf": hit}

    def boom(_path: Path, _config: Config) -> ScanReading:
        raise AssertionError("the VLM must not run on a cache hit")

    p = intake.build_proposal(
        "Inbox/scan.pdf",
        store,
        config,
        docs=[],
        readings={},
        extract=boom,
        cache=cache,
    )
    assert p.doc.name == "Cached"  # reused the cached reading, no re-scan


def test_build_proposal_populates_the_cache_on_a_miss(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/scan.pdf")
    cache: dict[str, ScanReading] = {}
    intake.build_proposal(
        "Inbox/scan.pdf",
        store,
        config,
        docs=[],
        readings={},
        extract=_fixed(_reading(document_type="Passport")),
        cache=cache,
    )
    assert cache["Inbox/scan.pdf"].document_type == "Passport"
    assert cache["Inbox/scan.pdf"].fingerprint  # stamped for the next run's hit test


def test_intake_cache_round_trips_through_the_store(tmp_path: Path):
    store, _config, _root = _store(tmp_path)
    reading = ScanReading.from_payload(
        {"document_type": "X", "fingerprint": "9:9"}, model="m"
    )
    store.save_intake_cache({"a/b.pdf": reading})
    back = store.load_intake_cache()
    assert back["a/b.pdf"].document_type == "X"
    assert back["a/b.pdf"].fingerprint == "9:9"  # fingerprint survives the round-trip


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


# -- duplicate/subset detection ----------------------------------------------


def _hasher(pages: dict[str, list[int]]):
    """A stand-in for dedup_cache.cached_page_hashes returning fixed page hashes."""

    def run(_paths, _root):
        return dict(pages)

    return run


def _with_rendition(store: Store, doc_id: str, rel: str) -> None:
    store.save(
        Document(id=doc_id, name="Medical", files=[Rendition("scan", rel, True)])
    )


def test_build_proposal_detects_an_exact_duplicate(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _with_rendition(store, "med-old", "Marine/old.pdf")
    _drop(root, "Inbox/new.pdf")
    p = intake.build_proposal(
        "Inbox/new.pdf",
        store,
        config,
        docs=store.load_all(),
        readings={},
        extract=_fixed(_reading(document_type="Medical")),
        hasher=_hasher({"Inbox/new.pdf": [1, 2, 3], "Marine/old.pdf": [1, 2, 3]}),
    )
    assert p.duplicate is not None
    assert p.duplicate.doc_id == "med-old"
    assert p.duplicate.path == "Marine/old.pdf"
    assert p.duplicate.exact is True


def test_build_proposal_detects_a_subset(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _with_rendition(store, "med-old", "Marine/old.pdf")
    _drop(root, "Inbox/new.pdf")
    p = intake.build_proposal(
        "Inbox/new.pdf",
        store,
        config,
        docs=store.load_all(),
        readings={},
        extract=_fixed(_reading(document_type="Medical")),
        # the drop is a 2-page subset of the 4-page existing rendition
        hasher=_hasher({"Inbox/new.pdf": [1, 2], "Marine/old.pdf": [1, 2, 3, 4]}),
    )
    assert p.duplicate is not None and p.duplicate.exact is False


def test_build_proposal_no_duplicate_when_unrelated(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _with_rendition(store, "med-old", "Marine/old.pdf")
    _drop(root, "Inbox/new.pdf")
    p = intake.build_proposal(
        "Inbox/new.pdf",
        store,
        config,
        docs=store.load_all(),
        readings={},
        extract=_fixed(_reading(document_type="Medical")),
        # far apart (>6 bits) so the default fuzzy distance doesn't match them
        hasher=_hasher(
            {"Inbox/new.pdf": [1, 2], "Marine/old.pdf": [0xFFFFFFFFFFFF, 0x0F0F0F0F]}
        ),
    )
    assert p.duplicate is None


def test_build_proposal_skips_dedup_when_the_extra_is_absent(tmp_path: Path):
    from dossier import dedup_hash

    store, config, root = _store(tmp_path)
    _with_rendition(store, "med-old", "Marine/old.pdf")
    _drop(root, "Inbox/new.pdf")

    def boom(_paths, _root):
        raise dedup_hash.DedupError("no [dedup] extra")

    p = intake.build_proposal(
        "Inbox/new.pdf",
        store,
        config,
        docs=store.load_all(),
        readings={},
        extract=_fixed(_reading(document_type="Medical")),
        hasher=boom,
    )
    assert p.duplicate is None  # graceful skip, no crash


def test_build_proposal_skips_dedup_for_a_non_page_drop(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/notes.txt")
    called: list[int] = []

    def hasher(_paths, _root):
        called.append(1)
        return {}

    p = intake.build_proposal(
        "Inbox/notes.txt",
        store,
        config,
        docs=[],
        readings={},
        extract=_fixed(_reading(document_type="Notes")),
        hasher=hasher,
    )
    assert p.duplicate is None and called == []  # never hashed a non-page file


# -- apply_fold --------------------------------------------------------------


def _dup_proposal(store, config, root, *, exact: bool):
    """A proposal whose drop duplicates med-old's Marine/old.pdf rendition."""
    store.save(
        Document(
            id="med-old",
            name="Medical Certificate",
            files=[Rendition("scan", "Marine/old.pdf", True)],
        )
    )
    (root / "Marine").mkdir(parents=True, exist_ok=True)
    (root / "Marine" / "old.pdf").write_bytes(b"keep")
    _drop(root, "Inbox/new.pdf")
    keep_pages = [1, 2, 3] if exact else [1, 2, 3, 4]
    return intake.build_proposal(
        "Inbox/new.pdf",
        store,
        config,
        docs=store.load_all(),
        readings={},
        extract=_fixed(_reading(document_type="Medical Certificate")),
        hasher=_hasher({"Inbox/new.pdf": [1, 2, 3], "Marine/old.pdf": keep_pages}),
    )


def test_apply_fold_adds_a_rendition_without_a_new_record(tmp_path: Path):
    store, config, root = _store(tmp_path)
    p = _dup_proposal(store, config, root, exact=True)
    assert p.duplicate is not None and p.duplicate.exact

    doc, errors = intake.apply_fold(p, store, config)
    assert errors == []
    assert doc.id == "med-old"
    assert {d.id for d in store.load_all()} == {"med-old"}  # no new record minted

    target = store.load("med-old")
    fold = next(r for r in target.files if r.label == "duplicate")
    assert fold.primary is False
    assert fold.path == "Marine/medical-certificate--duplicate.pdf"  # beside the keep
    assert (root / fold.path).exists()
    assert not (root / "Inbox/new.pdf").exists()  # the inbox is emptied
    # recorded in the fold sidecar so a later dedup scan won't re-ask
    assert fold.path in store.load_reconcile().folded.get("Marine/old.pdf", set())
    assert intake.pending_files(store, config) == []  # no longer pending


def test_apply_fold_subset_uses_a_partial_label(tmp_path: Path):
    store, config, root = _store(tmp_path)
    p = _dup_proposal(store, config, root, exact=False)
    doc, errors = intake.apply_fold(p, store, config)
    assert errors == []
    labels = {r.label for r in store.load("med-old").files}
    assert "partial" in labels


def test_apply_fold_keeps_the_targets_existing_reading(tmp_path: Path):
    store, config, root = _store(tmp_path)
    p = _dup_proposal(store, config, root, exact=True)
    store.save_scans({"med-old": _reading(document_type="The Better Reading")})
    intake.apply_fold(p, store, config)
    # setdefault must not clobber the target's existing (better) reading.
    assert store.load_scans()["med-old"].document_type == "The Better Reading"


def test_apply_fold_raises_without_a_duplicate(tmp_path: Path):
    store, config, root = _store(tmp_path)
    _drop(root, "Inbox/plain.pdf")
    p = intake.build_proposal(
        "Inbox/plain.pdf",
        store,
        config,
        docs=[],
        readings={},
        extract=_fixed(_reading(document_type="Passport")),
    )
    assert p.duplicate is None
    with pytest.raises(IntakeError):
        intake.apply_fold(p, store, config)


def test_apply_fold_raises_when_source_vanished(tmp_path: Path):
    store, config, root = _store(tmp_path)
    p = _dup_proposal(store, config, root, exact=True)
    (root / "Inbox" / "new.pdf").unlink()
    with pytest.raises(IntakeError):
        intake.apply_fold(p, store, config)


def test_apply_fold_raises_when_target_no_longer_links_the_keep(tmp_path: Path):
    store, config, root = _store(tmp_path)
    p = _dup_proposal(store, config, root, exact=True)
    # The keep rendition is unlinked between proposal and apply.
    target = store.load("med-old")
    target.files = []
    store.save(target)
    with pytest.raises(IntakeError):
        intake.apply_fold(p, store, config)
