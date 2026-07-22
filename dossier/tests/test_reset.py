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

"""Tests for ``ds reset`` — clearing .dossier data / device config, safely."""

import argparse
from pathlib import Path

import pytest

from dossier import cli, reset
from dossier.config import Config
from dossier.model import Document
from dossier.store import Store


def _configured_root(tmp_path: Path) -> Config:
    root = tmp_path / "docs"
    (root / "Marine").mkdir(parents=True)
    (root / "Marine" / "CoC.pdf").write_bytes(b"real file")  # a real document file
    config = Config(syncthing_root=root, history_dir=tmp_path / "_history")
    store = Store(config)
    store.ensure_layout()
    store.save(Document(id="passport", name="Passport"))
    return config


def test_reset_folder_backs_up_clears_and_keeps_real_files(tmp_path: Path):
    config = _configured_root(tmp_path)
    assert list(config.documents_dir.glob("*.md"))  # sanity: a doc is stored

    backup = reset.reset_folder_data(config)

    assert backup is not None and backup.is_dir()
    assert (backup / "documents" / "passport.md").is_file()  # old data recoverable
    assert list(config.documents_dir.glob("*.md")) == []  # store cleared
    assert config.locations_path.is_file()  # clean empty layout recreated
    # The real document file in the tree is NEVER touched.
    assert (config.syncthing_root / "Marine" / "CoC.pdf").read_bytes() == b"real file"


def test_reset_folder_none_when_no_dossier(tmp_path: Path):
    config = Config(syncthing_root=tmp_path / "empty", history_dir=tmp_path / "_h")
    assert reset.reset_folder_data(config) is None
    assert reset.folder_reset_entries(config) == []


def test_reset_device_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cfg = tmp_path / "device.toml"
    cfg.write_text('syncthing_root = "x"\n', encoding="utf-8")
    monkeypatch.setattr(reset, "per_device_config_path", lambda: cfg)

    assert reset.reset_device_config() == cfg
    assert not cfg.exists()
    assert reset.reset_device_config() is None  # already gone


def test_cmd_reset_folder_clears_and_keeps_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = _configured_root(tmp_path)
    monkeypatch.setattr(cli.Config, "load", classmethod(lambda cls: config))
    args = argparse.Namespace(root=None, global_config=False, yes=True)

    assert cli.cmd_reset(args) == 0
    assert list(config.documents_dir.glob("*.md")) == []  # cleared
    assert (config.syncthing_root / "Marine" / "CoC.pdf").exists()  # real file kept


def test_cmd_reset_global_removes_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    cfg = tmp_path / "device.toml"
    cfg.write_text('syncthing_root = "x"\n', encoding="utf-8")
    monkeypatch.setattr(cli, "per_device_config_path", lambda: cfg)
    monkeypatch.setattr(reset, "per_device_config_path", lambda: cfg)
    args = argparse.Namespace(root=None, global_config=True, yes=True)

    assert cli.cmd_reset(args) == 0
    assert not cfg.exists()
