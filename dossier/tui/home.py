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
keeping the columns. Under the touch/Termux UI a bottom action bar (Open / Edit /
New / Bundle / Watch / Commands) replaces the desktop keybind footer; **Commands**
opens the searchable command palette (its search box focusing raises the soft
keyboard), which is the home for every action not on a button or a core key.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical
from textual.events import DescendantFocus, Key, Resize
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    OptionList,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from dossier import query, scan, suggest
from dossier.config import Config
from dossier.errors import ScanError
from dossier.model import Document, ExpiryStatus, Location, SuggestionState
from dossier.platform_open import OpenError, copy_path, reveal_file
from dossier.store import Store
from dossier.tui import glyphs, rows
from dossier.tui.detail_pane import DetailPane, format_saved_at
from dossier.tui.doclist import DocumentList
from dossier.tui.intake import IntakeScreen
from dossier.tui.review import ReviewPane
from dossier.tui.rows import RowMode
from dossier.tui.screens import (
    BundlesScreen,
    ChoiceScreen,
    SettingsScreen,
    SupersedeScreen,
    WatchScreen,
    open_doc_file,
    toggle_help_panel,
)

# Sentinel option ids for the two synthetic locations-pane rows (real location
# slugs are kebab-case, so a NUL prefix can never collide with one).
_ALL = "\x00all"
_UNLOCATED = "\x00none"

# Below this many columns the panes stop sharing the screen; matches the
# ``-narrow`` breakpoint so pane collapse and row density agree.
_NARROW_COLS = 60

# Rows rendered into the documents pane at once. Textual measures every option it
# holds on each refresh, so an unbounded list makes every keystroke O(store). A few
# screens' worth is all anyone scrolls; past that, narrowing beats scrolling.
_MAX_ROWS = 200

# The search box is always on screen, so it is where the command surface gets to
# announce itself — the palette was otherwise reachable only by a key the UI never
# mentioned (Textual's own ctrl+p binding is show=False).
_SEARCH_HINT = "Search name / tags / notes / scans…  ·  ctrl+p commands"
_SEARCH_HINT_CONTENT = "Search name / tags / notes / scans + contents…"

# Home actions suppressed while the detail pane is in edit mode, so a bare letter
# typed into a form Checkbox/SelectionList (which don't swallow it like an Input
# does) can't fire a home binding — and the footer stops advertising them. Only the
# still-bound letters matter here now (the rest moved to the command palette).
_EDIT_LOCKED = frozenset(
    {
        "open_file",
        "bundle",
        "edit",
        "new",
        "accept_suggestion",
        "focus_search",
        "drill_in",
        "drill_out",
    }
)

