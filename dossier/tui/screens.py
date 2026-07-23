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

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
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

from dossier import doctor, preparedness, query, resolve, scan, suggest
from dossier.config import Config, update_per_device, update_synced
from dossier.errors import ResolveBusyError, ScanError, StaleWriteError, StoreError
from dossier.model import Bundle, Document, ExpiryStatus, Location, Template
from dossier.platform_open import OpenError, open_file
from dossier.store import Store
from dossier.tui import (
    forms,
    glyphs as glyphset,
    rows,
)


class DoctorScreen(ModalScreen[str | None]):
    """List doctor findings. Dismisses with a document id to open its editor."""

    _SEP = "\x00"  # composite option id: f"{doc_id}{sep}{index}" (ids must be unique)

    CSS = """
    DoctorScreen { align: center middle; }
    #dpanel {
        width: 85%; height: 80%; padding: 1 2;
        background: $panel; border: round $primary;
    }
    #findings { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("o", "open_file", "Open"),
    ]

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
        index = 0
        for check, items in sorted(report.by_check().items()):
            options.add_option(Option(f"— {check} ({len(items)}) —", id=None))
            hint = doctor.CHECK_HINTS.get(check)
            if hint:
                options.add_option(Option(f"  → {hint}", id=None))
            for finding in items:
                # A doc can appear in several findings; a composite id keeps them
                # unique (else OptionList raises DuplicateID). Conflicts aren't docs.
                oid = (
                    None
                    if finding.check == "sync-conflict"
                    else f"{finding.subject}{self._SEP}{index}"
                )
                options.add_option(
                    Option(f"  {finding.subject}: {finding.detail}", id=oid)
                )
                index += 1

    def action_close(self) -> None:
        self.dismiss(None)

    def action_open_file(self) -> None:
        """Open the highlighted finding's document file (xdg/termux opener)."""
        option_id = _highlighted_id(self.query_one("#findings", OptionList))
        if option_id is None:
            return
        doc_id = option_id.split(self._SEP, 1)[0]
        doc = next((d for d in self._store.load_all() if d.id == doc_id), None)
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

    @on(OptionList.OptionSelected)
    def _open(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is not None:
            self.dismiss(event.option_id.split(self._SEP, 1)[0])  # back to the doc id


class ResolveScreen(ModalScreen[bool]):
    """Review and merge Syncthing conflict files in-app (the TUI face of `ds resolve`).

    Read-only until the user acts: highlighting a conflict previews the planned
    merge (contested fields and their last-writer-wins verdict); ``a`` merges them
    all, Enter merges the highlighted one. Every merge is recoverable — the losing
    copy is archived first — so this stays a one-key action, not a wizard.
    """

    CSS = """
    ResolveScreen { align: center middle; }
    #rvpanel {
        width: 85%; height: 80%; padding: 1 2;
        background: $panel; border: round $primary;
    }
    #rvsummary { margin-bottom: 1; }
    #rvlist { height: 1fr; }
    #rvdetail {
        height: auto; max-height: 45%; padding-top: 1;
        border-top: solid $primary 30%; color: $text-muted;
    }
    """
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("a", "apply_all", "Merge all"),
        Binding("enter", "apply_one", "Merge selected", show=False),
    ]

    def __init__(self, store: Store, config: Config) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._plans: list[resolve.Resolution] = []
        self._applied = False

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="rvpanel"):
            yield Label(id="rvsummary")
            yield OptionList(id="rvlist")
            yield Static(id="rvdetail")

    def on_mount(self) -> None:
        self._refresh()

    def _refresh(self) -> None:
        self._plans = []
        for item in resolve.find_conflicts(self._store):
            try:
                self._plans.append(resolve.plan(self._store, item))
            except StoreError:
                continue  # an unreadable conflict; doctor surfaces it instead
        summary = self.query_one("#rvsummary", Label)
        options = self.query_one("#rvlist", OptionList)
        options.clear_options()
        detail = self.query_one("#rvdetail", Static)
        if not self._plans:
            summary.update("No sync conflicts to merge.  (Esc closes)")
            detail.update("")
            return
        summary.update(
            f"{len(self._plans)} conflict(s).  "
            "a merges all · Enter merges the selected · Esc closes"
        )
        for index, plan in enumerate(self._plans):
            options.add_option(Option(self._headline(plan), id=str(index)))
        self._show_detail(0)

    @staticmethod
    def _headline(plan: resolve.Resolution) -> str:
        if plan.loud:
            tag = "whole-file replace"
        elif plan.contested:
            tag = f"{len(plan.contested)} contested field(s)"
        elif plan.changed:
            tag = "auto-merge"
        else:
            tag = "identical — will clear"
        return f"{plan.kind:11} {plan.name}  —  {tag}"

    @on(OptionList.OptionHighlighted, "#rvlist")
    def _on_highlight(self, event: OptionList.OptionHighlighted) -> None:
        if event.option_id is not None:
            self._show_detail(int(event.option_id))

    def _show_detail(self, index: int) -> None:
        if not 0 <= index < len(self._plans):
            return
        plan = self._plans[index]
        lines = [_resolve_decision_text(d) for d in plan.contested]
        fills = sum(1 for d in plan.decisions if d.action == "fill")
        unions = sum(1 for d in plan.decisions if d.action == "union")
        if fills:
            lines.append(f"+ {fills} field(s) filled from the other copy")
        if unions:
            lines.append(f"∪ {unions} list(s)/table(s) merged")
        if not lines:
            lines.append("identical copy — will be cleared")
        self.query_one("#rvdetail", Static).update("\n".join(lines))

    def action_apply_all(self) -> None:
        report = resolve.resolve_all(self._store, apply=True)
        if report.resolutions:
            self._applied = True
        message = f"merged {len(report.resolutions)} conflict(s)"
        severity = "information"
        if report.skipped:
            message += f", {len(report.skipped)} changed mid-merge (retry)"
            severity = "warning"
        self.notify(message, severity=severity)
        self._refresh()

    def action_apply_one(self) -> None:
        option_id = _highlighted_id(self.query_one("#rvlist", OptionList))
        if option_id is None:
            return
        item = self._plans[int(option_id)].item
        try:
            fresh = resolve.plan(self._store, item)  # re-plan against current live
            resolve.apply_resolution(self._store, fresh)
        except ResolveBusyError:
            self.notify("changed mid-merge — retry", severity="warning")
        except StoreError as exc:
            self.notify(str(exc), severity="error")
        else:
            self._applied = True
            self.notify(f"merged {fresh.name}")
        self._refresh()

    def action_close(self) -> None:
        self.dismiss(self._applied)


