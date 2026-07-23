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

"""Pure Rich renderers for a document row in the documents pane.

No widgets and no I/O — every function takes a :class:`~dossier.query.DocumentView`
and returns a Rich renderable, so the whole module is testable with a plain
``rich.console.Console`` capture (no Textual pilot needed). The home screen wraps
these in ``OptionList`` options.

Three shapes, one status vocabulary (see :data:`_STATUS_STYLE`):

* :attr:`RowMode.DENSE` — a wide pane: ``name`` left-truncated, expiry + file
  glyphs right-aligned to the pane edge (DESIGN §14 "name left, exp + emoji
  right").
* :attr:`RowMode.COMPACT` — the documents column collapsed to names when the
  detail pane opens; each row keeps a one-char expiry cue so expired items still
  stand out while scanning.
* :attr:`RowMode.MULTILINE` — narrow panes / portrait: name (+ marker) on line
  one, then always a dim meta line (date · slot · tags · flags, or ``—`` when
  empty) so every row keeps a steady two-line rhythm.

Icons come from a :class:`~dossier.tui.glyphs.GlyphSet` (Nerd Font or ASCII),
chosen per device — see :mod:`dossier.tui.glyphs`.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from dossier.model import Bundle, Document, ExpiryStatus, FileStatus
from dossier.query import DocumentView
from dossier.tui.glyphs import ASCII, GlyphSet


class RowMode(StrEnum):
    """How a document row is laid out for the current pane width."""

    DENSE = "dense"
    COMPACT = "compact"
    MULTILINE = "multiline"


# Expiry status → row style. Expired and expiring are the attention states that
# also carry the permanent marker; ok is a quiet green, none is unstyled.
_STATUS_STYLE = {
    ExpiryStatus.EXPIRED: "bold red",
    ExpiryStatus.EXPIRING: "yellow",
    ExpiryStatus.OK: "green",
    ExpiryStatus.NONE: "",
}

# Dense-row gap columns (cells): between the name and the right-aligned status,
# and between the status and the pane's scrollbar.
_NAME_STATUS_GAP = 2
_SCROLLBAR_GAP = 2


def doc_row(
    view: DocumentView,
    *,
    mode: RowMode = RowMode.DENSE,
    superseded: bool = False,
    show_issue: bool = False,
    glyphs: GlyphSet = ASCII,
) -> RenderableType:
    """Render one document row in the given ``mode``.

    ``superseded`` dims the whole row (the doc has been renewed away but is kept).
    ``show_issue`` swaps the displayed date from expiry to issue (the ``i``
    toggle); the expiry *colour and marker* stay, so urgency reads either way.
    ``glyphs`` picks the icon set (Nerd Font vs ASCII).
    """
    if mode is RowMode.COMPACT:
        return _compact(view, superseded=superseded, glyphs=glyphs)
    if mode is RowMode.MULTILINE:
        return _multiline(
            view, superseded=superseded, show_issue=show_issue, glyphs=glyphs
        )
    return _dense(view, superseded=superseded, show_issue=show_issue, glyphs=glyphs)


def watch_row(
    view: DocumentView,
    *,
    location_label: str | None = None,
    glyphs: GlyphSet = ASCII,
    event_note: str = "",
) -> Text:
    """A row for the expiry-watch surface: date first, then name · location · tags,
    coloured by expiry status (``⚠ 10 Jul 26   ENG-1 Med Cert   Cert File · 2``).
    ``event_note`` (e.g. ``· needed 2026-03-11 for travel/india``) marks a doc that
    lapses before a dated bundle needs it."""
    doc = view.document
    style = _STATUS_STYLE[view.expiry]
    marker = _marker(view.expiry, glyphs)
    row = Text()
    row.append(f"{marker or ' '} ", style=style)
    dated = _fmt(doc.expiry_date) if doc.expiry_date is not None else ""
    row.append(f"{dated:>9}", style=style)
    row.append("  ")
    row.append(doc.name or doc.id)
    meta = [part for part in (location_label, " ".join(doc.tags) or None) if part]
    if meta:
        row.append("   " + "  ".join(meta), style="dim")
    if event_note:
        row.append(f"   {event_note}", style="dim")
    return row


def bundle_row(
    bundle: Bundle, *, count: int, glyphs: GlyphSet = ASCII, readiness: str = ""
) -> Text:
    """A row for the bundles surface: title, date, member count.

    ``  India 2024        11 Mar 2024   6 docs``. ``readiness`` (e.g. ``4/6 ready``)
    is appended for a bundle measured against a template.
    """
    row = Text()
    if glyphs.bundle:
        row.append(f"{glyphs.bundle} ", style="dim")
    row.append(bundle.title or bundle.slug)
    dated = _fmt(bundle.date) if bundle.date is not None else ""
    if dated:
        row.append(f"   {dated}", style="dim")
    row.append(f"   {count} doc{'' if count == 1 else 's'}", style="dim")
    if readiness:
        row.append(f"   {readiness}", style="dim")
    return row


# -- the three shapes --------------------------------------------------------


def _dense(
    view: DocumentView, *, superseded: bool, show_issue: bool, glyphs: GlyphSet
) -> Table:
    doc = view.document
    dim = _dim(superseded)
    name = Text(doc.name or doc.id, no_wrap=True, overflow="ellipsis", style=dim)

    right = Text(style=dim)
    _append_status(right, view, show_issue=show_issue, glyphs=glyphs, dim=dim)
    files = _file_glyphs(doc, glyphs)
    if files:
        if right.plain:
            right.append("  ")
        right.append(files, style=dim)

    # Explicit gap columns rather than pane padding: a Textual scroll container
    # draws its scrollbar *outside* its padding, so padding never separates the
    # content from the scrollbar — a trailing spacer column does.
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")  # name
    grid.add_column(width=_NAME_STATUS_GAP)  # gap
    grid.add_column(justify="right", no_wrap=True)  # status + files
    grid.add_column(width=_SCROLLBAR_GAP)  # gap before the scrollbar
    grid.add_row(name, "", right, "")
    return grid


def _compact(view: DocumentView, *, superseded: bool, glyphs: GlyphSet) -> Table:
    doc = view.document
    dim = _dim(superseded)
    glyph, style = _gutter(view.expiry, glyphs)
    gutter = Text(glyph or " ", style=_join(dim, style))
    name = Text(doc.name or doc.id, no_wrap=True, overflow="ellipsis", style=dim)
    # A status glyph in a fixed gutter column anchors every item at the left; the
    # name is ellipsized to one line so the collapsed list stays a tidy single
    # row per document (no wrapping when the detail pane is open).
    grid = Table.grid(expand=True)
    grid.add_column(width=2)
    grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    grid.add_row(gutter, name)
    return grid


def _multiline(
    view: DocumentView, *, superseded: bool, show_issue: bool, glyphs: GlyphSet
) -> Text:
    doc = view.document
    dim = _dim(superseded)
    row = Text(style=dim)
    marker = _marker(view.expiry, glyphs)
    if marker:
        row.append(f"{marker} ", style=_join(dim, _STATUS_STYLE[view.expiry]))
    row.append(doc.name or doc.id, style=dim)

    # Always emit the second line — a dim "—" when there's nothing to show — so
    # every row is at least two lines and the list keeps a steady rhythm (a name
    # that overflows the pane still wraps to more).
    meta = _meta_parts(view, show_issue=show_issue, glyphs=glyphs)
    line2 = "  ·  ".join(meta) if meta else "—"
    row.append(f"\n  {line2}", style=_join(dim, "dim"))
    return row


# -- pieces ------------------------------------------------------------------


def _append_status(
    text: Text,
    view: DocumentView,
    *,
    show_issue: bool,
    glyphs: GlyphSet,
    dim: str,
) -> None:
    """Append ``[marker ]label date`` (styled by expiry) to ``text`` in place."""
    style = _join(dim, _STATUS_STYLE[view.expiry])
    marker = _marker(view.expiry, glyphs)
    if marker:
        text.append(f"{marker} ", style=style)
    dated = _date_str(view.document, show_issue)
    if dated:
        text.append(dated, style=style)


def _meta_parts(view: DocumentView, *, show_issue: bool, glyphs: GlyphSet) -> list[str]:
    doc = view.document
    parts: list[str] = []
    dated = _date_str(doc, show_issue)
    if dated:
        parts.append(dated)
    slot = _slot_str(doc)
    if slot:
        parts.append(f"slot {slot}")
    if doc.tags:
        parts.append(" ".join(doc.tags))
    files = _file_glyphs(doc, glyphs)
    if files:
        parts.append(files)
    if view.file is FileStatus.MISSING:
        parts.append("file missing")
    return parts


def _date_str(doc: Document, show_issue: bool) -> str:
    when = doc.issue_date if show_issue else doc.expiry_date
    label = "iss" if show_issue else "exp"
    return f"{label} {_fmt(when)}" if when is not None else ""


def _fmt(when: date) -> str:
    return when.strftime("%d %b %y")


def _marker(status: ExpiryStatus, glyphs: GlyphSet) -> str:
    return {
        ExpiryStatus.EXPIRED: glyphs.expired,
        ExpiryStatus.EXPIRING: glyphs.expiring,
    }.get(status, "")


def _gutter(status: ExpiryStatus, glyphs: GlyphSet) -> tuple[str, str]:
    """The compact-row gutter glyph + style for a status — one per state, so
    every item is anchored (⚠ attention, check ok, dim dot no-expiry)."""
    glyph = {
        ExpiryStatus.EXPIRED: glyphs.expired,
        ExpiryStatus.EXPIRING: glyphs.expiring,
        ExpiryStatus.OK: glyphs.ok,
        ExpiryStatus.NONE: glyphs.neutral,
    }[status]
    return glyph, _STATUS_STYLE[status] or "dim"


def _file_glyphs(doc: Document, glyphs: GlyphSet) -> str:
    physical = glyphs.physical if doc.has_physical else ""
    digital = glyphs.digital if doc.has_digital else ""
    return " ".join(part for part in (physical, digital) if part)


def _slot_str(doc: Document) -> str:
    slot = doc.effective_slot
    if slot is None:
        return ""
    sub = doc.effective_subslot
    return f"{slot}.{sub}" if sub is not None else str(slot)


def _dim(superseded: bool) -> str:
    return "dim" if superseded else ""


def _join(*styles: str) -> str:
    """Combine Rich style fragments, dropping empties (``"dim" + "bold red"``)."""
    return " ".join(s for s in styles if s)
