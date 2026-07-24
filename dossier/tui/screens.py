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

"""Assorted modal screens: supersede/doc-picker/prompt, watch, bundles, settings."""

from __future__ import annotations

from collections import Counter
from datetime import date

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import (
    Button,
    Input,
    Label,
    OptionList,
    RadioButton,
    RadioSet,
    Select,
    Static,
)
from textual.widgets.option_list import Option

from dossier import preparedness, query, scan, suggest
from dossier.config import Config, update_per_device, update_synced
from dossier.errors import ScanError, StaleWriteError, StoreError
from dossier.model import Bundle, Document, ExpiryStatus, Location, Template
from dossier.platform_open import OpenError, open_file
from dossier.store import Store
from dossier.tui import (
    forms,
    glyphs as glyphset,
    rows,
)
from dossier.tui.doclist import DocumentList


def open_doc_file(node: Widget, config: Config, doc: Document) -> None:
    """Open a document's primary rendition with the platform opener.

    The app-wide **activate** verb (Enter/tap opens the file; ``→`` shows detail),
    shared by the home and every modal so all of them report the same misses the
    same way instead of each raising or notifying differently.
    """
    rendition = doc.primary_rendition()
    if rendition is None:
        node.notify(f"{doc.name}: no digital file linked", severity="warning")
        return
    path = query.resolve_path(config.syncthing_root, rendition.path)
    if not path.exists():
        node.notify(f"file not found: {path}", severity="error")
        return
    try:
        open_file(path)
    except OpenError as exc:
        node.notify(str(exc), severity="error")
    else:
        node.notify(f"opened {doc.name}")


def toggle_help_panel(node: Widget) -> None:
    """Show or hide Textual's HelpPanel — the full, tab-aware keybind list.

    Every modal binds ``?`` to a one-line ``action_toggle_help_panel`` that calls
    this, so the "what keys can I press here?" affordance is identical everywhere.
    Takes any ``Widget`` (a Screen is one) so non-screen surfaces can share it.
    Queries the containing *screen*, never ``node`` itself: Textual mounts the panel
    on the screen, so a widget asking whether it is open would always be told "no"
    and this would only ever show, never hide.
    """
    from textual.widgets import HelpPanel

    if node.screen.query(HelpPanel):
        node.app.action_hide_help_panel()
        return
    node.app.action_show_help_panel()
    # Textual's panel lists *keys*, and the occasional commands have none — so on
    # its own it never mentions the larger half of what the app can do. Append a
    # compact index of them, grouped, after the panel mounts.
    node.app.call_after_refresh(_append_command_index, node)


def _append_command_index(node: Widget) -> None:
    from textual.widgets import HelpPanel, KeyPanel

    panels = node.screen.query(HelpPanel)
    if not panels or panels.first().query("#help-commands"):
        return
    panel = panels.first()
    keys = panel.query(KeyPanel)
    summary = Static(_command_index_text(), id="help-commands")
    # *Before* the key list, not after: KeyPanel is `height: 1fr`, so anything
    # mounted below it is allotted no space at all and never appears.
    panel.mount(summary, before=keys.first()) if keys else panel.mount(summary)


def _command_index_text() -> Text:
    """A count-per-group index of the commands, for the ``?`` panel.

    Deliberately *not* the full list. The panel is a 30–60 column split and the
    keybindings alone already fill most of it — measured, the two together
    overflow a 34-row terminal — so listing every command here would push out the
    keys the panel exists to show. What it can honestly do in three lines is
    answer "how much else is there, and roughly what kind": the `:` command bar
    answers "which one", and is one keystroke away.
    """
    from dossier.tui.commands import ENTRIES, Kind

    counts = [
        (kind, sum(1 for e in ENTRIES if e.kind is kind))
        for kind in Kind
        if any(e.kind is kind for e in ENTRIES)
    ]
    text = Text()
    text.append(f"\n {len(ENTRIES)} commands  ", style="bold")
    text.append(":  or  ctrl+p\n", style="dim")
    for kind, count in counts:
        text.append(f"   {kind.value} {count}\n", style="dim")
    return text


