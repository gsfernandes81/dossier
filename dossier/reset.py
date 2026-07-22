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

"""``ds reset`` — clear a folder's ``.dossier`` data, or this device's config.

**Hard guarantee: only ever touches ``<root>/.dossier/``.** The real document
files in the Syncthing tree are never removed. A folder reset backs ``.dossier/``
up to the local (non-synced) history dir first — so it is recoverable — then
leaves a clean empty layout ready to re-migrate.
"""

from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path

from dossier.config import META_DIRNAME, Config, per_device_config_path
from dossier.errors import DossierError
from dossier.store import Store


class ResetError(DossierError):
    """A reset could not be performed safely."""


def folder_reset_entries(config: Config) -> list[str]:
    """Names of the top-level ``.dossier/`` entries a folder reset would clear."""
    meta = config.meta_dir
    if not meta.is_dir():
        return []
    return sorted(p.name for p in meta.iterdir())


def reset_folder_data(config: Config) -> Path | None:
    """Back up ``.dossier/``, remove it, and recreate a clean empty layout.

    Returns the backup directory, or ``None`` if there was no ``.dossier/`` to
    reset. Never touches anything but ``<root>/.dossier/``.
    """
    meta = config.meta_dir
    if not meta.is_dir():
        return None
    # Defence in depth for a destructive op: resolve symlinks and refuse unless the
    # real target is a ".dossier" directory (so a .dossier symlinked at real data
    # is never rmtree'd). meta is config.meta_dir, so the unresolved name always
    # matches — resolving is what makes this a genuine guard.
    resolved = meta.resolve()
    if resolved.name != META_DIRNAME:
        raise ResetError(f"refusing to reset an unexpected path: {meta} -> {resolved}")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup = config.history_dir / f"reset-{stamp}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(meta, backup)
    shutil.rmtree(meta)
    Store(config).ensure_layout()  # clean slate, ready to re-migrate
    return backup


def reset_device_config() -> Path | None:
    """Remove this device's per-device config. Returns the path, or ``None``."""
    path = per_device_config_path()
    if not path.is_file():
        return None
    path.unlink()
    return path