def _resolve_decision_text(decision: resolve.FieldDecision) -> str:
    winner = decision.winner.value if decision.winner else "ours"
    return f"~ {decision.field}: {decision.ours} vs {decision.theirs} → keep {winner}"


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
        docs = self._store.load_all()
        tracked = query.tracked(docs, today=self._today)
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
        option_id = _highlighted_id(self.query_one("#watch", OptionList))
        if option_id is None:
            return None
        return next((d for d in self._store.load_all() if d.id == option_id), None)

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
        Binding("t", "set_template", "Template"),
        Binding("c", "check", "Readiness"),
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
            "Enter opens · d date · t template · c readiness · "
            "a accept · i dismiss · Esc closes."
        )
        templates = self._store.load_templates()
        readings = self._store.load_scans()
        for category, group in query.group_bundles(bundles.values()):
            header = f"{category} ▸" if category else "— other —"
            options.add_option(Option(header, id=None))
            for bundle in group:
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
        self._refresh()

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
        self._refresh()

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


class SettingsScreen(ModalScreen[bool]):
    """Edit device + synced settings; dismisses True when something changed.

    Device settings (icons, scan endpoint / model / temperature / DPI) write to the
    per-device config; the expiry threshold is synced. Changes apply on the next
    home reload — except the icon set, baked into composed widgets, which takes
    effect on restart.
    """

    CSS = """
    SettingsScreen { align: center middle; }
    #setpanel {
        width: 80%; max-width: 84; height: 85%;
        padding: 1 2; background: $panel; border: round $primary;
    }
    SettingsScreen .section { color: $accent; margin-top: 1; }
    SettingsScreen .hint { color: $text-muted; }
    SettingsScreen Input, SettingsScreen Select { width: 1fr; margin-bottom: 1; }
    SettingsScreen RadioSet { margin-bottom: 1; }
    """
    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

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
                yield RadioButton("Nerd Font", value=cfg.glyphs != "ascii")
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

    @work(thread=True, exclusive=True)
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
        self.dismiss(False)

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
        self.dismiss(True)