class SupersedeScreen(ModalScreen[bool]):
    """Pick the document a renewal replaces, setting its ``supersedes`` link."""

    CSS = """
    SupersedeScreen { align: center middle; }
    #spanel {
        width: 80%; max-width: 90; height: 80%;
        padding: 1 2; background: $panel; border: round $primary;
    }
    #sfilter { margin-bottom: 1; }
    #scandidates { height: 1fr; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    _CLEAR = "\x00clear"

    def __init__(self, store: Store, docs: list[Document], doc: Document) -> None:
        super().__init__()
        self._store = store
        self._docs = docs
        self._doc = doc

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="spanel"):
            yield Label(
                f'Which document does "{self._doc.name or self._doc.id}" replace?'
            )
            yield Input(placeholder="filter…", id="sfilter")
            yield OptionList(id="scandidates")

    def on_mount(self) -> None:
        self._populate("")
        self.query_one("#sfilter", Input).focus()

    def _populate(self, needle: str) -> None:
        options = self.query_one("#scandidates", OptionList)
        lead = (
            Option("— clear supersession —", id=self._CLEAR)
            if self._doc.supersedes
            else None
        )
        _fill_doc_options(options, self._docs, needle, exclude=self._doc.id, lead=lead)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "sfilter":
            self._populate(event.value)

    @on(OptionList.OptionSelected, "#scandidates")
    def _pick(self, event: OptionList.OptionSelected) -> None:
        self._doc.supersedes = (
            None if event.option_id == self._CLEAR else event.option_id
        )
        try:
            self._store.save(self._doc)
        except StaleWriteError:
            self.notify(
                "changed on disk since load; reopen and retry", severity="error"
            )
            return
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            return
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class DocPickerScreen(ModalScreen[str | None]):
    """Pick a document from a filterable list. Dismisses its id, or ``None``.

    A read-only sibling of :class:`SupersedeScreen` — it *chooses* a document and
    hands the id back to the caller instead of writing anything itself.
    """

    CSS = """
    DocPickerScreen { align: center middle; }
    #ppanel {
        width: 80%; max-width: 90; height: 80%;
        padding: 1 2; background: $panel; border: round $primary;
    }
    #pfilter { margin-bottom: 1; }
    #pcandidates { height: 1fr; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self,
        docs: list[Document],
        *,
        prompt: str,
        initial: str = "",
        lead: Option | None = None,
    ) -> None:
        super().__init__()
        self._docs = docs
        self._prompt = prompt
        self._initial = initial
        self._lead = lead  # an always-first sentinel row, e.g. "— no succession —"

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="ppanel"):
            yield Label(self._prompt)
            yield Input(value=self._initial, placeholder="filter…", id="pfilter")
            yield OptionList(id="pcandidates")

    def on_mount(self) -> None:
        self._populate(self._initial)
        self.query_one("#pfilter", Input).focus()

    def _populate(self, needle: str) -> None:
        options = self.query_one("#pcandidates", OptionList)
        _fill_doc_options(options, self._docs, needle, lead=self._lead)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "pfilter":
            self._populate(event.value)

    @on(OptionList.OptionSelected, "#pcandidates")
    def _pick(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ChoiceScreen(ModalScreen[str | None]):
    """Pick one of a handful of labelled choices. Dismisses the id, or ``None``.

    Deliberately unfiltered, unlike :class:`DocPickerScreen`: this asks "which of
    these two?", so a search box would be furniture. Used where a surface has more
    than one candidate file — a succession's older and newer sides, say — and the
    action can only sensibly take one.
    """

    CSS = """
    ChoiceScreen { align: center middle; }
    #cpanel {
        width: 80%; max-width: 90; height: auto; max-height: 80%;
        padding: 1 2; background: $panel; border: round $primary;
    }
    #cchoices { height: auto; max-height: 20; margin-top: 1; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, prompt: str, choices: list[tuple[str, str]]) -> None:
        super().__init__()
        self._prompt = prompt
        self._choices = choices  # (id, label)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="cpanel"):
            yield Label(self._prompt)
            yield OptionList(
                *[Option(label, id=key) for key, label in self._choices],
                id="cchoices",
            )

    def on_mount(self) -> None:
        self.query_one("#cchoices", OptionList).focus()

    @on(OptionList.OptionSelected, "#cchoices")
    def _pick(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(event.option_id)

    def action_cancel(self) -> None:
        self.dismiss(None)


class TextPromptScreen(ModalScreen[str | None]):
    """A one-line text prompt. Dismisses the entered text, or ``None`` on cancel."""

    CSS = """
    TextPromptScreen { align: center middle; }
    #tppanel {
        width: 70%; max-width: 80; height: auto;
        padding: 1 2; background: $panel; border: round $primary;
    }
    #tpinput { margin-top: 1; margin-bottom: 1; }
    #tpbuttons { height: auto; align: right middle; }
    #tpbuttons Button { margin-left: 2; }
    """
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(
        self, prompt: str, *, initial: str = "", placeholder: str = ""
    ) -> None:
        super().__init__()
        self._prompt = prompt
        self._initial = initial
        self._placeholder = placeholder

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="tppanel"):
            yield Label(self._prompt)
            yield Input(
                value=self._initial, placeholder=self._placeholder, id="tpinput"
            )
            with Horizontal(id="tpbuttons"):
                yield Button("Cancel", id="tpcancel")
                yield Button("OK", id="tpok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#tpinput", Input).focus()

    @on(Input.Submitted, "#tpinput")
    def _submit(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    @on(Button.Pressed, "#tpok")
    def _ok(self) -> None:
        self.dismiss(self.query_one("#tpinput", Input).value)

    @on(Button.Pressed, "#tpcancel")
    def _cancel(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class WatchPane(Vertical):
    """The expiry watch as a home *mode* (columns 1+2), not a modal.

    Follows the app-wide verb: **Enter opens the file**, a first click / ``→`` shows
    the record in column 3 *beside* the list (an ``OpenDocument`` message the host
    honours), ``x`` ignores the highlighted document (drops it from the watch by
    setting ``ignore_expiry``). Talks to the host only by message; the persistent bar
    filters the list via :meth:`apply_filter`. Lazily mounted once and never torn
    down, so its cursor/scroll survive being hidden.
    """

    DEFAULT_CSS = """
    WatchPane { height: 1fr; }
    WatchPane #wsummary { margin-bottom: 1; }
    WatchPane #watch { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("right", "detail", "Details"),
        Binding("x", "ignore", "Ignore"),
        Binding("question_mark", "toggle_help_panel", "Keys"),
    ]

    class OpenDocument(Message):
        """Show this document beside the watch — the host opens column 3."""

        def __init__(self, doc_id: str) -> None:
            super().__init__()
            self.doc_id = doc_id

    class CloseRequested(Message):
        """Esc — the host peels the newest layer (detail, then the mode)."""

    def __init__(self, store: Store, config: Config, *, today: date) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._today = today
        self._glyphs = glyphset.resolve(config.glyphs)
        self._filter = ""  # the persistent bar's per-surface search

    def compose(self) -> ComposeResult:
        yield Label(id="wsummary")
        yield DocumentList(id="watch")

    def on_mount(self) -> None:
        # First mount: load + take focus here (the host's _enter_watch_mode focus is
        # a tolerant no-op until the children exist — same as ReviewPane).
        self.refresh_watch()
        self.focus_active_pane()

    def apply_filter(self, text: str) -> None:
        """Filter the tracked list by name/tags (the bar's per-surface search)."""
        self._filter = text
        self.refresh_watch()

    def refresh_on_enter(self) -> None:
        """Host calls this on (re)entry: drop any stale filter and reload if already
        mounted. The first mount's on_mount does the initial load, so this must not
        query children before they exist."""
        self._filter = ""
        if self.is_mounted:
            self.refresh_watch()

    def reload_if_stale(self) -> None:
        # No cheap staleness flag here (unlike review): a returned-from-detail edit
        # just reloads — one load_all, and the watch is entered rarely.
        if self.is_mounted:
            self.refresh_watch()

    def focus_active_pane(self) -> None:
        lists = self.query("#watch")  # tolerant: absent until the pane mounts
        if lists:
            lists.first().focus()

    def refresh_watch(self) -> None:
        docs = self._store.load_all()
        tracked = query.tracked(docs, today=self._today)
        if self._filter:
            tracked = query.search(
                tracked,
                query.Filter(text=self._filter),
                today=self._today,
                threshold_days=self._config.expiry_threshold_days,
            )
        locations = self._store.load_locations()
        threshold = self._config.expiry_threshold_days
        # Flag members that lapse before a dated bundle needs them (Phase 10).
        flags = preparedness.event_flags(
            docs,
            self._store.load_bundles().values(),
            today=self._today,
            margin_days=threshold,
        )
        summary = self.query_one("#wsummary", Label)
        options = self.query_one("#watch", OptionList)
        options.clear_options()
        if not tracked:
            note = "nothing matches." if self._filter else "nothing tracked."
            summary.update(f"Expiry watch: {note}  (/ search · Esc closes)")
            return
        red = sum(
            1
            for doc in tracked
            if doc.expiry_status(self._today, threshold)
            in (ExpiryStatus.EXPIRED, ExpiryStatus.EXPIRING)
        )
        summary.update(
            f"Expiry watch — {len(tracked)} tracked · {red} within {threshold}d.  "
            "Enter opens the file · → details · x ignores · / search · Esc closes."
        )
        for doc in tracked:
            view = query.view(
                doc,
                root=self._config.syncthing_root,
                today=self._today,
                threshold_days=threshold,
            )
            docflags = flags.get(doc.id)
            note = ""
            if docflags:
                flag = docflags[0]  # worst (soonest-expired) first
                note = f"· needed {flag.event} for {flag.bundle_slug}"
            row = rows.watch_row(
                view,
                location_label=_loc_label(doc, locations),
                glyphs=self._glyphs,
                event_note=note,
            )
            options.add_option(Option(row, id=doc.id))

    def action_close(self) -> None:
        self.post_message(self.CloseRequested())

    def action_toggle_help_panel(self) -> None:
        toggle_help_panel(self)

    def action_ignore(self) -> None:
        doc = self._highlighted()
        if doc is None:
            return
        doc.ignore_expiry = True
        try:
            self._store.save(doc)
        except StaleWriteError:
            self.notify(
                "changed on disk since load; reopen the watch", severity="error"
            )
            return
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            return
        self.notify(f"ignoring {doc.name}")
        self.refresh_watch()

    def _highlighted(self) -> Document | None:
        option_id = _highlighted_id(self.query_one("#watch", OptionList))
        if option_id is None:
            return None
        try:  # one file read — not a whole load_all just to find a single document
            return self._store.load(option_id)
        except StoreError:
            return None

    @on(DocumentList.Previewed, "#watch")
    def _previewed(self, event: DocumentList.Previewed) -> None:
        """First click shows the record beside the list (stop it here so the host's
        #documents Previewed handler never sees this pane's clicks)."""
        event.stop()
        if event.option_id is not None:
            self.post_message(self.OpenDocument(event.option_id))

    @on(OptionList.OptionSelected, "#watch")
    def _activate(self, event: OptionList.OptionSelected) -> None:
        """Enter / second tap opens the document's file — the app-wide activate verb."""
        event.stop()
        doc = self._highlighted()
        if doc is not None:
            open_doc_file(self, self._config, doc)

    def action_detail(self) -> None:
        """`→` — ask the host to show the document's record in column 3."""
        doc = self._highlighted()
        if doc is not None:
            self.post_message(self.OpenDocument(doc.id))


