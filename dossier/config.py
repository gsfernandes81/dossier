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

"""Per-device and synced configuration.

Two layers:

* **Per-device** (not synced) — a small TOML in the platform config dir holding
  only ``syncthing_root``, since the absolute root differs across devices.
* **Synced** — ``<root>/.dossier/config.toml`` holding shared settings
  (``expiry_threshold_days``, reconcile ``include``/``ignore`` globs).

All document paths are stored relative to ``syncthing_root`` and resolved per
device against it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import platformdirs

from dossier.errors import ConfigError

APP_NAME = "dossier"
META_DIRNAME = ".dossier"
DEFAULT_EXPIRY_THRESHOLD_DAYS = 90
# Icon style for the TUI; per-device since a terminal may lack a Nerd Font.
# Interpreted by dossier.tui.glyphs ("nerd" | "ascii").
DEFAULT_GLYPHS = "nerd"


def per_device_config_path() -> Path:
    """This device's config file (holds only ``syncthing_root``)."""
    config_dir = platformdirs.user_config_dir(APP_NAME, appauthor=False)
    return Path(config_dir) / "config.toml"


def default_history_dir() -> Path:
    """Local, non-synced directory for pre-save document backups."""
    data_dir = platformdirs.user_data_dir(APP_NAME, appauthor=False)
    return Path(data_dir) / "history"


@dataclass
class Config:
    """Resolved configuration for one device."""

    syncthing_root: Path
    expiry_threshold_days: int = DEFAULT_EXPIRY_THRESHOLD_DAYS
    include: list[str] = field(default_factory=list)
    ignore: list[str] = field(default_factory=list)
    history_dir: Path = field(default_factory=default_history_dir)
    glyphs: str = DEFAULT_GLYPHS

    @property
    def meta_dir(self) -> Path:
        return self.syncthing_root / META_DIRNAME

    @property
    def documents_dir(self) -> Path:
        return self.meta_dir / "documents"

    @property
    def locations_path(self) -> Path:
        return self.meta_dir / "locations.toml"

    @property
    def bundles_path(self) -> Path:
        return self.meta_dir / "bundles.toml"

    @property
    def reconcile_path(self) -> Path:
        """Machine-owned sidecar of reconcile decisions (dismiss / fold / ...)."""
        return self.meta_dir / "reconcile.toml"

    @property
    def suggestions_path(self) -> Path:
        """Machine-owned sidecar of dismissed field suggestions."""
        return self.meta_dir / "suggestions.toml"

    @property
    def synced_config_path(self) -> Path:
        return self.meta_dir / "config.toml"

    def validate(self) -> None:
        """Fail loudly if the root or the ``.dossier`` layout is missing.

        Distinguishes a misconfigured root from an un-initialised one so the
        caller never renders every document as "missing".
        """
        if not self.syncthing_root.is_dir():
            raise ConfigError(
                "syncthing_root does not exist or is not a directory: "
                f"{self.syncthing_root}"
            )
        if not self.meta_dir.is_dir():
            raise ConfigError(
                f"no {META_DIRNAME}/ found under {self.syncthing_root} — "
                "run `ds init` to set up this device."
            )

    @classmethod
    def load(cls) -> Config:
        """Load per-device config, then merge the synced ``.dossier/config.toml``."""
        device_path = per_device_config_path()
        if not device_path.is_file():
            raise ConfigError(
                f"no per-device config at {device_path} — run `ds init` first."
            )
        device = _read_toml(device_path)
        root_raw = device.get("syncthing_root")
        if not root_raw:
            raise ConfigError(
                f"{device_path} is missing the required 'syncthing_root' key."
            )
        cfg = cls(syncthing_root=Path(str(root_raw)).expanduser())
        glyphs = device.get("glyphs")
        if isinstance(glyphs, str) and glyphs:
            cfg.glyphs = glyphs
        cfg.validate()
        cfg.merge_synced()
        return cfg

    def merge_synced(self) -> None:
        """Overlay settings from the synced ``.dossier/config.toml`` if present."""
        if not self.synced_config_path.is_file():
            return
        synced = _read_toml(self.synced_config_path)
        threshold = synced.get("expiry_threshold_days")
        if isinstance(threshold, int) and not isinstance(threshold, bool):
            self.expiry_threshold_days = threshold
        include = synced.get("include")
        if isinstance(include, list):
            self.include = [str(x) for x in include]
        ignore = synced.get("ignore")
        if isinstance(ignore, list):
            self.ignore = [str(x) for x in ignore]


def _read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc
