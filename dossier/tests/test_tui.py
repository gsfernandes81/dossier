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
from textual.widgets import Input, OptionList, TextArea

from dossier.config import Config
from dossier.model import Document, Rendition
from dossier.store import Store
from dossier.tui import (
    DossierApp,
    home as tui_home,
)
from dossier.tui.screens import DetailScreen, DoctorScreen, MoveScreen

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