# Home actions review takes over while it holds columns 1+2. `drill_out` stays live
# whenever the detail is open, so `←` still means "close detail, back to review".
_REVIEW_LOCKED = frozenset({"focus_search", "drill_in", "drill_out"})


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
    #footrow { height: 1; }
    #footrow Footer { width: 1fr; }
    #attention { width: auto; color: $text-muted; padding: 0 1; text-align: right; }
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

    /* Review takes columns 1+2, leaving the detail pane as column 3 — so acting
       on a finding shows the record *beside* it instead of tearing review down.
       These rules live here, not in ReviewPane.DEFAULT_CSS: a widget's CSS is
       scoped to its own type, and a rule leading with another type is rewritten
       into one that can never match. Only wide can afford both at once; narrow
       and medium swap to the detail (display:none is not teardown — review keeps
       its tab, cursor and loaded report while hidden). */
    ReviewPane { display: none; width: 1fr; }
    HomeScreen.review-mode ReviewPane { display: block; }
    HomeScreen.review-mode #locations { display: none; }
    HomeScreen.review-mode #documents { display: none; }
    HomeScreen.-narrow.review-mode.show-detail ReviewPane { display: none; }
    HomeScreen.-medium.review-mode.show-detail ReviewPane { display: none; }
    """

    # Find-fast home: typing anything routes into search (see on_key), so the home
    # keeps no letter bindings at all — browse is arrows / Enter (open the file) / →
    # (detail) / Esc, search is any printable or `/`, and every action (open, edit,
    # new, bundle, accept, plus the occasional ones) lives in the **command palette**
    # (`ctrl+p` / the Commands touch button) and on the touch button bar. `?` opens
    # the HelpPanel; `ctrl+q` quits (Textual built-in). Letters own nothing here so
    # the first keystroke can always be the start of a find.
    BINDINGS = [
        Binding("slash", "focus_search", "Search"),
        Binding("escape", "escape", "Back"),
        Binding("right", "drill_in", "Detail", show=False),
        Binding("left", "drill_out", "Back", show=False),
        Binding("question_mark", "toggle_help_panel", "Help"),
        # Advertised by action_toggle_search_content's own notices ("ctrl+t to
        # toggle off") but bound nowhere — the UI was promising a key that did not
        # exist. Hidden from the footer: it's a niche toggle, and the notice says it.
        Binding("ctrl+t", "toggle_search_content", "Search contents", show=False),
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
        self._readings: dict[str, scan.ScanReading] = {}  # ds scan, refreshed on reload
        self._search_content = (
            False  # `/` also matches scan transcripts (ctrl+t toggles)
        )
        self._selection: str = _ALL
        self._filter_text = ""
        self._expiring_only = False
        self._bundle_filter: str | None = None  # scope to one bundle's docs
        # Footer attention counts. Conflicts + inbox need directory I/O (a `.dossier`
        # walk, slow on a synced FS), so they're scanned on mount and after the
        # actions that change them — not on every reload; expiring is free from docs.
        self._conflict_count = 0
        self._inbox_count = 0
        self._show_detail = False
        self._review: ReviewPane | None = None  # mounted on first `action_review`
        self._show_issue = False
        self._detail_id: str | None = None
        self._narrow = False
        self._portrait = False
        self._last_mode = RowMode.DENSE

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="panes"):
            yield OptionList(id="locations")
            yield DocumentList(id="documents")
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
                # The 6th tile is the touch entry to the command palette — the
                # searchable home for everything not on a button (its search box
                # focusing raises the soft keyboard via the app's focus handler).
                yield Button(_btn_label(g.commands, "Commands"), id="act-commands")
            yield Input(placeholder=_SEARCH_HINT, id="search")
            # Attention counts ride *beside* the footer, dim and non-focusable, so
            # they never sit in front of the find path (they replaced a toast that
            # overlapped the search box).
            with Horizontal(id="footrow"):
                yield Footer(compact=True)
                yield Static("", id="attention")

    def on_mount(self) -> None:
        # Composed once in compose() and never remounted, so cache it instead of
        # re-querying the DOM on every arrow-key detail refresh.
        self._detail_pane = self.query_one("#detail", DetailPane)
        self.set_class(self._touch, "touch")
        # Never select-all on focus: `/` and the type-to-search router should let
        # you keep refining the filter, not replace it with the next keystroke.
        self.query_one("#search", Input).select_on_focus = False
        self._scan_attention()  # conflict/inbox counts (I/O) before the first render
        self._reload()
        self._focus_default()
        self._warn_slow_yaml()

    def _warn_slow_yaml(self) -> None:
        """Nudge to enable the fast C YAML backend when PyYAML fell back to pure
        Python (usually Termux without ``pkg install libyaml``). Self-resolving:
        :func:`store.libyaml_hint` returns None once libyaml is active, so the
        notice simply stops appearing after the one-time fix — no dismiss flag."""
        from dossier.store import libyaml_hint

        hint = libyaml_hint()
        if hint:
            self.notify(hint, severity="warning", timeout=12)

    def _scan_attention(self) -> None:
        """Refresh the directory-backed attention counts (conflicts + inbox). These
        each cost a walk, so this runs on mount and after the actions that change
        them (resolve / intake), not on every reload."""
        self._conflict_count = len(self._store.list_conflicts())
        self._inbox_count = 0
        if self._config.intake_inbox:
            from dossier import intake

            self._inbox_count = len(intake.pending_files(self._store, self._config))

    def _refresh_attention(self) -> None:
        """Rebuild the dim footer segment. Expiring is free (from the loaded docs);
        conflicts/inbox reuse the cached counts (see :meth:`_scan_attention`)."""
        threshold = self._config.expiry_threshold_days
        expiring = sum(
            1
            for d in self._docs
            if d.expiry_status(self._today, threshold)
            in (ExpiryStatus.EXPIRED, ExpiryStatus.EXPIRING)
        )
        parts: list[str] = []
        if expiring:
            parts.append(f"{expiring} expiring")
        if self._conflict_count:
            noun = "conflict" if self._conflict_count == 1 else "conflicts"
            parts.append(f"{self._conflict_count} {noun}")
        if self._inbox_count:
            parts.append(f"{self._inbox_count} inbox")
        self.query_one("#attention", Static).update("  ·  ".join(parts))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if self.editing and action in _EDIT_LOCKED:
            return None  # suppressed, but still shown (greyed) in the footer
        # Review holds the columns these act on. Unlike a modal, a widget lets
        # unhandled keys bubble up here — `→` would otherwise open the detail for
        # whatever the *hidden* documents cursor sits on. False, not None: dead as a
        # key AND absent from the footer, which is the honest reading. `drill_out`
        # survives while the detail is open, so `←` means "close detail, to review".
        # Contextual commands: hidden (not greyed) when they cannot act. The
        # palette respects check_action now, so gating here is what keeps
        # "Cancel vision scan" out of the list when nothing is scanning — the
        # list shrinks to what is actually actionable instead of listing verbs
        # that would no-op.
        if action == "cancel_scan":
            return bool(self.workers._workers) and any(
                w.group == "vision" and w.is_running for w in self.workers._workers
            )
        if action == "accept_suggestion":
            return self._show_detail and self._detail_pane.has_pending_suggestion()
        return not (
            self.has_class("review-mode")
            and action in _REVIEW_LOCKED
            and not (action == "drill_out" and self._show_detail)
        )

    # -- data ----------------------------------------------------------------

    def _reload(self, *, restale_review: bool = True) -> None:
        """Re-read the store into the columns.

        ``restale_review=False`` for reloads *caused by* review — it refreshes
        itself internally, and marking it stale for its own effects would make the
        free Esc-return expensive again.
        """
        if restale_review and self._review is not None:
            self._review.mark_stale()
        self._docs = self._store.load_all()
        self._locations = self._store.load_locations()
        self._by_location = dict(query.group_by_location(self._docs))
        self._suggestion_state = self._store.load_suggestions()
        self._readings = self._store.load_scans()
        self._refresh_locations()
        self._refresh_documents()
        self._refresh_attention()  # expiring count follows the (reloaded) docs
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
            readings=self._readings,  # content search: match a scan's structured fields
            include_content=self._search_content,  # + full transcript when toggled on
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
        # One `add_options`, never a loop of `add_option`: each singular call ends in
        # `refresh()` + `_update_lines()`, so adding N rows re-lays-out the list N
        # times. At ~950 documents that alone was 429 ms of a 632 ms keystroke.
        shown = docs[:_MAX_ROWS]
        options.add_options(
            [
                Option(
                    rows.doc_row(
                        self._view(doc),
                        mode=mode,
                        superseded=doc.id in superseded,
                        show_issue=self._show_issue,
                        glyphs=self._glyphs,
                    ),
                    id=doc.id,
                )
                for doc in shown
            ]
        )
        if len(docs) > len(shown):
            # Even batched, Textual measures every option it holds, so a full store
            # costs ~260 ms a keystroke for rows no one can see. Cap the render and
            # say so: with this many matches the answer is to type, not to scroll.
            hidden = len(docs) - len(shown)
            options.add_option(
                Option(Text(f"  … and {hidden} more — keep typing", style="dim italic"))
            )
        ids = [doc.id for doc in shown]
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
            suggestions=suggest.live(
                doc, self._suggestion_state, self._readings.get(doc.id)
            ),
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
        """Reveal the detail pane for ``doc_id`` (Enter / drill right).

        Column 3 either way: opened from the miller columns or from a review
        finding. Review no longer needs an "origin" to return to, because it was
        never dismissed — it is still sitting in columns 1+2 behind this.
        """
        self._detail_id = doc_id
        first_open = not self._show_detail
        self._show_detail = True
        self.set_class(True, "show-detail")
        if first_open and not self.has_class("review-mode"):
            self._refresh_documents()  # rows collapse to their compact shape
        self._update_detail()
        if self._narrow or self.has_class("review-mode"):
            self._detail_pane.focus()

    def close_detail(self) -> None:
        if not self._show_detail:
            return
        if self._detail_pane.editing:
            return  # an edit in progress owns Esc; don't fall through to close
        self._show_detail = False
        self.set_class(False, "show-detail")
        if self.has_class("review-mode") and self._review is not None:
            # An edit made in column 3 belongs in review's lists; an untouched
            # record costs nothing, which is the point of the flag.
            self._review.reload_if_stale()
            self._review.focus_active_pane()  # tab and cursor exactly as left
            return
        self._refresh_documents()
        self._focus_documents()

    def open_document(self, doc_id: str) -> None:
        """Open a document's primary rendition with the platform opener."""
        doc = self._doc_by_id(doc_id)
        if doc is not None:
            open_doc_file(self, self._config, doc)

    # -- events --------------------------------------------------------------

    def on_resize(self, event: Resize) -> None:
        size = event.size
        self._portrait = size.height > size.width
        self._narrow = size.width < _NARROW_COLS
        self.set_class(self._portrait, "-portrait")
        if self._row_mode() != self._last_mode:
            self._refresh_documents()
        self._ensure_focus_visible()

    def on_key(self, event: Key) -> None:
        """Find-fast routing that runs before any binding (Textual dispatches key
        handlers before non-priority BINDINGS):

        * a printable typed while a column is focused jumps into the search box —
          first character kept — so finding never needs a mode key first;
        * ``↓`` from the search box steps into the documents list keeping the filter
          (Enter now *opens* the top match, so this is the "browse the hits" move).

        ``/`` and ``?`` stay reserved (search-focus and help), and the router never
        fires mid-edit or from the detail pane / buttons — only the two lists.
        """
        search = self.query_one("#search", Input)
        if self.app.focused is search:
            if event.key == "down":
                event.stop()
                self._focus_documents()
            return  # everything else in search is the Input's own to handle
        if (
            self.editing
            or search.disabled
            or not event.is_printable
            or not event.character
            or event.character in ("/", "?")
        ):
            return
        lists = (self.query_one("#documents", OptionList), self.query_one("#locations"))
        if self.app.focused in lists:
            event.stop()
            event.prevent_default()
            # Append to the value directly (not insert_text_at_cursor): focusing an
            # Input selects its text, and an insert would then replace it — appending
            # keeps whatever's already typed and lands the cursor at the end.
            search.value += event.character
            search.focus()
            search.cursor_position = len(search.value)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self._filter_text = event.value
            self._update_searching()
            self._refresh_documents()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Enter from search = find-fast: open the top match's file straight away.
        # (`↓` is the way to step into the list keeping the filter; `→` opens detail.)
        if event.input.id != "search":
            return
        doc = self._highlighted_doc()  # _refresh_documents pins the top hit to row 0
        if doc is None:
            self.notify("no matches")
            return
        self._activate_doc(doc.id)

    def _activate_doc(self, doc_id: str) -> None:
        """The Enter/activate verb: open the document's file. A physical-only record
        has nothing to open, so show its detail (where the physical copy lives)
        instead. ``→`` (drill-in) is the way to inspect any record's detail."""
        doc = self._doc_by_id(doc_id)
        if doc is None:
            return
        if doc.primary_rendition() is None:
            self.open_detail(doc.id)  # nothing to open — show where the copy lives
            self.notify(f"{doc.name}: no digital file", severity="warning")
            return
        self._set_mouse_reporting(True)  # left the search field → tap mode resumes
        self.open_document(doc.id)
        if not self._narrow:
            self._focus_documents()  # keep the list navigable after the file opens

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

    def on_document_list_previewed(self, event: DocumentList.Previewed) -> None:
        """First click on a row — show its detail. A second click opens the file."""
        if self.editing or event.option_id is None:
            return  # a click mid-edit must not swap the doc being edited
        self.open_detail(event.option_id)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "documents":
            if self.editing or event.option_id is None:
                return  # a click mid-edit must not swap the doc being edited
            self._activate_doc(event.option_id)  # Enter/tap opens the file; → = detail
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
        elif event.button.id == "act-commands":
            self.app.action_command_palette()

    # -- actions -------------------------------------------------------------

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_toggle_search_content(self) -> None:
        """Toggle whether `/` also matches inside scan transcripts.

        Off by default — the full body text is noisy — so it's an opt-in (ctrl+t),
        advertised in the placeholder and the `?` help panel.
        """
        self._search_content = not self._search_content
        search = self.query_one("#search", Input)
        if self._search_content:
            search.placeholder = f"{_SEARCH_HINT_CONTENT}  ·  ctrl+p commands"
            self.notify("searching inside scan contents  (ctrl+t to toggle off)")
        else:
            search.placeholder = _SEARCH_HINT
            self.notify("content search off  (ctrl+t to toggle on)")
        self._refresh_documents()

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

    # -- "where does this live?" ---------------------------------------------
    #
    # Two commands rather than one that changes meaning per platform: revealing
    # and copying are different intents, and a verb that silently becomes another
    # verb on another machine is exactly what the rest of the key map avoids.
    # Palette-only — occasional, and review's footer is full.

    def action_reveal_file(self) -> None:
        """Show the file under the cursor in the platform's file manager."""
        self._with_cursor_path("Reveal which file?", self._reveal_one)

    def action_copy_path(self) -> None:
        """Copy the path of the file under the cursor to the clipboard."""
        self._with_cursor_path("Copy which path?", self._copy_one)

    def _with_cursor_path(self, prompt: str, then: Callable[[Path], None]) -> None:
        """Resolve what the cursor points at, asking when it points at two things.

        **An open record wins**, review's list or not: it is the thing being looked
        at, and on a narrow screen (a phone) the record *replaces* review entirely —
        acting on a hidden list's cursor would be acting on something invisible.
        Otherwise review's cursor, else the documents column.

        Review is why the ask exists at all: its Succession rows straddle *two*
        documents, and while opening both is meaningful ("does this replace that?"),
        revealing or copying both is not.
        """
        in_review = (
            self.has_class("review-mode")
            and self._review is not None
            and not self._show_detail
        )
        choices = (
            self._review.path_labels()
            if in_review and self._review is not None
            else self._current_doc_path_labels()
        )
        if not choices:
            self.notify("no file under the cursor", severity="warning")
            return
        if len(choices) == 1:
            then(query.resolve_path(self._config.syncthing_root, choices[0][0]))
            return
        self.app.push_screen(
            ChoiceScreen(prompt, choices),
            lambda rel: (
                then(query.resolve_path(self._config.syncthing_root, rel))
                if rel
                else None
            ),
        )

    def _current_doc_path_labels(self) -> list[tuple[str, str]]:
        doc = self._current_doc()
        rendition = doc.primary_rendition() if doc is not None else None
        return [(rendition.path, rendition.path)] if rendition is not None else []

    def _reveal_one(self, path: Path) -> None:
        if not path.exists():
            self.notify(f"file not found: {path}", severity="error")
            return
        try:
            caveat = reveal_file(path)
        except OpenError as exc:
            self.notify(str(exc), severity="error")
            return
        # Android can only *ask* — the intent is accepted but what happens next is
        # the OEM file manager's call, so say so rather than claiming success.
        if caveat is None:
            self.notify(f"revealed {path.name}")
        else:
            self.notify(f"{path.parent.name}: {caveat}", severity="warning")

    def _copy_one(self, path: Path) -> None:
        try:
            copy_path(path)
        except OpenError as exc:
            self.notify(str(exc), severity="error")
        else:
            self.notify(f"copied {path}")

    def action_history(self) -> None:
        """Pick an earlier version of the current document and restore it.

        The arbitrary-depth companion to the detail pane's ``ctrl+z``, which only
        toggles the most recent version. Reuses the same restore path, so a pick
        from here is archived and undoable exactly as a ctrl+z is.
        """
        doc = self._current_doc()
        if doc is None:
            self.notify("no document selected", severity="warning")
            return
        entries = {str(e.saved_at.timestamp()): e for e in self._store.history(doc.id)}
        if not entries:
            self.notify(f"{doc.name}: no earlier version saved")
            return
        choices = [(key, format_saved_at(e)) for key, e in entries.items()]

        def restore(key: str | None) -> None:
            if key is not None:
                self.open_detail(doc.id)  # show what changed, wherever we were
                self._detail_pane.restore_version(entries[key])

        self.app.push_screen(
            ChoiceScreen(f"Restore which version of {doc.name}?", choices), restore
        )

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
        # The shared helper every modal uses — the home had its own copy, which is
        # why the command index it appends never showed up here.
        toggle_help_panel(self)

    # -- vision scan (ds scan, from the TUI) ---------------------------------

    @work(thread=True, group="vision", exclusive=True)
    def action_scan_doc(self) -> None:
        """Read the current document with the VLM (a ~few-second blocking call)."""
        doc = self._current_doc()
        if doc is None or doc.primary_rendition() is None:
            self.app.call_from_thread(
                self.notify, "no linked file to scan", severity="warning"
            )
            return
        self._scan_docs([doc])

    @work(thread=True, group="vision", exclusive=True)
    def action_scan_all(self) -> None:
        """Read every linked document (minutes); cancellable via the palette."""
        linked = [d for d in self._docs if d.primary_rendition() is not None]
        self._scan_docs(linked)

    def action_cancel_scan(self) -> None:
        self.workers.cancel_group(self, "vision")
        self.notify("cancelling vision scan…")

    def action_settings(self) -> None:
        self.app.push_screen(SettingsScreen(self._config), self._after_settings)

    def _after_settings(self, changed: bool | None) -> None:
        if changed:
            self._reload()  # expiry threshold + scan_* apply now; glyphs on restart

    def _scan_docs(self, docs: list[Document]) -> None:
        """Worker body: read each doc with the VLM, persisting after each success
        (so a cancel keeps progress) and reporting live via the header sub_title."""
        worker = get_current_worker()
        readings = self._store.load_scans()
        total, seen, scanned, failed = len(docs), 0, 0, 0
        for doc in docs:
            if worker.is_cancelled:
                break
            rendition = doc.primary_rendition()
            if rendition is None:
                continue
            path = query.resolve_path(self._config.syncthing_root, rendition.path)
            if not path.exists():
                continue
            fingerprint = scan.file_fingerprint(path)
            seen += 1
            if doc.id in readings and readings[doc.id].fingerprint == fingerprint:
                continue  # unchanged since the last scan
            name = doc.name or doc.id
            self.app.call_from_thread(
                setattr, self.app, "sub_title", f"scanning {seen}/{total}: {name}"
            )
            try:
                reading = scan.extract(path, self._config)
            except ScanError as exc:
                failed += 1
                self.app.call_from_thread(
                    self.notify, f"{doc.id}: {exc}", severity="error"
                )
                continue
            readings[doc.id] = replace(reading, fingerprint=fingerprint)
            self._store.save_scans(readings)  # persist after each (cancel-safe)
            scanned += 1
        self.app.call_from_thread(self._scan_done, scanned, failed, worker.is_cancelled)

    def _scan_done(self, scanned: int, failed: int, cancelled: bool) -> None:
        self._reload()  # re-reads readings + refreshes suggestions + resets sub_title
        verb = "cancelled" if cancelled else "complete"
        tail = f", {failed} failed" if failed else ""
        self.notify(f"vision scan {verb}: {scanned} read{tail}")

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

    def action_review(self) -> None:
        """Toggle review into columns 1+2. Mounted once, then shown and hidden.

        Never unmounted: tearing it down is what made the old modal lossy, and a
        remount would re-run the whole load (documents + reconcile + conflict plans)
        just to show a surface the user already had.
        """
        if self.has_class("review-mode"):
            self._exit_review_mode()
            return
        if self._review is None:
            self._review = ReviewPane(self._store, self._config)
            self.query_one("#panes").mount(self._review, before=self._detail_pane)
        self._enter_review_mode()

    def _enter_review_mode(self) -> None:
        # Normalise the filter state rather than out-specifying it in CSS: a stale
        # `searching` class carries two class selectors and would out-rank
        # `.review-mode #documents { display: none }`, resurrecting the documents
        # column underneath review on a narrow screen.
        search = self.query_one("#search", Input)
        search.value = ""
        search.disabled = True  # the filter targets a hidden pane while review is up
        self._filter_text = ""
        self._bundle_filter = None
        self._expiring_only = False
        self.remove_class("searching", "show-documents")
        self.add_class("review-mode")
        if self._review is not None:
            self._review.reload_if_stale()  # catch up on writes made outside review
            self._review.focus_active_pane()

    def _exit_review_mode(self) -> None:
        self.remove_class("review-mode")
        self.query_one("#search", Input).disabled = False
        self._scan_attention()  # a merge in review may have cleared conflicts
        self._reload(restale_review=False)
        self._focus_documents()

    @on(ReviewPane.OpenDocument)
    def _review_open_document(self, event: ReviewPane.OpenDocument) -> None:
        """Review asked for a document — show it in column 3, review stays up."""
        event.stop()
        self._scan_attention()
        self._reload(restale_review=False)
        doc = self._doc_by_id(event.doc_id)
        if doc is None:
            self.notify(f"{event.doc_id}: no such document", severity="warning")
            return
        self.open_detail(doc.id)
        if event.edit:
            self._detail_pane.start_edit(doc, self._docs)

    @on(ReviewPane.CloseRequested)
    def _review_close_requested(self, event: ReviewPane.CloseRequested) -> None:
        """Esc inside review — peel the newest layer, don't collapse the stack."""
        event.stop()
        if self._show_detail:
            self.close_detail()
        else:
            self._exit_review_mode()

    def action_intake(self) -> None:
        if not self._config.intake_inbox:
            self.notify(
                "no inbox configured — set [intake] inbox in .dossier/config.toml",
                severity="warning",
            )
            return
        self.app.push_screen(
            IntakeScreen(self._store, self._config), self._after_intake
        )

    def _after_intake(self, doc_id: str | None) -> None:
        self._scan_attention()  # inbox drained (and a fold can clear a conflict)
        self._reload()  # documents were filed (new records, moved files)
        if doc_id is not None:
            doc = self._doc_by_id(doc_id)
            if doc is not None:
                self.open_detail(doc.id)
                self._detail_pane.start_edit(doc, self._docs)

    def action_bundles(self) -> None:
        self.app.push_screen(
            BundlesScreen(self._store, self._config, today=self._today),
            self._after_bundles,
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
        # Termux opens type-first: focus search so a find starts on the first
        # keystroke (this drops mouse reporting, so taps need an Esc first — the
        # accepted trade for a find-first phone; a one-line flip to tap-first).
        # Desktop keeps the list focused — the type-to-search router already lands
        # the first key in search, so arrows still browse instantly.
        if self._touch:
            self.query_one("#search", Input).focus()
            return
        documents = self.query_one("#documents", OptionList)
        (documents if documents.display else self.query_one("#locations")).focus()

    def _ensure_focus_visible(self) -> None:
        focused = self.app.focused
        if focused is not None and focused.display:
            return
        # ReviewPane is mounted lazily, so query (which tolerates a miss) rather
        # than query_one (which raises) — it simply isn't there until first use.
        panes = ("ReviewPane", "#documents", "#detail", "#locations", "#search")
        for selector in panes:
            hits = self.query(selector)
            if hits and hits.first().display:
                hits.first().focus()
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
