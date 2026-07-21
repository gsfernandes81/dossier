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

"""The home screen: a Miller-columns browser (locations │ documents).

One screen, every layout mode is a CSS class toggle rather than a different
screen (DESIGN §14):

* Width bands come from :attr:`Screen.HORIZONTAL_BREAKPOINTS` — ``-narrow`` (<60
  cols) shows one pane at a time and drills with ``→``/``←``; ``-medium`` and
  ``-wide`` show both side by side.
* ``-portrait`` (taller than wide, e.g. a phone) is set from :meth:`on_resize`
  and, together with ``-narrow``, switches document rows to their multi-line
  shape.
* ``searching`` (a non-empty query or the expiring filter) hides the locations
  pane and shows a flat, root-wide result list.

The detail pane, the bottom command bar, and the touch action bar arrive in
later slices; here ``Enter`` opens a document's file and search stays docked top.
"""

from __future__ import annotations

from datetime import date

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal
from textual.events import Resize
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, OptionList
from textual.widgets.option_list import Option

from dossier import query
from dossier.config import Config
from dossier.model import Document, ExpiryStatus, Location
from dossier.platform_open import OpenError, open_file
from dossier.store import Store
from dossier.tui import rows
from dossier.tui.rows import RowMode
from dossier.tui.screens import DetailScreen, DoctorScreen, MoveScreen

# Sentinel option ids for the two synthetic locations-pane rows (real location
# slugs are kebab-case, so a NUL prefix can never collide with one).
_ALL = "\x00all"
_UNLOCATED = "\x00none"

# Below this many columns the panes stop sharing the screen; matches the
# ``-narrow`` breakpoint so pane collapse and row density agree.
_NARROW_COLS = 60


