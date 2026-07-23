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


def test_glyphs_defaults_to_nerd_and_loads_from_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    assert Config(syncthing_root=tmp_path).glyphs == "nerd"  # per-device default

    root = tmp_path / "docs"
    (root / ".dossier").mkdir(parents=True)
    device = tmp_path / "device.toml"
    device.write_text(
        f'syncthing_root = "{root.as_posix()}"\nglyphs = "ascii"\n', encoding="utf-8"
    )
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: device)

    assert Config.load().glyphs == "ascii"


def test_merge_synced_reads_organize_folders(tmp_path: Path):
    config = Config(syncthing_root=tmp_path)
    config.meta_dir.mkdir(parents=True)
    config.synced_config_path.write_text(
        "[organize.folders]\n"
        'marine = "Marine"\n'
        '"marine/safety" = "Marine/Safety Course Certs"\n',
        encoding="utf-8",
    )
    config.merge_synced()
    assert config.organize_folders == {
        "marine": "Marine",
        "marine/safety": "Marine/Safety Course Certs",
    }


def test_organize_folders_defaults_empty(tmp_path: Path):
    assert Config(syncthing_root=tmp_path).organize_folders == {}


def test_merge_synced_reads_intake_config(tmp_path: Path):
    config = Config(syncthing_root=tmp_path)
    config.meta_dir.mkdir(parents=True)
    config.synced_config_path.write_text(
        '[intake]\ninbox = "Inbox"\nfiled = "Archive"\n'
        '[intake.tags]\ncompetency = "marine/coc"\n',
        encoding="utf-8",
    )
    config.merge_synced()
    assert config.intake_inbox == "Inbox"
    assert config.intake_filed == "Archive"
    assert config.intake_tags == {"competency": "marine/coc"}


def test_intake_defaults_when_unconfigured(tmp_path: Path):
    cfg = Config(syncthing_root=tmp_path)
    assert cfg.intake_inbox is None  # intake disabled
    assert cfg.intake_filed == "Filed"
    assert cfg.intake_tags == {}


def test_update_synced_merges_preserving_other_keys(tmp_path: Path):
    config = Config(syncthing_root=tmp_path)
    config.meta_dir.mkdir(parents=True)
    config.synced_config_path.write_text(
        'include = ["*.pdf"]\nexpiry_threshold_days = 90\n'
    )
    config_mod.update_synced(config, {"expiry_threshold_days": 60})
    reloaded = Config(syncthing_root=tmp_path)
    reloaded.merge_synced()
    assert reloaded.expiry_threshold_days == 60  # updated
    assert reloaded.include == ["*.pdf"]  # preserved


def test_update_per_device_merges_preserving_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    device_path = tmp_path / "config.toml"
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: device_path)
    device_path.write_text(
        f'syncthing_root = "{tmp_path.as_posix()}"\nglyphs = "nerd"\n'
    )
    config_mod.update_per_device({"glyphs": "ascii", "scan_model": "qwen3vl"})
    import tomllib

    back = tomllib.loads(device_path.read_text())
    assert back["glyphs"] == "ascii"  # updated
    assert back["scan_model"] == "qwen3vl"  # added
    assert back["syncthing_root"] == tmp_path.as_posix()  # preserved
