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

"""Tests for configuration loading."""

from pathlib import Path

import pytest

from dossier import config as config_mod
from dossier.config import Config
from dossier.errors import ConfigError


def test_derived_paths(tmp_path: Path):
    cfg = Config(syncthing_root=tmp_path)
    assert cfg.meta_dir == tmp_path / ".dossier"
    assert cfg.documents_dir == tmp_path / ".dossier" / "documents"
    assert cfg.locations_path.name == "locations.toml"
    assert cfg.bundles_path.name == "bundles.toml"


def test_validate_requires_meta_dir(tmp_path: Path):
    cfg = Config(syncthing_root=tmp_path)
    with pytest.raises(ConfigError):
        cfg.validate()
    cfg.meta_dir.mkdir()
    cfg.validate()  # now valid


def test_validate_rejects_missing_root(tmp_path: Path):
    cfg = Config(syncthing_root=tmp_path / "does-not-exist")
    with pytest.raises(ConfigError):
        cfg.validate()


def test_load_reads_device_and_synced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "docs"
    (root / ".dossier").mkdir(parents=True)
    device = tmp_path / "device.toml"
    device.write_text(f'syncthing_root = "{root.as_posix()}"\n', encoding="utf-8")
    (root / ".dossier" / "config.toml").write_text(
        "expiry_threshold_days = 30\ninclude = ['Official Documents/**']\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: device)

    cfg = Config.load()
    assert cfg.syncthing_root == root
    assert cfg.expiry_threshold_days == 30
    assert cfg.include == ["Official Documents/**"]


def test_load_without_device_config_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        config_mod, "per_device_config_path", lambda: tmp_path / "missing.toml"
    )
    with pytest.raises(ConfigError):
        Config.load()
