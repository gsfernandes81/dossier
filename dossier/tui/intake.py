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
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Label
from textual.widgets.option_list import Option
from textual.worker import get_current_worker

from dossier import intake
from dossier.config import Config
from dossier.errors import IntakeError
from dossier.platform_open import OpenError, open_file
from dossier.query import resolve_path
from dossier.store import Store
from dossier.tui.screens import (
    DocPickerScreen,
    TextPromptScreen,
    toggle_help_panel,
)

# Sentinel option id for the "clear the succession link" row in the retarget picker.
_NO_SUCCESSION = "\x00no-succession"


class IntakePane(Vertical):
    """Review + file dropped documents as a home *mode* (columns 1+2), not a modal.

    One proposal card at a time; a background worker reads each file with the VLM.
    Filing-with-edit (``e``) posts :class:`OpenDocument` and the host opens the record
    in the detail pane; Esc / an emptied queue posts :class:`CloseRequested`. Lazily
    mounted once and *restarted* on each entry (the pending queue is a one-shot
    session). Its reads run in a private ``intake`` worker group, distinct from the
    home's ``vision`` scan jobs, so "Cancel vision scan" doesn't dangle over intake.
    """

    can_focus = True  # keys-only card: the pane itself takes focus so its keys fire

    BINDINGS = [
        Binding("a", "accept", "File"),
        Binding("e", "accept_edit", "File + edit"),
        Binding("f", "fold", "Fold duplicate"),
        Binding("n", "rename", "Rename"),
        Binding("r", "retarget", "Renews"),
        Binding("k", "skip", "Skip"),
        Binding("x", "reject", "Not a doc"),
        Binding("o", "open_file", "Open"),
        Binding("question_mark", "toggle_help_panel", "Keys"),
        Binding("escape", "close", "Close"),
    ]

    DEFAULT_CSS = """
    IntakePane { height: 1fr; }
    IntakePane #ipanel { height: 1fr; padding: 0 1; }
    IntakePane #ihead { text-style: bold; }
    IntakePane #ifoot { color: $text-muted; margin-top: 1; }
    """

    class OpenDocument(Message):
        """File-and-edit: the host opens this record in the detail pane."""

        def __init__(self, doc_id: str) -> None:
            super().__init__()
            self.doc_id = doc_id

    class CloseRequested(Message):
        """Esc, or the queue drained — the host exits intake mode."""

    def __init__(self, store: Store, config: Config) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._pending: list[str] = []
        self._index = 0
        self._filed = 0
        self._proposal: intake.IntakeProposal | None = None

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="ipanel"):
            yield Label(id="ihead")
            # markup off: the body is plain text with literal brackets ("[f folds]",
            # note tags like "[fallback-folder]") that Rich would otherwise eat.
            yield Label(id="ibody", markup=False)
            yield Label(_KEYS, id="ifoot")

    def on_mount(self) -> None:
        self._start_queue()
        self.focus()

    def refresh_on_enter(self) -> None:
        """Host calls this on (re)entry: restart the one-shot queue. The first mount's
        on_mount does the initial start, so this must not run before children exist."""
        if self.is_mounted:
            self._start_queue()

    def focus_active_pane(self) -> None:
        if self.is_mounted:
            self.focus()

    def cancel_reads(self) -> None:
        """Cancel any in-flight VLM read (called by the host on exit, so a late read
        can't paint after the mode is gone)."""
        self.workers.cancel_group(self, "intake")

    def _start_queue(self) -> None:
        self._pending = intake.pending_files(self._store, self._config)
        self._index = 0
        self._filed = 0
        self._proposal = None
        if not self._pending:
            self.query_one("#ihead", Label).update("Inbox empty — nothing to file.")
            self.query_one("#ibody", Label).update("")
            self.query_one("#ifoot", Label).update("Esc  close")
            return
        self.query_one("#ifoot", Label).update(_KEYS)
        self._read_current()

    # -- reading (background VLM) --------------------------------------------

    @work(thread=True, group="intake", exclusive=True)
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
        doc = proposal.doc
        lines: list[str] = []
        dup = proposal.duplicate
        if dup is not None:  # lead with it — filing as new is probably wrong
            kind = "exact duplicate of" if dup.exact else "subset of"
            tail = "" if dup.exact else " — fewer pages"
            lines.append(
                f"copy    {kind} {dup.doc_name} ({dup.doc_id}){tail}  [f folds]"
            )
        lines.append(f"name    {doc.name}   (id {doc.id})")
        if doc.tags:
            lines.append(f"tags    {' '.join(doc.tags)}")
        if doc.issue_date:
            lines.append(f"issue   {doc.issue_date}")
        if doc.expiry_date:
            lines.append(f"expiry  {doc.expiry_date}")
        if doc.notes:
            lines.append(f"notes   {doc.notes.splitlines()[-1]}")
        renews = self._succession_line(proposal)
        if renews:
            lines.append(renews)
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
        head = f"Intake  {index + 1}/{total}"
        if dup is not None:
            head += "  — possible duplicate"
        self.query_one("#ihead", Label).update(head)
        self.query_one("#ibody", Label).update("\n".join(lines))

    @staticmethod
    def _succession_line(proposal: intake.IntakeProposal) -> str:
        """The ``renews …`` line, derived from ``doc.supersedes`` (r retargets it)."""
        doc = proposal.doc
        proposed = proposal.succession
        if doc.supersedes:
            if proposed is not None and doc.supersedes == proposed.older:
                return f"renews  {doc.supersedes}  (conf {proposed.confidence:.2f})"
            return f"renews  {doc.supersedes}  (manual)"
        if proposed is not None:  # a link was proposed but the user cleared it
            return f"renews  {proposed.older}  — off (press r)"
        return ""

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
            self.post_message(self.OpenDocument(doc.id))  # host edits it in the pane
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

    def action_fold(self) -> None:
        """File the drop as a fold of the duplicate it matches — no new record."""
        proposal = self._proposal
        if proposal is None:
            self.notify("still reading — wait a moment", severity="warning")
            return
        if proposal.duplicate is None:
            self.notify("no duplicate detected — press a to file as new")
            return
        try:
            doc, errors = intake.apply_fold(proposal, self._store, self._config)
        except IntakeError as exc:
            self.notify(str(exc), severity="error")
            self._advance()
            return
        for message in errors:
            self.notify(message, severity="warning")
        self._filed += 1
        self.notify(f"folded into {doc.id}")
        self._advance()

    def action_retarget(self) -> None:
        """Pick which existing document this one renews (or clear the link)."""
        proposal = self._proposal
        if proposal is None:
            return
        doc = proposal.doc
        docs = self._store.load_all()
        current = ""
        if doc.supersedes:
            match = next((d for d in docs if d.id == doc.supersedes), None)
            current = match.name if match is not None else ""
        lead = (
            Option("— no succession —", id=_NO_SUCCESSION) if doc.supersedes else None
        )
        self.app.push_screen(
            DocPickerScreen(
                docs,
                prompt=f'"{doc.name}" renews which document?',
                initial=current,
                lead=lead,
            ),
            self._retargeted,
        )

    def _retargeted(self, doc_id: str | None) -> None:
        proposal = self._proposal
        if proposal is None or doc_id is None:  # cancelled
            return
        proposal.doc.supersedes = None if doc_id == _NO_SUCCESSION else doc_id
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
        self.post_message(self.CloseRequested())

    def action_toggle_help_panel(self) -> None:
        toggle_help_panel(self)

    def _advance(self) -> None:
        self._proposal = None
        self._index += 1
        if self._index >= len(self._pending):
            self.notify(f"intake done — filed {self._filed}")
            self.post_message(self.CloseRequested())
            return
        self._read_current()


_KEYS = (
    "a file · f fold · e edit · n rename · r renews · k skip · "
    "x not-doc · o open · ? keys · Esc"
)
