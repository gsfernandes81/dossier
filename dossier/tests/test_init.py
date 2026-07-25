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

"""The conversational `ds init` engine — scripted-I/O unit tests, no PTY needed."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from dossier import init


def _redirect_device(monkeypatch: pytest.MonkeyPatch, device: Path) -> None:
    # init reads its own device path; update_per_device reads config's — patch both.
    monkeypatch.setattr("dossier.init.per_device_config_path", lambda: device)
    monkeypatch.setattr("dossier.config.per_device_config_path", lambda: device)


def _io(answers: list[str], *, interactive: bool = True):
    said: list[str] = []
    it = iter(answers)
    io = init.InitIO(
        ask=lambda prompt, default: next(it, ""),
        say=said.append,
        interactive=interactive,
    )
    return io, said


def _saved(device: Path) -> dict:
    return tomllib.loads(device.read_text())


def test_init_creates_the_root_and_layout(tmp_path: Path, monkeypatch):
    device = tmp_path / "device.toml"
    _redirect_device(monkeypatch, device)
    root = tmp_path / "store"  # does not exist yet
    io, _ = _io(["y", "y"])  # create it? yes · icons render? yes

    code = init.run(init.InitOptions(root=root), io)

    assert code == 0
    assert root.is_dir()  # created
    assert (root / ".dossier" / "documents").is_dir()  # layout ensured
    saved = _saved(device)
    assert saved["syncthing_root"] == str(root.resolve())
    assert saved["glyphs"] == "nerd"


def test_init_ascii_pick(tmp_path: Path, monkeypatch):
    device = tmp_path / "device.toml"
    _redirect_device(monkeypatch, device)
    root = tmp_path / "store"
    root.mkdir()  # exists → no "create it?" question
    io, _ = _io(["n"])  # icons render? no → ascii

    assert init.run(init.InitOptions(root=root), io) == 0
    assert _saved(device)["glyphs"] == "ascii"


def test_init_declines_reconfigure_of_an_existing_device(tmp_path: Path, monkeypatch):
    device = tmp_path / "device.toml"
    root = tmp_path / "store"
    root.mkdir()
    device.write_text(f'syncthing_root = "{root.as_posix()}"\nglyphs = "ascii"\n')
    _redirect_device(monkeypatch, device)
    io, said = _io(["n"])  # reconfigure? no

    code = init.run(init.InitOptions(), io)

    assert code == 0
    assert any("Nothing changed" in line for line in said)
    assert _saved(device)["glyphs"] == "ascii"  # untouched


def test_init_non_interactive_without_root_refuses(tmp_path: Path, monkeypatch):
    device = tmp_path / "device.toml"
    _redirect_device(monkeypatch, device)
    io, _ = _io([], interactive=False)  # no TTY, no --root

    assert init.run(init.InitOptions(), io) == 2  # not enough info
    assert not device.exists()


def test_init_reconfigure_preserves_other_device_keys(tmp_path: Path, monkeypatch):
    device = tmp_path / "device.toml"
    old_root = tmp_path / "old"
    old_root.mkdir()
    device.write_text(
        f'syncthing_root = "{old_root.as_posix()}"\n'
        'glyphs = "nerd"\n'
        'scan_model = "qwen3-vl"\n'
        "scan_dpi = 200\n"
    )
    _redirect_device(monkeypatch, device)
    new_root = tmp_path / "new"
    new_root.mkdir()
    # --root given → reconfigure non-interactively; a merge, never a replace.
    io, _ = _io([], interactive=False)

    assert init.run(init.InitOptions(root=new_root), io) == 0
    saved = _saved(device)
    assert saved["syncthing_root"] == str(new_root.resolve())  # repointed
    assert saved["scan_model"] == "qwen3-vl"  # survived the merge
    assert saved["scan_dpi"] == 200
