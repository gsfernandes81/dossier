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

from dossier import doctor, syncthing
from dossier.config import Config
from dossier.model import Document, Location, ReconcileState, Rendition
from dossier.store import Store
from dossier.syncthing import FolderStatus, SyncState, SyncStatus


@pytest.fixture
def store(tmp_path: Path) -> Store:
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_history")
    st = Store(config)
    st.ensure_layout()
    return st


@pytest.fixture(autouse=True)
def _isolate_syncthing(monkeypatch: pytest.MonkeyPatch):
    """Keep ``doctor.run`` offline by default — no test may touch a real Syncthing.

    The syncthing-specific tests below re-patch ``query_status`` to the status they
    need (a later ``setattr`` wins over this one).
    """
    monkeypatch.setattr(
        syncthing,
        "query_status",
        lambda *a, **k: SyncStatus(state=SyncState.UNCONFIGURED),
    )
    monkeypatch.setattr(syncthing, "probe_health", lambda *a, **k: False)


def _kinds(report: doctor.Report) -> dict[str, int]:
    return {check: len(items) for check, items in report.by_check().items()}


def test_clean_store_has_no_findings(store: Store):
    store.save_locations({"file": Location(slug="file", title="File")})
    store.save(Document(id="ok", name="Passport", perm_location="file"))
    # skip the syncthing group — this asserts the *store* is clean, not the sync setup
    clean = doctor.run(store, store.config, skip=frozenset({"syncthing"}))
    assert clean.findings == []


def test_run_reuses_a_passed_docs_snapshot(
    store: Store, monkeypatch: pytest.MonkeyPatch
):
    # With docs= given, run() must not re-read the store — the Review screen shares
    # one snapshot across tabs to avoid N reloads on a slow synced filesystem.
    store.save(Document(id="amb", name="Cert 21-08-23"))  # an ambiguous-date finding
    docs = store.load_all()
    real = Store.load_all
    calls = 0

    def counting(self: Store) -> list[Document]:
        nonlocal calls
        calls += 1
        return real(self)

    monkeypatch.setattr(Store, "load_all", counting)
    report = doctor.run(store, store.config, docs=docs)
    assert calls == 0  # no reload when the snapshot is supplied
    assert _kinds(report) == _kinds(doctor.run(store, store.config))  # same findings


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


def test_round_trip_uses_source_hash_without_rereading(store: Store):
    # The check compares against Document.source_hash, so it needs no file read: a
    # freshly-saved doc reports clean even with its file deleted out from under it.
    doc = store.save(Document(id="h", name="Hand"))  # source_hash set on save
    store.document_path("h").unlink()  # file gone — a re-read would OSError/crash
    report = doctor.run(store, store.config, docs=[doc])
    assert _kinds(report).get("round-trip") is None  # clean, no re-read


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


def _conflict_beside(store: Store, doc_id: str, theirs: Document) -> None:
    live = store.document_path(doc_id)
    live.with_name(f"{doc_id}.sync-conflict-20260101-120000-AAAAAAA.md").write_bytes(
        store.serialize(theirs).encode()
    )


def test_conflict_finding_names_the_contested_fields(store: Store):
    store.save(Document(id="eng-1", name="Passport"))
    _conflict_beside(store, "eng-1", Document(id="eng-1", name="Renewed"))
    findings = doctor.run(store, store.config).by_check().get("sync-conflict", [])
    assert len(findings) == 1
    assert "contested field(s): name" in findings[0].detail


def test_conflict_finding_flags_a_clean_auto_merge(store: Store):
    store.save(Document(id="eng-1", name="Passport", tags=["gov"]))
    _conflict_beside(
        store, "eng-1", Document(id="eng-1", name="Passport", tags=["gov", "travel"])
    )
    findings = doctor.run(store, store.config).by_check().get("sync-conflict", [])
    assert "auto-merges cleanly" in findings[0].detail


# -- Syncthing checks (Phase 15) ---------------------------------------------
# `_check_syncthing` imports the module lazily and calls `syncthing.query_status`,
# so patching that attribute (and `probe_health`) drives every branch without a
# network. `store.config.syncthing_root` is a real tmp dir from the fixture.


def _folder(
    root: Path, *, versioning: str = "staggered", paused: bool = False, shared: int = 1
) -> FolderStatus:
    return FolderStatus(
        id="docs",
        label="Docs",
        path=str(root),
        paused=paused,
        versioning=versioning,
        shared_with=shared,
    )


def _sync_findings(report: doctor.Report) -> dict[str, doctor.Finding]:
    return {f.check: f for f in report.findings if f.check.startswith("syncthing")}


def test_syncthing_all_good_is_silent(store: Store, monkeypatch: pytest.MonkeyPatch):
    status = SyncStatus(
        state=SyncState.IDLE,
        store_folder=_folder(store.config.syncthing_root),
        connected_devices=1,
        total_devices=1,
    )
    monkeypatch.setattr(syncthing, "query_status", lambda *a, **k: status)
    assert _sync_findings(doctor.run(store, store.config)) == {}


def test_syncthing_versioning_off_is_the_headline_warn(
    store: Store, monkeypatch: pytest.MonkeyPatch
):
    status = SyncStatus(
        state=SyncState.IDLE,
        store_folder=_folder(store.config.syncthing_root, versioning=""),
        connected_devices=1,
        total_devices=1,
    )
    monkeypatch.setattr(syncthing, "query_status", lambda *a, **k: status)
    found = _sync_findings(doctor.run(store, store.config))
    assert "syncthing-versioning" in found
    assert found["syncthing-versioning"].severity == "warn"


