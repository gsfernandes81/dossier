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
    SelectionList,
    TextArea,
)
from textual.widgets.option_list import Option
from textual.widgets.selection_list import Selection

from dossier import doctor, query
from dossier.config import Config
from dossier.errors import StaleWriteError, StoreError
from dossier.migrate import slugify
from dossier.model import Bundle, Document, ExpiryStatus, Location
from dossier.store import Store
from dossier.tui import (
    glyphs as glyphset,
    rows,
)


class DetailScreen(ModalScreen[bool]):
    """View and edit one document. Dismisses ``True`` when saved."""

    CSS = """
    DetailScreen { align: center middle; }
    #panel {
        width: 80%; max-width: 96; height: auto; max-height: 90%;
        padding: 1 2; background: $panel; border: round $primary;
    }
    #panel Input, #panel TextArea { margin-bottom: 1; }
    #panel TextArea { height: 5; }
    .hint { color: $text-muted; }
    #buttons { height: auto; align: right middle; }
    #buttons Button { margin-left: 2; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, store: Store, doc: Document, *, is_new: bool = False) -> None:
        super().__init__()
        self._store = store
        self._doc = doc
        self._is_new = is_new

    def compose(self) -> ComposeResult:
        doc = self._doc
        with VerticalScroll(id="panel"):
            yield Label("New document" if self._is_new else f"Editing: {doc.id}")
            yield Label("Name")
            yield Input(value=doc.name, id="name")
            for token, readings in doctor.candidate_readings(doc.name):
                dates = " / ".join(d.isoformat() for d in readings)
                yield Label(f"ambiguous {token} -> {dates}", classes="hint")
            yield Label("Issue date (YYYY-MM-DD)")
            yield Input(value=_iso(doc.issue_date), id="issue")
            yield Label("Expiry date (YYYY-MM-DD)")
            yield Input(value=_iso(doc.expiry_date), id="expiry")
            yield Label("Permanent location (slug)")
            yield Input(value=doc.perm_location or "", id="perm")
            yield Label("Temporary location (slug)")
            yield Input(value=doc.temp_location or "", id="temp")
            yield Label("Tags (space-separated)")
            yield Input(value=" ".join(doc.tags), id="tags")
            yield Label("Notes")
            yield TextArea(doc.notes, id="notes")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="save", variant="primary")

    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#cancel")
    def _on_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#save")
    def _on_save(self) -> None:
        self.action_save()

    def action_save(self) -> None:
        doc = self._doc
        try:
            issue = _parse_iso(self.query_one("#issue", Input).value)
            expiry = _parse_iso(self.query_one("#expiry", Input).value)
        except ValueError as exc:
            self.notify(f"invalid date: {exc}", severity="error")
            return

        doc.name = self.query_one("#name", Input).value.strip()
        if self._is_new:
            if not doc.name:
                self.notify("a new document needs a name", severity="error")
                return
            doc.id = _unique_id(self._store, slugify(doc.name))
        doc.issue_date = issue
        doc.expiry_date = expiry
        doc.perm_location = _slug(self.query_one("#perm", Input).value)
        doc.temp_location = _slug(self.query_one("#temp", Input).value)
        doc.tags = self.query_one("#tags", Input).value.split()
        doc.notes = self.query_one("#notes", TextArea).text.strip()

        try:
            self._store.save(doc)
        except StaleWriteError:
            self.notify(
                "changed on disk since load; reopen and retry", severity="error"
            )
            return
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            return
        self.dismiss(True)


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


class MoveScreen(ModalScreen[bool]):
    """Move a document to a location + slot, shifting neighbours to insert."""

    CSS = """
    MoveScreen { align: center middle; }
    #mpanel {
        width: 70%; max-width: 80; height: auto;
        padding: 1 2; background: $panel; border: round $primary;
    }
    #mpanel Input { margin-bottom: 1; }
    #mbuttons { height: auto; align: right middle; }
    #mbuttons Button { margin-left: 2; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Move"),
    ]

    def __init__(self, store: Store, docs: list[Document], doc: Document) -> None:
        super().__init__()
        self._store = store
        self._docs = docs
        self._doc = doc

    def compose(self) -> ComposeResult:
        doc = self._doc
        with VerticalScroll(id="mpanel"):
            yield Label(f"Move: {doc.name}")
            yield Label("Permanent location (slug, blank to clear)")
            yield Input(value=doc.perm_location or "", id="mloc")
            yield Label("Slot (blank for none; inserting shifts neighbours)")
            yield Input(value=_int(doc.perm_slot), id="mslot")
            with Horizontal(id="mbuttons"):
                yield Button("Cancel", id="mcancel")
                yield Button("Move", id="mmove", variant="primary")

    def action_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#mcancel")
    def _on_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#mmove")
    def _on_move(self) -> None:
        self.action_save()

    def action_save(self) -> None:
        location = _slug(self.query_one("#mloc", Input).value)
        try:
            slot = _parse_int(self.query_one("#mslot", Input).value)
        except ValueError:
            self.notify("slot must be a whole number", severity="error")
            return
        try:
            for doc in query.plan_move(self._docs, self._doc, location, slot):
                self._store.save(doc)
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            return
        self.dismiss(True)


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


