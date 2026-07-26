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

from dataclasses import replace
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
    # The buckets that discriminate the fixes are all measured.
    assert timings.stat_ms >= 0 and timings.parse_ms >= 0
    assert timings.search_ms is not None and timings.search_ms >= 0
    assert timings.render_ms is not None and timings.render_ms >= 0
    assert timings.render_rows == 2  # both docs, under the cap


_BASE_TIMINGS = profiling.StoreTimings(
    documents_dir="/home/x/.dossier/documents",
    doc_count=10,
    total_kib=50.0,
    read_ms=5.0,
    stat_ms=1.0,
    parse_ms=5.0,
    load_all_ms=8.0,
    load_all_again_ms=8.0,
    load_one_ms=0.5,
    scan_files_ms=2.0,
    scan_files_count=10,
    reconcile_ms=3.0,
    doctor_ms=4.0,
    search_ms=1.0,
    render_ms=2.0,
    render_rows=10,
)


def _timings(**over: object) -> profiling.StoreTimings:
    """A fast-profile StoreTimings with per-test overrides (replace keeps ty happy)."""
    return replace(_BASE_TIMINGS, **over)


def test_recommendations_quiet_on_a_fast_profile():
    recs = profiling._recommendations(
        _timings(),
        backend_slow=False,
        fuse=False,
        bytecode_warm=True,
        floor_ms=70,
        textual_ms=900,
        tui_ms=950,
    )
    assert len(recs) == 1 and recs[0].startswith("[ok]")


def test_recommendations_rank_by_payoff_and_name_the_fix():
    # A slow FUSE store: big load_all, tiny stat floor, laggy keystroke, fat import.
    recs = profiling._recommendations(
        _timings(
            documents_dir="/storage/emulated/0/D/.dossier/documents",
            load_all_ms=1200,
            stat_ms=40,
            parse_ms=500,
            search_ms=25,
            render_ms=70,
        ),
        backend_slow=False,
        fuse=True,
        bytecode_warm=True,
        floor_ms=70,
        textual_ms=900,
        tui_ms=1100,
    )
    text = "\n".join(recs)
    assert "parse cache" in text and "paint-first" in text
    assert "low-power" in text and "lazy-import" in text
    assert (
        "libyaml" not in text
    )  # backend is the C parser — don't suggest installing it
    assert "FUSE" in text or "shared storage" in text
    # Ranked by estimated payoff: the ~1200 ms paint-first win leads.
    ranked = [r for r in recs if r.startswith("[~")]
    assert ranked[0].split("]")[1].strip().startswith("paint-first")


def test_recommendations_flag_pure_python_yaml_only_when_slow():
    slow = profiling._recommendations(
        _timings(parse_ms=400, load_all_ms=500, stat_ms=20),
        backend_slow=True,
        fuse=False,
        bytecode_warm=False,
        floor_ms=70,
        textual_ms=900,
        tui_ms=950,
    )
    joined = "\n".join(slow)
    assert "install libyaml" in joined  # pure-Python backend → recommend it
    assert "bytecode" in joined  # cold .pyc → note it


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
    assert "recommendations" in out


def test_profile_run_without_config_is_imports_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    monkeypatch.setattr(profiling, "_time_subprocess", lambda code, runs: 10.0)
    assert profiling.run(None, runs=1) == 0
    out = capsys.readouterr().out
    assert "no device config found" in out
    assert "store data" not in out
