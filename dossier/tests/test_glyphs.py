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

"""Tests for the TUI glyph sets."""

from dossier.tui import glyphs
from dossier.tui.glyphs import GlyphStyle


def test_resolve_selects_style_and_falls_back():
    assert glyphs.resolve("ascii") is glyphs.ASCII
    assert glyphs.resolve("nerd") is glyphs.NERD
    assert glyphs.resolve(GlyphStyle.NERD) is glyphs.NERD
    assert glyphs.resolve("bogus") is glyphs.ASCII  # unknown -> safe fallback


def test_nerd_glyphs_are_single_private_use_codepoints():
    for value in (
        glyphs.NERD.expired,
        glyphs.NERD.expiring,
        glyphs.NERD.physical,
        glyphs.NERD.digital,
        glyphs.NERD.primary,
    ):
        assert len(value) == 1  # one cell, one codepoint
        assert 0xE000 <= ord(value) <= 0xF8FF  # Unicode Private Use Area
