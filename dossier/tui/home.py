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

"""The home screen: a Miller-columns browser (locations │ documents │ detail).

One screen, every layout mode is a CSS class toggle rather than a different
screen (DESIGN §14):

* Width bands come from :attr:`Screen.HORIZONTAL_BREAKPOINTS` — ``-narrow`` (<60
  cols) shows one pane at a time and drills with ``→``/``←``; ``-medium`` and
  ``-wide`` show more side by side.
* ``-portrait`` (taller than wide, e.g. a phone) is set from :meth:`on_resize`
  and, with ``-narrow``, switches document rows to their multi-line shape.
* ``show-detail`` opens the third column: the documents rows collapse to names
  (keeping a one-char expiry cue); at medium width the *locations* column drops
  rather than shrinking everything; in narrow/portrait the detail goes
  full-screen.
* ``searching`` (a non-empty query or the expiring filter) hides the locations
  pane and shows a flat, root-wide result list.

``Enter`` opens the detail pane for a document; ``o`` opens its file from
anywhere. The search box is docked at the bottom as a thumb-reachable command
bar (``/`` focuses it); typing collapses the panes to a flat root-wide result
list. The touch action bar arrives in a later slice.
"""

from __future__ import annotations

from datetime import date

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, OptionList, Static
from textual.widgets.option_list import Option

from dossier import query
from dossier.config import Config
from dossier.model import Document, ExpiryStatus, Location
from dossier.platform_open import OpenError, open_file
from dossier.store import Store
from dossier.tui import detail, glyphs, rows
from dossier.tui.rows import RowMode
from dossier.tui.screens import (
    BundleScreen,
    DetailScreen,
    DoctorScreen,
    MoveScreen,
    SupersedeScreen,
)

# Sentinel option ids for the two synthetic locations-pane rows (real location
# slugs are kebab-case, so a NUL prefix can never collide with one).
_ALL = "\x00all"
_UNLOCATED = "\x00none"

# Below this many columns the panes stop sharing the screen; matches the
# ``-narrow`` breakpoint so pane collapse and row density agree.
_NARROW_COLS = 60


