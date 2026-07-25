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

"""Tests for the background scan service (:mod:`dossier.service`).

The VLM, the power reading, the clock, and the lock dir are all injected, so a
whole pass runs in-process with no hardware and no real model.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from dossier import platform_open, scan, service
from dossier.config import Config
from dossier.errors import ScanError
from dossier.model import Document, Rendition
from dossier.power import PowerSample
from dossier.store import Store


def _ac() -> PowerSample:
    return PowerSample(on_ac=True, saver=False, percent=None, source="test")


def _battery() -> PowerSample:
    return PowerSample(on_ac=False, saver=None, percent=None, source="test")


def _reading(document_type: str = "Passport") -> scan.ScanReading:
    return scan.ScanReading.from_payload({"document_type": document_type}, model="m")


def _store_with_linked_doc(tmp_path: Path) -> tuple[Store, Config, Path]:
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_history")
    store = Store(config)
    store.ensure_layout()
    (tmp_path / "a.pdf").write_bytes(b"pdf-bytes")
    store.save(Document(id="d", name="Doc", files=[Rendition("full", "a.pdf", True)]))
    return store, config, tmp_path / "lock"


def test_gated_on_battery_scans_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config, lock = _store_with_linked_doc(tmp_path)
    calls: list[int] = []
    monkeypatch.setattr(scan, "extract", lambda p, c: calls.append(1) or _reading())

    result = service.run_service(store, config, probe=_battery, lock_dir=lock)
    assert result.gate == "battery" and result.exit_code == 0
    assert calls == []  # never invoked the model
    assert store.load_scans() == {}


def test_runs_scan_and_transcribe_on_ac(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config, lock = _store_with_linked_doc(tmp_path)
    monkeypatch.setattr(scan, "extract", lambda p, c: _reading("Passport"))
    monkeypatch.setattr(scan, "transcribe", lambda p, c: ("full text", ("kw",)))

    result = service.run_service(store, config, probe=_ac, lock_dir=lock)
    assert result.gate == "ok" and result.scanned == 1 and result.transcribed == 1
    assert result.exit_code == 0
    reading = store.load_scans()["d"]
    assert reading.document_type == "Passport" and reading.transcript == "full text"


def test_scan_failure_yields_exit_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store, config, lock = _store_with_linked_doc(tmp_path)

    def boom(path: Path, cfg: Config) -> scan.ScanReading:
        raise ScanError("VLM unreachable")

    monkeypatch.setattr(scan, "extract", boom)
    result = service.run_service(store, config, probe=_ac, lock_dir=lock)
    assert result.failed == 1 and result.exit_code == 1


def test_a_held_fresh_lock_blocks_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config, lock = _store_with_linked_doc(tmp_path)
    monkeypatch.setattr(scan, "extract", lambda p, c: _reading())
    lock.mkdir()
    fresh = datetime.now(UTC).isoformat()
    (lock / "scan-service.lock").write_text(f"999999\n{fresh}\n")

    result = service.run_service(store, config, probe=_ac, lock_dir=lock)
    assert result.gate == "locked" and result.exit_code == 0
    assert store.load_scans() == {}  # the other "instance" owns the pass


def test_a_stale_lock_is_stolen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store, config, lock = _store_with_linked_doc(tmp_path)
    monkeypatch.setattr(scan, "extract", lambda p, c: _reading())
    monkeypatch.setattr(scan, "transcribe", lambda p, c: ("t", ()))
    lock.mkdir()
    old = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
    (lock / "scan-service.lock").write_text(f"1\n{old}\n")

    result = service.run_service(store, config, probe=_ac, lock_dir=lock)
    assert result.gate == "ok" and result.scanned == 1  # broke the abandoned lock
    assert not (lock / "scan-service.lock").exists()  # released after the run


def test_termux_is_a_clean_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store, config, lock = _store_with_linked_doc(tmp_path)
    monkeypatch.setattr(platform_open, "is_termux", lambda: True)
    calls: list[int] = []
    monkeypatch.setattr(scan, "extract", lambda p, c: calls.append(1) or _reading())

    result = service.run_service(store, config, probe=_ac, lock_dir=lock)
    assert result.gate == "termux" and result.exit_code == 0
    assert calls == []  # never runs the model on the phone


def test_unplugging_mid_run_stops_before_transcribe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config, lock = _store_with_linked_doc(tmp_path)
    monkeypatch.setattr(scan, "extract", lambda p, c: _reading())
    transcribed: list[int] = []
    monkeypatch.setattr(
        scan, "transcribe", lambda p, c: transcribed.append(1) or ("t", ())
    )
    # AC at the start (gate passes, scan runs), on battery at the mid-run re-check.
    samples = iter([_ac(), _battery(), _battery()])

    result = service.run_service(
        store, config, probe=lambda: next(samples), lock_dir=lock
    )
    assert result.gate == "ok" and result.scanned == 1
    assert result.transcribed == 0 and transcribed == []  # stopped gracefully


def test_summary_line_is_key_value():
    result = service.ServiceResult(
        "ok", scanned=2, transcribed=1, exit_code=0, sync_wait="idle"
    )
    assert result.summary() == (
        "gate=ok sync=idle scanned=2 transcribed=1 intake=0 failed=0 exit=0"
    )


def test_service_waits_for_sync_idle_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config, lock = _store_with_linked_doc(tmp_path)
    order: list[str] = []
    monkeypatch.setattr(
        scan, "extract", lambda p, c: order.append("scan") or _reading()
    )
    monkeypatch.setattr(scan, "transcribe", lambda p, c: ("", []))
    result = service.run_service(
        store,
        config,
        probe=_ac,
        lock_dir=lock,
        wait_idle=lambda _c: order.append("wait") or "idle",
    )
    assert result.sync_wait == "idle"
    assert order[0] == "wait" and "scan" in order  # settled before the batch write


def test_service_proceeds_when_sync_never_settles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config, lock = _store_with_linked_doc(tmp_path)
    monkeypatch.setattr(scan, "extract", lambda p, c: _reading())
    monkeypatch.setattr(scan, "transcribe", lambda p, c: ("", []))
    result = service.run_service(
        store, config, probe=_ac, lock_dir=lock, wait_idle=lambda _c: "timeout"
    )
    # availability > strictness: a timeout is logged but the pass still runs
    assert result.sync_wait == "timeout" and result.exit_code == 0
    assert result.scanned == 1


def test_service_skips_sync_wait_when_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config, lock = _store_with_linked_doc(tmp_path)
    waited: list[int] = []
    result = service.run_service(
        store,
        config,
        probe=_battery,
        lock_dir=lock,
        wait_idle=lambda _c: waited.append(1) or "idle",
    )
    assert result.gate == "battery" and result.sync_wait == "skipped"
    assert waited == []  # never got past the power gate, so never waited
