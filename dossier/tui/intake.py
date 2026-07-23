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

"""The intake review card — file dropped documents one at a time.

A modal over the home screen: for each unfiled inbox file it reads the document
with the VLM in a background worker (:mod:`dossier.intake`), shows the whole
proposed record, and files it on one keystroke. Editing hands off to the home
detail pane (the card never grows its own form). Dismisses with a filed doc id
when the user chose to edit it, else ``None``.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label
from textual.worker import get_current_worker

from dossier import intake
from dossier.config import Config
from dossier.errors import IntakeError
from dossier.platform_open import OpenError, open_file
from dossier.query import resolve_path
from dossier.store import Store
from dossier.tui.screens import TextPromptScreen


class IntakeScreen(ModalScreen[str | None]):
    """Review + file dropped documents, one proposal card at a time."""

    BINDINGS = [
        Binding("a", "accept", "File"),
        Binding("e", "accept_edit", "File + edit"),
        Binding("n", "rename", "Rename"),
        Binding("s", "toggle_succession", "Succession"),
        Binding("k", "skip", "Skip"),
        Binding("x", "reject", "Not a doc"),
        Binding("o", "open_file", "Open"),
        Binding("escape", "close", "Close"),
    ]

    DEFAULT_CSS = """
    IntakeScreen { align: center middle; }
    IntakeScreen #ipanel {
        width: 90%; max-width: 100; height: auto; max-height: 90%;
        padding: 1 2; border: round $primary; background: $surface;
    }
    IntakeScreen #ihead { text-style: bold; }
    IntakeScreen #ifoot { color: $text-muted; margin-top: 1; }
    """

    def __init__(self, store: Store, config: Config) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._pending: list[str] = []
        self._index = 0
        self._filed = 0
        self._proposal: intake.IntakeProposal | None = None
        self._succession_on = True

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="ipanel"):
            yield Label(id="ihead")
            yield Label(id="ibody")
            yield Label(_KEYS, id="ifoot")

    def on_mount(self) -> None:
        self._pending = intake.pending_files(self._store, self._config)
        if not self._pending:
            self.query_one("#ihead", Label).update("Inbox empty — nothing to file.")
            self.query_one("#ifoot", Label).update("Esc  close")
            return
        self._read_current()

    # -- reading (background VLM) --------------------------------------------

    @work(thread=True, group="vision", exclusive=True)
    def _read_current(self) -> None:
        """Read the current file with the VLM and render its proposal."""
        worker = get_current_worker()
        # docs/readings reloaded per card so a succession link sees just-filed docs.
        docs = self._store.load_all()
        readings = self._store.load_scans()
        index, total = self._index, len(self._pending)
        rel = self._pending[index]
        self.app.call_from_thread(
            self._show_progress, f"reading {index + 1}/{total}…", rel
        )
        try:
            proposal = intake.build_proposal(
                rel, self._store, self._config, docs=docs, readings=readings
            )
        except Exception as exc:  # unreadable file (non-doc, VLM down): let user skip
            if not worker.is_cancelled:
                self.app.call_from_thread(self._show_error, index, total, rel, str(exc))
            return
        if not worker.is_cancelled:
            self.app.call_from_thread(self._show_proposal, proposal)

    def _show_progress(self, head: str, rel: str) -> None:
        self._proposal = None
        self.query_one("#ihead", Label).update(head)
        self.query_one("#ibody", Label).update(rel)

    def _show_error(self, index: int, total: int, rel: str, message: str) -> None:
        self._proposal = None
        self.query_one("#ihead", Label).update(f"could not read  {index + 1}/{total}")
        self.query_one("#ibody", Label).update(
            f"{rel}\n\n{message}\n\nk skip · x not-a-doc"
        )

    def _show_proposal(self, proposal: intake.IntakeProposal) -> None:
        self._proposal = proposal
        self._succession_on = proposal.succession is not None
        doc = proposal.doc
        lines = [f"name    {doc.name}   (id {doc.id})"]
        if doc.tags:
            lines.append(f"tags    {' '.join(doc.tags)}")
        if doc.issue_date:
            lines.append(f"issue   {doc.issue_date}")
        if doc.expiry_date:
            lines.append(f"expiry  {doc.expiry_date}")
        if doc.notes:
            lines.append(f"notes   {doc.notes.splitlines()[-1]}")
        if proposal.succession is not None:
            mark = "on" if self._succession_on else "off (press s)"
            conf = proposal.succession.confidence
            lines.append(
                f"renews  {proposal.succession.older}  (conf {conf:.2f}) [{mark}]"
            )
        note = f"  [{','.join(proposal.notes)}]" if proposal.notes else ""
        arrow = "->" if proposal.moves else "= (in place)"
        lines.append(f"file    {proposal.src_rel}  {arrow}  {proposal.dst_rel}{note}")
        for question in proposal.open_questions:
            vals = " / ".join(question.values)
            lines.append(f"?       {question.field.value}: {vals}  (pick in the pane)")
        lines.append(
            f"read    conf {proposal.reading.confidence:.2f}, {proposal.reading.model}"
        )
        index, total = self._index, len(self._pending)
        self.query_one("#ihead", Label).update(f"Intake  {index + 1}/{total}")
        self.query_one("#ibody", Label).update("\n".join(lines))

    # -- actions -------------------------------------------------------------

    def action_accept(self) -> None:
        self._file(edit=False)

    def action_accept_edit(self) -> None:
        self._file(edit=True)

    def _file(self, *, edit: bool) -> None:
        proposal = self._proposal
        if proposal is None:
            self.notify("still reading — wait a moment", severity="warning")
            return
        try:
            doc, errors = intake.apply_proposal(proposal, self._store, self._config)
        except IntakeError as exc:
            self.notify(str(exc), severity="error")
            self._advance()
            return
        for message in errors:
            self.notify(message, severity="warning")
        self._filed += 1
        if edit:
            self.dismiss(doc.id)  # home opens the detail pane in edit mode
            return
        self.notify(f"filed {doc.id}")
        self._advance()

    def action_rename(self) -> None:
        proposal = self._proposal
        if proposal is None:
            return
        self.app.push_screen(
            TextPromptScreen("Document name:", initial=proposal.doc.name), self._renamed
        )

    def _renamed(self, name: str | None) -> None:
        proposal = self._proposal
        if proposal is None or not name or not name.strip():
            return
        self._proposal = intake.with_name(
            proposal, name.strip(), self._store, self._config
        )
        self._show_proposal(self._proposal)

    def action_toggle_succession(self) -> None:
        proposal = self._proposal
        if proposal is None or proposal.succession is None:
            return
        self._succession_on = not self._succession_on
        proposal.doc.supersedes = (
            proposal.succession.older if self._succession_on else None
        )
        self._show_proposal(proposal)

    def action_skip(self) -> None:
        self._advance()

    def action_reject(self) -> None:
        if self._index >= len(self._pending):
            return
        rel = self._pending[self._index]
        state = self._store.load_reconcile()
        state.dismissed.add(rel)  # reconcile already means "not a document"
        self._store.save_reconcile(state)
        self.notify(f"rejected {rel}")
        self._advance()

    def action_open_file(self) -> None:
        if self._index >= len(self._pending):
            return
        rel = self._pending[self._index]
        try:
            open_file(resolve_path(self._config.syncthing_root, rel))
        except OpenError as exc:
            self.notify(str(exc), severity="error")

    def action_close(self) -> None:
        self.dismiss(None)

    def _advance(self) -> None:
        self._proposal = None
        self._index += 1
        if self._index >= len(self._pending):
            self.notify(f"intake done — filed {self._filed}")
            self.dismiss(None)
            return
        self._read_current()


_KEYS = "a file · e edit · n rename · s succ · k skip · x not-doc · o open · Esc close"
