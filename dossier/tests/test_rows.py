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
from dossier.tui import rows
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
    assert "PD" in line  # ascii file glyphs
    # status sits to the right of the name
    assert line.index("exp 15 Jan 34") > line.index("British Passport")


def test_expired_has_permanent_marker_ok_does_not():
    doc = Document(id="x", name="IDP 1926", expiry_date=date(2026, 3, 10))
    expired = rows.doc_row(
        _view(doc, expiry=ExpiryStatus.EXPIRED), mode=RowMode.COMPACT
    )
    assert isinstance(expired, Text)
    assert expired.plain.startswith("! ")  # permanent cue, ascii

    ok = rows.doc_row(_view(doc, expiry=ExpiryStatus.OK), mode=RowMode.COMPACT)
    assert isinstance(ok, Text)
    assert ok.plain.startswith("  ")  # blank placeholder keeps names aligned
    assert ok.plain.strip() == "IDP 1926"


def test_marker_switches_ascii_to_emoji():
    doc = Document(id="x", name="Cert", expiry_date=date(2026, 3, 10))
    view = _view(doc, expiry=ExpiryStatus.EXPIRED)
    ascii_row = rows.doc_row(view, mode=RowMode.COMPACT, ascii_only=True)
    emoji_row = rows.doc_row(view, mode=RowMode.COMPACT, ascii_only=False)
    assert isinstance(ascii_row, Text) and isinstance(emoji_row, Text)
    assert ascii_row.plain.startswith("!")
    assert emoji_row.plain.startswith("⚠")


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
