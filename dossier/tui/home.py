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
* ``searching`` (a non-empty query or the expiring filter) filters the documents
  pane in place, root-wide, keeping the Miller columns; the detail preview (when
  open) follows the highlighted result.

``Enter`` opens the detail pane for a document; ``o`` opens its file from
anywhere. The search box is docked at the bottom as a thumb-reachable command
bar (``/`` focuses it); typing filters the documents pane in place (root-wide),
keeping the columns. The touch action bar arrives in a later slice.
"""

from __future__ import annotations

from datetime import date

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.events import DescendantFocus, Resize
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, OptionList, TextArea
from textual.widgets.option_list import Option

from dossier import query, suggest
from dossier.config import Config
from dossier.model import Document, ExpiryStatus, Location, SuggestionState
from dossier.platform_open import OpenError, open_file
from dossier.store import Store
from dossier.tui import glyphs, rows
from dossier.tui.detail_pane import DetailPane
from dossier.tui.reconcile import ReconcileScreen
from dossier.tui.rows import RowMode
from dossier.tui.screens import (
    BundlesScreen,
    DoctorScreen,
    SupersedeScreen,
    WatchScreen,
)

# Sentinel option ids for the two synthetic locations-pane rows (real location
# slugs are kebab-case, so a NUL prefix can never collide with one).
_ALL = "\x00all"
_UNLOCATED = "\x00none"

# Below this many columns the panes stop sharing the screen; matches the
# ``-narrow`` breakpoint so pane collapse and row density agree.
_NARROW_COLS = 60

# Home actions suppressed while the detail pane is in edit mode, so a bare letter
# typed into a form Checkbox/SelectionList (which don't swallow it like an Input
# does) can't fire a home binding — and the footer stops advertising them.
_EDIT_LOCKED = frozenset(
    {
        "open_file",
        "toggle_dates",
        "bundle",
        "edit",
        "new",
        "accept_suggestion",
        "move",
        "supersede",
        "watch",
        "reconcile",
        "doctor",
        "bundles",
        "toggle_expiring",
        "focus_search",
        "drill_in",
        "drill_out",
    }
)


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
    #bottombar { dock: bottom; height: 4; }
    #actionbar {
        display: none; layout: grid; grid-rows: 5; grid-gutter: 0 1;
        height: auto; width: 1fr;
    }
    #actionbar Button {
        width: 1fr; min-width: 6; height: 5; content-align: center middle;
    }
    /* The search box reads as its own little command panel: a soft rounded
       edge at rest that brightens distinctly when focused (so it's obvious
       where typed text goes, on desktop and touch alike). */
    #search {
        height: 3; border: round $primary 40%; background: $panel; padding: 0 1;
    }
    #search:focus { border: round $accent; background: $boost; }
    #panes { height: 1fr; }

    /* Touch (Termux): a tap-action grid above the command bar — big thumb
       targets. Landscape lays the six actions 3-wide (2 rows); a tall portrait
       phone gets them 2-wide (3 rows). */
    HomeScreen.touch #bottombar { height: 14; }
    HomeScreen.touch.-portrait #bottombar { height: 19; }
    HomeScreen.touch #actionbar { display: block; grid-size: 3; }
    HomeScreen.touch.-portrait #actionbar { grid-size: 2; }
    /* Two-line location rows on touch: a bigger tap target per category and a
       far better fit for a tall phone screen than thin single lines. */
    HomeScreen.touch #locations { height: 1fr; }
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

    /* Searching filters the documents pane in place (root-wide); the Miller
       columns stay put. Only narrow needs help: front the documents pane so
       results are visible while typing. Ordered BEFORE the show-detail rules
       (same specificity) so drilling into a result still goes full-screen. */
    HomeScreen.-narrow.searching #locations { display: none; }
    HomeScreen.-narrow.searching #documents { display: block; }

    /* Detail open: reveal the third column. At medium width drop the locations
       column instead of shrinking; in narrow the detail takes the whole screen. */
    HomeScreen.show-detail #detail { display: block; }
    HomeScreen.-medium.show-detail #locations { display: none; }
    HomeScreen.-narrow.show-detail #locations { display: none; }
    HomeScreen.-narrow.show-detail #documents { display: none; }
    HomeScreen.-narrow.show-detail #detail { width: 1fr; border-left: none; }
    """

    # The Footer advertises only the high-frequency actions so it never overflows
    # (and silently clips keys) at medium width. The rest stay one keystroke away
    # and remain fully discoverable: `?` opens Textual's HelpPanel, which lists
    # `show=False` bindings too. Edit-mode gating (check_action) carries over to
    # both the footer and the panel automatically.
    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
        Binding("escape", "escape", "Back"),
        Binding("right", "drill_in", "Detail", show=False),
        Binding("left", "drill_out", "Back", show=False),
        Binding("o", "open_file", "Open"),
        Binding("e", "edit", "Edit"),
        Binding("n", "new", "New"),
        Binding("b", "bundle", "Bundle"),
        Binding("question_mark", "toggle_help_panel", "Help"),
        # Quick-accept the shown doc's top suggestion; off the footer (the detail
        # read view already prints "a accept · e review"), so it stays uncrowded.
        Binding("a", "accept_suggestion", "Accept", show=False),
        # Kept working, but off the footer — surfaced in the help panel (`?`):
        Binding("i", "toggle_dates", "Iss/Exp", show=False),
        Binding("m", "move", "Move", show=False),
        Binding("s", "supersede", "Supersede", show=False),
        Binding("w", "watch", "Watch", show=False),
        Binding("r", "reconcile", "Reconcile", show=False),
        Binding("d", "doctor", "Doctor", show=False),
        Binding("B", "bundles", "Bundles", show=False),
        Binding("x", "toggle_expiring", "Expiring", show=False),
    ]

    # True while the detail pane is editing; drives check_action (and, via
    # bindings=True, refreshes the footer to hide the suppressed actions).
    editing: reactive[bool] = reactive(False, bindings=True)

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
        self._suggestion_state = SuggestionState()  # cached; refreshed on reload
        self._selection: str = _ALL
        self._filter_text = ""
        self._expiring_only = False
        self._bundle_filter: str | None = None  # scope to one bundle's docs
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
            yield DetailPane(self._store, glyphs=self._glyphs, id="detail")
        # A fixed-height bottom bar reserves the space for the (touch-only)
        # action row, the command line, and the footer so they stack cleanly
        # (docking them directly onto the screen lets the footer overlap).
        g = self._glyphs
        with Vertical(id="bottombar"):
            with Grid(id="actionbar"):
                yield Button(_btn_label(g.open, "Open"), id="act-open")
                yield Button(_btn_label(g.edit, "Edit"), id="act-edit")
                yield Button(_btn_label(g.new, "New"), id="act-new")
                yield Button(_btn_label(g.bundle, "Bundle"), id="act-bundle")
                yield Button(_btn_label(g.calendar, "Watch"), id="act-watch")
            yield Input(placeholder="Search name / tags / notes…", id="search")
            yield Footer(compact=True)

    def on_mount(self) -> None:
        # Composed once in compose() and never remounted, so cache it instead of
        # re-querying the DOM on every arrow-key detail refresh.
        self._detail_pane = self.query_one("#detail", DetailPane)
        self.set_class(self._touch, "touch")
        self._reload()
        self._focus_default()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self.editing and action in _EDIT_LOCKED:
            return None  # suppressed + hidden from the footer while editing
        return True

    # -- data ----------------------------------------------------------------

    def _reload(self) -> None:
        self._docs = self._store.load_all()
        self._locations = self._store.load_locations()
        self._by_location = dict(query.group_by_location(self._docs))
        self._suggestion_state = self._store.load_suggestions()
        self._refresh_locations()
        self._refresh_documents()
        if self._show_detail:
            self._update_detail()

    def visible_docs(self) -> list[Document]:
        """Documents passing the current search + expiring filter (unscoped)."""
        expiry = (
            (ExpiryStatus.EXPIRED, ExpiryStatus.EXPIRING) if self._expiring_only else ()
        )
        bundles = (self._bundle_filter,) if self._bundle_filter else ()
        flt = query.Filter(text=self._filter_text, expiry=expiry, bundles=bundles)
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
        return (
            bool(self._filter_text)
            or self._expiring_only
            or self._bundle_filter is not None
        )

    def _row_mode(self) -> RowMode:
        if self._narrow or self._portrait:
            return RowMode.MULTILINE
        if self._show_detail:
            return RowMode.COMPACT  # collapsed to names beside the detail pane
        return RowMode.DENSE

    # -- rendering -----------------------------------------------------------

    def _refresh_locations(self) -> None:
        options = self.query_one("#locations", OptionList)
        options.clear_options()
        g = self._glyphs
        # On touch (Termux) each category is a two-line row — a bigger tap target.
        wide = self._touch
        options.add_option(
            Option(_loc_label(g.inbox, "All", len(self._docs), two_line=wide), id=_ALL)
        )
        for loc, group in self._by_location.items():
            if loc is None:
                label = _loc_label(
                    g.unlocated, "— no location —", len(group), two_line=wide
                )
                options.add_option(Option(label, id=_UNLOCATED))
            else:
                title = self._locations[loc].title if loc in self._locations else loc
                label = _loc_label(g.folder, title, len(group), two_line=wide)
                options.add_option(Option(label, id=loc))

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
        elif self._is_searching() and ids:
            options.highlighted = 0  # the preview follows the top hit
        # Drop the noun when narrow (so the header truncates the word cleanly, not
        # mid-word), and use the short "docs" otherwise.
        noun = "" if self._narrow else " docs"
        self.app.sub_title = f"{len(docs)} / {len(self._docs)}{noun}"

    def _update_detail(self) -> None:
        pane = self._detail_pane
        if pane.editing:
            return  # never clobber an in-progress edit with a cursor move
        doc = self._doc_by_id(self._detail_id) if self._detail_id else None
        if doc is None:
            pane.clear()
            return
        pane.show_document(
            self._view(doc),
            location_label=self._location_label(doc),
            chain=query.supersession_chain(self._docs, doc),
            superseded_by=self._superseded_by(doc),
            suggestions=suggest.live(doc, self._suggestion_state),
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
            self._detail_pane.focus()

    def close_detail(self) -> None:
        if not self._show_detail:
            return
        if self._detail_pane.editing:
            return  # an edit in progress owns Esc; don't fall through to close
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

    def on_descendant_focus(self, event: DescendantFocus) -> None:
        # Termux keyboard model: a tap only raises the soft keyboard while mouse
        # reporting is OFF, but the app only receives taps while it's ON. So make
        # *focus* the switch — focusing a text field (search or an edit-form
        # Input/TextArea) drops reporting so the IME can appear; focusing anything
        # else (a list, a button) restores it so taps land again. Keys still
        # arrive either way, so Tab/Enter hop fields and Esc returns to tap mode.
        if not self._touch:
            return
        typing = isinstance(event.widget, (Input, TextArea))
        self._set_mouse_reporting(not typing)

    def on_option_list_option_highlighted(
        self, event: OptionList.OptionHighlighted
    ) -> None:
        if event.option_list.id == "locations" and event.option_id is not None:
            self.select_location(event.option_id)
        elif event.option_list.id == "documents":
            if self.editing:
                return  # a cursor move must not swap the doc being edited
            self._detail_id = event.option_id
            if self._show_detail:
                self._update_detail()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "documents":
            if self.editing or event.option_id is None:
                return  # a click mid-edit must not swap the doc being edited
            self.open_detail(event.option_id)
        elif event.option_list.id == "locations":
            self.action_drill_in()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "act-open":
            self.action_open_file()
        elif event.button.id == "act-edit":
            self.action_edit()
        elif event.button.id == "act-new":
            self.action_new()
        elif event.button.id == "act-bundle":
            self.action_bundle()
        elif event.button.id == "act-watch":
            self.action_watch()

    # -- actions -------------------------------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_escape(self) -> None:
        pane = self._detail_pane
        if pane.editing:
            pane.action_cancel_edit()  # covers focus having left the pane mid-edit
            return
        search = self.query_one("#search", Input)
        if search.value or self.has_class("searching") or self.app.focused is search:
            self._set_mouse_reporting(True)
            search.value = ""
            self._filter_text = ""
            self._bundle_filter = None  # clearing search also drops a bundle scope
            self._expiring_only = False  # …and the expiring filter, or Esc gets stuck
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
        # Progressive "Open": drill locations → documents → detail, then (once
        # the detail pane already shows the current doc) open its physical file.
        # Each step changes the screen visibly, so the drilling is self-evident.
        # Desktop-wide keeps the pane open and following the highlight, so after
        # the first drill this collapses to one-press file-open; touch-narrow
        # closes the pane on Back, so it genuinely re-drills each time.
        if self.app.focused is self.query_one("#locations", OptionList):
            self.action_drill_in()  # locations → documents
            return
        doc = self._current_doc()
        if doc is None:
            return
        if not (self._show_detail and self._detail_id == doc.id):
            self.open_detail(doc.id)  # documents → detail (third column)
            return
        self.open_document(doc.id)  # detail already up → open the file

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

    def _open_and_edit(self, focus: str = "f-name") -> None:
        """Open the detail pane on the current doc and start editing at ``focus``."""
        doc = self._current_doc()
        if doc is None:
            return
        if not self._show_detail:
            self.open_detail(doc.id)
        self._detail_pane.start_edit(doc, self._docs, focus=focus)

    def action_bundle(self) -> None:
        self._open_and_edit(focus="f-bundles")

    def action_toggle_expiring(self) -> None:
        self._expiring_only = not self._expiring_only
        self._update_searching()
        self._refresh_documents()

    def action_edit(self) -> None:
        self._open_and_edit()

    def action_accept_suggestion(self) -> None:
        # Works while browsing (documents pane focused) — the detail pane isn't
        # focused in the wide layout, so the accept lives here and delegates.
        if self._show_detail:
            self._detail_pane.action_accept_suggestion()

    def action_toggle_help_panel(self) -> None:
        # `?` toggles — Textual only offers separate show/hide actions, so binding
        # the built-in show action left no way to dismiss it with the same key.
        from textual.widgets import HelpPanel

        if self.query(HelpPanel):
            self.app.action_hide_help_panel()
        else:
            self.app.action_show_help_panel()

    def action_new(self) -> None:
        if not self._show_detail:
            self._detail_id = None
            self._show_detail = True
            self.set_class(True, "show-detail")
            self._refresh_documents()  # rows collapse beside the pane
        self._detail_pane.start_edit(Document(), self._docs, is_new=True)

    def action_move(self) -> None:
        self._open_and_edit(focus="f-perm")

    def action_watch(self) -> None:
        self.app.push_screen(
            WatchScreen(self._store, self._config, today=self._today), self._after_watch
        )

    def action_reconcile(self) -> None:
        self.app.push_screen(
            ReconcileScreen(self._store, self._config), self._after_watch
        )

    def action_bundles(self) -> None:
        self.app.push_screen(
            BundlesScreen(self._store, self._config, today=self._today),
            self._after_bundles,
        )

    def action_doctor(self) -> None:
        self.app.push_screen(
            DoctorScreen(self._store, self._config), self._after_doctor
        )

    # -- helpers -------------------------------------------------------------

    def _update_searching(self) -> None:
        searching = self._is_searching()
        was = self.has_class("searching")
        self.set_class(searching, "searching")
        if searching and not was:
            # Root-wide results: snap the locations pane to "All" so the left
            # column reflects what the middle now shows (set both the state and
            # the highlight — an unchanged highlight wouldn't fire select_location).
            self._selection = _ALL
            self.query_one("#locations", OptionList).highlighted = 0

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
            self.open_detail(doc.id)
            self._detail_pane.start_edit(doc, self._docs)

    def _after_watch(self, doc_id: str | None) -> None:
        self._reload()  # an ignore-expiry change in the watch may have landed
        if doc_id is not None:
            doc = self._doc_by_id(doc_id)
            if doc is not None:
                self.open_detail(doc.id)

    def _after_bundles(self, slug: str | None) -> None:
        self._reload()  # a bundle date edit may have landed
        if slug is not None:
            self._bundle_filter = slug  # scope the documents pane to this bundle
            self._update_searching()
            self._refresh_documents()
            self.notify(f"showing bundle {slug} — Esc clears")

    # -- detail-pane edit messages -------------------------------------------

    def on_detail_pane_editing_changed(self, event: DetailPane.EditingChanged) -> None:
        self.editing = event.editing
        # Disable the search box while editing: a mid-edit filter could drop the
        # edited doc from the list, and Tab or a click into it would steal focus.
        self.query_one("#search", Input).disabled = event.editing

    def on_detail_pane_saved(self, event: DetailPane.Saved) -> None:
        self._reload()
        self.open_detail(event.doc_id)
        self._detail_pane.focus()

    def on_detail_pane_reload_requested(
        self, event: DetailPane.ReloadRequested
    ) -> None:
        self._reload()  # resync the list; _update_detail no-ops while editing


def _highlighted_id(options: OptionList) -> str | None:
    index = options.highlighted
    if index is None:
        return None
    return options.get_option_at_index(index).id


def _loc_label(icon: str, title: str, count: int, *, two_line: bool = False) -> Text:
    prefix = f"{icon}  " if icon else ""
    label = Text(f"{prefix}{title}", no_wrap=True, overflow="ellipsis")
    if two_line:
        indent = "    " if icon else ""
        label.append(f"\n{indent}{count} document{'' if count == 1 else 's'}", "dim")
    else:
        label.append(f"  {count}", style="dim")
    return label


def _btn_label(icon: str, text: str) -> str:
    return f"{icon}  {text}" if icon else text
