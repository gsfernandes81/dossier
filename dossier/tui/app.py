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

"""Narrow-first Textual app: a location-grouped, searchable document list.

Rows come from :mod:`dossier.tui.rows` (two-line ``MULTILINE`` shape here, so it
fits a portrait phone terminal). ASCII status glyphs avoid emoji width issues.
"""

from __future__ import annotations

from datetime import date

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Input, OptionList
from textual.widgets.option_list import Option

from dossier import query
from dossier.config import Config
from dossier.model import Document, ExpiryStatus
from dossier.platform_open import OpenError, open_file
from dossier.store import Store
from dossier.tui import rows
from dossier.tui.screens import DetailScreen, DoctorScreen, MoveScreen


class DossierApp(App[None]):
    """Browse, search, and open documents from the store."""

    TITLE = "dossier"
    CSS = """
    #search { dock: top; margin: 0 1; }
    OptionList { height: 1fr; }
    """
    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
        Binding("escape", "clear_search", "Clear"),
        Binding("e", "edit_selected", "Edit"),
        Binding("n", "new_document", "New"),
        Binding("m", "move_selected", "Move"),
        Binding("d", "doctor", "Doctor"),
        Binding("x", "toggle_expiring", "Expiring"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(
        self, store: Store, config: Config, *, today: date | None = None
    ) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._today = today or date.today()
        self._docs: list[Document] = []
        self._filter_text = ""
        self._expiring_only = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search name / tags / notes…", id="search")
        yield OptionList(id="list")
        yield Footer()

    def on_mount(self) -> None:
        self._docs = self._store.load_all()
        self._refresh_list()

    # -- data ----------------------------------------------------------------

    def visible_docs(self) -> list[Document]:
        """Documents passing the current search + expiring toggle."""
        expiry = (
            (ExpiryStatus.EXPIRED, ExpiryStatus.EXPIRING) if self._expiring_only else ()
        )
        flt = query.Filter(text=self._filter_text, expiry=expiry)
        return query.search(
            self._docs,
            flt,
            today=self._today,
            threshold_days=self._config.expiry_threshold_days,
        )

    def open_document(self, doc_id: str) -> None:
        """Open a document's primary rendition with the platform opener."""
        doc = next((d for d in self._docs if d.id == doc_id), None)
        if doc is None:
            return
        rendition = doc.primary_rendition()
        if rendition is None:
            self.notify(f"{doc.name}: no digital file linked", severity="warning")
            return
        path = query.resolve_path(self._config.syncthing_root, rendition.path)
        if not path.exists():
            self.notify(f"file not found: {path}", severity="error")
            return
        try:
            open_file(path)
        except OpenError as exc:
            self.notify(str(exc), severity="error")
        else:
            self.notify(f"opened {doc.name}")

    # -- rendering -----------------------------------------------------------

    def _refresh_list(self) -> None:
        option_list = self.query_one("#list", OptionList)
        option_list.clear_options()
        docs = self.visible_docs()
        superseded = query.superseded_ids(self._docs)
        for location, group in query.group_by_location(docs):
            option_list.add_option(Option(_header(location), id=None))
            for doc in group:
                view = query.view(
                    doc,
                    root=self._config.syncthing_root,
                    today=self._today,
                    threshold_days=self._config.expiry_threshold_days,
                )
                row = rows.doc_row(
                    view, mode=rows.RowMode.MULTILINE, superseded=doc.id in superseded
                )
                option_list.add_option(Option(row, id=doc.id))
        self.sub_title = f"{len(docs)} / {len(self._docs)} documents"

    # -- actions -------------------------------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_clear_search(self) -> None:
        self.query_one("#search", Input).value = ""
        self._filter_text = ""
        self._refresh_list()
        self.query_one("#list", OptionList).focus()

    def action_toggle_expiring(self) -> None:
        self._expiring_only = not self._expiring_only
        self._refresh_list()

    def action_edit_selected(self) -> None:
        doc = self._highlighted_doc()
        if doc is not None:
            self.push_screen(DetailScreen(self._store, doc), self._after_edit)

    def action_new_document(self) -> None:
        screen = DetailScreen(self._store, Document(), is_new=True)
        self.push_screen(screen, self._after_edit)

    def action_move_selected(self) -> None:
        doc = self._highlighted_doc()
        if doc is not None:
            screen = MoveScreen(self._store, self._docs, doc)
            self.push_screen(screen, self._after_edit)

    def action_doctor(self) -> None:
        self.push_screen(DoctorScreen(self._store, self._config), self._after_doctor)

    def _highlighted_doc(self) -> Document | None:
        option_list = self.query_one("#list", OptionList)
        index = option_list.highlighted
        if index is None:
            return None
        option = option_list.get_option_at_index(index)
        if option.id is None:
            return None
        return self._doc_by_id(option.id)

    def _doc_by_id(self, doc_id: str) -> Document | None:
        return next((d for d in self._docs if d.id == doc_id), None)

    def _after_edit(self, saved: bool | None) -> None:
        if saved:
            self._docs = self._store.load_all()
            self._refresh_list()

    def _after_doctor(self, doc_id: str | None) -> None:
        if doc_id is None:
            return
        doc = self._doc_by_id(doc_id)
        if doc is not None:
            self.push_screen(DetailScreen(self._store, doc), self._after_edit)

    # -- events --------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._filter_text = event.value
            self._refresh_list()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is not None:
            self.open_document(event.option_id)


def _header(location: str | None) -> Text:
    return Text(location or "— no location —", style="bold underline")
