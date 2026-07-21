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

"""Icon sets for the TUI.

Two styles selected by the per-device ``glyphs`` config value (a device may not
have a Nerd Font, so this is per-device, not synced):

* ``nerd`` — Font Awesome glyphs from a `Nerd Font <https://nerdfonts.com>`_
  (the default; both of the author's terminals have one installed).
* ``ascii`` — plain fallbacks (``!``/``~``/``P``/``D``) for terminals without a
  Nerd Font, where the private-use codepoints would render as boxes.

The Nerd Font codepoints are the original Font Awesome 4 range (U+F0xx–U+F1xx),
present in every Nerd Font patch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class GlyphStyle(StrEnum):
    ASCII = "ascii"
    NERD = "nerd"


DEFAULT_STYLE = GlyphStyle.NERD


@dataclass(frozen=True)
class GlyphSet:
    """The glyphs a row / detail renderer draws, resolved from a style."""

    expired: str  # a document past its expiry
    expiring: str  # inside the warn window
    physical: str  # a physical copy exists
    digital: str  # a digital file exists
    primary: str  # the primary rendition marker


ASCII = GlyphSet(expired="!", expiring="~", physical="P", digital="D", primary="*")

NERD = GlyphSet(
    expired="",  #  nf-fa-warning
    expiring="",  #  nf-fa-clock_o
    physical="",  #  nf-fa-file_text
    digital="",  #  nf-fa-paperclip
    primary="",  #  nf-fa-star
)

_BY_STYLE = {GlyphStyle.ASCII: ASCII, GlyphStyle.NERD: NERD}


def resolve(style: GlyphStyle | str) -> GlyphSet:
    """The glyph set for ``style``; unknown values fall back to ASCII."""
    try:
        return _BY_STYLE[GlyphStyle(str(style))]
    except ValueError:
        return ASCII