class BundlesPane(Vertical):
    """The bundles surface as a home *mode* (columns 1+2), not a modal.

    Grouped by category, sorted chronologically. Opening a bundle posts
    ``OpenBundle`` (the host exits the mode and scopes the documents pane to it);
    ``d`` sets its date, ``t`` a template, ``c`` shows a readiness report (all via
    helper modals pushed from here — transient questions, not surfaces). A
    "suggested" section lists folder-derived proposals — ``a`` accepts, ``x``
    dismisses. The persistent bar filters the list via :meth:`apply_filter`. Lazily
    mounted once and never torn down.
    """

    _SUGGESTED = "\x00sug:"  # option-id prefix for a folder-bundle suggestion

    DEFAULT_CSS = """
    BundlesPane { height: 1fr; }
    BundlesPane #blsummary { margin-bottom: 1; }
    BundlesPane #bundle-list { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("d", "set_date", "Set date"),
        Binding("t", "set_template", "Template"),
        Binding("c", "check", "Readiness"),
        Binding("a", "accept", "Accept"),
        Binding("x", "ignore", "Dismiss"),
        Binding("question_mark", "toggle_help_panel", "Keys"),
    ]

    class OpenBundle(Message):
        """Scope the home's documents to this bundle — the host exits the mode."""

        def __init__(self, slug: str) -> None:
            super().__init__()
            self.slug = slug

    class CloseRequested(Message):
        """Esc — the host peels the newest layer."""

    def __init__(self, store: Store, config: Config, *, today: date) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._today = today
        self._glyphs = glyphset.resolve(config.glyphs)
        self._suggested: list[suggest.BundleSuggestion] = []
        self._filter = ""  # the persistent bar's per-surface search

    def compose(self) -> ComposeResult:
        yield Label(id="blsummary")
        yield OptionList(id="bundle-list")

    def on_mount(self) -> None:
        self.refresh_bundles()
        self.focus_active_pane()

    def apply_filter(self, text: str) -> None:
        """Filter bundles by slug/title (the bar's per-surface search); the suggested
        section drops out under a query."""
        self._filter = text
        self.refresh_bundles()

    def refresh_on_enter(self) -> None:
        self._filter = ""
        if self.is_mounted:
            self.refresh_bundles()

    def reload_if_stale(self) -> None:
        if self.is_mounted:
            self.refresh_bundles()

    def focus_active_pane(self) -> None:
        lists = self.query("#bundle-list")  # tolerant: absent until the pane mounts
        if lists:
            lists.first().focus()

    def refresh_bundles(self) -> None:
        term = self._filter.strip().lower()
        bundles = self._store.load_bundles()
        docs = self._store.load_all()
        counts = Counter(slug for doc in docs for slug in doc.bundles)
        state = self._store.load_suggestions()
        self._suggested = [] if term else suggest.live_bundles(docs, bundles, state)
        summary = self.query_one("#blsummary", Label)
        options = self.query_one("#bundle-list", OptionList)
        options.clear_options()
        if not bundles and not self._suggested:
            note = "no bundles match." if term else "No bundles yet."
            summary.update(f"{note}  (/ search · Esc closes)")
            return
        summary.update(
            f"{len(bundles)} bundles · {len(self._suggested)} suggested.  "
            "Enter opens · d date · t template · c readiness · "
            "a accept · x dismiss · / search · Esc closes."
        )
        templates = self._store.load_templates()
        readings = self._store.load_scans()
        for category, group in query.group_bundles(bundles.values()):
            shown = [b for b in group if self._matches(b, term)]
            if not shown:
                continue
            header = f"{category} ▸" if category else "— other —"
            options.add_option(Option(header, id=None))
            for bundle in shown:
                readiness = ""
                template = templates.get(bundle.template) if bundle.template else None
                if template is not None:
                    readiness = preparedness.check_bundle(
                        bundle,
                        template,
                        docs,
                        readings,
                        today=self._today,
                        margin_days=self._config.expiry_threshold_days,
                    ).summary
                row = rows.bundle_row(
                    bundle,
                    count=counts.get(bundle.slug, 0),
                    glyphs=self._glyphs,
                    readiness=readiness,
                )
                options.add_option(Option(row, id=bundle.slug))
        if self._suggested:
            options.add_option(
                Option("suggested ▸  (a accepts · x dismisses)", id=None)
            )
            for index, sug in enumerate(self._suggested):
                label = f"  {sug.slug}   ({len(sug.doc_ids)} docs · {sug.folder})"
                options.add_option(Option(label, id=f"{self._SUGGESTED}{index}"))

    @staticmethod
    def _matches(bundle: Bundle, term: str) -> bool:
        return (
            not term
            or term in bundle.slug.lower()
            or term in (bundle.title or "").lower()
        )

    def action_close(self) -> None:
        self.post_message(self.CloseRequested())

    def action_toggle_help_panel(self) -> None:
        toggle_help_panel(self)

    @on(OptionList.OptionSelected, "#bundle-list")
    def _open(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option_id is None or event.option_id.startswith(self._SUGGESTED):
            return  # a suggestion isn't a bundle yet — `a` accepts it, Enter waits
        self.post_message(self.OpenBundle(event.option_id))

    def action_accept(self) -> None:
        sug = self._highlighted_suggestion()
        if sug is None:
            return
        bundles = self._store.load_bundles()
        bundles.setdefault(sug.slug, Bundle(slug=sug.slug, title=sug.title))
        self._store.save_bundles(bundles)
        for doc_id in sug.doc_ids:
            doc = self._store.load(doc_id)
            if sug.slug in doc.bundles:
                continue
            doc.bundles = sorted({*doc.bundles, sug.slug})
            try:
                self._store.save(doc)
            except StaleWriteError:
                self.notify(f"{doc_id} changed on disk; skipped", severity="error")
            except StoreError as exc:
                self.notify(str(exc), severity="error")
        self.notify(f"created bundle {sug.slug}")
        self.refresh_bundles()

    def action_ignore(self) -> None:
        sug = self._highlighted_suggestion()
        if sug is None:
            return
        state = self._store.load_suggestions()
        state.dismiss_key(sug.key)
        self._store.save_suggestions(state)
        self.refresh_bundles()

    def _highlighted_suggestion(self) -> suggest.BundleSuggestion | None:
        option_id = _highlighted_id(self.query_one("#bundle-list", OptionList))
        if option_id is None or not option_id.startswith(self._SUGGESTED):
            return None
        which = int(option_id[len(self._SUGGESTED) :])
        return self._suggested[which] if 0 <= which < len(self._suggested) else None

    def action_set_date(self) -> None:
        bundle = self._highlighted()
        if bundle is None:
            return
        current = forms.iso(bundle.date)
        self.app.push_screen(
            TextPromptScreen(
                f"Date for {bundle.title} (YYYY-MM-DD, blank clears):",
                initial=current,
                placeholder="YYYY-MM-DD",
            ),
            lambda value: self._save_date(bundle.slug, value),
        )

    def _save_date(self, slug: str, value: str | None) -> None:
        if value is None:
            return
        try:
            new_date = forms.parse_iso(value)
        except ValueError as exc:
            self.notify(f"invalid date: {exc}", severity="error")
            return
        bundles = self._store.load_bundles()
        if slug not in bundles:
            return
        bundles[slug].date = new_date
        self._store.save_bundles(bundles)
        self.refresh_bundles()

    def action_set_template(self) -> None:
        bundle = self._highlighted()
        if bundle is None:
            return
        available = ", ".join(sorted(self._store.load_templates())) or "(none defined)"
        self.app.push_screen(
            TextPromptScreen(
                f"Template for {bundle.title} (blank clears). Available: {available}",
                initial=bundle.template or "",
            ),
            lambda value: self._save_template(bundle.slug, value),
        )

    def _save_template(self, slug: str, value: str | None) -> None:
        if value is None:
            return
        bundles = self._store.load_bundles()
        if slug not in bundles:
            return
        bundles[slug].template = value.strip() or None
        self._store.save_bundles(bundles)
        self.refresh_bundles()

    def action_check(self) -> None:
        bundle = self._highlighted()
        if bundle is None:
            return
        if not bundle.template:
            self.notify("attach a template first (t)", severity="warning")
            return
        template = self._store.load_templates().get(bundle.template)
        if template is None:
            self.notify(
                f"template '{bundle.template}' not in templates.toml", severity="error"
            )
            return
        self.app.push_screen(
            ReadinessScreen(
                self._store,
                self._config,
                bundle=bundle,
                template=template,
                today=self._today,
            )
        )

    def _highlighted(self) -> Bundle | None:
        option_id = _highlighted_id(self.query_one("#bundle-list", OptionList))
        if option_id is None:
            return None
        return self._store.load_bundles().get(option_id)


class ReadinessScreen(ModalScreen[None]):
    """A bundle's template checklist — gathered / problem / missing per requirement.

    Read-only: shows which required document types are gathered, which lapse before
    the event date, and which are missing, plus members matching no requirement.
    """

    CSS = """
    ReadinessScreen { align: center middle; }
    #rdpanel {
        width: 85%; height: 80%; padding: 1 2;
        background: $panel; border: round $primary;
    }
    #readiness { height: 1fr; }
    """
    BINDINGS = [Binding("escape", "close", "Close")]

    _MARK = {"gathered": "+", "problem": "!", "missing": "x"}

    def __init__(
        self,
        store: Store,
        config: Config,
        *,
        bundle: Bundle,
        template: Template,
        today: date,
    ) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._bundle = bundle
        self._template = template
        self._today = today

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="rdpanel"):
            yield Label(id="rdsummary")
            yield OptionList(id="readiness")

    def on_mount(self) -> None:
        docs = self._store.load_all()
        names = {d.id: (d.name or d.id) for d in docs}
        readiness = preparedness.check_bundle(
            self._bundle,
            self._template,
            docs,
            self._store.load_scans(),
            today=self._today,
            margin_days=self._config.expiry_threshold_days,
        )
        verdict = "READY" if readiness.ready else "not ready"
        self.query_one("#rdsummary", Label).update(
            f"{self._bundle.title} vs {self._template.title} — {verdict} · "
            f"{readiness.summary}.  (Esc closes)"
        )
        options = self.query_one("#readiness", OptionList)
        flagged = (preparedness.EventStatus.EXPIRED, preparedness.EventStatus.EXPIRING)
        for check in readiness.checks:
            mark = self._MARK[check.state.value]
            label = check.requirement.label + (
                " (optional)" if check.requirement.optional else ""
            )
            if not check.matched:
                options.add_option(Option(f"{mark} {label}: — missing —", id=None))
                continue
            for doc_id in check.matched:
                status = check.statuses.get(doc_id)
                flag = f"  [{status.value}]" if status in flagged else ""
                name = names.get(doc_id, doc_id)
                options.add_option(Option(f"{mark} {label}: {name}{flag}", id=doc_id))
        if readiness.extras:
            options.add_option(Option("extras (match no requirement) ▸", id=None))
            for doc_id in readiness.extras:
                options.add_option(Option(f"  {names.get(doc_id, doc_id)}", id=doc_id))

    def action_close(self) -> None:
        self.dismiss(None)


