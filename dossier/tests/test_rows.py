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

"""Tests for the pure document-row renderers (Rich Console capture, no pilot)."""

from datetime import date

from rich.console import Console, RenderableType
from rich.text import Text

from dossier import query
from dossier.model import Document, ExpiryStatus, FileStatus
from dossier.tui import glyphs, rows
from dossier.tui.rows import RowMode


def _view(
    doc: Document,
    *,
    expiry: ExpiryStatus = ExpiryStatus.NONE,
    file: FileStatus = FileStatus.NONE,
) -> query.DocumentView:
    return query.DocumentView(document=doc, expiry=expiry, file=file)


def _render(renderable: RenderableType, width: int = 48) -> str:
    console = Console(width=width, color_system=None)
    with console.capture() as capture:
        console.print(renderable)
    return capture.get()


def test_dense_name_left_status_right():
    doc = Document(id="p", name="British Passport", has_physical=True, has_digital=True)
    doc.expiry_date = date(2034, 1, 15)
    line = _render(rows.doc_row(_view(doc, expiry=ExpiryStatus.OK)))

    assert "British Passport" in line
    assert "exp 15 Jan 34" in line
    assert "P D" in line  # ascii file glyphs, now spaced apart
    # status sits to the right of the name
    assert line.index("exp 15 Jan 34") > line.index("British Passport")


def test_compact_gutter_marks_every_status():
    doc = Document(id="x", name="IDP 1926", expiry_date=date(2026, 3, 10))

    def gutter(status: ExpiryStatus) -> str:
        return _render(rows.doc_row(_view(doc, expiry=status), mode=RowMode.COMPACT))

    assert gutter(ExpiryStatus.EXPIRED).startswith("!")  # attention
    assert gutter(ExpiryStatus.EXPIRING).startswith("~")
    assert gutter(ExpiryStatus.OK).startswith("+")  # an icon, not a blank gutter
    assert gutter(ExpiryStatus.NONE).startswith("·")  # no-expiry still anchored
    assert "IDP 1926" in gutter(ExpiryStatus.NONE)


def test_marker_switches_ascii_to_nerd():
    doc = Document(id="x", name="Cert", expiry_date=date(2026, 3, 10))
    view = _view(doc, expiry=ExpiryStatus.EXPIRED)
    ascii_row = _render(rows.doc_row(view, mode=RowMode.COMPACT, glyphs=glyphs.ASCII))
    nerd_row = _render(rows.doc_row(view, mode=RowMode.COMPACT, glyphs=glyphs.NERD))
    assert ascii_row.startswith("!")
    assert glyphs.NERD.expired in nerd_row
    assert glyphs.NERD.expired != "!"  # a real Nerd Font codepoint, not ASCII


def test_compact_name_is_one_ellipsized_line():
    long_name = "Certificate of Competency Card renewal 2026"
    doc = Document(id="c", name=long_name, expiry_date=date(2026, 3, 10))
    out = _render(
        rows.doc_row(_view(doc, expiry=ExpiryStatus.EXPIRED), mode=RowMode.COMPACT),
        width=20,
    )
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) == 1  # collapses to a single row, no wrapping
    assert "…" in lines[0]  # the over-long name is ellipsized to the pane width


def test_superseded_dims_the_row():
    doc = Document(id="old", name="Old Passport")
    row = rows.doc_row(_view(doc), mode=RowMode.MULTILINE, superseded=True)
    assert isinstance(row, Text)
    assert row.style == "dim"

    live = rows.doc_row(_view(doc), mode=RowMode.MULTILINE, superseded=False)
    assert isinstance(live, Text)
    assert live.style == ""


def test_multiline_meta_line_carries_slot_tags_and_missing_file():
    doc = Document(
        id="coc",
        name="CoC Card",
        tags=["marine", "marine/coc"],
        expiry_date=date(2026, 9, 1),
        perm_location="file",
        perm_slot=2,
        has_physical=True,
    )
    row = rows.doc_row(
        _view(doc, expiry=ExpiryStatus.EXPIRING, file=FileStatus.MISSING),
        mode=RowMode.MULTILINE,
    )
    assert isinstance(row, Text)
    first, meta = row.plain.split("\n", 1)
    assert first.startswith("~ ")  # expiring marker on the name line
    assert "CoC Card" in first
    assert "exp 01 Sep 26" in meta
    assert "slot 2" in meta
    assert "marine marine/coc" in meta
    assert "file missing" in meta


def test_multiline_always_has_a_second_line():
    # A bare document (no dates, location, tags or files) still gets a meta line
    # so the touch list keeps a steady two-line rhythm.
    bare = rows.doc_row(_view(Document(id="d", name="Bare")), mode=RowMode.MULTILINE)
    assert isinstance(bare, Text)
    first, meta = bare.plain.split("\n", 1)
    assert first.strip() == "Bare"
    assert meta.strip() == "—"


def test_show_issue_swaps_the_displayed_date():
    doc = Document(
        id="d",
        name="Doc",
        issue_date=date(2020, 3, 12),
        expiry_date=date(2026, 9, 1),
    )
    exp = _render(rows.doc_row(_view(doc, expiry=ExpiryStatus.OK)))
    iss = _render(rows.doc_row(_view(doc, expiry=ExpiryStatus.OK), show_issue=True))
    assert "exp 01 Sep 26" in exp and "iss" not in exp
    assert "iss 12 Mar 20" in iss and "exp" not in iss


def test_watch_row_is_date_first_with_location_and_tags():
    doc = Document(
        id="c", name="ENG-1 Med Cert", tags=["medical"], expiry_date=date(2026, 7, 10)
    )
    out = _render(
        rows.watch_row(
            _view(doc, expiry=ExpiryStatus.EXPIRED),
            location_label="Cert File · 2",
            glyphs=glyphs.ASCII,
        )
    )
    assert out.index("10 Jul 26") < out.index("ENG-1 Med Cert")  # date before name
    assert out.lstrip().startswith("!")  # expired marker leads
    assert "Cert File · 2" in out and "medical" in out
