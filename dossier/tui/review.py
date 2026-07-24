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

"""The review pane: sync conflicts, orphans, missing files, duplicates, successions.

The single hub for tidying the collection, living as **columns 1+2 of the home's
miller view** rather than a modal — so activating a finding opens its record in
column 3 *beside* it, and Esc peels the record without disturbing the tab or the
cursor. Everything it wants from its host it asks for by message
(:class:`ReviewPane.OpenDocument` / :class:`ReviewPane.CloseRequested`).

Conflicts merges Syncthing
conflict copies in-app (the TUI face of ``ds resolve``); orphans are a
per-folder tree (leaves fill lazily on expand) with a "suggested matches" node
on top; missing is a list where ``Enter`` opens the document; the duplicate scan
runs in a **thread worker** off ``s`` (rasterizing is blocking), reusing the
per-device page-hash cache so a warm cache is ~instant. Succession lists renewals
inferred from ``ds scan`` readings (see :mod:`dossier.succession`).

``a`` is the primary *accept*, dispatched by the active tab: merge the
highlighted conflict, adopt an orphan as a new document, or accept a succession
link; ``A`` merges every conflict at once. ``x`` records a *decision* in the
``.dossier/reconcile.toml`` sidecar — dismiss an orphan (it's not a document),
acknowledge a missing file, or dismiss a succession — so it stays gone on re-run.
``l`` links an orphan to an existing document, ``u`` unlinks a dead rendition,
``f`` folds a duplicate cluster (records the copies as dupes of the keep) and
``g`` adds a reconcile ignore-glob. Folds and globs live in the sidecar;
link/adopt/unlink edit the ``.dossier`` store. Conflict merges archive the losing
copy first, so every action here is recoverable and no real file is deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Label, OptionList, Static, TabbedContent, TabPane, Tree
from textual.widgets.option_list import Option

from dossier import (
    dedup,
    dedup_cache,
    dedup_hash,
    doctor,
    query,
    reconcile,
    resolve,
    scan,
    succession,
)
from dossier.config import Config
from dossier.errors import ResolveBusyError, StaleWriteError, StoreError
from dossier.migrate import slugify
from dossier.model import Document, ReconcileState, Rendition
from dossier.platform_open import OpenError, open_file
from dossier.store import Store
from dossier.tui import forms
from dossier.tui.screens import (
    DocPickerScreen,
    TextPromptScreen,
    toggle_help_panel,
)

if TYPE_CHECKING:
    from textual.widgets.tree import TreeNode

_SUGGESTED = "\x00suggested"  # data sentinel for the suggested-matches node
_MISSING_SEP = "\x00"  # composite missing-row id: f"{doc_id}{sep}{path}"


@dataclass(frozen=True)
class _Leaf:
    """Payload on an orphan leaf: its path, plus the doc it best matches (if any)."""

    path: str  # POSIX, relative to the root
    suggestion: str | None  # id of the best-matching document — Enter opens it


class ReviewPane(Vertical):
    """Reconcile the folder: the six-tab surface itself, independent of its host.

    A widget rather than a screen so acting on a row need not *destroy* it — the host
    can show a document's detail beside the finding instead of tearing review down to
    do it. It reports outward the way :class:`DetailPane` does, by message
    (:class:`OpenDocument` / :class:`CloseRequested`), and never reaches into its host.
    """

    # A widget has no `CSS` classvar — only DEFAULT_CSS, which Textual scopes to this
    # type. That is why every rule names ReviewPane: an unprefixed `TabbedContent`
    # here would be rewritten anyway, and rules that lead with another type (e.g.
    # `HomeScreen.-narrow ReviewPane`) would be rewritten into something that can
    # never match — so host-and-breakpoint rules live in HomeScreen's CSS, not here.
    DEFAULT_CSS = """
    ReviewPane { height: 1fr; }
    ReviewPane #rsummary { margin-bottom: 1; }
    ReviewPane TabbedContent { height: 1fr; }
    ReviewPane #conflicts, ReviewPane #dups, ReviewPane #integrity,
    ReviewPane #missing, ReviewPane #orphans, ReviewPane #succession { height: 1fr; }
    ReviewPane #conflict-detail {
        height: auto; max-height: 40%; padding-top: 1;
        border-top: solid $primary 30%; color: $text-muted;
    }
    """
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "toggle_help_panel", "Keys"),
        # Tab / Shift+Tab cycle the tabs. priority=True so they win over Textual's
        # default focus-traversal (one widget per tab, so nothing here needs Tab for
        # focus). The priority pass walks the whole binding chain, so these still win
        # from a mid-chain widget — but only while focus is *inside* the pane.
        Binding("tab", "next_tab", "Next tab", priority=True),
        Binding("shift+tab", "prev_tab", "Prev tab", priority=True),
        Binding("o", "open_file", "Open"),
        Binding("right", "detail", "Details"),
        Binding("s", "scan_dups", "Find duplicates"),
        Binding("x", "reject", "Dismiss"),
        Binding("l", "link", "Link"),
        # `a` is the primary accept, dispatched per active tab (see action_accept);
        # `A` merges every conflict at once.
        Binding("a", "accept", "Accept"),
        Binding("A", "accept_all", "Merge all"),
        Binding("e", "edit", "Edit"),
        Binding("u", "unlink", "Unlink"),
        Binding("f", "fold", "Fold"),
        Binding("g", "ignore_glob", "Ignore glob"),
    ]

    class OpenDocument(Message):
        """Show this document — the host decides where (detail pane / column 3)."""

        def __init__(self, doc_id: str, *, edit: bool = False) -> None:
            super().__init__()
            self.doc_id = doc_id
            self.edit = edit

    class CloseRequested(Message):
        """Esc — the host decides what "back" means at its current depth."""

    # Tab order (as composed) and each pane's primary widget, for cycling + focus.
    _TAB_ORDER = (
        "tab-conflicts",
        "tab-orphans",
        "tab-missing",
        "tab-dups",
        "tab-succession",
        "tab-integrity",
    )
    _TAB_PANE = {
        "tab-conflicts": "#conflicts",
        "tab-dups": "#dups",
        "tab-orphans": "#orphans",
        "tab-missing": "#missing",
        "tab-succession": "#succession",
        "tab-integrity": "#integrity",
    }
    # Integrity re-runs the doctor checks, minus the two other tabs already own.
    _INTEGRITY_SKIP = frozenset({"sync-conflict", "missing-file"})
    _INTEG_SEP = "\x00"  # composite integrity-row id: f"{doc_id}{sep}{index}"
    _SUCC_SEP = "\x00"  # composite succession-row id: f"{newer}{sep}{older}"

    def __init__(self, store: Store, config: Config) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._state: ReconcileState = ReconcileState()
        self._report: reconcile.ReconcileReport | None = None
        self._filled: set[str] = set()  # folders whose leaves are loaded
        self._dups_count: int | None = None
        self._pages: dict[str, list[int]] | None = None  # last scan, for re-filter
        self._groups: list[dedup.DupGroup] = []  # clusters currently listed
        self._row_group: list[int] = []  # dups option index → group index (−1 = none)
        self._readings: dict[str, scan.ScanReading] = {}
        self._successions: dict[str, succession.Succession] = {}  # row id → proposal
        self._plans: list[resolve.Resolution] = []  # planned conflict merges
        # One document snapshot, loaded once and reused across tabs; a write
        # invalidates it (see _snapshot / _invalidate_docs) so it never goes stale.
        self._docs: list[Document] | None = None
        self._loads = 0  # completed loads, so tests can prove one didn't re-run
        self._stale = False  # an outside write landed; reload on next entry
        # Integrity is the priciest tab (a full `ds doctor` run), so it's deferred
        # until first opened: count is None until checked, then the finding total.
        self._integrity_count: int | None = None
        self._integrity_started = False

    def compose(self) -> ComposeResult:
        yield Label(id="rsummary")
        with TabbedContent():
            with TabPane("Conflicts", id="tab-conflicts"):
                yield OptionList(id="conflicts")
                yield Static(id="conflict-detail")
            with TabPane("Orphans", id="tab-orphans"):
                yield Tree("orphans", id="orphans")
            with TabPane("Missing", id="tab-missing"):
                yield OptionList(id="missing")
            with TabPane("Duplicates", id="tab-dups"):
                yield OptionList(id="dups")
            with TabPane("Succession", id="tab-succession"):
                yield OptionList(id="succession")
            with TabPane("Integrity", id="tab-integrity"):
                yield OptionList(id="integrity")

    def on_mount(self) -> None:
        # The reads here — load_all plus reconcile.run's folder walk and per-file
        # stat checks — are slow on a synced/network store (seconds on Termux).
        # Do them in a thread worker so the screen paints and stays responsive
        # (Esc/Tab/typing all work) instead of freezing until the load finishes.
        self._seed_integrity_placeholder()
        self.query_one("#dups", OptionList).add_option(
            Option("press  s  to scan for duplicates (cached after the first run)")
        )
        self._show_loading()
        self.focus_active_pane()
        self._load()

    def _show_loading(self) -> None:
        self.query_one("#rsummary", Label).update("loading the collection…")
        for pane in ("#conflicts", "#missing", "#succession"):
            options = self.query_one(pane, OptionList)
            options.clear_options()
            options.add_option(Option("loading…"))
        tree = self.query_one("#orphans", Tree)
        tree.clear()
        tree.root.expand()
        tree.root.add_leaf("loading…")  # a leaf, so _populate_orphans' clear resets it

    @work(thread=True, exclusive=True, group="review-load")
    def _load(self) -> None:
        """Do the slow store reads off-thread, then render on the UI thread."""
        self._loads += 1  # observable: the load is the expensive thing to avoid
        state = self._store.load_reconcile()
        docs = self._store.load_all()
        report = reconcile.run(self._store, self._config, state=state, docs=docs)
        readings = self._store.load_scans()
        plans = self._plan_conflicts()
        self.app.call_from_thread(
            self._apply_load, state, docs, report, readings, plans
        )

    def _apply_load(
        self,
        state: ReconcileState,
        docs: list[Document],
        report: reconcile.ReconcileReport,
        readings: dict[str, scan.ScanReading],
        plans: list[resolve.Resolution],
    ) -> None:
        if not self.is_mounted:
            return  # dismissed mid-load (Esc) — nothing left to populate
        self._state = state
        self._docs = docs
        self._report = report
        self._readings = readings
        self._plans = plans
        self._render_conflicts()
        self._populate_orphans()
        self._populate_missing()
        self._populate_succession()
        self._update_summary()
        # Open on a tab that actually has something to do — conflicts first (they
        # touch real files and block clean sync), then orphans/missing. Only if the
        # user hasn't already navigated during the load, so we never yank the tab.
        tabs = self.query_one(TabbedContent)
        if tabs.active == "tab-conflicts":
            tabs.active = self._default_tab()
        self.focus_active_pane()

    def _default_tab(self) -> str:
        report = self._report
        if self._plans:
            return "tab-conflicts"
        if report is not None and report.orphans:
            return "tab-orphans"
        if report is not None and report.missing:
            return "tab-missing"
        if self._successions:
            return "tab-succession"
        return "tab-orphans"  # nothing pending: still avoid leading with the scan tab

    def _populate_succession(self) -> None:
        options = self.query_one("#succession", OptionList)
        options.clear_options()
        self._successions = {}
        if not self._readings:
            options.add_option(Option("run  ds scan  first to propose successions"))
            return
        docs = self._snapshot()
        names = {doc.id: (doc.name or doc.id) for doc in docs}
        proposals = [
            s
            for s in succession.propose(docs, self._readings)
            if s.key not in self._state.succession_dismissed
        ]
        if not proposals:
            options.add_option(Option("no successions proposed"))
            return
        for proposal in proposals:
            row_id = f"{proposal.newer}{self._SUCC_SEP}{proposal.older}"
            self._successions[row_id] = proposal
            label = (
                f"{names.get(proposal.newer, proposal.newer)}  supersedes  "
                f"{names.get(proposal.older, proposal.older)}"
                f"   ({proposal.rationale}, conf {proposal.confidence:.2f})"
            )
            options.add_option(Option(label, id=row_id))

    def _highlighted_succession(self) -> succession.Succession | None:
        options = self.query_one("#succession", OptionList)
        index = options.highlighted
        if index is None:
            return None
        option_id = options.get_option_at_index(index).id
        return self._successions.get(option_id) if option_id else None

    def action_accept_succession(self) -> None:
        """Set the ``supersedes`` link the proposal describes, then re-propose."""
        proposal = self._highlighted_succession()
        if proposal is None:
            return
        try:  # a fresh single-file read, not the snapshot — shrinks the write window
            doc = self._store.load(proposal.newer)
        except StoreError:
            return
        doc.supersedes = proposal.older
        if self._save_doc(doc):  # invalidates the snapshot; re-propose reloads fresh
            self.notify(f"{proposal.newer} now supersedes {proposal.older}")
            self._populate_succession()
            self._update_summary()

    # -- conflicts (in-app `ds resolve`) -------------------------------------

    def _plan_conflicts(self) -> list[resolve.Resolution]:
        """Plan a merge for every Syncthing conflict copy (read-only store I/O; no
        widget access, so it's safe to run off the UI thread during the load)."""
        plans: list[resolve.Resolution] = []
        for item in resolve.find_conflicts(self._store):
            try:
                plans.append(resolve.plan(self._store, item))
            except StoreError:
                continue  # an unreadable conflict; the Integrity tab surfaces it
        return plans

    def _render_conflicts(self) -> None:
        """Render ``self._plans`` into the Conflicts tab (UI thread only)."""
        options = self.query_one("#conflicts", OptionList)
        options.clear_options()
        detail = self.query_one("#conflict-detail", Static)
        if not self._plans:
            options.add_option(Option("no sync conflicts to merge."))
            detail.update("")
            return
        for index, plan in enumerate(self._plans):
            options.add_option(Option(_conflict_headline(plan), id=str(index)))
        self._show_conflict_detail(0)

    def _populate_conflicts(self) -> None:
        """Re-plan then re-render conflicts (used after a merge)."""
        self._plans = self._plan_conflicts()
        self._render_conflicts()

    @on(OptionList.OptionHighlighted, "#conflicts")
    def _on_conflict_highlight(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()  # the host screen has OptionList handlers too; this one is ours
        if event.option_id is not None:
            self._show_conflict_detail(int(event.option_id))

    def _show_conflict_detail(self, index: int) -> None:
        if not 0 <= index < len(self._plans):
            return
        plan = self._plans[index]
        lines = [_conflict_decision_text(d) for d in plan.contested]
        fills = sum(1 for d in plan.decisions if d.action == "fill")
        unions = sum(1 for d in plan.decisions if d.action == "union")
        if fills:
            lines.append(f"+ {fills} field(s) filled from the other copy")
        if unions:
            lines.append(f"∪ {unions} list(s)/table(s) merged")
        if not lines:
            lines.append("identical copy — will be cleared")
        self.query_one("#conflict-detail", Static).update("\n".join(lines))

    def action_accept_all(self) -> None:
        """`A` — merge every conflict at once (the losing copy is archived first)."""
        report = resolve.resolve_all(self._store, apply=True)
        message = f"merged {len(report.resolutions)} conflict(s)"
        severity = "information"
        if report.skipped:
            message += f", {len(report.skipped)} changed mid-merge (retry)"
            severity = "warning"
        self.notify(message, severity=severity)
        self._invalidate_docs()  # merges rewrote sidecars → snapshot + integrity stale
        self._populate_conflicts()
        self._update_summary()

    def _merge_highlighted(self) -> None:
        """Merge the highlighted conflict, re-planning against the live copy first."""
        options = self.query_one("#conflicts", OptionList)
        index = options.highlighted
        if index is None or not self._plans:
            return
        item = self._plans[index].item
        try:
            fresh = resolve.plan(self._store, item)  # re-plan against current live
            resolve.apply_resolution(self._store, fresh)
        except ResolveBusyError:
            self.notify("changed mid-merge — retry", severity="warning")
        except StoreError as exc:
            self.notify(str(exc), severity="error")
        else:
            self.notify(f"merged {fresh.name}")
        self._invalidate_docs()  # a merge rewrote the live sidecar
        self._populate_conflicts()
        self._update_summary()

    # -- integrity (in-app `ds doctor`, minus what other tabs already own) ----
    #
    # Deferred: a full doctor run is the screen's priciest work (it re-reads and
    # re-serialises every document), so it doesn't run on mount — the tab shows a
    # placeholder until first opened, then runs in a thread worker. A write that
    # could change the findings invalidates it so the next open re-checks.

    _INTEGRITY_HINT = "open this tab to run the integrity check"

    def _seed_integrity_placeholder(self) -> None:
        options = self.query_one("#integrity", OptionList)
        options.clear_options()
        options.add_option(Option(self._INTEGRITY_HINT))

    @work(thread=True, exclusive=True, group="integrity")
    def _run_integrity(self) -> None:
        """Run the (blocking) doctor check off-thread, then render its findings."""
        self.app.call_from_thread(self._integrity_checking)
        # Reuse the shared snapshot if present (a plain reference read — the worker
        # never assigns it); load fresh off-thread if a write invalidated it.
        docs = self._docs if self._docs is not None else self._store.load_all()
        report = doctor.run(
            self._store, self._config, skip=self._INTEGRITY_SKIP, docs=docs
        )
        self.app.call_from_thread(self._populate_integrity_results, report)

    def _integrity_checking(self) -> None:
        options = self.query_one("#integrity", OptionList)
        options.clear_options()
        options.add_option(Option("checking integrity…"))

    def _populate_integrity_results(self, report: doctor.Report) -> None:
        self._integrity_count = len(report.findings)
        options = self.query_one("#integrity", OptionList)
        options.clear_options()
        if not report.findings:
            options.add_option(Option("integrity: all clear."))
        else:
            index = 0
            for check, items in sorted(report.by_check().items()):
                options.add_option(Option(f"— {check} ({len(items)}) —", id=None))
                hint = doctor.CHECK_HINTS.get(check)
                if hint:
                    options.add_option(Option(f"  → {hint}", id=None))
                for finding in items:
                    # A doc can appear in several findings; a composite id keeps
                    # them unique (else OptionList raises DuplicateID).
                    oid = f"{finding.subject}{self._INTEG_SEP}{index}"
                    options.add_option(
                        Option(f"  {finding.subject}: {finding.detail}", id=oid)
                    )
                    index += 1
        self._update_summary()

    def _invalidate_integrity(self) -> None:
        """Drop any computed integrity result so the next tab-open re-checks.

        Called (via :meth:`_invalidate_docs`) from the write paths: a mutation can
        change what doctor would find. Never runs while Integrity is the active tab
        — every mutating key is gated (``check_action``) to the other tabs.
        """
        self._integrity_started = False
        self._integrity_count = None
        self._seed_integrity_placeholder()

    def _snapshot(self) -> list[Document]:
        """The screen's document list — loaded once, reused across tabs.

        Every tab used to reload the whole store independently; on the user's
        Proton Drive store that was hundreds of slow reads per open. This loads
        them once; a write invalidates it (see :meth:`_invalidate_docs`).
        """
        if self._docs is None:
            self._docs = self._store.load_all()
        return self._docs

    def _invalidate_docs(self) -> None:
        """After a document write: drop the snapshot (reloaded fresh next use) and
        the integrity result. Sidecar-only writes (dismiss/ack/fold/glob) don't
        touch documents, so they leave the snapshot alone."""
        self._docs = None
        self._invalidate_integrity()

    @on(OptionList.OptionSelected, "#integrity")
    def _open_integrity(self, event: OptionList.OptionSelected) -> None:
        """Enter — the app-wide activate verb: open the flagged document's file."""
        event.stop()
        rel = self._integrity_rendition_path()
        if rel is None:
            # Most integrity findings are sidecar problems (date order, supersession,
            # location refs) that no PDF can answer — so fall through to the record.
            self.action_detail()
            return
        if self._open_one(rel):
            self.notify(f"opened {rel}")

    def action_detail(self) -> None:
        """`→` — show the flagged record, which is where most findings get fixed."""
        doc_id = self._integrity_doc_id()
        if doc_id is not None:
            self.post_message(self.OpenDocument(doc_id))

    def action_edit(self) -> None:
        """`e` — open the highlighted Integrity finding's document in edit mode."""
        doc_id = self._integrity_doc_id()
        if doc_id is not None:
            self.post_message(self.OpenDocument(doc_id, edit=True))

    def _integrity_doc_id(self) -> str | None:
        options = self.query_one("#integrity", OptionList)
        index = options.highlighted
        if index is None:
            return None
        option = options.get_option_at_index(index)
        return option.id.split(self._INTEG_SEP, 1)[0] if option.id else None

    def _integrity_rendition_path(self) -> str | None:
        doc_id = self._integrity_doc_id()
        if doc_id is None:
            return None
        doc = next((d for d in self._snapshot() if d.id == doc_id), None)
        rendition = doc.primary_rendition() if doc is not None else None
        return rendition.path if rendition is not None else None

    # -- population ----------------------------------------------------------

    def _update_summary(self) -> None:
        report = self._report
        assert report is not None
        dups = self._dups_count
        conf_part = f"{len(self._plans)} conflicts · " if self._plans else ""
        dup_part = f" · {dups} duplicate clusters" if dups is not None else ""
        succ_part = (
            f" · {len(self._successions)} successions" if self._successions else ""
        )
        supp = self._suppressed_count()
        supp_part = f" · {supp} suppressed" if supp else ""
        if self._integrity_count is None:  # not checked yet (tab still unopened)
            integ_part = " · integrity: open tab to check"
        elif self._integrity_count:
            integ_part = f" · {self._integrity_count} integrity"
        else:  # checked, all clear
            integ_part = ""
        ignore = [*self._config.ignore, *self._state.ignore]
        scope = f"   scope: {len(ignore)} ignore glob(s)" if ignore else ""
        self.query_one("#rsummary", Label).update(
            f"review: {conf_part}{len(report.orphans)} orphans · "
            f"{len(report.linked)} linked · "
            f"{len(report.missing)} missing{dup_part}{succ_part}{integ_part}"
            f"{supp_part}{scope}\n"
            "Tab/Shift+Tab switch tabs · ? shows all keys · Esc closes"
        )

    def _suppressed_count(self) -> int:
        state = self._state
        return (
            len(state.dismissed)
            + sum(len(ids) for ids in state.missing_ok.values())
            + sum(len(subs) for subs in state.folded.values())
            + sum(len(subs) for subs in state.dup_dismissed.values())
        )

    def _populate_orphans(self, expanded: set[str] | None = None) -> None:
        report = self._report
        assert report is not None
        self._filled = set()
        tree = self.query_one("#orphans", Tree)
        tree.clear()
        tree.root.expand()
        suggested = sorted(
            (o for o in report.orphans if o.suggestion), key=lambda o: -o.score
        )
        if suggested:
            node = tree.root.add(
                f"suggested matches ({len(suggested)})", data=_SUGGESTED
            )
            for orphan in suggested:
                leaf = node.add_leaf(f"{orphan.path}  →  {orphan.suggestion}")
                leaf.data = _Leaf(orphan.path, orphan.suggestion)
        by_folder: dict[str, int] = {}
        for orphan in report.orphans:
            folder = _folder_of(orphan.path)
            by_folder[folder] = by_folder.get(folder, 0) + 1
        for folder, count in sorted(
            by_folder.items(), key=lambda item: (-item[1], item[0])
        ):
            branch = tree.root.add(f"{folder}  ({count})", data=folder)
            branch.add_leaf("…")  # placeholder so the node is expandable
            if expanded and folder in expanded:
                branch.expand()  # re-trigger the lazy fill

    def _populate_missing(self) -> None:
        report = self._report
        assert report is not None
        options = self.query_one("#missing", OptionList)
        options.clear_options()
        if not report.missing:
            options.add_option(Option("no missing files."))
            return
        for miss in report.missing:
            oid = f"{miss.doc_id}{_MISSING_SEP}{miss.path}"
            options.add_option(Option(f"{miss.doc_id}: {miss.path}", id=oid))

    def _populate_dups(self, groups: list[dedup.DupGroup]) -> None:
        options = self.query_one("#dups", OptionList)
        options.clear_options()
        self._dups_count = len(groups)
        self._groups = groups
        self._row_group = []  # aligned with the option list; f reads the cursor row
        if not groups:
            options.add_option(Option("no duplicate clusters found."))
            self._row_group.append(-1)
        for i, group in enumerate(groups):
            tag = "  ⚠ partial overlap" if group.ambiguous else ""
            options.add_option(
                Option(
                    f"— cluster {i + 1} · keep + {len(group.subsets)}{tag}  (f folds) —"
                )
            )
            self._row_group.append(i)
            options.add_option(Option(f"  keep  {group.keep}"))
            self._row_group.append(i)
            for subset in group.subsets:
                options.add_option(Option(f"  copy  {subset}"))
                self._row_group.append(i)
        self._update_summary()

    # -- events --------------------------------------------------------------

    @on(TabbedContent.TabActivated)
    def _tab_changed(self) -> None:
        self.refresh_bindings()  # footer shows only the active tab's actions
        self.focus_active_pane()  # so its list/tree is immediately navigable
        # Integrity runs lazily on first open (never the default tab, so this is
        # always user-driven and post-mount — _report is set by then).
        if (
            self._active_tab() == "tab-integrity"
            and not self._integrity_started
            and self._report is not None
        ):
            self._integrity_started = True
            self._run_integrity()

    def mark_stale(self) -> None:
        """Note that something outside review changed the store.

        The pane is long-lived now, so it can miss a write made from the detail
        pane, intake or a scan. Rather than reload on every return (the cost the
        old modal paid every single time), record it and reload on the next entry.
        """
        self._stale = True

    def reload_if_stale(self) -> None:
        """Reload only if an outside write landed since the last load."""
        if not self._stale:
            return
        self._stale = False
        self._show_loading()  # no key can reach a summary rebuild mid-flight
        self._load()

    def focus_active_pane(self) -> None:
        """Focus the active tab's list/tree. Public: the host calls it to hand focus
        back after closing the detail column, landing on the row you came from."""
        pane = self._TAB_PANE.get(self._active_tab())
        if pane:
            hits = self.query(pane)
            if hits:
                hits.first().focus()

    def action_next_tab(self) -> None:
        self._cycle_tab(1)

    def action_prev_tab(self) -> None:
        self._cycle_tab(-1)

    def _cycle_tab(self, delta: int) -> None:
        order = self._TAB_ORDER
        current = self._active_tab()
        index = order.index(current) if current in order else 0
        self.query_one(TabbedContent).active = order[(index + delta) % len(order)]

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Gate every verb on the active tab.

        ``False``, not ``None``: Textual reads ``None`` as *disabled but visible*
        (greyed in the footer) and ``False`` as disabled **and** hidden. Returning
        None here made every tab advertise every other tab's verbs — which is how
        "how do I dismiss a duplicate?" became a real question about a key that was
        listed, greyed, and dead. Hiding them makes the footer and the `?` panel
        per-tab for free, with no help text to write or keep in step.
        """
        active = self._active_tab()
        if action == "scan_dups":
            return active == "tab-dups"
        if action == "reject":
            return active in (
                "tab-orphans",
                "tab-missing",
                "tab-dups",
                "tab-succession",
            )
        if action == "accept":  # `a` — dispatched: merge / adopt / supersede
            return active in ("tab-conflicts", "tab-orphans", "tab-succession")
        if action == "accept_all":  # `A` — merge every conflict at once
            return active == "tab-conflicts"
        if action in ("edit", "detail"):  # `e` edit, `→` show the flagged record
            return active == "tab-integrity"
        if action in ("link", "ignore_glob"):
            return active == "tab-orphans"
        if action == "unlink":
            return active == "tab-missing"
        if action == "fold":
            return active == "tab-dups"
        if action == "open_file":  # only where a real file sits under the cursor
            return active in (
                "tab-orphans",
                "tab-dups",
                "tab-succession",
                "tab-integrity",
            )
        return True

    @on(Tree.NodeExpanded, "#orphans")
    def _fill_folder(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        folder = node.data
        if (
            not isinstance(folder, str)
            or folder == _SUGGESTED
            or folder in self._filled
        ):
            return  # root, the suggested node, or an already-filled folder
        report = self._report
        assert report is not None
        node.remove_children()
        for orphan in report.orphans:
            if _folder_of(orphan.path) == folder:
                leaf = node.add_leaf(orphan.path.rsplit("/", 1)[-1])
                leaf.data = _Leaf(orphan.path, orphan.suggestion)
        self._filled.add(folder)

    @on(Tree.NodeSelected, "#orphans")
    def _open_orphan_match(self, event: Tree.NodeSelected) -> None:
        event.stop()
        data = event.node.data
        if isinstance(data, _Leaf) and data.suggestion is not None:
            # open the best-matching document
            self.post_message(self.OpenDocument(data.suggestion))

    @on(OptionList.OptionSelected, "#missing")
    def _open_missing(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option_id is not None:
            doc_id = event.option_id.split(_MISSING_SEP, 1)[0]
            self.post_message(self.OpenDocument(doc_id))

    def action_close(self) -> None:
        self.post_message(self.CloseRequested())

    def action_toggle_help_panel(self) -> None:
        # `?` — the full keybind list, tab-gated actions correctly greyed via
        # check_action. Shared with the other modals (see tui.screens).
        toggle_help_panel(self)

    def cursor_paths(self) -> list[str]:
        """Relative paths the cursor points at on the active tab — 0, 1 or 2.

        Public and single-sourced: opening, revealing and copying a path all mean
        "whatever is under the cursor", and they must never disagree about what that
        is. Only Succession yields two (the older and newer sides).
        """
        active = self._active_tab()
        if active == "tab-orphans":
            leaf = self._cursor_leaf()
            return [leaf.path] if leaf is not None else []
        if active == "tab-dups":
            return [rel for rel in (self._cursor_dup_path(),) if rel]
        if active == "tab-succession":
            return self._succession_rendition_paths()
        if active == "tab-integrity":
            return [rel for rel in (self._integrity_rendition_path(),) if rel]
        return []

    def path_labels(self) -> list[tuple[str, str]]:
        """``(relpath, label)`` for each cursor path, for a "which one?" prompt."""
        rels = self.cursor_paths()
        if self._active_tab() == "tab-succession" and len(rels) == 2:
            return [(rels[0], f"older — {rels[0]}"), (rels[1], f"newer — {rels[1]}")]
        return [(rel, rel) for rel in rels]

    def action_open_file(self) -> None:
        """Open what the cursor points at with the platform opener (xdg/termux).

        Usually one file; on Succession, *both* — "does this renewal really replace
        that one?" is a comparison, and one file cannot answer it. (Revealing or
        copying instead asks which, since neither is meaningful for two at once.)
        """
        rels = self.cursor_paths()
        if not rels:
            self.notify("no file under the cursor")
            return
        opened = [rel for rel in rels if self._open_one(rel)]
        if opened:
            self.notify(f"opened {' + '.join(opened)}")

    def _open_one(self, rel: str) -> bool:
        """Open one relative path, reporting rather than raising. True if it opened."""
        path = query.resolve_path(self._config.syncthing_root, rel)
        if not path.exists():
            self.notify(f"file not found: {path}", severity="error")
            return False
        try:
            open_file(path)
        except OpenError as exc:
            self.notify(str(exc), severity="error")
            return False
        return True

    def _cursor_dup_path(self) -> str | None:
        options = self.query_one("#dups", OptionList)
        index = options.highlighted
        if index is None:
            return None
        # dup rows are "  keep  <path>" / "  copy  <path>"; headers have neither.
        parts = str(options.get_option_at_index(index).prompt).strip().split(None, 1)
        return parts[1] if len(parts) == 2 and parts[0] in ("keep", "copy") else None

    # -- decisions (sidecar only — never touches a real file) ----------------

    def _succession_rendition_paths(self) -> list[str]:
        """Both sides of the proposal, older first so the renewal lands frontmost.

        Whichever side has a digital file: a paper-only predecessor is common, and
        showing the one you *can* read beats refusing both.
        """
        proposal = self._highlighted_succession()
        if proposal is None:
            return []
        by_id = {d.id: d for d in self._snapshot()}
        paths = []
        for doc_id in (proposal.older, proposal.newer):
            doc = by_id.get(doc_id)
            rendition = doc.primary_rendition() if doc is not None else None
            if rendition is not None:
                paths.append(rendition.path)
        return paths

    def action_accept(self) -> None:
        """`a` — the primary accept, meaning whatever the active tab affirms.

        One key across the hub: merge the highlighted conflict, adopt an orphan as
        a new document, or accept a proposed succession. ``x`` is its inverse
        (dismiss); ``A`` is the conflicts-only bulk form.
        """
        active = self._active_tab()
        if active == "tab-conflicts":
            self._merge_highlighted()
        elif active == "tab-orphans":
            self.action_adopt()
        elif active == "tab-succession":
            self.action_accept_succession()

    def action_reject(self) -> None:
        active = self._active_tab()
        if active == "tab-orphans":
            self._dismiss_orphan()
        elif active == "tab-missing":
            self._ack_missing()
        elif active == "tab-dups":
            self._dismiss_dups()
        elif active == "tab-succession":
            self._dismiss_succession()

    def _dismiss_succession(self) -> None:
        proposal = self._highlighted_succession()
        if proposal is None:
            return
        self._state.succession_dismissed.add(proposal.key)
        if self._persist_state():
            self._populate_succession()
            self._update_summary()

    def _dismiss_orphan(self) -> None:
        leaf = self._cursor_leaf()
        if leaf is None:
            return
        self._state.dismissed.add(leaf.path)
        self._save_and_refresh()

    def _ack_missing(self) -> None:
        picked = self._highlighted_missing()
        if picked is None:
            return
        doc_id, path = picked
        self._state.missing_ok.setdefault(path, set()).add(doc_id)
        self._save_and_refresh()

    def action_link(self) -> None:
        leaf = self._cursor_leaf()
        if leaf is None:
            return
        path = leaf.path
        docs = self._snapshot()  # read-only: the picker filters; _do_link reloads
        initial = ""
        if leaf.suggestion is not None:
            match = next((d for d in docs if d.id == leaf.suggestion), None)
            if match is not None:
                initial = match.name
        picker = DocPickerScreen(
            docs, prompt=f"Link  {path}  to which document?", initial=initial
        )
        self.app.push_screen(picker, lambda doc_id: self._do_link(path, doc_id))

    def _do_link(self, path: str, doc_id: str | None) -> None:
        if doc_id is None:
            return
        doc = self._store.load(doc_id)  # fresh, to shrink the stale-write window
        if any(rendition.path == path for rendition in doc.files):
            self.notify(f"{doc_id} already links that file")
            return
        doc.files.append(Rendition(label=_stem(path), path=path, primary=not doc.files))
        doc.has_digital = True
        if self._save_doc(doc):
            self.notify(f"linked to {doc.name or doc.id}")
            self._refresh()

    def action_adopt(self) -> None:
        leaf = self._cursor_leaf()
        if leaf is None:
            return
        name = _pretty_name(leaf.path)
        doc = Document(
            name=name,
            has_digital=True,
            files=[Rendition(label="default", path=leaf.path, primary=True)],
        )
        doc.id = forms.unique_id(self._store, slugify(name) or "document")
        if self._save_doc(doc):
            self.notify(f"adopted {doc.id} — open it to edit the details")
            # the host opens it for editing
            self.post_message(self.OpenDocument(doc.id, edit=True))

    def action_unlink(self) -> None:
        picked = self._highlighted_missing()
        if picked is None:
            return
        doc_id, path = picked
        doc = self._store.load(doc_id)
        kept = [rendition for rendition in doc.files if rendition.path != path]
        if len(kept) == len(doc.files):
            self.notify("nothing to unlink")
            return
        doc.files = kept
        doc.has_digital = bool(kept)
        if self._save_doc(doc):
            self.notify(f"unlinked {path}")
            self._refresh()

    def _cursor_group(self) -> dedup.DupGroup | None:
        options = self.query_one("#dups", OptionList)
        index = options.highlighted
        if index is None or index >= len(self._row_group):
            return None
        gi = self._row_group[index]
        if not (0 <= gi < len(self._groups)):
            return None  # cursor on a placeholder / "no duplicates" row
        return self._groups[gi]

    def action_fold(self) -> None:
        """`f` — yes, these are the same file: record the copies under the keep."""
        group = self._cursor_group()
        if group is None:
            return
        self._state.folded.setdefault(group.keep, set()).update(group.subsets)
        if not self._persist_state():
            return
        self.notify(f"folded {len(group.subsets)} copy(ies) under {_stem(group.keep)}")
        self._refresh(refilter_dups=True)

    def _dismiss_dups(self) -> None:
        """`x` — no, these aren't duplicates: settle the cluster without claiming it.

        The mirror of fold, and deliberately *not* fold: folding would hide these
        paths from the orphan list (``suppressed_orphans``), so a genuinely
        different document still awaiting adoption would vanish from the list that
        would have prompted you to adopt it. Keyed like fold, so a new copy
        resurfaces the cluster for a fresh look rather than staying buried.
        """
        group = self._cursor_group()
        if group is None:
            return
        self._state.dup_dismissed.setdefault(group.keep, set()).update(group.subsets)
        if not self._persist_state():
            return
        self.notify(f"not duplicates: {_stem(group.keep)} — cluster dismissed")
        self._refresh(refilter_dups=True)

    def action_ignore_glob(self) -> None:
        self.app.push_screen(
            TextPromptScreen(
                "Add a reconcile ignore-glob (fnmatch; * crosses /):",
                initial=self._suggested_glob(),
                placeholder="Folder/*",
            ),
            self._add_glob,
        )

    def _suggested_glob(self) -> str:
        node = self.query_one("#orphans", Tree).cursor_node
        if node is None:
            return ""
        data = node.data
        if isinstance(data, _Leaf):
            return f"{_folder_of(data.path)}/*"
        if isinstance(data, str) and data != _SUGGESTED:
            return f"{data}/*"
        return ""

    def _add_glob(self, glob: str | None) -> None:
        if glob is None:
            return
        glob = glob.strip()
        if not glob or glob in self._state.ignore:
            return
        self._state.ignore.append(glob)
        if not self._persist_state():
            return
        self.notify(f"ignoring {glob}")
        self._refresh()

    def _cursor_leaf(self) -> _Leaf | None:
        node = self.query_one("#orphans", Tree).cursor_node
        data = node.data if node is not None else None
        return data if isinstance(data, _Leaf) else None

    def _highlighted_missing(self) -> tuple[str, str] | None:
        options = self.query_one("#missing", OptionList)
        index = options.highlighted
        if index is None:
            return None
        option = options.get_option_at_index(index)
        if option.id is None:
            return None
        doc_id, _, path = option.id.partition(_MISSING_SEP)
        return (doc_id, path) if path else None

    def _save_doc(self, doc: Document) -> bool:
        try:
            self._store.save(doc)
        except StaleWriteError:
            self.notify("changed on disk; reopen reconcile", severity="error")
            return False
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            return False
        self._invalidate_docs()  # the snapshot + any integrity result are now stale
        return True

    def _persist_state(self) -> bool:
        try:
            self._store.save_reconcile(self._state)
        except OSError as exc:
            self.notify(f"could not save decisions: {exc}", severity="error")
            return False
        return True

    def _save_and_refresh(self) -> None:
        if self._persist_state():
            self._refresh()

    def _refresh(self, *, refilter_dups: bool = False) -> None:
        """Re-run the (cheap) engine and rebuild the orphan tree + missing list.

        Preserves which folders were expanded and the tree cursor. The duplicate
        tab is left as-is unless ``refilter_dups`` — dismiss/ack don't affect it,
        and a fresh scan would re-rasterize. Folding passes ``refilter_dups`` so
        the folded cluster drops out, reusing the cached hashes (no rasterizing).
        """
        tree = self.query_one("#orphans", Tree)
        expanded = self._expanded_folders(tree)
        cursor = tree.cursor_line
        pages = self._pages if refilter_dups else None
        # A doc write already invalidated the snapshot, so this reloads fresh; a
        # sidecar-only change (dismiss/ack/fold) reuses the still-valid snapshot.
        self._report = reconcile.run(
            self._store,
            self._config,
            pages_by_file=pages,
            state=self._state,
            docs=self._snapshot(),
        )
        self._populate_orphans(expanded=expanded)
        self._populate_missing()
        tree.cursor_line = cursor  # Textual clamps out-of-range lines
        if refilter_dups and self._report.groups is not None:
            self._populate_dups(self._report.groups)  # rebuilds dups + summary
        else:
            self._update_summary()

    def _expanded_folders(self, tree: Tree) -> set[str]:
        out: set[str] = set()
        stack: list[TreeNode] = list(tree.root.children)
        while stack:
            node = stack.pop()
            data = node.data
            if isinstance(data, str) and data != _SUGGESTED and node.is_expanded:
                out.add(data)
            stack.extend(node.children)
        return out

    # -- duplicate scan (thread worker) --------------------------------------

    @work(thread=True, exclusive=True)
    def action_scan_dups(self) -> None:
        root = self._config.syncthing_root
        candidates = [
            root / rel
            for rel in reconcile.scan_files(self._config, self._state.ignore)
            if _is_page_file(rel)
        ]
        self.app.call_from_thread(self._scanning, len(candidates))
        try:
            pages = dedup_cache.cached_page_hashes(candidates, root)
        except dedup_hash.DedupError as exc:
            self.app.call_from_thread(self._scan_failed, str(exc))
            return
        self._pages = pages  # keep the raw hashes so folding re-filters cheaply
        groups = dedup.group_files(pages)
        groups = [g for g in groups if not self._state.covers(g.keep, g.subsets)]
        self.app.call_from_thread(self._populate_dups, groups)

    def _scanning(self, total: int) -> None:
        self._groups = []
        self._row_group = []
        options = self.query_one("#dups", OptionList)
        options.clear_options()
        options.add_option(Option(f"scanning {total} files… (first run is slow)"))

    def _scan_failed(self, message: str) -> None:
        options = self.query_one("#dups", OptionList)
        options.clear_options()
        options.add_option(Option(message))

    def _active_tab(self) -> str:
        try:
            return self.query_one(TabbedContent).active
        except Exception:
            return ""


def _conflict_headline(plan: resolve.Resolution) -> str:
    if plan.loud:
        tag = "whole-file replace"
    elif plan.contested:
        tag = f"{len(plan.contested)} contested field(s)"
    elif plan.changed:
        tag = "auto-merge"
    else:
        tag = "identical — will clear"
    return f"{plan.kind:11} {plan.name}  —  {tag}"


def _conflict_decision_text(decision: resolve.FieldDecision) -> str:
    winner = decision.winner.value if decision.winner else "ours"
    return f"~ {decision.field}: {decision.ours} vs {decision.theirs} → keep {winner}"


def _folder_of(rel: str) -> str:
    return rel.rsplit("/", 1)[0] if "/" in rel else "."


def _stem(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def _pretty_name(rel: str) -> str:
    stem = _stem(rel)
    return stem.replace("_", " ").strip() or stem


def _is_page_file(rel: str) -> bool:
    dot = rel.rfind(".")
    return dot != -1 and rel[dot:].lower() in dedup_hash.PAGE_SUFFIXES
