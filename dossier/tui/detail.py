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

"""Pure Rich renderer for the detail pane (the third Miller column).

Like :mod:`dossier.tui.rows`, this is widget-free and Console-testable: it takes
a :class:`~dossier.query.DocumentView` plus the resolved context the home screen
gathers (location label, supersession chain, the doc that supersedes this one)
and returns a Rich renderable the pane drops into a ``Static``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from rich.console import Group, RenderableType
from rich.rule import Rule
from rich.text import Text

from dossier.model import Document, ExpiryStatus, FileStatus, SuggestedField, Suggestion
from dossier.query import DocumentView
from dossier.tui.glyphs import ASCII, GlyphSet

_FIELD_LABEL = {
    SuggestedField.ISSUE: "issue date",
    SuggestedField.EXPIRY: "expiry date",
    SuggestedField.NOTES: "period",
}

_EXPIRY_STYLE = {
    ExpiryStatus.EXPIRED: "bold red",
    ExpiryStatus.EXPIRING: "yellow",
    ExpiryStatus.OK: "green",
    ExpiryStatus.NONE: "dim",
}


def render_detail(
    view: DocumentView,
    *,
    location_label: str | None,
    chain: list[Document],
    superseded_by: Document | None,
    suggestions: Sequence[Suggestion] = (),
    glyphs: GlyphSet = ASCII,
) -> RenderableType:
    """Render the full detail of one document.

    ``location_label`` is the effective location already resolved to a title +
    slot; ``chain`` is the documents this one supersedes (newest replaced first);
    ``superseded_by`` is the newer document that replaced this one, if any;
    ``suggestions`` are the live field suggestions (a dim "press e to review" hint).
    """
    doc = view.document
    header = Text(doc.name or doc.id, style="bold", overflow="fold")

    facts = Text()
    if superseded_by is not None:
        _icon(facts, glyphs.link)
        facts.append("superseded by ", style="dim")
        facts.append(f"{superseded_by.name or superseded_by.id}\n", style="yellow")
    _field(facts, glyphs.location, "Location", location_label or "—")
    _field(facts, glyphs.calendar, "Issued", _fmt(doc.issue_date))
    _field(facts, glyphs.calendar, "Expires", _expiry_value(view))
    if doc.ignore_expiry:
        facts.append("expiry ignored\n", style="dim")
    _field(facts, glyphs.tag, "Tags", " ".join(doc.tags) or "—")
    _field(facts, glyphs.bundle, "Bundles", " ".join(doc.bundles) or "—")
    _field(facts, glyphs.physical, "Copies", _copies(doc))

    parts: list[RenderableType] = [header, Rule(style="dim"), facts]

    files = _files(view, glyphs)
    if files is not None:
        parts.append(files)
    if chain:
        parts.append(_chain(chain, glyphs))
    if suggestions:
        parts.append(_suggestions(suggestions, glyphs))
    if doc.notes:
        parts.append(Rule(style="dim"))
        notes = Text()
        _icon(notes, glyphs.note)
        notes.append(doc.notes)
        parts.append(notes)
    return Group(*parts)


def _icon(text: Text, icon: str) -> None:
    """Append a leading ``icon`` + space (nothing when the icon is empty/ASCII)."""
    if icon:
        text.append(f"{icon} ", style="dim")


def _field(text: Text, icon: str, label: str, value: str) -> None:
    _icon(text, icon)
    text.append(f"{label}: ", style="bold")
    text.append(f"{value}\n")


def _expiry_value(view: DocumentView) -> str:
    doc = view.document
    if doc.expiry_date is None:
        return "—"
    return f"{_fmt(doc.expiry_date)}  ({view.expiry.value})"


def _copies(doc: Document) -> str:
    marks = []
    if doc.has_physical:
        marks.append("physical")
    if doc.has_digital:
        marks.append("digital")
    return " + ".join(marks) or "—"


def _files(view: DocumentView, glyphs: GlyphSet) -> RenderableType | None:
    doc = view.document
    if not doc.files:
        return Text("No digital file linked", style="dim")
    body = Text()
    _icon(body, glyphs.digital)
    body.append("Files\n", style="bold")
    for rendition in doc.files:
        mark = glyphs.primary if rendition.primary else " "
        body.append(f" {mark} ")
        body.append(rendition.label or "file")
        body.append(f"  {rendition.path}\n", style="dim")
    if view.file is FileStatus.MISSING:
        body.append("a linked file is missing on disk\n", style="bold red")
    return body


def _suggestions(suggestions: Sequence[Suggestion], glyphs: GlyphSet) -> RenderableType:
    body = Text()
    _icon(body, glyphs.note)
    body.append(f"Suggestions ({len(suggestions)})", style="bold")
    body.append("  a accept · e review\n", style="dim")
    for suggestion in suggestions:
        label = _FIELD_LABEL[suggestion.field]
        body.append(f"  {label}: ", style="dim")
        body.append(" / ".join(suggestion.values), style="yellow")
        body.append(f"  · from {suggestion.source}\n", style="dim")
    return body


def _chain(chain: list[Document], glyphs: GlyphSet) -> RenderableType:
    body = Text()
    _icon(body, glyphs.link)
    body.append("Supersedes\n", style="bold")
    for doc in chain:
        body.append("  ← ", style="dim")
        body.append(f"{doc.name or doc.id}\n", style="dim")
    return body


def _fmt(when: date | None) -> str:
    return when.strftime("%d %b %Y") if when is not None else "—"
