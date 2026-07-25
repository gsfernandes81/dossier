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

"""The Textual TUI."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dossier.tui.app import DossierApp

__all__ = ["DossierApp"]


def __getattr__(name: str) -> object:
    # Lazy so importing a leaf module (e.g. `from dossier.tui import glyphs` in
    # `ds init`) doesn't drag in Textual just to read a glyph set. Accessing
    # `dossier.tui.DossierApp` still works — it imports the app on first use.
    if name == "DossierApp":
        from dossier.tui.app import DossierApp

        return DossierApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