def _highlighted_id(options: OptionList) -> str | None:
    """The id of the currently highlighted option, or ``None`` if none is."""
    index = options.highlighted
    if index is None:
        return None
    return options.get_option_at_index(index).id


def _fill_doc_options(
    options: OptionList,
    docs: list[Document],
    needle: str,
    *,
    exclude: str | None = None,
    lead: Option | None = None,
) -> None:
    """Rebuild ``options`` as the docs matching ``needle`` (name/id, casefolded).

    ``exclude`` drops a doc by id; ``lead``, when given, is added first (e.g. a
    "clear supersession" sentinel) so it always sorts above the matches.
    """
    options.clear_options()
    if lead is not None:
        options.add_option(lead)
    needle = needle.casefold()
    for doc in docs:
        if doc.id == exclude:
            continue
        if needle and needle not in f"{doc.name} {doc.id}".casefold():
            continue
        options.add_option(Option(doc.name or doc.id, id=doc.id))


def _loc_label(doc: Document, locations: dict[str, Location]) -> str | None:
    slug = doc.effective_location
    if slug is None:
        return None
    title = locations[slug].title if slug in locations else slug
    slot = doc.effective_slot
    if slot is not None:
        sub = doc.effective_subslot
        title += f" · {slot}.{sub}" if sub is not None else f" · {slot}"
    return title


