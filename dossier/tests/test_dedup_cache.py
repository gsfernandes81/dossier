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

"""Tests for the per-device page-hash cache (mocking the real hasher)."""

from pathlib import Path

import pytest

from dossier import dedup_cache, dedup_hash


def test_cache_reuses_then_rehashes_on_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "root"
    root.mkdir()
    scan = root / "a.pdf"
    scan.write_bytes(b"one")
    calls: list[Path] = []
    monkeypatch.setattr(dedup_hash, "page_hashes", lambda p: calls.append(p) or [1, 2])
    monkeypatch.setattr(dedup_cache, "_cache_path", lambda r: tmp_path / "cache.json")

    assert dedup_cache.cached_page_hashes([scan], root) == {"a.pdf": [1, 2]}
    assert len(calls) == 1
    # unchanged -> cache hit, no re-hash
    assert dedup_cache.cached_page_hashes([scan], root) == {"a.pdf": [1, 2]}
    assert len(calls) == 1
    # size/mtime change -> re-hash
    scan.write_bytes(b"different bytes")
    dedup_cache.cached_page_hashes([scan], root)
    assert len(calls) == 2


def test_cache_omits_non_page_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "root"
    root.mkdir()
    note = root / "notes.txt"
    note.write_text("x", encoding="utf-8")
    monkeypatch.setattr(dedup_hash, "page_hashes", lambda p: [])
    monkeypatch.setattr(dedup_cache, "_cache_path", lambda r: tmp_path / "cache.json")

    assert dedup_cache.cached_page_hashes([note], root) == {}
