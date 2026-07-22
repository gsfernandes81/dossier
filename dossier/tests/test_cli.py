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

"""Tests for the CLI dispatch and ``ds init``."""

from pathlib import Path

import pytest

from dossier import (
    cli,
    config as config_mod,
)
from dossier.config import Config


def test_init_creates_layout_and_loadable_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "docs"
    root.mkdir()
    device = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr(cli, "per_device_config_path", lambda: device)
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: device)

    assert cli.main(["init", "--root", str(root)]) == 0
    assert device.is_file()
    assert (root / ".dossier" / "documents").is_dir()
    assert (root / ".dossier" / "locations.toml").is_file()
    assert (root / ".dossier" / "config.toml").is_file()

    # The config the CLI wrote must load end-to-end.
    cfg = Config.load()
    assert cfg.syncthing_root == root.resolve()
    assert cfg.expiry_threshold_days == 90  # from the seeded synced config


def test_init_rejects_missing_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    device = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr(cli, "per_device_config_path", lambda: device)
    assert cli.main(["init", "--root", str(tmp_path / "nope")]) == 1
    assert not device.exists()


def test_init_is_idempotent_without_force(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "docs"
    root.mkdir()
    device = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr(cli, "per_device_config_path", lambda: device)

    assert cli.main(["init", "--root", str(root)]) == 0
    first = device.read_bytes()

    other = tmp_path / "other"
    other.mkdir()
    assert cli.main(["init", "--root", str(other)]) == 0  # no --force
    assert device.read_bytes() == first  # unchanged


def test_default_command_without_config_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # Bare `ds` launches the TUI, which needs a configured device first.
    missing = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: missing)
    assert cli.main([]) == 1
    assert "init" in capsys.readouterr().err.lower()


def _touch_for(argv: list[str], *, termux: bool, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "is_termux", lambda: termux)
    args = cli.build_parser().parse_args(argv)
    return cli._resolve_touch(args)


def test_touch_follows_platform_by_default(monkeypatch: pytest.MonkeyPatch):
    # No flag: the touch UI tracks the platform (on under Termux, off elsewhere).
    assert _touch_for([], termux=True, monkeypatch=monkeypatch) is True
    assert _touch_for([], termux=False, monkeypatch=monkeypatch) is False


def test_mobile_and_desktop_flags_override_platform(monkeypatch: pytest.MonkeyPatch):
    # The point of the flags: drive either UI on any platform (e.g. the touch UI
    # on a desktop terminal, for the tools/ PTY harness).
    assert _touch_for(["--mobile"], termux=False, monkeypatch=monkeypatch) is True
    assert _touch_for(["--desktop"], termux=True, monkeypatch=monkeypatch) is False


def test_mobile_and_desktop_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--mobile", "--desktop"])
