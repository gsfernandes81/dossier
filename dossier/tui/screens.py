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
from textual.widgets import Button, Input, Label, OptionList, TextArea
from textual.widgets.option_list import Option

from dossier import doctor
from dossier.config import Config
from dossier.errors import StaleWriteError, StoreError
from dossier.model import Document
from dossier.store import Store


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

    def __init__(self, store: Store, doc: Document) -> None:
        super().__init__()
        self._store = store
        self._doc = doc

    def compose(self) -> ComposeResult:
        doc = self._doc
        with VerticalScroll(id="panel"):
            yield Label(f"Editing: {doc.id}")
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


def _iso(value: date | None) -> str:
    return value.isoformat() if value else ""


def _parse_iso(text: str) -> date | None:
    text = text.strip()
    return date.fromisoformat(text) if text else None


def _slug(text: str) -> str | None:
    return text.strip() or None