class SettingsPane(Vertical):
    """Edit device + synced settings as a home *mode* (columns 1+2), not a modal.

    Device settings (icons, scan endpoint / model / temperature / DPI) write to the
    per-device config; the expiry threshold is synced. ``ctrl+s`` saves and posts
    :class:`Saved` (the host reloads); Esc posts :class:`CloseRequested` (a cancel —
    unsaved edits are discarded). No per-surface search: it's a form, not a list.
    Lazily mounted once; entry resets the fields to the live config.
    """

    DEFAULT_CSS = """
    SettingsPane { height: 1fr; }
    SettingsPane #setpanel { height: 1fr; padding: 0 1; }
    SettingsPane .section { color: $accent; margin-top: 1; }
    SettingsPane .hint { color: $text-muted; }
    SettingsPane Input, SettingsPane Select { width: 1fr; margin-bottom: 1; }
    SettingsPane RadioSet { margin-bottom: 1; }
    """
    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    class Saved(Message):
        """Settings changed — the host reloads (threshold/scan apply now)."""

    class CloseRequested(Message):
        """Esc — cancel, discarding unsaved edits; the host exits the mode."""

    def __init__(self, config: Config) -> None:
        super().__init__()
        self._config = config

    def compose(self) -> ComposeResult:
        cfg = self._config
        with VerticalScroll(id="setpanel"):
            yield Label("Settings   ctrl+s save · esc cancel")
            yield Label("— This device —", classes="section")
            yield Label("Icons  (takes effect on restart)", classes="hint")
            with RadioSet(id="set-glyphs"):
                yield RadioButton(
                    "Nerd Font", value=cfg.glyphs != "ascii", id="glyph-nerd"
                )
                yield RadioButton(
                    "ASCII", value=cfg.glyphs == "ascii", id="glyph-ascii"
                )
            yield Label("Scan endpoint (base URL)")
            yield Input(value=cfg.scan_base_url, id="set-url")
            yield Label("Scan model")
            yield Select(
                [(cfg.scan_model, cfg.scan_model)],
                value=cfg.scan_model,
                allow_blank=False,
                id="set-model",
            )
            yield Label("Scan temperature")
            yield Input(value=str(cfg.scan_temperature), id="set-temp")
            yield Label("Scan DPI")
            yield Input(value=str(cfg.scan_dpi), id="set-dpi")
            yield Label("— Synced (shared across devices) —", classes="section")
            yield Label("Expiry threshold (days)")
            yield Input(value=str(cfg.expiry_threshold_days), id="set-threshold")

    def on_mount(self) -> None:
        self._load_models()
        self.focus_active_pane()

    def refresh_on_enter(self) -> None:
        """Reset the fields to the live config on (re)entry — a resident form would
        otherwise show the previous visit's half-typed, cancelled edits."""
        if not self.is_mounted:
            return  # first mount: compose() already seeded from config
        cfg = self._config
        self.query_one("#set-url", Input).value = cfg.scan_base_url
        self.query_one("#set-temp", Input).value = str(cfg.scan_temperature)
        self.query_one("#set-dpi", Input).value = str(cfg.scan_dpi)
        self.query_one("#set-threshold", Input).value = str(cfg.expiry_threshold_days)
        ascii_on = cfg.glyphs == "ascii"
        self.query_one("#glyph-ascii", RadioButton).value = ascii_on
        self.query_one("#glyph-nerd", RadioButton).value = not ascii_on
        self._load_models()  # refresh the model options + selection

    def focus_active_pane(self) -> None:
        fields = self.query("#set-url")  # tolerant: absent until the pane mounts
        if fields:
            fields.first().focus()

    @work(thread=True, exclusive=True, group="settings-models")
    def _load_models(self) -> None:
        try:  # a network call — never block compose
            models = scan.list_models(self._config)
        except ScanError:
            return  # router down: keep the current model as the sole option
        ids = [m.id for m in models if m.vision]
        if self._config.scan_model not in ids:
            ids.insert(0, self._config.scan_model)
        self.app.call_from_thread(self._set_model_options, ids)

    def _set_model_options(self, ids: list[str]) -> None:
        select = self.query_one("#set-model", Select)
        select.set_options((model_id, model_id) for model_id in ids)
        select.value = self._config.scan_model

    def action_cancel(self) -> None:
        self.post_message(self.CloseRequested())

    def action_save(self) -> None:
        cfg = self._config
        try:
            temperature = float(self.query_one("#set-temp", Input).value)
            dpi = int(self.query_one("#set-dpi", Input).value)
            threshold = int(self.query_one("#set-threshold", Input).value)
        except ValueError:
            self.notify("temperature/DPI/threshold must be numbers", severity="error")
            return
        ascii_on = self.query_one("#glyph-ascii", RadioButton).value
        glyphs = "ascii" if ascii_on else "nerd"
        url = self.query_one("#set-url", Input).value.strip() or cfg.scan_base_url
        model = str(self.query_one("#set-model", Select).value or cfg.scan_model)
        device = {
            "glyphs": glyphs,
            "scan_base_url": url,
            "scan_model": model,
            "scan_temperature": temperature,
            "scan_dpi": dpi,
        }
        (
            cfg.glyphs,
            cfg.scan_base_url,
            cfg.scan_model,
            cfg.scan_temperature,
            cfg.scan_dpi,
            cfg.expiry_threshold_days,
        ) = (glyphs, url, model, temperature, dpi, threshold)
        update_per_device(device)
        update_synced(cfg, {"expiry_threshold_days": threshold})
        self.post_message(self.Saved())