def test_syncthing_paused_and_unshared_warn(
    store: Store, monkeypatch: pytest.MonkeyPatch
):
    folder = _folder(store.config.syncthing_root, paused=True, shared=0)
    status = SyncStatus(state=SyncState.IDLE, store_folder=folder)
    monkeypatch.setattr(syncthing, "query_status", lambda *a, **k: status)
    found = _sync_findings(doctor.run(store, store.config))
    assert found["syncthing-paused"].severity == "warn"
    assert found["syncthing-unshared"].severity == "warn"


def test_syncthing_folder_not_found_warn(store: Store, monkeypatch: pytest.MonkeyPatch):
    status = SyncStatus(state=SyncState.IDLE, store_folder=None)
    monkeypatch.setattr(syncthing, "query_status", lambda *a, **k: status)
    found = _sync_findings(doctor.run(store, store.config))
    assert "syncthing-folder" in found and found["syncthing-folder"].severity == "warn"


def test_syncthing_unconfigured_is_info(store: Store, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        syncthing,
        "query_status",
        lambda *a, **k: SyncStatus(state=SyncState.UNCONFIGURED),
    )
    monkeypatch.setattr(syncthing, "probe_health", lambda *a, **k: False)
    found = _sync_findings(doctor.run(store, store.config))
    assert list(found) == ["syncthing-unconfigured"]
    assert found["syncthing-unconfigured"].severity == "info"


def test_syncthing_unconfigured_notes_running_daemon(
    store: Store, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        syncthing,
        "query_status",
        lambda *a, **k: SyncStatus(state=SyncState.UNCONFIGURED),
    )
    monkeypatch.setattr(syncthing, "probe_health", lambda *a, **k: True)  # it's running
    found = _sync_findings(doctor.run(store, store.config))
    assert "no API key" in found["syncthing-unconfigured"].detail


def test_syncthing_unreachable_is_info(store: Store, monkeypatch: pytest.MonkeyPatch):
    status = SyncStatus(state=SyncState.UNREACHABLE, error="connection refused")
    monkeypatch.setattr(syncthing, "query_status", lambda *a, **k: status)
    found = _sync_findings(doctor.run(store, store.config))
    assert found["syncthing-unreachable"].severity == "info"


def test_syncthing_auth_is_warn(store: Store, monkeypatch: pytest.MonkeyPatch):
    status = SyncStatus(state=SyncState.UNAUTHORIZED, error="403")
    monkeypatch.setattr(syncthing, "query_status", lambda *a, **k: status)
    found = _sync_findings(doctor.run(store, store.config))
    assert found["syncthing-auth"].severity == "warn"


def test_syncthing_disconnected_is_info(store: Store, monkeypatch: pytest.MonkeyPatch):
    status = SyncStatus(
        state=SyncState.IDLE,
        store_folder=_folder(store.config.syncthing_root),
        connected_devices=0,
        total_devices=2,
    )
    monkeypatch.setattr(syncthing, "query_status", lambda *a, **k: status)
    found = _sync_findings(doctor.run(store, store.config))
    assert found["syncthing-connectivity"].severity == "info"


def test_syncthing_skip_short_circuits_the_network(
    store: Store, monkeypatch: pytest.MonkeyPatch
):
    def boom(*a, **k):
        raise AssertionError("query_status must not run when syncthing is skipped")

    monkeypatch.setattr(syncthing, "query_status", boom)
    report = doctor.run(store, store.config, skip=frozenset({"syncthing"}))
    assert not any(f.check.startswith("syncthing") for f in report.findings)


# -- reconcile-sidecar consistency -------------------------------------------
def _recon(report: doctor.Report) -> list[doctor.Finding]:
    return [f for f in report.findings if f.check.startswith("reconcile")]


def test_reconcile_folded_link_is_a_warn(store: Store):
    root = store.config.syncthing_root
    (root / "keep.pdf").write_bytes(b"x")
    (root / "dup.pdf").write_bytes(b"x")  # folding never deletes — the copy remains
    store.save(Document(id="d", name="Doc", files=[Rendition("dup", "dup.pdf", True)]))
    store.save_reconcile(ReconcileState(folded={"keep.pdf": {"dup.pdf"}}))

    folded = [
        f for f in _recon(doctor.run(store, store.config)) if f.check.endswith("link")
    ]
    assert len(folded) == 1
    assert folded[0].severity == "warn"
    assert folded[0].subject == "d" and "keep.pdf" in folded[0].detail


def test_reconcile_stale_entries_are_info(store: Store):
    # a dismissed orphan and a folded keep whose files are gone from disk
    store.save_reconcile(
        ReconcileState(dismissed={"gone.pdf"}, folded={"missing-keep.pdf": {"sub.pdf"}})
    )
    stale = [
        f for f in _recon(doctor.run(store, store.config)) if f.check.endswith("stale")
    ]
    assert {f.subject for f in stale} == {"gone.pdf", "missing-keep.pdf"}
    assert all(f.severity == "info" for f in stale)


def test_reconcile_clean_when_doc_links_the_kept_file(store: Store):
    root = store.config.syncthing_root
    (root / "keep.pdf").write_bytes(b"x")
    (root / "dup.pdf").write_bytes(b"x")
    store.save(Document(id="d", name="Doc", files=[Rendition("k", "keep.pdf", True)]))
    store.save_reconcile(ReconcileState(folded={"keep.pdf": {"dup.pdf"}}))
    # links the KEEP, both files present → nothing for either reconcile check
    assert _recon(doctor.run(store, store.config)) == []
