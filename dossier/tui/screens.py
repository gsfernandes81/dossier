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

"""Modal screens: document detail/edit, and the doctor review list."""

from __future__ import annotations

from collections import Counter
from datetime import date

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    Label,
    OptionList,
)
from textual.widgets.option_list import Option

from dossier import doctor, query, suggest
from dossier.config import Config
from dossier.errors import StaleWriteError, StoreError
from dossier.model import Bundle, Document, ExpiryStatus, Location
from dossier.store import Store
from dossier.tui import (
    forms,
    glyphs as glyphset,
    rows,
)


class DoctorScreen(ModalScreen[str | None]):
    """List doctor findings. Dismisses with a document id to open its editor."""

    CSS = """
    DoctorScreen { align: center middle; }
    #dpanel {
        width: 85%; height: 80%; padding: 1 2;
        background: $panel; border: round $primary;
    }
    #findings { height: 1fr; }
    """
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, store: Store, config: Config) -> None:
        super().__init__()
        self._store = store
        self._config = config

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="dpanel"):
            yield Label(id="summary")
            yield OptionList(id="findings")

    def on_mount(self) -> None:
        report = doctor.run(self._store, self._config)
        summary = self.query_one("#summary", Label)
        options = self.query_one("#findings", OptionList)
        if not report.findings:
            summary.update("doctor: all clear.  (Esc to close)")
            return
        summary.update(
            f"doctor: {len(report.findings)} finding(s). "
            "Enter a document to edit it; Esc closes."
        )
        for check, items in sorted(report.by_check().items()):
            options.add_option(Option(f"— {check} ({len(items)}) —", id=None))
            for finding in items:
                doc_id = None if finding.check == "sync-conflict" else finding.subject
                options.add_option(
                    Option(f"  {finding.subject}: {finding.detail}", id=doc_id)
                )

    def action_close(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected)
    def _open(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is not None:
            self.dismiss(event.option_id)


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
        options.clear_options()
        if self._doc.supersedes:
            options.add_option(Option("— clear supersession —", id=self._CLEAR))
        needle = needle.casefold()
        for doc in self._docs:
            if doc.id == self._doc.id:
                continue
            if needle and needle not in f"{doc.name} {doc.id}".casefold():
                continue
            options.add_option(Option(doc.name or doc.id, id=doc.id))

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

    def __init__(self, docs: list[Document], *, prompt: str, initial: str = "") -> None:
        super().__init__()
        self._docs = docs
        self._prompt = prompt
        self._initial = initial

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
        options.clear_options()
        needle = needle.casefold()
        for doc in self._docs:
            if needle and needle not in f"{doc.name} {doc.id}".casefold():
                continue
            options.add_option(Option(doc.name or doc.id, id=doc.id))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "pfilter":
            self._populate(event.value)

    @on(OptionList.OptionSelected, "#pcandidates")
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


class WatchScreen(ModalScreen[str | None]):
    """The expiry watch — tracked documents, soonest expiry first.

    Dismisses with a document id (open it) or ``None``. ``i`` ignores the
    highlighted document, dropping it from the watch (sets ``ignore_expiry``).
    """

    CSS = """
    WatchScreen { align: center middle; }
    #wpanel {
        width: 85%; height: 80%; padding: 1 2;
        background: $panel; border: round $primary;
    }
    #watch { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("i", "ignore", "Ignore"),
    ]

    def __init__(self, store: Store, config: Config, *, today: date) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._today = today
        self._glyphs = glyphset.resolve(config.glyphs)

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="wpanel"):
            yield Label(id="wsummary")
            yield OptionList(id="watch")

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        tracked = query.tracked(self._store.load_all(), today=self._today)
        locations = self._store.load_locations()
        threshold = self._config.expiry_threshold_days
        summary = self.query_one("#wsummary", Label)
        options = self.query_one("#watch", OptionList)
        options.clear_options()
        if not tracked:
            summary.update("Expiry watch: nothing tracked.  (Esc to close)")
            return
        red = sum(
            1
            for doc in tracked
            if doc.expiry_status(self._today, threshold)
            in (ExpiryStatus.EXPIRED, ExpiryStatus.EXPIRING)
        )
        summary.update(
            f"Expiry watch — {len(tracked)} tracked · {red} within {threshold}d.  "
            "Enter opens · i ignores · Esc closes."
        )
        for doc in tracked:
            view = query.view(
                doc,
                root=self._config.syncthing_root,
                today=self._today,
                threshold_days=threshold,
            )
            row = rows.watch_row(
                view, location_label=_loc_label(doc, locations), glyphs=self._glyphs
            )
            options.add_option(Option(row, id=doc.id))

    def action_close(self) -> None:
        self.dismiss(None)

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
        self._refresh()

    def _highlighted(self) -> Document | None:
        options = self.query_one("#watch", OptionList)
        index = options.highlighted
        if index is None:
            return None
        option = options.get_option_at_index(index)
        if option.id is None:
            return None
        return next((d for d in self._store.load_all() if d.id == option.id), None)

    @on(OptionList.OptionSelected, "#watch")
    def _open(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is not None:
            self.dismiss(event.option_id)


class BundlesScreen(ModalScreen[str | None]):
    """The bundles surface — grouped by category, sorted chronologically.

    Dismisses with a bundle slug (the home filters the documents pane to it) or
    ``None``. ``Enter`` opens a bundle; ``d`` sets its date. A "suggested" section
    lists folder-derived bundle proposals — ``a`` accepts (creates the bundle and
    assigns its documents), ``i`` dismisses (persists, never reappears).
    """

    _SUGGESTED = "\x00sug:"  # option-id prefix for a folder-bundle suggestion

    CSS = """
    BundlesScreen { align: center middle; }
    #blpanel {
        width: 85%; height: 80%; padding: 1 2;
        background: $panel; border: round $primary;
    }
    #bundle-list { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("d", "set_date", "Set date"),
        Binding("a", "accept", "Accept"),
        Binding("i", "ignore", "Dismiss"),
    ]

    def __init__(self, store: Store, config: Config, *, today: date) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._today = today
        self._glyphs = glyphset.resolve(config.glyphs)
        self._suggested: list[suggest.BundleSuggestion] = []

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="blpanel"):
            yield Label(id="blsummary")
            yield OptionList(id="bundle-list")

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        bundles = self._store.load_bundles()
        docs = self._store.load_all()
        counts = Counter(slug for doc in docs for slug in doc.bundles)
        state = self._store.load_suggestions()
        self._suggested = suggest.live_bundles(docs, bundles, state)
        summary = self.query_one("#blsummary", Label)
        options = self.query_one("#bundle-list", OptionList)
        options.clear_options()
        if not bundles and not self._suggested:
            summary.update("No bundles yet.  (Esc to close)")
            return
        summary.update(
            f"{len(bundles)} bundles · {len(self._suggested)} suggested.  "
            "Enter opens · d date · a accept · i dismiss · Esc closes."
        )
        for category, group in query.group_bundles(bundles.values()):
            header = f"{category} ▸" if category else "— other —"
            options.add_option(Option(header, id=None))
            for bundle in group:
                row = rows.bundle_row(
                    bundle, count=counts.get(bundle.slug, 0), glyphs=self._glyphs
                )
                options.add_option(Option(row, id=bundle.slug))
        if self._suggested:
            options.add_option(
                Option("suggested ▸  (a accepts · i dismisses)", id=None)
            )
            for index, sug in enumerate(self._suggested):
                label = f"  {sug.slug}   ({len(sug.doc_ids)} docs · {sug.folder})"
                options.add_option(Option(label, id=f"{self._SUGGESTED}{index}"))

    def action_close(self) -> None:
        self.dismiss(None)

    @on(OptionList.OptionSelected, "#bundle-list")
    def _open(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is None:
            return
        if event.option_id.startswith(self._SUGGESTED):
            self.action_accept()  # Enter on a suggestion accepts it
        else:
            self.dismiss(event.option_id)

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
        self._refresh()

    def action_ignore(self) -> None:
        sug = self._highlighted_suggestion()
        if sug is None:
            return
        state = self._store.load_suggestions()
        state.dismiss_key(sug.key)
        self._store.save_suggestions(state)
        self._refresh()

    def _highlighted_suggestion(self) -> suggest.BundleSuggestion | None:
        options = self.query_one("#bundle-list", OptionList)
        index = options.highlighted
        if index is None:
            return None
        option_id = options.get_option_at_index(index).id
        if option_id is None or not option_id.startswith(self._SUGGESTED):
            return None
        which = int(option_id[len(self._SUGGESTED) :])
        return self._suggested[which] if 0 <= which < len(self._suggested) else None

    def action_set_date(self) -> None:
        bundle = self._highlighted()
        if bundle is None:
            return
        current = bundle.date.isoformat() if bundle.date else ""
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
        self._refresh()

    def _highlighted(self) -> Bundle | None:
        options = self.query_one("#bundle-list", OptionList)
        index = options.highlighted
        if index is None:
            return None
        option = options.get_option_at_index(index)
        if option.id is None:
            return None
        return self._store.load_bundles().get(option.id)


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