class HomeScreen(Screen[None]):
    """Browse, search, open, and inspect documents; the app's default screen."""

    HORIZONTAL_BREAKPOINTS = [(0, "-narrow"), (_NARROW_COLS, "-medium"), (100, "-wide")]

    # Unscoped so the responsive rules can key off this screen's own mode classes
    # (`HomeScreen.-narrow #documents`); Textual's default scoping rewrites such
    # self-type selectors so they never match. Note: a Screen's own styles must
    # live in DEFAULT_CSS — a `CSS` classvar on the app's default screen is
    # silently dropped.
    SCOPED_CSS = False

    DEFAULT_CSS = """
    #bottombar { dock: bottom; height: 2; }
    #actionbar { display: none; height: 3; width: 1fr; }
    #actionbar Button { width: 1fr; min-width: 4; margin: 0; border: none; height: 3; }
    #search { height: 1; border: none; padding: 0 1; background: $panel; }
    #panes { height: 1fr; }

    /* Touch (Termux): a tap action row above the command bar. */
    HomeScreen.touch #bottombar { height: 5; }
    HomeScreen.touch #actionbar { display: block; }
    #locations { width: 30; border-right: solid $panel; }
    #documents { width: 1fr; }  /* scrollbar gap comes from the row's spacer column */
    /* max-width caps the detail column on wide terminals so it stops hogging
       the (often sparse) right third; the surplus goes to the documents pane. */
    #detail {
        display: none; width: 2fr; max-width: 60;
        padding: 0 1; border-left: solid $panel;
    }

    /* Narrow: one pane at a time, drilled with the arrow keys. */
    HomeScreen.-narrow #locations { width: 1fr; border-right: none; }
    HomeScreen.-narrow #documents { display: none; }
    HomeScreen.-narrow.show-documents #locations { display: none; }
    HomeScreen.-narrow.show-documents #documents { display: block; }

    /* Detail open: reveal the third column. At medium width drop the locations
       column instead of shrinking; in narrow the detail takes the whole screen. */
    HomeScreen.show-detail #detail { display: block; }
    HomeScreen.-medium.show-detail #locations { display: none; }
    HomeScreen.-narrow.show-detail #locations { display: none; }
    HomeScreen.-narrow.show-detail #documents { display: none; }
    HomeScreen.-narrow.show-detail #detail { width: 1fr; border-left: none; }

    /* Searching: flat root-wide results, no location scoping or detail. Ordered
       last so it wins over the narrow/detail rules and keeps results visible. */
    HomeScreen.searching #locations { display: none; }
    HomeScreen.searching #detail { display: none; }
    HomeScreen.searching #documents { display: block; width: 1fr; }
    """

    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
        Binding("escape", "escape", "Back"),
        Binding("right", "drill_in", "Detail", show=False),
        Binding("left", "drill_out", "Back", show=False),
        Binding("o", "open_file", "Open"),
        Binding("i", "toggle_dates", "Iss/Exp"),
        Binding("b", "bundle", "Bundle"),
        Binding("e", "edit", "Edit"),
        Binding("n", "new", "New"),
        Binding("m", "move", "Move"),
        Binding("s", "supersede", "Supersede"),
        Binding("d", "doctor", "Doctor"),
        Binding("x", "toggle_expiring", "Expiring"),
    ]

    def __init__(
        self, store: Store, config: Config, *, today: date, touch: bool = False
    ) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._today = today
        self._touch = touch
        self._glyphs = glyphs.resolve(config.glyphs)
        self._docs: list[Document] = []
        self._locations: dict[str, Location] = {}
        self._by_location: dict[str | None, list[Document]] = {}
        self._selection: str = _ALL
        self._filter_text = ""
        self._expiring_only = False
        self._show_detail = False
        self._show_issue = False
        self._detail_id: str | None = None
        self._narrow = False
        self._portrait = False
        self._last_mode = RowMode.DENSE

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="panes"):
            yield OptionList(id="locations")
            yield OptionList(id="documents")
            with VerticalScroll(id="detail"):
                yield Static(id="detail-body")
        # A fixed-height bottom bar reserves the space for the (touch-only)
        # action row, the command line, and the footer so they stack cleanly
        # (docking them directly onto the screen lets the footer overlap).
        g = self._glyphs
        with Vertical(id="bottombar"):
            with Horizontal(id="actionbar"):
                yield Button(_btn_label(g.open, "Open"), id="act-open")
                yield Button(_btn_label(g.bundle, "Bundle"), id="act-bundle")
                yield Button(_btn_label(g.new, "New"), id="act-new")
                yield Button(g.keyboard or "Key", id="act-kbd")
            yield Input(placeholder="Search name / tags / notes…", id="search")
            yield Footer()

    def on_mount(self) -> None:
        self.query_one("#detail", VerticalScroll).can_focus = True
        self.set_class(self._touch, "touch")
        self._reload()
        self._focus_default()

    # -- data ----------------------------------------------------------------

    def _reload(self) -> None:
        self._docs = self._store.load_all()
        self._locations = self._store.load_locations()
        self._by_location = dict(query.group_by_location(self._docs))
        self._refresh_locations()
        self._refresh_documents()
        if self._show_detail:
            self._update_detail()

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

    def _row_mode(self) -> RowMode:
        if self._narrow or self._portrait:
            return RowMode.MULTILINE
        # Search shows a flat root-wide list, so use full rows even with the
        # detail pane still nominally open (it is hidden while searching).
        if self._show_detail and not self._is_searching():
            return RowMode.COMPACT  # collapsed to names beside the detail pane
        return RowMode.DENSE

    # -- rendering -----------------------------------------------------------

    def _refresh_locations(self) -> None:
        options = self.query_one("#locations", OptionList)
        options.clear_options()
        g = self._glyphs
        options.add_option(Option(_loc_label(g.inbox, "All", len(self._docs)), id=_ALL))
        for loc, group in self._by_location.items():
            if loc is None:
                label = _loc_label(g.unlocated, "— no location —", len(group))
                options.add_option(Option(label, id=_UNLOCATED))
            else:
                title = self._locations[loc].title if loc in self._locations else loc
                options.add_option(
                    Option(_loc_label(g.folder, title, len(group)), id=loc)
                )

    def _refresh_documents(self) -> None:
        options = self.query_one("#documents", OptionList)
        previous = _highlighted_id(options)
        options.clear_options()
        docs = self.documents_in_view()
        superseded = query.superseded_ids(self._docs)
        mode = self._row_mode()
        self._last_mode = mode
        ids: list[str] = []
        for doc in docs:
            view = self._view(doc)
            options.add_option(
                Option(
                    rows.doc_row(
                        view,
                        mode=mode,
                        superseded=doc.id in superseded,
                        show_issue=self._show_issue,
                        glyphs=self._glyphs,
                    ),
                    id=doc.id,
                )
            )
            ids.append(doc.id)
        if previous is not None and previous in ids:
            options.highlighted = ids.index(previous)
        self.app.sub_title = f"{len(docs)} / {len(self._docs)} documents"

    def _update_detail(self) -> None:
        body = self.query_one("#detail-body", Static)
        doc = self._doc_by_id(self._detail_id) if self._detail_id else None
        if doc is None:
            body.update("")
            return
        body.update(
            detail.render_detail(
                self._view(doc),
                location_label=self._location_label(doc),
                chain=query.supersession_chain(self._docs, doc),
                superseded_by=self._superseded_by(doc),
                glyphs=self._glyphs,
            )
        )

    def _view(self, doc: Document) -> query.DocumentView:
        return query.view(
            doc,
            root=self._config.syncthing_root,
            today=self._today,
            threshold_days=self._config.expiry_threshold_days,
        )

    # -- selection, detail & opening -----------------------------------------

    def select_location(self, selection: str) -> None:
        """Scope the documents pane to a locations-pane row (a slug or sentinel)."""
        self._selection = selection
        self._refresh_documents()

    def open_detail(self, doc_id: str) -> None:
        """Reveal the detail pane for ``doc_id`` (Enter / drill right)."""
        self._detail_id = doc_id
        first_open = not self._show_detail
        self._show_detail = True
        self.set_class(True, "show-detail")
        if first_open:
            self._refresh_documents()  # rows collapse to their compact shape
        self._update_detail()
        if self._narrow:
            self.query_one("#detail", VerticalScroll).focus()

    def close_detail(self) -> None:
        if not self._show_detail:
            return
        self._show_detail = False
        self.set_class(False, "show-detail")
        self._refresh_documents()
        self._focus_documents()

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
        self._portrait = size.height > size.width
        self._narrow = size.width < _NARROW_COLS
        self.set_class(self._portrait, "-portrait")
        if self._row_mode() != self._last_mode:
            self._refresh_documents()
        self._ensure_focus_visible()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._filter_text = event.value
            self._update_searching()
            self._refresh_documents()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "search":
            self._set_mouse_reporting(True)
            self._focus_documents()

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id == "locations" and event.option_id is not None:
            self.select_location(event.option_id)
        elif event.option_list.id == "documents":
            self._detail_id = event.option_id
            if self._show_detail:
                self._update_detail()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "documents" and event.option_id is not None:
            self.open_detail(event.option_id)
        elif event.option_list.id == "locations":
            self.action_drill_in()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "act-open":
            self.action_open_file()
        elif event.button.id == "act-bundle":
            self.action_bundle()
        elif event.button.id == "act-new":
            self.action_new()
        elif event.button.id == "act-kbd":
            self._raise_keyboard()

    # -- actions -------------------------------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_escape(self) -> None:
        search = self.query_one("#search", Input)
        if search.value or self.has_class("searching") or self.app.focused is search:
            self._set_mouse_reporting(True)
            search.value = ""
            self._filter_text = ""
            self._update_searching()
            self._refresh_documents()
            self._focus_documents()
        elif self._show_detail:
            self.close_detail()
        elif self._narrow and self.has_class("show-documents"):
            self.action_drill_out()

    def action_drill_in(self) -> None:
        locations = self.query_one("#locations", OptionList)
        if self.app.focused is locations:
            if self._narrow:
                self.add_class("show-documents")
            self._focus_documents()
            return
        doc = self._highlighted_doc()
        if doc is not None:
            self.open_detail(doc.id)

    def action_drill_out(self) -> None:
        if self._show_detail:
            self.close_detail()
        elif self._narrow and self.has_class("show-documents"):
            self.remove_class("show-documents")
            self.query_one("#locations", OptionList).focus()

    def action_open_file(self) -> None:
        doc = self._current_doc()
        if doc is not None:
            self.open_document(doc.id)

    def action_toggle_dates(self) -> None:
        self._show_issue = not self._show_issue
        self._refresh_documents()
        self.notify(f"showing {'issue' if self._show_issue else 'expiry'} dates")

    def action_supersede(self) -> None:
        doc = self._current_doc()
        if doc is not None:
            self.app.push_screen(
                SupersedeScreen(self._store, self._docs, doc), self._after_edit
            )

    def action_bundle(self) -> None:
        doc = self._current_doc()
        if doc is not None:
            self.app.push_screen(
                BundleScreen(self._store, self._docs, doc), self._after_edit
            )

    def action_toggle_expiring(self) -> None:
        self._expiring_only = not self._expiring_only
        self._update_searching()
        self._refresh_documents()

    def action_edit(self) -> None:
        doc = self._current_doc()
        if doc is not None:
            self.app.push_screen(DetailScreen(self._store, doc), self._after_edit)

    def action_new(self) -> None:
        screen = DetailScreen(self._store, Document(), is_new=True)
        self.app.push_screen(screen, self._after_edit)

    def action_move(self) -> None:
        doc = self._current_doc()
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

    def _raise_keyboard(self) -> None:
        # Termux only raises the soft keyboard while mouse tracking is off, and
        # the app can't summon the IME itself — so drop mouse reporting and focus
        # the command bar; the user's next tap on it brings the keyboard up.
        self.query_one("#search", Input).focus()
        self._set_mouse_reporting(False)

    def _set_mouse_reporting(self, enabled: bool) -> None:
        setter = getattr(self.app, "set_mouse_reporting", None)
        if callable(setter):
            setter(enabled)

    def _focus_documents(self) -> None:
        self.query_one("#documents", OptionList).focus()

    def _focus_default(self) -> None:
        documents = self.query_one("#documents", OptionList)
        (documents if documents.display else self.query_one("#locations")).focus()

    def _ensure_focus_visible(self) -> None:
        focused = self.app.focused
        if focused is not None and focused.display:
            return
        for selector in ("#documents", "#detail", "#locations", "#search"):
            widget = self.query_one(selector)
            if widget.display:
                widget.focus()
                return

    def _current_doc(self) -> Document | None:
        if self._show_detail and self._detail_id is not None:
            return self._doc_by_id(self._detail_id)
        return self._highlighted_doc()

    def _highlighted_doc(self) -> Document | None:
        options = self.query_one("#documents", OptionList)
        doc_id = _highlighted_id(options)
        return self._doc_by_id(doc_id) if doc_id is not None else None

    def _doc_by_id(self, doc_id: str) -> Document | None:
        return next((d for d in self._docs if d.id == doc_id), None)

    def _location_label(self, doc: Document) -> str | None:
        loc = doc.effective_location
        if loc is None:
            return None
        title = self._locations[loc].title if loc in self._locations else loc
        slot = doc.effective_slot
        if slot is not None:
            sub = doc.effective_subslot
            title += f" · {slot}.{sub}" if sub is not None else f" · {slot}"
        return f"{title} (carried)" if doc.is_temp_located else title

    def _superseded_by(self, doc: Document) -> Document | None:
        return next((d for d in self._docs if d.supersedes == doc.id), None)

    def _after_edit(self, saved: bool | None) -> None:
        if saved:
            self._reload()

    def _after_doctor(self, doc_id: str | None) -> None:
        if doc_id is None:
            return
        doc = self._doc_by_id(doc_id)
        if doc is not None:
            self.app.push_screen(DetailScreen(self._store, doc), self._after_edit)


def _highlighted_id(options: OptionList) -> str | None:
    index = options.highlighted
    if index is None:
        return None
    return options.get_option_at_index(index).id


def _loc_label(icon: str, title: str, count: int) -> Text:
    prefix = f"{icon}  " if icon else ""
    label = Text(f"{prefix}{title}", no_wrap=True, overflow="ellipsis")
    label.append(f"  {count}", style="dim")
    return label


def _btn_label(icon: str, text: str) -> str:
    return f"{icon}  {text}" if icon else text
