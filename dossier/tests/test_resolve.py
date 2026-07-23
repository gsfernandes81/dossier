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

"""Tests for conflict discovery and crash-safe apply (:mod:`dossier.resolve`).

The apply path is the durability-critical seam, so several tests inject a fault
at each step (write fails · unlink fails · a concurrent write races us) and prove
the invariant that matters: **a conflict is never lost and never silently
clobbers a newer copy** — a crash mid-resolve always leaves something the next
run converges from.
"""

import os
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from dossier import (
    resolve,
    scan,
    store as store_module,
)
from dossier.config import Config
from dossier.errors import ResolveBusyError
from dossier.model import Document, ReconcileState
from dossier.store import Store


def _store(tmp_path: Path) -> tuple[Store, Config]:
    config = Config(syncthing_root=tmp_path)
    config.history_dir = tmp_path / "history"  # keep archives off the real data dir
    store = Store(config, now=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    store.ensure_layout()
    return store, config


def _conflict_sibling(live: Path, stamp: str = "20260101-120000-AAAAAAA") -> Path:
    """The Syncthing conflict path beside ``live`` (marker before the extension)."""
    return live.with_name(f"{live.stem}.sync-conflict-{stamp}{live.suffix}")


def _write_conflict(live: Path, data: bytes, **kw: str) -> Path:
    path = _conflict_sibling(live, **kw)
    path.write_bytes(data)
    return path


def _touch_newer(newer: Path, older: Path) -> None:
    """Force ``newer``'s mtime well past ``older``'s (beyond the tie tolerance)."""
    os.utime(older, (1_000_000, 1_000_000))
    os.utime(newer, (1_000_000 + 100, 1_000_000 + 100))


def _reading(document_type: str, **over: object) -> scan.ScanReading:
    base = scan.ScanReading.from_payload({"document_type": document_type}, model="m")
    return replace(base, **over)  # type: ignore[arg-type]


# -- discovery ---------------------------------------------------------------


def test_live_path_strips_the_conflict_marker():
    docs = Path("/root/.dossier/documents")
    assert (
        resolve._live_path(docs / "eng-1.sync-conflict-20260722-120000-ABCDEFG.md")
        == docs / "eng-1.md"
    )
    meta = Path("/root/.dossier")
    assert (
        resolve._live_path(meta / "scans.sync-conflict-20260722-120000-ABCDEFG.toml")
        == meta / "scans.toml"
    )


def test_find_conflicts_classifies_documents_sidecars_and_skips_unknowns(
    tmp_path: Path,
):
    store, config = _store(tmp_path)
    store.save(Document(id="eng-1", name="Passport"))
    doc_conflict = _write_conflict(
        store.document_path("eng-1"), store.serialize(Document(id="eng-1")).encode()
    )
    scans_conflict = _write_conflict(config.scans_path, b"")
    # A conflict file we can't classify (wrong dir / extension) must be left alone.
    unknown = config.meta_dir / "notes.sync-conflict-x.txt"
    unknown.write_bytes(b"")

    items = resolve.find_conflicts(store)
    by_kind = {item.kind: item for item in items}
    assert set(by_kind) == {"document", "scans"}
    assert by_kind["document"].conflict_path == doc_conflict
    assert by_kind["document"].live_path == store.document_path("eng-1")
    assert by_kind["scans"].conflict_path == scans_conflict


def test_prefer_reads_the_last_writer_from_mtimes(tmp_path: Path):
    live = tmp_path / "live"
    conflict = tmp_path / "conflict"
    live.write_bytes(b"a")
    conflict.write_bytes(b"b")

    _touch_newer(conflict, live)
    assert resolve._prefer(live, conflict) == (resolve.Side.THEIRS, False)
    _touch_newer(live, conflict)
    assert resolve._prefer(live, conflict) == (resolve.Side.OURS, False)
    os.utime(live, (5000, 5000))  # within tolerance → tie
    os.utime(conflict, (5000, 5000))
    assert resolve._prefer(live, conflict) == (resolve.Side.OURS, True)


def test_prefer_when_live_missing_is_theirs(tmp_path: Path):
    conflict = tmp_path / "conflict"
    conflict.write_bytes(b"b")
    assert resolve._prefer(tmp_path / "gone", conflict) == (resolve.Side.THEIRS, False)


# -- clean merges ------------------------------------------------------------


def test_document_auto_merges_cleanly_and_archives_recoverably(tmp_path: Path):
    store, config = _store(tmp_path)
    store.save(Document(id="eng-1", name="Passport", tags=["gov"]))
    live = store.document_path("eng-1")
    live_before = live.read_bytes()
    theirs = Document(
        id="eng-1",
        name="Passport",
        tags=["gov", "travel"],
        expiry_date=date(2030, 1, 1),
    )
    conflict = _write_conflict(live, store.serialize(theirs).encode())

    report = resolve.resolve_all(store, apply=True)

    merged = store.load("eng-1")
    assert merged.tags == ["gov", "travel"]  # union
    assert merged.expiry_date == date(2030, 1, 1)  # filled from theirs
    assert not conflict.exists()  # conflict cleared
    assert len(report.resolutions) == 1 and not report.contested  # clean
    # recoverable: the losing conflict and the pre-merge live are both stashed.
    archived = list((config.history_dir / "conflicts").glob("*"))
    superseded = list((config.history_dir / "superseded").glob("*"))
    assert (
        len(archived) == 1
        and archived[0].read_bytes() == store.serialize(theirs).encode()
    )
    assert superseded[0].read_bytes() == live_before


def test_dry_run_writes_nothing(tmp_path: Path):
    store, _ = _store(tmp_path)
    store.save(Document(id="eng-1", name="Passport"))
    live = store.document_path("eng-1")
    before = live.read_bytes()
    conflict = _write_conflict(
        live, store.serialize(Document(id="eng-1", name="Renamed")).encode()
    )

    report = resolve.resolve_all(store, apply=False)
    assert not report.applied
    assert live.read_bytes() == before  # untouched
    assert conflict.exists()  # conflict still there
    assert report.resolutions[0].contested  # but the plan saw the clash


# -- last-writer-wins on contested fields ------------------------------------


def test_contested_field_keeps_theirs_when_conflict_is_newer(tmp_path: Path):
    store, _ = _store(tmp_path)
    store.save(Document(id="eng-1", name="Passport"))
    live = store.document_path("eng-1")
    conflict = _write_conflict(
        live, store.serialize(Document(id="eng-1", name="Passport (renewed)")).encode()
    )
    _touch_newer(conflict, live)

    resolve.resolve_all(store, apply=True)
    assert store.load("eng-1").name == "Passport (renewed)"


def test_contested_field_keeps_ours_on_a_tie(tmp_path: Path):
    store, _ = _store(tmp_path)
    store.save(Document(id="eng-1", name="Passport"))
    live = store.document_path("eng-1")
    conflict = _write_conflict(
        live, store.serialize(Document(id="eng-1", name="Other")).encode()
    )
    now = 5_000_000
    os.utime(live, (now, now))  # equal mtimes → tie → ours
    os.utime(conflict, (now, now))

    resolve.resolve_all(store, apply=True)
    assert store.load("eng-1").name == "Passport"


# -- sidecars ----------------------------------------------------------------


def test_scans_sidecar_unions_readings(tmp_path: Path):
    store, config = _store(tmp_path)
    store.save_scans({"a": _reading("Passport")})
    _write_conflict(
        config.scans_path, store.serialize_readings({"b": _reading("Visa")})
    )

    resolve.resolve_all(store, apply=True)
    assert set(store.load_scans()) == {"a", "b"}


def test_reconcile_sidecar_unions_suppressions(tmp_path: Path):
    store, config = _store(tmp_path)
    store.save_reconcile(ReconcileState(dismissed={"o1"}))
    _write_conflict(
        config.reconcile_path,
        store.serialize_reconcile(ReconcileState(dismissed={"o2"})),
    )
    resolve.resolve_all(store, apply=True)
    assert store.load_reconcile().dismissed == {"o1", "o2"}


def test_config_is_whole_file_last_writer_wins_and_loud(tmp_path: Path):
    store, config = _store(tmp_path)
    live = config.synced_config_path
    live.write_bytes(b"expiry_threshold_days = 90\n")
    conflict = _write_conflict(live, b"expiry_threshold_days = 30\n")
    _touch_newer(conflict, live)

    report = resolve.resolve_all(store, apply=True)
    assert live.read_bytes() == b"expiry_threshold_days = 30\n"  # newer whole file wins
    assert report.resolutions[0].loud  # surfaced prominently


def test_deleted_live_is_restored_from_the_conflict(tmp_path: Path):
    store, _ = _store(tmp_path)
    live = store.document_path("eng-1")  # never created
    _write_conflict(
        live, store.serialize(Document(id="eng-1", name="Recovered")).encode()
    )

    resolve.resolve_all(store, apply=True)
    assert live.exists()
    assert store.load("eng-1").name == "Recovered"


# -- fault injection: crash-safety at each step ------------------------------


def test_crash_before_live_write_leaves_live_intact_and_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, _ = _store(tmp_path)
    store.save(Document(id="eng-1", name="Passport", tags=["gov"]))
    live = store.document_path("eng-1")
    live_before = live.read_bytes()
    conflict = _write_conflict(
        live, store.serialize(Document(id="eng-1", tags=["gov", "new"])).encode()
    )

    real = store_module.atomic_write_bytes

    def flaky(path: Path, data: bytes) -> None:
        if path == live:
            raise OSError("simulated crash before the live write lands")
        real(path, data)

    monkeypatch.setattr(resolve, "atomic_write_bytes", flaky)
    plan = resolve.plan(store, resolve.find_conflicts(store)[0])
    with pytest.raises(OSError, match="simulated crash"):
        resolve.apply_resolution(store, plan)

    assert live.read_bytes() == live_before  # never half-written
    assert conflict.exists()  # conflict preserved for a retry

    monkeypatch.setattr(resolve, "atomic_write_bytes", real)  # "reboot"
    resolve.resolve_all(store, apply=True)
    assert store.load("eng-1").tags == ["gov", "new"]  # converges
    assert not conflict.exists()


def test_crash_before_unlink_is_idempotent_on_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, _ = _store(tmp_path)
    store.save(Document(id="eng-1", name="Passport", tags=["gov"]))
    live = store.document_path("eng-1")
    conflict = _write_conflict(
        live, store.serialize(Document(id="eng-1", tags=["gov", "new"])).encode()
    )

    real_unlink = Path.unlink

    def boom(self: Path, *a: object, **k: object) -> None:
        raise OSError("simulated crash after the write, before the unlink")

    monkeypatch.setattr(Path, "unlink", boom)
    plan = resolve.plan(store, resolve.find_conflicts(store)[0])
    with pytest.raises(OSError, match="before the unlink"):
        resolve.apply_resolution(store, plan)

    assert store.load("eng-1").tags == ["gov", "new"]  # the merge did land
    assert conflict.exists()  # but the conflict wasn't cleared

    monkeypatch.setattr(Path, "unlink", real_unlink)  # "reboot"
    resolve.resolve_all(store, apply=True)  # re-plan is idempotent
    assert store.load("eng-1").tags == ["gov", "new"]  # no duplication, no loss
    assert not conflict.exists()


def test_concurrent_write_is_detected_not_clobbered(tmp_path: Path):
    store, _ = _store(tmp_path)
    store.save(Document(id="eng-1", name="Passport"))
    live = store.document_path("eng-1")
    conflict = _write_conflict(
        live, store.serialize(Document(id="eng-1", name="From conflict")).encode()
    )

    plan = resolve.plan(store, resolve.find_conflicts(store)[0])
    # Syncthing delivers a newer live copy AFTER we planned but BEFORE we apply.
    racing = store.serialize(Document(id="eng-1", name="Raced in")).encode()
    store_module.atomic_write_bytes(live, racing)

    with pytest.raises(ResolveBusyError):
        resolve.apply_resolution(store, plan)
    assert live.read_bytes() == racing  # the racing copy is never clobbered
    assert conflict.exists()  # left for the next run to re-plan against


def test_multiple_conflicts_for_one_doc_fold_sequentially(tmp_path: Path):
    store, _ = _store(tmp_path)
    store.save(Document(id="eng-1", name="Passport", tags=["gov"]))
    live = store.document_path("eng-1")
    _write_conflict(
        live,
        store.serialize(Document(id="eng-1", tags=["gov", "a"])).encode(),
        stamp="20260101-120000-AAAAAAA",
    )
    _write_conflict(
        live,
        store.serialize(Document(id="eng-1", tags=["gov", "b"])).encode(),
        stamp="20260101-130000-BBBBBBB",
    )

    resolve.resolve_all(store, apply=True)
    assert store.load("eng-1").tags == ["gov", "a", "b"]  # both folded in
    assert not list(live.parent.glob("*.sync-conflict-*"))  # all cleared
