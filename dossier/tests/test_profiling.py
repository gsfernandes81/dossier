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

"""Tests for the ``ds profile`` timing harness."""

from pathlib import Path

import pytest

from dossier import profiling
from dossier.config import Config
from dossier.model import Document
from dossier.store import Store


def _store(tmp_path: Path) -> tuple[Store, Config]:
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    store.save(Document(id="a", name="Alpha 21-08-23"))  # an ambiguous-date finding
    store.save(Document(id="b", name="Beta"))
    return store, config


def test_profile_store_times_the_load_paths(tmp_path: Path):
    _, config = _store(tmp_path)
    timings = profiling._profile_store(config)
    assert timings is not None
    assert timings.doc_count == 2
    # Every phase is a non-negative wall-clock measurement.
    assert timings.read_ms >= 0
    assert timings.load_all_ms >= 0
    assert timings.load_one_ms is not None and timings.load_one_ms >= 0
    assert timings.scan_files_ms >= 0
    assert timings.total_kib > 0


def test_profile_store_none_for_empty_store(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    Store(config).ensure_layout()
    assert profiling._profile_store(config) is None


def test_profile_run_prints_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    # Stub the subprocess import timings so the test never spawns interpreters.
    _, config = _store(tmp_path)
    monkeypatch.setattr(profiling, "_time_subprocess", lambda code, runs: 10.0)
    assert profiling.run(config, runs=1) == 0
    out = capsys.readouterr().out
    assert "dossier performance profile" in out
    assert "startup imports" in out
    assert "store data" in out
    assert "diagnosis" in out


def test_profile_run_without_config_is_imports_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(profiling, "_time_subprocess", lambda code, runs: 10.0)
    assert profiling.run(None, runs=1) == 0
    out = capsys.readouterr().out
    assert "no device config found" in out
    assert "store data" not in out
