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


def _record_clipboard(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Capture what would be piped to a clipboard tool, without running one."""
    calls: dict[str, object] = {}

    def fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        calls["argv"] = argv
        calls["input"] = kw.get("input")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(platform_open.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(platform_open.subprocess, "run", fake_run)
    return calls


def test_android_folder_uri_encodes_the_document_id_whole():
    # Android addresses files by URI, not path: the document id "primary:<rel>" is
    # percent-encoded entire, colon and slashes included.
    uri = platform_open.android_folder_uri(
        Path("/storage/emulated/0/Documents/Off Docs")
    )
    assert uri == (
        "content://com.android.externalstorage.documents/document/"
        "primary%3ADocuments%2FOff%20Docs"
    )


def test_android_folder_uri_none_outside_shared_storage():
    assert platform_open.android_folder_uri(Path("/data/data/com.termux/files")) is None


def test_reveal_asks_android_to_open_the_folder(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(platform_open, "is_termux", lambda: True)
    calls: dict[str, list[str]] = {}

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        calls["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "Starting: Intent { ... }", "")

    monkeypatch.setattr(platform_open.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(platform_open.subprocess, "run", fake_run)
    caveat = platform_open.reveal_file(Path("/storage/emulated/0/Documents/x.pdf"))
    assert calls["argv"][:4] == [
        "/usr/bin/am",
        "start",
        "-a",
        "android.intent.action.VIEW",
    ]
    assert calls["argv"][-1] == "vnd.android.document/directory"
    # Accepted is not the same as handled — the caller says so rather than claiming
    # success, because what happens next is the OEM file manager's call.
    assert caveat == platform_open.ANDROID_REVEAL_CAVEAT


def test_reveal_on_android_reports_when_nothing_handled_the_intent(
    monkeypatch: pytest.MonkeyPatch,
):
    # `am` exits 0 even when no activity matched; it only says so on stdout.
    monkeypatch.setattr(platform_open, "is_termux", lambda: True)
    monkeypatch.setattr(platform_open.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        platform_open.subprocess,
        "run",
        lambda argv, **_kw: subprocess.CompletedProcess(
            argv, 0, "Error: Activity not started, unable to resolve Intent", ""
        ),
    )
    with pytest.raises(OpenError, match="no file manager handled"):
        platform_open.reveal_file(Path("/storage/emulated/0/Documents/x.pdf"))


def test_reveal_opens_the_containing_folder_on_linux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(platform_open, "is_termux", lambda: False)
    monkeypatch.setattr(platform_open.sys, "platform", "linux")
    calls = _record_opener(monkeypatch)
    platform_open.reveal_file(tmp_path / "sub" / "x.pdf")
    assert calls["argv"] == ["/usr/bin/xdg-open", str(tmp_path / "sub")]


def test_reveal_selects_the_file_on_macos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(platform_open, "is_termux", lambda: False)
    monkeypatch.setattr(platform_open.sys, "platform", "darwin")
    calls = _record_opener(monkeypatch)
    platform_open.reveal_file(tmp_path / "x.pdf")
    assert calls["argv"] == ["/usr/bin/open", "-R", str(tmp_path / "x.pdf")]


def test_reveal_on_windows_ignores_explorers_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # explorer.exe returns 1 even when it worked, so a non-zero exit must not be
    # reported as a failure the way every other opener's is.
    monkeypatch.setattr(platform_open, "is_termux", lambda: False)
    monkeypatch.setattr(platform_open.sys, "platform", "win32")
    calls: dict[str, list[str]] = {}

    def fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[bytes]:
        calls["argv"] = argv
        return subprocess.CompletedProcess(argv, 1, b"", b"")

    monkeypatch.setattr(platform_open.shutil, "which", lambda name: f"C:/{name}.exe")
    monkeypatch.setattr(platform_open.subprocess, "run", fake_run)
    platform_open.reveal_file(tmp_path / "x.pdf")  # must not raise
    assert calls["argv"] == ["C:/explorer.exe", f"/select,{tmp_path / 'x.pdf'}"]


def test_copy_path_uses_utf16le_without_bom_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # clip.exe reads the console codepage, which mangles non-ASCII; UTF-16-LE with
    # no BOM round-trips exactly (a BOM would land in the text as U+FEFF).
    monkeypatch.setattr(platform_open, "is_termux", lambda: False)
    monkeypatch.setattr(platform_open.sys, "platform", "win32")
    calls = _record_clipboard(monkeypatch)
    target = tmp_path / "Café.pdf"
    platform_open.copy_path(target)
    data = calls["input"]
    assert isinstance(data, bytes)
    assert calls["argv"] == ["/usr/bin/clip"]
    assert data == str(target).encode("utf-16-le")
    assert not data.startswith(b"\xff\xfe"), "a BOM would land in the pasted text"


def test_copy_path_uses_termux_clipboard_on_android(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    monkeypatch.setattr(platform_open, "is_termux", lambda: True)
    calls = _record_clipboard(monkeypatch)
    platform_open.copy_path(tmp_path / "x.pdf")
    assert calls["argv"] == ["/usr/bin/termux-clipboard-set"]
    assert calls["input"] == str(tmp_path / "x.pdf").encode("utf-8")
