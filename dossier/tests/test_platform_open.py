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

"""Tests for platform detection and the file opener."""

import subprocess
import sys
from pathlib import Path

import pytest

from dossier import platform_open
from dossier.platform_open import OpenError, is_termux, termux_preconditions


def _record_opener(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Capture the argv `_run_opener` would exec, without launching anything."""
    calls: dict[str, list[str]] = {}

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        calls["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(platform_open.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(platform_open.subprocess, "run", fake_run)
    return calls


def test_is_termux_via_prefix(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    assert is_termux()


def test_is_termux_false_off_android(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PREFIX", "/usr")
    monkeypatch.delenv("TERMUX_VERSION", raising=False)
    assert not is_termux()


def test_open_file_raises_when_termux_open_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(platform_open, "is_termux", lambda: True)
    monkeypatch.setattr(platform_open.shutil, "which", lambda _name: None)
    with pytest.raises(OpenError):
        platform_open.open_file(tmp_path / "x.pdf")


def test_termux_preconditions_reports_missing_pieces(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(platform_open.shutil, "which", lambda _name: None)
    monkeypatch.setattr(platform_open.Path, "home", classmethod(lambda _cls: tmp_path))
    problems = termux_preconditions()
    assert any("termux-open" in p for p in problems)
    assert any("storage" in p for p in problems)


# The per-OS opener choice — mock the platform so each branch is provable on any
# CI leg (the matrix then re-runs the whole file on a real Windows runner too).


def test_open_file_execs_termux_open_under_termux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(platform_open, "is_termux", lambda: True)
    calls = _record_opener(monkeypatch)
    platform_open.open_file(tmp_path / "x.pdf")
    assert calls["argv"] == ["/usr/bin/termux-open", str(tmp_path / "x.pdf")]


def test_open_file_execs_xdg_open_on_linux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(platform_open, "is_termux", lambda: False)
    monkeypatch.setattr(platform_open.sys, "platform", "linux")
    calls = _record_opener(monkeypatch)
    platform_open.open_file(tmp_path / "x.pdf")
    assert calls["argv"] == ["/usr/bin/xdg-open", str(tmp_path / "x.pdf")]


def test_open_file_execs_open_on_macos(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(platform_open, "is_termux", lambda: False)
    monkeypatch.setattr(platform_open.sys, "platform", "darwin")
    calls = _record_opener(monkeypatch)
    platform_open.open_file(tmp_path / "x.pdf")
    assert calls["argv"] == ["/usr/bin/open", str(tmp_path / "x.pdf")]


@pytest.mark.skipif(
    not sys.platform.startswith("win"), reason="os.startfile is Windows-only"
)
def test_open_file_windows_missing_file_raises(tmp_path: Path):
    # Real os.startfile on a nonexistent path raises → OpenError. Uses the true
    # Windows API but never actually opens anything (the file does not exist).
    with pytest.raises(OpenError):
        platform_open.open_file(tmp_path / "does-not-exist.pdf")