class HomeScreen(Screen[None]):
    """Browse, search, and open documents; the app's default screen."""

    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (_NARROW_COLS, "-medium"), (100, "-wide")]

    # Unscoped so the responsive rules can key off this screen's own mode classes
    # (`HomeScreen.-narrow #documents`); Textual's default scoping rewrites such
    # self-type selectors so they never match.
    SCOPED_CSS = False

    DEFAULT_CSS = """
    #search { dock: top; margin: 0 1; }
    #panes { height: 1fr; }
    #locations { width: 30; border-right: solid $panel; }
    #documents { width: 1fr; }

    /* Narrow: one pane at a time, drilled with the arrow keys. */
    HomeScreen.-narrow #locations { width: 1fr; border-right: none; }
    HomeScreen.-narrow #documents { display: none; }
    HomeScreen.-narrow.show-documents #locations { display: none; }
    HomeScreen.-narrow.show-documents #documents { display: block; }

    /* Searching: flat root-wide results, no location scoping. Ordered last so it
       wins over the narrow rule and keeps the results visible. */
    HomeScreen.searching #locations { display: none; }
    HomeScreen.searching #documents { display: block; width: 1fr; }
    """

    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
        Binding("escape", "escape", "Back"),
        Binding("right", "drill_in", "Open", show=False),
        Binding("left", "drill_out", "Back", show=False),
        Binding("e", "edit", "Edit"),
        Binding("n", "new", "New"),
        Binding("m", "move", "Move"),
        Binding("d", "doctor", "Doctor"),
        Binding("x", "toggle_expiring", "Expiring"),
    ]

    def __init__(self, store: Store, config: Config, *, today: date) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._today = today
        self._docs: list[Document] = []
        self._locations: dict[str, Location] = {}
        self._by_location: dict[str | None, list[Document]] = {}
        self._selection: str = _ALL
        self._filter_text = ""
        self._expiring_only = False
        self._row_mode = RowMode.DENSE

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="Search name / tags / notes…", id="search")
        with Horizontal(id="panes"):
            yield OptionList(id="locations")
            yield OptionList(id="documents")
        yield Footer()

    def on_mount(self) -> None:
        self._reload()
        self._focus_default()

    # -- data ----------------------------------------------------------------

    def _reload(self) -> None:
        self._docs = self._store.load_all()
        self._locations = self._store.load_locations()
        self._by_location = dict(query.group_by_location(self._docs))
        self._refresh_locations()
        self._refresh_documents()

    def visible_docs(self) -> list[Document]:
        """Documents passing the current search + expiring filter (unscoped)."""
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

    def documents_in_view(self) -> list[Document]:
        """The documents the middle pane currently shows, in display order."""
        if self._is_searching():
            return query.sort_documents(self.visible_docs())
        if self._selection == _ALL:
            return query.sort_documents(self._docs)
        if self._selection == _UNLOCATED:
            return self._by_location.get(None, [])
        return self._by_location.get(self._selection, [])

    def _is_searching(self) -> bool:
        return bool(self._filter_text) or self._expiring_only

    # -- rendering -----------------------------------------------------------

    def _refresh_locations(self) -> None:
        options = self.query_one("#locations", OptionList)
        options.clear_options()
        options.add_option(Option(_loc_label("All", len(self._docs)), id=_ALL))
        for loc, group in self._by_location.items():
            if loc is None:
                label = _loc_label("— no location —", len(group))
                options.add_option(Option(label, id=_UNLOCATED))
            else:
                title = self._locations[loc].title if loc in self._locations else loc
                options.add_option(Option(_loc_label(title, len(group)), id=loc))

    def _refresh_documents(self) -> None:
        options = self.query_one("#documents", OptionList)
        options.clear_options()
        docs = self.documents_in_view()
        superseded = query.superseded_ids(self._docs)
        for doc in docs:
            view = query.view(
                doc,
                root=self._config.syncthing_root,
                today=self._today,
                threshold_days=self._config.expiry_threshold_days,
            )
            row = rows.doc_row(
                view, mode=self._row_mode, superseded=doc.id in superseded
            )
            options.add_option(Option(row, id=doc.id))
        self.app.sub_title = f"{len(docs)} / {len(self._docs)} documents"

    # -- selection & opening -------------------------------------------------

    def select_location(self, selection: str) -> None:
        """Scope the documents pane to a locations-pane row (a slug or sentinel)."""
        self._selection = selection
        self._refresh_documents()

    def open_document(self, doc_id: str) -> None:
        """Open a document's primary rendition with the platform opener."""
        doc = self._doc_by_id(doc_id)
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

    # -- events --------------------------------------------------------------

    def on_resize(self, event: Resize) -> None:
        size = event.size
        self.set_class(size.height > size.width, "-portrait")
        narrow = size.width < _NARROW_COLS or size.height > size.width
        mode = RowMode.MULTILINE if narrow else RowMode.DENSE
        if mode != self._row_mode:
            self._row_mode = mode
            self._refresh_documents()
        self._ensure_focus_visible()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._filter_text = event.value
            self._update_searching()
            self._refresh_documents()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search":
            self._focus_documents()

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id == "locations" and event.option_id is not None:
            self.select_location(event.option_id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "documents" and event.option_id is not None:
            self.open_document(event.option_id)
        elif event.option_list.id == "locations":
            self.action_drill_in()

    # -- actions -------------------------------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_escape(self) -> None:
        search = self.query_one("#search", Input)
        if search.value or self.has_class("searching"):
            search.value = ""
            self._filter_text = ""
            self._update_searching()
            self._refresh_documents()
            self._focus_documents()
        elif self.has_class("-narrow") and self.has_class("show-documents"):
            self.action_drill_out()

    def action_drill_in(self) -> None:
        if self.has_class("-narrow"):
            self.add_class("show-documents")
        self._focus_documents()

    def action_drill_out(self) -> None:
        if self.has_class("-narrow"):
            self.remove_class("show-documents")
        self.query_one("#locations", OptionList).focus()

    def action_toggle_expiring(self) -> None:
        self._expiring_only = not self._expiring_only
        self._update_searching()
        self._refresh_documents()

    def action_edit(self) -> None:
        doc = self._highlighted_doc()
        if doc is not None:
            self.app.push_screen(DetailScreen(self._store, doc), self._after_edit)

    def action_new(self) -> None:
        screen = DetailScreen(self._store, Document(), is_new=True)
        self.app.push_screen(screen, self._after_edit)

    def action_move(self) -> None:
        doc = self._highlighted_doc()
        if doc is not None:
            self.app.push_screen(
                MoveScreen(self._store, self._docs, doc), self._after_edit
            )

    def action_doctor(self) -> None:
        self.app.push_screen(
            DoctorScreen(self._store, self._config), self._after_doctor
        )

    # -- helpers -------------------------------------------------------------

    def _update_searching(self) -> None:
        self.set_class(self._is_searching(), "searching")

    def _focus_documents(self) -> None:
        self.query_one("#documents", OptionList).focus()

    def _focus_default(self) -> None:
        documents = self.query_one("#documents", OptionList)
        (documents if documents.display else self.query_one("#locations")).focus()

    def _ensure_focus_visible(self) -> None:
        focused = self.app.focused
        if focused is not None and focused.display:
            return
        for selector in ("#documents", "#locations", "#search"):
            widget = self.query_one(selector)
            if widget.display:
                widget.focus()
                return

    def _highlighted_doc(self) -> Document | None:
        options = self.query_one("#documents", OptionList)
        index = options.highlighted
        if index is None:
            return None
        option = options.get_option_at_index(index)
        if option.id is None:
            return None
        return self._doc_by_id(option.id)

    def _doc_by_id(self, doc_id: str) -> Document | None:
        return next((d for d in self._docs if d.id == doc_id), None)

    def _after_edit(self, saved: bool | None) -> None:
        if saved:
            self._reload()

    def _after_doctor(self, doc_id: str | None) -> None:
        if doc_id is None:
            return
        doc = self._doc_by_id(doc_id)
        if doc is not None:
            self.app.push_screen(DetailScreen(self._store, doc), self._after_edit)


def _loc_label(title: str, count: int) -> Text:
    label = Text(title, no_wrap=True, overflow="ellipsis")
    label.append(f"  {count}", style="dim")
    return label
