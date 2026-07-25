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

Nerd codepoints are written as ``\\uXXXX`` escapes (the original Font Awesome 4
range, U+F0xx–U+F2xx, present in every Nerd Font patch) so the source stays
readable in editors that can't render private-use glyphs; the comment names each.
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
    """The glyphs a row / detail renderer draws, resolved from a style.

    The status markers (first block) are shown per document; the rest are leading
    icons for labels and controls and default to empty, so the ASCII set omits
    them (leaving the plain text label) while the Nerd set supplies an icon.
    """

    # Per-document status markers.
    expired: str  # a document past its expiry
    expiring: str  # inside the warn window
    ok: str  # has an expiry, still comfortably valid
    neutral: str  # no expiry / nothing to track
    physical: str  # a physical copy exists
    digital: str  # a digital file exists
    primary: str  # the primary rendition marker
    # Sync-status markers (the home footer sync glyph, Phase 15) — supplied by both
    # styles like the other status markers, since a bare icon needs an ASCII fallback.
    sync_idle: str  # in sync / settled
    sync_active: str  # scanning or syncing
    sync_off: str  # unreachable / unauthorized — sync is not happening
    # Leading icons (empty in ASCII).
    folder: str = ""  # a physical location
    inbox: str = ""  # the "All" locations row
    unlocated: str = ""  # the "no location" row
    location: str = ""  # the detail location field
    calendar: str = ""  # a date field
    tag: str = ""  # the tags field
    bundle: str = ""  # the bundles field / action
    link: str = ""  # supersession
    note: str = ""  # notes
    open: str = ""  # the open-file action
    new: str = ""  # the new-document action
    edit: str = ""  # the edit action
    keyboard: str = ""  # the raise-keyboard action
    commands: str = ""  # the command-palette action


ASCII = GlyphSet(
    expired="!",
    expiring="~",
    ok="+",
    neutral="·",  # middle dot
    sync_idle="=",  # settled
    sync_active="~",  # moving
    sync_off="x",  # not syncing
    physical="P",
    digital="D",
    primary="*",
)

NERD = GlyphSet(
    expired="",  # nf-fa-warning
    expiring="",  # nf-fa-clock_o
    ok="",  # nf-fa-check
    neutral="",  # nf-fa-circle
    physical="",  # nf-fa-file_text
    digital="",  # nf-fa-paperclip
    primary="",  # nf-fa-star
    sync_idle="\uf0c2",  # nf-fa-cloud — settled / in sync
    sync_active="\uf021",  # nf-fa-refresh — scanning / syncing
    sync_off="\uf127",  # nf-fa-chain_broken — unreachable / unauthorized
    folder="",  # nf-fa-folder
    inbox="",  # nf-fa-list_ul
    unlocated="",  # nf-fa-question_circle
    location="",  # nf-fa-map_marker
    calendar="",  # nf-fa-calendar
    tag="",  # nf-fa-tag
    bundle="",  # nf-fa-archive
    link="",  # nf-fa-link
    note="",  # nf-fa-sticky_note
    open="",  # nf-fa-external_link
    new="",  # nf-fa-plus
    edit="",  # nf-fa-edit (pencil-square)
    keyboard="",  # nf-fa-keyboard_o
    commands="",  # nf-fa-terminal — the command palette
)

_BY_STYLE = {GlyphStyle.ASCII: ASCII, GlyphStyle.NERD: NERD}


def resolve(style: GlyphStyle | str) -> GlyphSet:
    """The glyph set for ``style``; unknown values fall back to ASCII."""
    try:
        return _BY_STYLE[GlyphStyle(str(style))]
    except ValueError:
        return ASCII
