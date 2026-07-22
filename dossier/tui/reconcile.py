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
Read-only for now — the fold / adopt / link actions land next.
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, TabbedContent, TabPane, Tree
from textual.widgets.option_list import Option

from dossier import dedup, dedup_cache, dedup_hash, reconcile
from dossier.config import Config
from dossier.store import Store

_SUGGESTED = "\x00suggested"  # data sentinel for the suggested-matches node
_FILLED = "\x00filled"  # data sentinel for a folder node whose leaves are loaded


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
    ]

    def __init__(self, store: Store, config: Config) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._report: reconcile.ReconcileReport | None = None

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
        self._report = reconcile.run(self._store, self._config)
        self._populate_orphans()
        self._populate_missing()
        self.query_one("#dups", OptionList).add_option(
            Option("press  d  to scan for duplicates (cached after the first run)")
        )
        self._update_summary()

    # -- population ----------------------------------------------------------

    def _update_summary(self, dups: int | None = None) -> None:
        report = self._report
        assert report is not None
        dup_part = f" · {dups} duplicate clusters" if dups is not None else ""
        scope = ""
        if self._config.ignore:
            scope = f"   scope: {len(self._config.ignore)} ignore glob(s)"
        self.query_one("#rsummary", Label).update(
            f"reconcile: {len(report.orphans)} orphans · {len(report.linked)} linked · "
            f"{len(report.missing)} missing{dup_part}{scope}"
        )

    def _populate_orphans(self) -> None:
        report = self._report
        assert report is not None
        tree = self.query_one("#orphans", Tree)
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
                leaf.data = orphan.suggestion  # Enter opens the suggested document
        by_folder: dict[str, int] = {}
        for orphan in report.orphans:
            folder = orphan.path.rsplit("/", 1)[0] if "/" in orphan.path else "."
            by_folder[folder] = by_folder.get(folder, 0) + 1
        for folder, count in sorted(
            by_folder.items(), key=lambda item: (-item[1], item[0])
        ):
            branch = tree.root.add(f"{folder}  ({count})", data=folder)
            branch.add_leaf("…")  # placeholder so the node is expandable

    def _populate_missing(self) -> None:
        report = self._report
        assert report is not None
        options = self.query_one("#missing", OptionList)
        if not report.missing:
            options.add_option(Option("no missing files."))
            return
        for miss in report.missing:
            options.add_option(Option(f"{miss.doc_id}: {miss.path}", id=miss.doc_id))

    def _populate_dups(self, groups: list[dedup.DupGroup]) -> None:
        options = self.query_one("#dups", OptionList)
        options.clear_options()
        if not groups:
            options.add_option(Option("no duplicate clusters found."))
        for i, group in enumerate(groups, start=1):
            tag = "  ⚠ partial overlap" if group.ambiguous else ""
            options.add_option(
                Option(f"— cluster {i} · keep + {len(group.subsets)}{tag} —")
            )
            options.add_option(Option(f"  keep  {group.keep}"))
            for subset in group.subsets:
                options.add_option(Option(f"  copy  {subset}"))
        self._update_summary(dups=len(groups))

    # -- events --------------------------------------------------------------

    @on(Tree.NodeExpanded, "#orphans")
    def _fill_folder(self, event: Tree.NodeExpanded) -> None:
        node = event.node
        folder = node.data
        if not isinstance(folder, str) or folder in (_SUGGESTED, _FILLED):
            return  # root, the suggested node, or an already-filled folder
        report = self._report
        assert report is not None
        node.remove_children()
        for orphan in report.orphans:
            parent = orphan.path.rsplit("/", 1)[0] if "/" in orphan.path else "."
            if parent == folder:
                node.add_leaf(orphan.path.rsplit("/", 1)[-1])
        node.data = _FILLED

    @on(OptionList.OptionSelected, "#missing")
    def _open_missing(self, event: OptionList.OptionSelected) -> None:
        if event.option_id is not None:
            self.dismiss(event.option_id)

    def action_close(self) -> None:
        self.dismiss(None)

    @work(thread=True, exclusive=True)
    def action_scan_dups(self) -> None:
        root = self._config.syncthing_root
        candidates = [
            root / rel
            for rel in reconcile.scan_files(self._config)
            if _is_page_file(rel)
        ]
        self.app.call_from_thread(self._scanning, len(candidates))
        try:
            pages = dedup_cache.cached_page_hashes(candidates, root)
        except dedup_hash.DedupError as exc:
            self.app.call_from_thread(self._scan_failed, str(exc))
            return
        groups = dedup.group_files(pages)
        self.app.call_from_thread(self._populate_dups, groups)

    def _scanning(self, total: int) -> None:
        options = self.query_one("#dups", OptionList)
        options.clear_options()
        options.add_option(Option(f"scanning {total} files… (first run is slow)"))

    def _scan_failed(self, message: str) -> None:
        options = self.query_one("#dups", OptionList)
        options.clear_options()
        options.add_option(Option(message))


def _is_page_file(rel: str) -> bool:
    dot = rel.rfind(".")
    return dot != -1 and rel[dot:].lower() in dedup_hash.PAGE_SUFFIXES
