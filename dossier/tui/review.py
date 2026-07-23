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

"""The reconcile screen: orphans, missing files, duplicates, and successions.

Four tabs. Orphans are a per-folder tree (leaves fill lazily on expand) with a
"suggested matches" node on top; missing is a list where ``Enter`` opens the
document; the duplicate scan runs in a **thread worker** off ``d`` (rasterizing
is blocking), reusing the per-device page-hash cache so a warm cache is ~instant.
Succession lists renewals inferred from ``ds scan`` readings (see
:mod:`dossier.succession`); ``s`` accepts one (setting the ``supersedes`` link a
user would otherwise pick by hand), ``x`` dismisses it into the sidecar.

``x`` records a *decision* in the ``.dossier/reconcile.toml`` sidecar — dismiss an
orphan (it's not a document) or acknowledge a missing file — so it stays gone on
re-run. ``l`` links an orphan to an existing document, ``a`` adopts it as a new
document, and ``u`` unlinks a dead rendition. ``f`` folds a duplicate cluster
(records the copies as dupes of the keep) and ``g`` adds a reconcile ignore-glob.
Folds and globs live in the sidecar; link/adopt/unlink edit the ``.dossier``
store. No real file is ever moved or deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, TabbedContent, TabPane, Tree
from textual.widgets.option_list import Option

from dossier import dedup, dedup_cache, dedup_hash, query, reconcile, scan, succession
from dossier.config import Config
from dossier.errors import StaleWriteError, StoreError
from dossier.migrate import slugify
from dossier.model import Document, ReconcileState, Rendition
from dossier.platform_open import OpenError, open_file
from dossier.store import Store
from dossier.tui import forms
from dossier.tui.screens import DocPickerScreen, TextPromptScreen

if TYPE_CHECKING:
    from textual.widgets.tree import TreeNode

_SUGGESTED = "\x00suggested"  # data sentinel for the suggested-matches node
_MISSING_SEP = "\x00"  # composite missing-row id: f"{doc_id}{sep}{path}"


@dataclass(frozen=True)
class _Leaf:
    """Payload on an orphan leaf: its path, plus the doc it best matches (if any)."""

    path: str  # POSIX, relative to the root
    suggestion: str | None  # id of the best-matching document — Enter opens it


class ReviewScreen(ModalScreen[str | None]):
    """Reconcile the folder. Dismisses with a document id to open it, or ``None``."""

    CSS = """
    ReviewScreen { align: center middle; }
    #rpanel {
        width: 90%; height: 85%; padding: 1 2;
        background: $panel; border: round $primary;
    }
    #rsummary { margin-bottom: 1; }
    TabbedContent { height: 1fr; }
    #dups, #missing, #orphans, #succession { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("question_mark", "toggle_help_panel", "Keys"),
        # Tab / Shift+Tab cycle the tabs. priority=True so they win over Textual's
        # default focus-traversal (this modal has one widget per tab, so nothing
        # needs Tab for focus); the orphans Tree keeps left/right for expand/collapse.
        Binding("tab", "next_tab", "Next tab", priority=True),
        Binding("shift+tab", "prev_tab", "Prev tab", priority=True),
        Binding("o", "open_file", "Open"),
        Binding("d", "scan_dups", "Find duplicates"),
        Binding("x", "reject", "Dismiss"),
        Binding("l", "link", "Link"),
        Binding("a", "adopt", "Adopt"),
        Binding("s", "accept_succession", "Supersede"),
        Binding("u", "unlink", "Unlink"),
        Binding("f", "fold", "Fold"),
        Binding("g", "ignore_glob", "Ignore glob"),
    ]

    # Tab order (as composed) and each pane's primary widget, for cycling + focus.
    _TAB_ORDER = ("tab-dups", "tab-orphans", "tab-missing", "tab-succession")
    _TAB_PANE = {
        "tab-dups": "#dups",
        "tab-orphans": "#orphans",
        "tab-missing": "#missing",
        "tab-succession": "#succession",
    }
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

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="rpanel"):
            yield Label(id="rsummary")
            with TabbedContent():
                with TabPane("Duplicates", id="tab-dups"):
                    yield OptionList(id="dups")
                with TabPane("Orphans", id="tab-orphans"):
                    yield Tree("orphans", id="orphans")
                with TabPane("Missing", id="tab-missing"):
                    yield OptionList(id="missing")
                with TabPane("Succession", id="tab-succession"):
                    yield OptionList(id="succession")

    def on_mount(self) -> None:
        self._state = self._store.load_reconcile()
        self._report = reconcile.run(self._store, self._config, state=self._state)
        self._readings = self._store.load_scans()
        self._populate_orphans()
        self._populate_missing()
        self._populate_succession()
        self.query_one("#dups", OptionList).add_option(
            Option("press  d  to scan for duplicates (cached after the first run)")
        )
        self._update_summary()
        # Open on a tab that actually has something to do — orphans/missing are
        # always-available, no-deps actions; Duplicates (the composed default)
        # is empty until you scan and needs the optional dedup extras.
        self.query_one(TabbedContent).active = self._default_tab()

    def _default_tab(self) -> str:
        report = self._report
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
        docs = self._store.load_all()
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
        doc = next((d for d in self._store.load_all() if d.id == proposal.newer), None)
        if doc is None:
            return
        doc.supersedes = proposal.older
        if self._save_doc(doc):
            self.notify(f"{proposal.newer} now supersedes {proposal.older}")
            self._populate_succession()
            self._update_summary()

    # -- population ----------------------------------------------------------

    def _update_summary(self) -> None:
        report = self._report
        assert report is not None
        dups = self._dups_count
        dup_part = f" · {dups} duplicate clusters" if dups is not None else ""
        succ_part = (
            f" · {len(self._successions)} successions" if self._successions else ""
        )
        supp = self._suppressed_count()
        supp_part = f" · {supp} suppressed" if supp else ""
        ignore = [*self._config.ignore, *self._state.ignore]
        scope = f"   scope: {len(ignore)} ignore glob(s)" if ignore else ""
        self.query_one("#rsummary", Label).update(
            f"reconcile: {len(report.orphans)} orphans · {len(report.linked)} linked · "
            f"{len(report.missing)} missing{dup_part}{succ_part}{supp_part}{scope}\n"
            "Tab/Shift+Tab switch tabs · ? shows all keys · Esc closes"
        )

    def _suppressed_count(self) -> int:
        state = self._state
        return (
            len(state.dismissed)
            + sum(len(ids) for ids in state.missing_ok.values())
            + sum(len(subs) for subs in state.folded.values())
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
        self._focus_active_pane()  # so its list/tree is immediately navigable

    def _focus_active_pane(self) -> None:
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
        active = self._active_tab()
        if action == "scan_dups":
            return True if active == "tab-dups" else None
        if action == "reject":
            return (
                True
                if active in ("tab-orphans", "tab-missing", "tab-succession")
                else None
            )
        if action in ("link", "adopt", "ignore_glob"):
            return True if active == "tab-orphans" else None
        if action == "unlink":
            return True if active == "tab-missing" else None
        if action == "fold":
            return True if active == "tab-dups" else None
        if action == "accept_succession":
            return True if active == "tab-succession" else None
        if action == "open_file":  # only where a real file sits under the cursor
            return (
                True
                if active in ("tab-orphans", "tab-dups", "tab-succession")
                else None
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
        data = event.node.data
        if isinstance(data, _Leaf) and data.suggestion is not None:
            self.dismiss(data.suggestion)  # open its best-matching document

    @on(OptionList.OptionSelected, "#missing")
    def _open_missing(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is not None:
            doc_id = event.option_id.split(_MISSING_SEP, 1)[0]
            self.dismiss(doc_id)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_toggle_help_panel(self) -> None:
        """`?` shows/hides Textual's HelpPanel — the full keybind list (with the
        tab-gated actions correctly greyed via ``check_action``)."""
        from textual.widgets import HelpPanel

        if self.query(HelpPanel):
            self.app.action_hide_help_panel()
        else:
            self.app.action_show_help_panel()

    def action_open_file(self) -> None:
        """Open the file under the cursor with the platform opener (xdg/termux)."""
        active = self._active_tab()
        if active == "tab-orphans":
            leaf = self._cursor_leaf()
            rel = leaf.path if leaf is not None else None
        elif active == "tab-dups":
            rel = self._cursor_dup_path()
        elif active == "tab-succession":
            rel = self._succession_rendition_path()
        else:
            rel = None
        if rel is None:
            self.notify("no file under the cursor")
            return
        path = query.resolve_path(self._config.syncthing_root, rel)
        if not path.exists():
            self.notify(f"file not found: {path}", severity="error")
            return
        try:
            open_file(path)
        except OpenError as exc:
            self.notify(str(exc), severity="error")
        else:
            self.notify(f"opened {rel}")

    def _cursor_dup_path(self) -> str | None:
        options = self.query_one("#dups", OptionList)
        index = options.highlighted
        if index is None:
            return None
        # dup rows are "  keep  <path>" / "  copy  <path>"; headers have neither.
        parts = str(options.get_option_at_index(index).prompt).strip().split(None, 1)
        return parts[1] if len(parts) == 2 and parts[0] in ("keep", "copy") else None

    # -- decisions (sidecar only — never touches a real file) ----------------

    def _succession_rendition_path(self) -> str | None:
        proposal = self._highlighted_succession()
        if proposal is None:
            return None
        doc = next((d for d in self._store.load_all() if d.id == proposal.newer), None)
        rendition = doc.primary_rendition() if doc is not None else None
        return rendition.path if rendition is not None else None

    def action_reject(self) -> None:
        active = self._active_tab()
        if active == "tab-orphans":
            self._dismiss_orphan()
        elif active == "tab-missing":
            self._ack_missing()
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
        docs = self._store.load_all()
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
            self.dismiss(doc.id)  # the home screen opens it for inline editing

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

    def action_fold(self) -> None:
        options = self.query_one("#dups", OptionList)
        index = options.highlighted
        if index is None or index >= len(self._row_group):
            return
        gi = self._row_group[index]
        if not (0 <= gi < len(self._groups)):
            return  # cursor on a placeholder / "no duplicates" row
        group = self._groups[gi]
        self._state.folded.setdefault(group.keep, set()).update(group.subsets)
        if not self._persist_state():
            return
        self.notify(f"folded {len(group.subsets)} copy(ies) under {_stem(group.keep)}")
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
        self._report = reconcile.run(
            self._store, self._config, pages_by_file=pages, state=self._state
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
