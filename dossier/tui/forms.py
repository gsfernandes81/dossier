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

"""Small pure helpers shared by the editing surfaces (detail pane + modals).

Date/int/slug coercions and the disk-checking unique-id used when a new document
is saved. Kept widget-free so both :mod:`dossier.tui.screens` and
:mod:`dossier.tui.detail_pane` can reuse them without importing each other.
"""

from __future__ import annotations

from datetime import date

# unique_id now lives in the store (a core module can create ids without importing
# the TUI); re-exported here so the editing surfaces keep using forms.unique_id.
from dossier.store import Store, unique_id

__all__ = [
    "Store",
    "unique_id",
    "iso",
    "parse_iso",
    "int_text",
    "parse_int",
    "slug",
]


def iso(value: date | None) -> str:
    """A date as ``YYYY-MM-DD`` for an input, or blank for ``None``."""
    return value.isoformat() if value else ""


def parse_iso(text: str) -> date | None:
    """Parse ``YYYY-MM-DD`` (blank → ``None``); raises ``ValueError`` on garbage."""
    text = text.strip()
    return date.fromisoformat(text) if text else None


def int_text(value: int | None) -> str:
    return "" if value is None else str(value)


def parse_int(text: str) -> int | None:
    """Parse a whole number (blank → ``None``); raises ``ValueError`` on garbage."""
    text = text.strip()
    return int(text) if text else None


def slug(text: str) -> str | None:
    """A trimmed slug field, or ``None`` when blank."""
    return text.strip() or None
