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

"""Tests for the dedup page-hasher (the pure parts; adapters need the extra)."""

from pathlib import Path

from dossier import dedup_hash

_W, _H = 9, 8


def test_dhash_from_grey_encodes_left_gt_right():
    assert dedup_hash.dhash_from_grey([5] * (_W * _H)) == 0  # equal -> no bits set
    # each row 9,8,…,1 → left > right at every step → all 64 bits set
    decreasing = [_W - col for _ in range(_H) for col in range(_W)]
    assert dedup_hash.dhash_from_grey(decreasing) == (1 << 64) - 1


def test_page_hashes_ignores_non_page_files(tmp_path: Path):
    note = tmp_path / "notes.txt"
    note.write_text("not a scan", encoding="utf-8")
    assert dedup_hash.page_hashes(note) == []
