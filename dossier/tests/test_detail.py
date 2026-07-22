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

"""Tests for the pure detail-pane renderer (Rich Console capture, no pilot)."""

from datetime import date

from rich.console import Console, RenderableType

from dossier import query
from dossier.model import (
    Document,
    ExpiryStatus,
    FileStatus,
    Rendition,
    SuggestedField,
    Suggestion,
)
from dossier.tui import detail, glyphs


def _view(
    doc: Document,
    *,
    expiry: ExpiryStatus = ExpiryStatus.NONE,
    file: FileStatus = FileStatus.NONE,
) -> query.DocumentView:
    return query.DocumentView(document=doc, expiry=expiry, file=file)


def _text(renderable: RenderableType, width: int = 60) -> str:
    console = Console(width=width, color_system=None)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def test_render_detail_shows_suggestions_hint():
    doc = Document(id="d", name="Some Doc 2023-08-15")
    s = Suggestion(
        doc_id="d",
        field=SuggestedField.ISSUE,
        values=("2023-08-15",),
        rationale="from the name",
    )
    out = _text(
        detail.render_detail(
            _view(doc),
            location_label=None,
            chain=[],
            superseded_by=None,
            suggestions=[s],
        )
    )
    assert "Suggestions (1)" in out
    assert "2023-08-15" in out
    assert "a accept · e review" in out


def test_render_detail_core_fields():
    doc = Document(
        id="p",
        name="British Passport",
        tags=["identity"],
        has_physical=True,
        has_digital=True,
        issue_date=date(2024, 1, 15),
        expiry_date=date(2034, 1, 15),
        files=[Rendition(label="scan", path="id/passport.pdf", primary=True)],
    )
    out = _text(
        detail.render_detail(
            _view(doc, expiry=ExpiryStatus.OK),
            location_label="Blue Pouch · 14",
            chain=[],
            superseded_by=None,
        )
    )
    assert "British Passport" in out
    assert "Blue Pouch · 14" in out
    assert "15 Jan 2024" in out
    assert "15 Jan 2034" in out and "(ok)" in out
    assert "identity" in out
    assert "physical + digital" in out
    assert "id/passport.pdf" in out


def test_render_detail_supersession_chain_and_missing_file():
    old = Document(id="p2016", name="Passport 2016")
    doc = Document(
        id="p2026",
        name="Passport 2026",
        supersedes="p2016",
        files=[Rendition(label="s", path="x.pdf")],
    )
    out = _text(
        detail.render_detail(
            _view(doc, file=FileStatus.MISSING),
            location_label=None,
            chain=[old],
            superseded_by=None,
        )
    )
    assert "Supersedes" in out
    assert "Passport 2016" in out
    assert "missing" in out


def test_render_detail_flags_superseded_document():
    doc = Document(id="old", name="Old Cert")
    newer = Document(id="new", name="New Cert", supersedes="old")
    out = _text(
        detail.render_detail(
            _view(doc), location_label=None, chain=[], superseded_by=newer
        )
    )
    assert "superseded by" in out
    assert "New Cert" in out


def test_render_detail_notes_no_file_and_ignored_expiry():
    doc = Document(
        id="d", name="Letter", ignore_expiry=True, notes="kept for reference"
    )
    out = _text(
        detail.render_detail(
            _view(doc), location_label=None, chain=[], superseded_by=None
        )
    )
    assert "No digital file linked" in out
    assert "expiry ignored" in out
    assert "kept for reference" in out


def test_render_detail_prefixes_nerd_field_icons():
    doc = Document(
        id="p",
        name="Passport",
        tags=["identity"],
        bundles=["us-visa"],
        issue_date=date(2024, 1, 1),
    )
    out = _text(
        detail.render_detail(
            _view(doc),
            location_label="File",
            chain=[],
            superseded_by=None,
            glyphs=glyphs.NERD,
        )
    )
    assert glyphs.NERD.location in out  # location field icon
    assert glyphs.NERD.calendar in out  # date field icon
    assert glyphs.NERD.tag in out  # tags field icon
    assert glyphs.NERD.bundle in out  # bundles field icon
    # ASCII default keeps the plain labels (no icons).
    plain = _text(
        detail.render_detail(
            _view(doc), location_label="File", chain=[], superseded_by=None
        )
    )
    assert glyphs.NERD.location not in plain
