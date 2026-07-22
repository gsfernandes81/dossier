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

"""Tests for the Textual TUI (driven headlessly via App.run_test)."""

from datetime import date
from pathlib import Path

import pytest
from textual.widgets import Button, Input, OptionList, TabbedContent, TextArea, Tree

from dossier import dedup
from dossier.config import Config
from dossier.model import Document, Rendition
from dossier.store import Store
from dossier.tui import (
    DossierApp,
    home as tui_home,
)
from dossier.tui.reconcile import ReconcileScreen
from dossier.tui.rows import RowMode
from dossier.tui.screens import (
    BundleScreen,
    DetailScreen,
    DocPickerScreen,
    DoctorScreen,
    MoveScreen,
    SupersedeScreen,
    TextPromptScreen,
    WatchScreen,
)

TODAY = date(2026, 7, 21)


def _setup(tmp_path: Path) -> tuple[Store, Config]:
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_history")
    store = Store(config)
    store.ensure_layout()
    store.save(
        Document(
            id="passport",
            name="Passport",
            tags=["identity"],
            perm_location="file",
            perm_slot=1,
            has_physical=True,
            has_digital=True,
            files=[Rendition(label="d", path="passport.pdf", primary=True)],
        )
    )
    (tmp_path / "passport.pdf").write_bytes(b"x")
    store.save(
        Document(
            id="coc",
            name="CoC Card",
            tags=["marine"],
            perm_location="file",
            perm_slot=2,
            expiry_date=date(2026, 9, 1),  # ~42 days out -> expiring
        )
    )
    return store, config


