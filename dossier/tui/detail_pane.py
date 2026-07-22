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

"""The third Miller column as a widget: a document's detail, read now / edit later.

A :class:`~textual.widgets.ContentSwitcher` frames a read view — the pure
:func:`dossier.tui.detail.render_detail` dropped into a ``Static`` — and, from a
later slice, an edit form. This slice is read-only: it wraps the exact rendering
the home screen used to do inline, keeping ``id="detail"`` so every responsive
``#detail`` CSS rule and the existing focus handling are unchanged.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import ContentSwitcher, Static

from dossier.model import Document
from dossier.query import DocumentView
from dossier.store import Store
from dossier.tui import detail
from dossier.tui.glyphs import GlyphSet

_READ = "detail-body"


class DetailPane(VerticalScroll):
    """Detail of the selected document. Read-only for now; editable in a later PR."""

    def __init__(
        self, store: Store, *, glyphs: GlyphSet, id: str | None = None
    ) -> None:
        super().__init__(id=id)
        self._store = store
        self._glyphs = glyphs
        self.can_focus = True

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial=_READ):
            yield Static(id=_READ)

    def show_document(
        self,
        view: DocumentView,
        *,
        location_label: str | None,
        chain: list[Document],
        superseded_by: Document | None,
    ) -> None:
        """Render one document into the read view."""
        self.query_one(f"#{_READ}", Static).update(
            detail.render_detail(
                view,
                location_label=location_label,
                chain=chain,
                superseded_by=superseded_by,
                glyphs=self._glyphs,
            )
        )

    def clear(self) -> None:
        """Blank the read view (no document selected)."""
        self.query_one(f"#{_READ}", Static).update("")