class BundleScreen(ModalScreen[bool]):
    """Toggle a document's bundle membership; type a name to make a new bundle."""

    CSS = """
    BundleScreen { align: center middle; }
    #bpanel {
        width: 70%; max-width: 80; height: auto; max-height: 90%;
        padding: 1 2; background: $panel; border: round $primary;
    }
    #bundles { height: auto; max-height: 12; margin-bottom: 1; }
    #bnew { margin-bottom: 1; }
    #bbuttons { height: auto; align: right middle; }
    #bbuttons Button { margin-left: 2; }
    """
    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+s", "save", "Save"),
    ]

    def __init__(self, store: Store, docs: list[Document], doc: Document) -> None:
        super().__init__()
        self._store = store
        self._docs = docs
        self._doc = doc
        self._slugs: set[str] = set()
        self._new_titles: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="bpanel"):
            yield Label(f'Bundles for "{self._doc.name or self._doc.id}"')
            yield SelectionList[str](id="bundles")
            yield Label("Add to a new bundle (type a name, Enter):")
            yield Input(placeholder="new bundle name…", id="bnew")
            with Horizontal(id="bbuttons"):
                yield Button("Cancel", id="bcancel")
                yield Button("Save", id="bsave", variant="primary")

    def on_mount(self) -> None:
        current = set(self._doc.bundles)
        selection = self.query_one("#bundles", SelectionList)
        for slug, title in self._known_bundles():
            self._slugs.add(slug)
            selection.add_option(Selection(title, slug, slug in current))

    def _known_bundles(self) -> list[tuple[str, str]]:
        titles = {
            slug: bundle.title for slug, bundle in self._store.load_bundles().items()
        }
        for doc in self._docs:
            for slug in doc.bundles:
                titles.setdefault(slug, slug)
        return sorted(titles.items())

    @on(Input.Submitted, "#bnew")
    def _add_new(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        event.input.value = ""
        if not name:
            return
        slug = slugify(name)
        if slug in self._slugs:
            self.notify(f"{slug} is already listed")
            return
        self._slugs.add(slug)
        self._new_titles[slug] = name
        self.query_one("#bundles", SelectionList).add_option(
            Selection(name, slug, True)
        )

    @on(Button.Pressed, "#bcancel")
    def _on_cancel(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#bsave")
    def _on_save(self) -> None:
        self.action_save()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_save(self) -> None:
        selected = list(self.query_one("#bundles", SelectionList).selected)
        self._doc.bundles = sorted(selected)

        bundles = self._store.load_bundles()
        new = [slug for slug in selected if slug not in bundles]
        if new:
            for slug in new:
                title = self._new_titles.get(slug, slug.replace("-", " "))
                bundles[slug] = Bundle(slug=slug, title=title)
            self._store.save_bundles(bundles)

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


def _iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def _int(value: int | None) -> str:
    return "" if value is None else str(value)


def _parse_int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text else None


def _unique_id(store: Store, base: str) -> str:
    slug, n = base, 2
    while store.document_path(slug).exists():
        slug, n = f"{base}-{n}", n + 1
    return slug


def _parse_iso(text: str) -> date | None:
    text = text.strip()
    return date.fromisoformat(text) if text else None


def _slug(text: str) -> str | None:
    return text.strip() or None