@pytest.mark.asyncio
async def test_app_loads_and_search_filters(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        assert {d.id for d in app.home.visible_docs()} == {"passport", "coc"}
        app.query_one("#search", Input).value = "passport"
        await pilot.pause()
        assert [d.id for d in app.home.visible_docs()] == ["passport"]


@pytest.mark.asyncio
async def test_locations_and_documents_populate(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test():
        home = app.home
        # "All" + the one real location.
        assert home.query_one("#locations", OptionList).option_count == 2
        assert [d.id for d in home.documents_in_view()] == ["passport", "coc"]


@pytest.mark.asyncio
async def test_selecting_location_scopes_documents(tmp_path: Path):
    store, config = _setup(tmp_path)
    store.save(Document(id="loose", name="Loose", perm_location=None))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.select_location("file")
        await pilot.pause()
        assert [d.id for d in home.documents_in_view()] == ["passport", "coc"]
        assert "loose" not in {d.id for d in home.documents_in_view()}


@pytest.mark.asyncio
async def test_search_shows_flat_results_and_hides_locations(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.query_one("#search", Input).value = "passport"
        await pilot.pause()
        await pilot.pause()  # let the searching class re-apply the stylesheet
        assert home.has_class("searching")
        assert not home.query_one("#locations", OptionList).display
        assert [d.id for d in home.documents_in_view()] == ["passport"]


@pytest.mark.asyncio
async def test_narrow_collapses_panes_and_drills(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(50, 40)) as pilot:
        home = app.home
        assert home.has_class("-narrow")
        documents = home.query_one("#documents", OptionList)
        assert not documents.display  # locations pane only, until we drill in
        home.action_drill_in()
        await pilot.pause()
        assert home.has_class("show-documents")
        assert documents.display
        home.action_drill_out()
        await pilot.pause()
        assert not home.has_class("show-documents")


@pytest.mark.asyncio
async def test_enter_opens_detail_and_collapses_rows(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("coc")
        await pilot.pause()
        assert home.has_class("show-detail")
        assert home._detail_id == "coc"
        assert home._row_mode() is RowMode.COMPACT  # documents column collapses

        home.close_detail()
        await pilot.pause()
        assert not home.has_class("show-detail")


@pytest.mark.asyncio
async def test_open_file_action_uses_detailed_doc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config = _setup(tmp_path)
    opened: list[Path] = []
    monkeypatch.setattr(tui_home, "open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("passport")
        await pilot.pause()
        home.action_open_file()
    assert opened == [tmp_path / "passport.pdf"]


@pytest.mark.asyncio
async def test_medium_width_detail_drops_locations(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(80, 30)) as pilot:  # medium band
        home = app.home
        assert home.has_class("-medium")
        assert home.query_one("#locations", OptionList).display
        home.open_detail("coc")
        await pilot.pause()
        await pilot.pause()
        assert home.query_one("#detail").display  # detail shown
        assert not home.query_one("#locations", OptionList).display  # locations drop


@pytest.mark.asyncio
async def test_search_overrides_open_detail_with_flat_results(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("passport")
        await pilot.pause()
        home.query_one("#search", Input).value = "card"
        await pilot.pause()
        await pilot.pause()  # let the searching class re-apply the stylesheet
        assert home.has_class("searching")
        assert not home.query_one("#detail").display  # detail hidden while searching
        assert home._row_mode() is RowMode.DENSE  # full rows, not the collapsed cue
        assert [d.id for d in home.documents_in_view()] == ["coc"]


@pytest.mark.asyncio
async def test_expiring_toggle(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test():
        app.home.action_toggle_expiring()
        assert [d.id for d in app.home.visible_docs()] == ["coc"]


@pytest.mark.asyncio
async def test_open_document_invokes_opener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config = _setup(tmp_path)
    opened: list[Path] = []
    monkeypatch.setattr(tui_home, "open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test():
        app.home.open_document("passport")
    assert opened == [tmp_path / "passport.pdf"]


@pytest.mark.asyncio
async def test_detail_screen_edits_and_saves(tmp_path: Path):
    store, config = _setup(tmp_path)
    doc = next(d for d in store.load_all() if d.id == "coc")
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.push_screen(DetailScreen(store, doc))
        await pilot.pause()
        app.screen.query_one("#issue", Input).value = "2020-01-15"
        app.screen.query_one("#expiry", Input).value = "2026-09-01"
        app.screen.query_one("#notes", TextArea).text = "renewed early"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()

    reloaded = store.load("coc")
    assert reloaded.issue_date == date(2020, 1, 15)
    assert reloaded.expiry_date == date(2026, 9, 1)
    assert reloaded.notes == "renewed early"


@pytest.mark.asyncio
async def test_doctor_screen_lists_findings(tmp_path: Path):
    store, config = _setup(tmp_path)
    store.save(Document(id="amb", name="Cert 21-08-23", expiry_date=date(2023, 8, 21)))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.push_screen(DoctorScreen(store, config))
        await pilot.pause()
        assert app.screen.query_one("#findings", OptionList).option_count > 0


@pytest.mark.asyncio
async def test_new_document_creates(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.push_screen(DetailScreen(store, Document(), is_new=True))
        await pilot.pause()
        app.screen.query_one("#name", Input).value = "New Passport 2026"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()

    assert "new-passport-2026" in {d.id for d in store.load_all()}


@pytest.mark.asyncio
async def test_move_shifts_neighbours(tmp_path: Path):
    store, config = _setup(tmp_path)  # passport@file/1, coc@file/2
    store.save(Document(id="z", name="Z", perm_location="pouch", perm_slot=1))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        z = app.home._doc_by_id("z")
        assert z is not None
        app.push_screen(MoveScreen(store, app.home._docs, z))
        await pilot.pause()
        app.screen.query_one("#mloc", Input).value = "file"
        app.screen.query_one("#mslot", Input).value = "1"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()

    assert (store.load("z").perm_location, store.load("z").perm_slot) == ("file", 1)
    assert store.load("passport").perm_slot == 2  # shifted 1 -> 2
    assert store.load("coc").perm_slot == 3  # shifted 2 -> 3


@pytest.mark.asyncio
async def test_issue_expiry_toggle_flips(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test():
        home = app.home
        assert home._show_issue is False
        home.action_toggle_dates()
        assert home._show_issue is True
        home.action_toggle_dates()
        assert home._show_issue is False


@pytest.mark.asyncio
async def test_supersede_screen_sets_link(tmp_path: Path):
    store, config = _setup(tmp_path)
    store.save(Document(id="passport-2026", name="Passport 2026"))
    store.save(Document(id="passport-2016", name="Passport 2016"))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        renewal = app.home._doc_by_id("passport-2026")
        assert renewal is not None
        app.push_screen(SupersedeScreen(store, app.home._docs, renewal))
        await pilot.pause()
        options = app.screen.query_one("#scandidates", OptionList)
        options.focus()
        index = next(
            i
            for i in range(options.option_count)
            if options.get_option_at_index(i).id == "passport-2016"
        )
        options.highlighted = index
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()

    assert store.load("passport-2026").supersedes == "passport-2016"


@pytest.mark.asyncio
async def test_bundle_screen_creates_and_assigns(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        coc = app.home._doc_by_id("coc")
        assert coc is not None
        app.push_screen(BundleScreen(store, app.home._docs, coc))
        await pilot.pause()
        new = app.screen.query_one("#bnew", Input)
        new.focus()
        new.value = "US Visa"
        await pilot.pause()
        await pilot.press("enter")  # add + select the new bundle
        await pilot.pause()
        await pilot.press("ctrl+s")  # save
        await pilot.pause()

    assert "us-visa" in store.load("coc").bundles
    assert "us-visa" in store.load_bundles()


@pytest.mark.asyncio
async def test_touch_shows_action_bar_and_keyboard_button(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY, touch=True)
    async with app.run_test(size=(50, 80)) as pilot:  # portrait phone
        home = app.home
        assert home.has_class("touch")
        assert home.query_one("#actionbar").display
        # The ⌨ button focuses the command bar (raising the IME on Termux); the
        # mouse-reporting drop is a no-op on the headless driver.
        home.query_one("#act-kbd", Button).press()
        await pilot.pause()
        assert app.focused is home.query_one("#search", Input)


@pytest.mark.asyncio
async def test_action_bar_hidden_without_touch(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test():
        assert not app.home.query_one("#actionbar").display


@pytest.mark.asyncio
async def test_watch_screen_lists_tracked_and_ignores(tmp_path: Path):
    store, config = _setup(tmp_path)  # coc has an expiry (tracked); passport does not
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = WatchScreen(store, config, today=TODAY)
        app.push_screen(screen)
        await pilot.pause()
        watch = screen.query_one("#watch", OptionList)
        assert watch.option_count == 1  # only the doc with an expiry is tracked
        assert watch.get_option_at_index(0).id == "coc"

        watch.highlighted = 0
        await pilot.pause()
        screen.action_ignore()  # drop it from the watch
        await pilot.pause()
        assert watch.option_count == 0

    assert store.load("coc").ignore_expiry is True


@pytest.mark.asyncio
async def test_reconcile_screen_shows_orphans_and_missing(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    (tmp_path / "Marine").mkdir()
    (tmp_path / "Marine" / "loose scan.pdf").write_bytes(b"x")  # orphan
    store.save(
        Document(
            id="d", name="Doc", files=[Rendition("x", "Marine/gone.pdf", primary=True)]
        )
    )  # links a missing file
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = ReconcileScreen(store, config)
        app.push_screen(screen)
        await pilot.pause()
        folders = [
            str(node.label) for node in screen.query_one("#orphans", Tree).root.children
        ]
        assert any("Marine" in label for label in folders)  # orphan grouped by folder
        missing = screen.query_one("#missing", OptionList)
        # composite id: doc id + NUL + path (so two dead renditions can't collide)
        assert missing.get_option_at_index(0).id == "d\x00Marine/gone.pdf"


@pytest.mark.asyncio
async def test_reconcile_dismiss_orphan_persists_and_hides(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    (tmp_path / "Wallpapers").mkdir()
    (tmp_path / "Wallpapers" / "bg.jpg").write_bytes(b"x")  # a non-document orphan
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = ReconcileScreen(store, config)
        app.push_screen(screen)
        await pilot.pause()
        tree = screen.query_one("#orphans", Tree)
        assert screen._report is not None
        assert any(o.path == "Wallpapers/bg.jpg" for o in screen._report.orphans)
        # Switch to the Orphans tab (x is scoped to the active tab), expand the
        # folder, land the cursor on the leaf, then dismiss it.
        screen.query_one(TabbedContent).active = "tab-orphans"
        folder = next(n for n in tree.root.children if n.data == "Wallpapers")
        folder.expand()
        await pilot.pause()
        leaf = folder.children[0]
        tree.move_cursor(leaf)
        assert tree.cursor_node is leaf
        screen.action_reject()
        await pilot.pause()
        # Gone from the live report, and recorded in the sidecar for next time.
        assert screen._report is not None
        assert not screen._report.orphans
        assert store.load_reconcile().dismissed == {"Wallpapers/bg.jpg"}


@pytest.mark.asyncio
async def test_reconcile_ack_missing_persists(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    store.save(
        Document(id="d", name="Doc", files=[Rendition("x", "gone.pdf", primary=True)])
    )
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = ReconcileScreen(store, config)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one(TabbedContent).active = "tab-missing"
        screen.query_one("#missing", OptionList).highlighted = 0
        await pilot.pause()
        screen.action_reject()
        await pilot.pause()
        assert screen._report is not None
        assert not screen._report.missing
        assert store.load_reconcile().missing_ok == {"gone.pdf": {"d"}}


async def _cursor_on_orphan(pilot, screen, folder_name: str):
    """Switch to Orphans, expand a folder, and put the cursor on its first leaf."""
    screen.query_one(TabbedContent).active = "tab-orphans"
    tree = screen.query_one("#orphans", Tree)
    folder = next(n for n in tree.root.children if n.data == folder_name)
    folder.expand()
    await pilot.pause()
    tree.move_cursor(folder.children[0])


@pytest.mark.asyncio
async def test_reconcile_link_orphan_to_existing_document(tmp_path: Path):
    root = tmp_path / "root"  # history_dir sibling, so backups aren't orphans
    root.mkdir()
    config = Config(syncthing_root=root, history_dir=tmp_path / "_hist")
    store = Store(config)
    store.ensure_layout()
    (root / "Marine").mkdir()
    (root / "Marine" / "CoC Card.pdf").write_bytes(b"x")  # orphan
    store.save(Document(id="coc", name="CoC Card"))  # a doc with no files yet
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = ReconcileScreen(store, config)
        app.push_screen(screen)
        await pilot.pause()
        await _cursor_on_orphan(pilot, screen, "Marine")
        screen.action_link()
        await pilot.pause()
        picker = app.screen
        assert isinstance(picker, DocPickerScreen)
        picker.dismiss("coc")  # choose the document
        await pilot.pause()
        doc = store.load("coc")
        assert [r.path for r in doc.files] == ["Marine/CoC Card.pdf"]
        assert doc.files[0].primary  # first rendition becomes primary
        assert doc.has_digital
        assert screen._report is not None
        assert not screen._report.orphans  # now linked → no longer an orphan


@pytest.mark.asyncio
async def test_reconcile_adopt_orphan_creates_document(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    (tmp_path / "Marine").mkdir()
    (tmp_path / "Marine" / "New Cert.pdf").write_bytes(b"x")  # orphan, no doc yet
    # (adopt creates a new doc → no overwrite backup, so root==tmp_path is fine)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = ReconcileScreen(store, config)
        app.push_screen(screen)
        await pilot.pause()
        await _cursor_on_orphan(pilot, screen, "Marine")
        screen.action_adopt()
        await pilot.pause()
        detail = app.screen
        assert isinstance(detail, DetailScreen)
        assert detail.query_one("#name", Input).value == "New Cert"  # prefilled
        detail.action_save()
        await pilot.pause()
        doc = store.load("new-cert")  # slug of the prefilled name
        assert [r.path for r in doc.files] == ["Marine/New Cert.pdf"]
        assert doc.has_digital
        assert screen._report is not None
        assert not screen._report.orphans  # adopted → no longer an orphan


@pytest.mark.asyncio
async def test_reconcile_unlink_dead_rendition(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    store.save(
        Document(
            id="d",
            name="Doc",
            has_digital=True,
            files=[Rendition("x", "gone.pdf", primary=True)],
        )
    )
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = ReconcileScreen(store, config)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one(TabbedContent).active = "tab-missing"
        screen.query_one("#missing", OptionList).highlighted = 0
        await pilot.pause()
        screen.action_unlink()
        await pilot.pause()
        doc = store.load("d")
        assert doc.files == []
        assert doc.has_digital is False
        assert screen._report is not None
        assert not screen._report.missing


@pytest.mark.asyncio
async def test_reconcile_fold_cluster_persists_and_suppresses(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = ReconcileScreen(store, config)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one(TabbedContent).active = "tab-dups"
        # Inject a scanned cluster directly (no rasterizing in the test).
        screen._pages = {"keep.pdf": [1, 2, 3], "copy.pdf": [1, 2]}
        screen._populate_dups(
            [
                dedup.DupGroup(
                    files=["copy.pdf", "keep.pdf"],
                    keep="keep.pdf",
                    subsets=["copy.pdf"],
                    ambiguous=False,
                )
            ]
        )
        await pilot.pause()
        screen.query_one("#dups", OptionList).highlighted = 0  # the cluster header
        screen.action_fold()
        await pilot.pause()
        assert store.load_reconcile().folded == {"keep.pdf": {"copy.pdf"}}
        assert screen._dups_count == 0  # cluster suppressed on the re-filter


@pytest.mark.asyncio
async def test_reconcile_ignore_glob_adds_and_hides(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    (tmp_path / "Wallpapers").mkdir()
    (tmp_path / "Wallpapers" / "bg.jpg").write_bytes(b"x")
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = ReconcileScreen(store, config)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one(TabbedContent).active = "tab-orphans"
        tree = screen.query_one("#orphans", Tree)
        folder = next(n for n in tree.root.children if n.data == "Wallpapers")
        tree.move_cursor(folder)  # prefills the glob from the folder
        screen.action_ignore_glob()
        await pilot.pause()
        prompt = app.screen
        assert isinstance(prompt, TextPromptScreen)
        assert prompt.query_one("#tpinput", Input).value == "Wallpapers/*"
        prompt.dismiss("Wallpapers/*")
        await pilot.pause()
        assert store.load_reconcile().ignore == ["Wallpapers/*"]
        assert screen._report is not None
        assert not screen._report.orphans  # ignored subtree → no orphans


def test_linux_driver_exposes_mouse_toggle():
    # Pin the private methods the keyboard trick relies on, so a Textual rename
    # fails here loudly instead of silently breaking the Termux keyboard. The
    # Linux driver is Unix-only (imports termios), so skip off Unix.
    pytest.importorskip("termios")
    from textual.drivers.linux_driver import LinuxDriver

    assert hasattr(LinuxDriver, "_enable_mouse_support")
    assert hasattr(LinuxDriver, "_disable_mouse_support")
