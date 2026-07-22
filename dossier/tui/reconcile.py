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

"""The reconcile screen: orphans, missing files, and duplicate/superset clusters.

Three tabs. Orphans are a per-folder tree (leaves fill lazily on expand) with a
"suggested matches" node on top; missing is a list where ``Enter`` opens the
document; the duplicate scan runs in a **thread worker** off ``d`` (rasterizing
is blocking), reusing the per-device page-hash cache so a warm cache is ~instant.

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

from dossier import dedup, dedup_cache, dedup_hash, reconcile
from dossier.config import Config
from dossier.errors import StaleWriteError, StoreError
from dossier.migrate import slugify
from dossier.model import Document, ReconcileState, Rendition
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


class ReconcileScreen(ModalScreen[str | None]):
    """Reconcile the folder. Dismisses with a document id to open it, or ``None``."""

    CSS = """
    ReconcileScreen { align: center middle; }
    #rpanel {
        width: 90%; height: 85%; padding: 1 2;
        background: $panel; border: round $primary;
    }
    #rsummary { margin-bottom: 1; }
    TabbedContent { height: 1fr; }
    #dups, #missing, #orphans { height: 1fr; }
    """
    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("d", "scan_dups", "Find duplicates"),
        Binding("x", "reject", "Dismiss"),
        Binding("l", "link", "Link"),
        Binding("a", "adopt", "Adopt"),
        Binding("u", "unlink", "Unlink"),
        Binding("f", "fold", "Fold"),
        Binding("g", "ignore_glob", "Ignore glob"),
    ]

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

    def on_mount(self) -> None:
        self._state = self._store.load_reconcile()
        self._report = reconcile.run(self._store, self._config, state=self._state)
        self._populate_orphans()
        self._populate_missing()
        self.query_one("#dups", OptionList).add_option(
            Option("press  d  to scan for duplicates (cached after the first run)")
        )
        self._update_summary()

    # -- population ----------------------------------------------------------

    def _update_summary(self) -> None:
        report = self._report
        assert report is not None
        dups = self._dups_count
        dup_part = f" · {dups} duplicate clusters" if dups is not None else ""
        supp = self._suppressed_count()
        supp_part = f" · {supp} suppressed" if supp else ""
        ignore = [*self._config.ignore, *self._state.ignore]
        scope = f"   scope: {len(ignore)} ignore glob(s)" if ignore else ""
        self.query_one("#rsummary", Label).update(
            f"reconcile: {len(report.orphans)} orphans · {len(report.linked)} linked · "
            f"{len(report.missing)} missing{dup_part}{supp_part}{scope}"
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

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        active = self._active_tab()
        if action == "scan_dups":
            return True if active == "tab-dups" else None
        if action == "reject":
            return True if active in ("tab-orphans", "tab-missing") else None
        if action in ("link", "adopt", "ignore_glob"):
            return True if active == "tab-orphans" else None
        if action == "unlink":
            return True if active == "tab-missing" else None
        if action == "fold":
            return True if active == "tab-dups" else None
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

    # -- decisions (sidecar only — never touches a real file) ----------------

    def action_reject(self) -> None:
        active = self._active_tab()
        if active == "tab-orphans":
            self._dismiss_orphan()
        elif active == "tab-missing":
            self._ack_missing()

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
