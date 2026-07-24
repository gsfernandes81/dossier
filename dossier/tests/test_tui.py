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

"""Tests for the Textual TUI (driven headlessly via App.run_test).

**Wait for the effect, never for a duration.** Anything the app does off the
message pump — a thread worker, an async ``remove_children`` — lands after an
unknown number of scheduler turns, so `pause()`-and-assert (or `pause()` twice,
which is the same guess with more rope) passes or fails on machine load. Use
:func:`_settle` with a predicate; it drains workers until the condition holds and
fails with a name instead of an assertion mystery. ``wait_for_complete()`` is not
a substitute: it returns immediately when the worker has not registered yet.
"""

import asyncio
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from textual.containers import Horizontal
from textual.widgets import (
    Button,
    Checkbox,
    HelpPanel,
    Input,
    OptionList,
    TabbedContent,
    TextArea,
    Tree,
)

from dossier import dedup, succession
from dossier.config import Config
from dossier.model import Bundle, Document, Rendition
from dossier.store import Store
from dossier.tui import (
    DossierApp,
    home as tui_home,
)
from dossier.tui.commands import ENTRIES
from dossier.tui.detail_pane import DetailPane
from dossier.tui.doclist import DocumentList
from dossier.tui.review import ReviewPane
from dossier.tui.rows import RowMode
from dossier.tui.screens import (
    BundlesScreen,
    ChoiceScreen,
    DocPickerScreen,
    SupersedeScreen,
    TextPromptScreen,
    WatchScreen,
    _command_index_text,
)

TODAY = date(2026, 7, 21)


async def _open_review(pilot) -> ReviewPane:
    """Open the review surface and wait for its load; return it.

    The single seam for *how review is activated* — now a mode of the home rather
    than a pushed modal, which is why this is the only place that had to learn.
    """
    pilot.app.home.action_review()
    return await _await_review_load(pilot)


async def _open_review_tab(pilot, tab: str) -> ReviewPane:
    """Open review, switch to `tab`, and settle any work that tab defers."""
    pane = await _await_review_load(pilot)
    pane.query_one(TabbedContent).active = tab
    await _settle(pilot)
    return pane


async def _settle(pilot, until=None, *, what: str = "the app to settle") -> None:
    """Drain background work, optionally polling until ``until()`` holds.

    The pattern this replaces — start work, ``pause()``, ``wait_for_complete()``,
    assert — looks synchronous but is not: ``wait_for_complete`` returns *immediately*
    when the worker has not registered yet, and one ``pause()`` does not guarantee it
    has. The assert then runs against the pre-work state and passes or fails on
    scheduling luck. Twice this session that cost a red CI run, once masking a real
    bug. Polling for the effect is both faster (returns as soon as it is true) and
    honest (fails with a message instead of a mystery).
    """
    for _ in range(300):
        await pilot.pause()
        await pilot.app.workers.wait_for_complete()
        await pilot.pause()
        if until is None or until():
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


async def _await_review_load(pilot) -> ReviewPane:
    """Wait for the review pane's load worker to land; return the pane."""

    def loaded() -> bool:
        panes = pilot.app.screen.query(ReviewPane)
        return bool(panes) and panes.first()._report is not None

    await _settle(pilot, loaded, what="review's load worker")
    return pilot.app.screen.query(ReviewPane).first()


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
async def test_documents_pane_caps_its_rows_and_says_so(tmp_path: Path):
    """Typing must not cost O(store).

    Textual measures every option a list holds on each refresh, so rendering a full
    store made each keystroke ~630 ms at ~950 documents — the cost is in rows nobody
    can see. Cap the render and tell the user, rather than quietly truncating.
    """
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    total = tui_home._MAX_ROWS + 25
    for i in range(total):
        store.save(Document(id=f"doc-{i:04d}", name=f"Document {i}"))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        await pilot.pause()
        options = home.query_one("#documents", OptionList)
        # The capped rows, plus one row that owns up to the rest.
        assert options.option_count == tui_home._MAX_ROWS + 1
        last = str(options.get_option_at_index(options.option_count - 1).prompt)
        assert "25 more" in last
        assert options.get_option_at_index(options.option_count - 1).id is None
        # The count stays honest about the whole store, not just what is drawn.
        assert app.sub_title.startswith(f"{total} / {total}")


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
async def test_footer_is_a_small_core_and_occasional_actions_left_the_keys(
    tmp_path: Path,
):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        visible = {
            ab.binding.description
            for ab in home.active_bindings.values()
            if ab.binding.show
        }
        # Find-fast home: the footer is just Search + Back + Help. No letter owns an
        # action any more (typing starts a search), so nothing competes with a find.
        assert {"Search", "Help"} <= visible
        assert not (visible & {"Open", "Edit", "New", "Bundle"})  # moved to the palette
        # No letter is a home binding — every printable is the start of a search.
        for gone in (
            "o",
            "e",
            "n",
            "b",
            "a",
            "d",
            "r",
            "w",
            "x",
            "m",
            "s",
            "v",
            "comma",
        ):
            assert gone not in home.active_bindings
        # `?` still reveals the remaining bindings via Textual's HelpPanel.
        assert len(app.screen.query(HelpPanel)) == 0
        await pilot.press("question_mark")
        await pilot.pause()
        assert len(app.screen.query(HelpPanel)) == 1


@pytest.mark.asyncio
async def test_typing_from_a_column_routes_into_search(tmp_path: Path):
    # Find-fast: a printable typed while a column is focused jumps into search,
    # first character kept — no `/` mode key first.
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.query_one("#documents", OptionList).focus()
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
        search = home.query_one("#search", Input)
        assert app.focused is search
        assert search.value == "p"  # the routed character was kept
        await pilot.press("a")  # subsequent keys land in the now-focused Input
        await pilot.pause()
        assert search.value == "pa"


@pytest.mark.asyncio
async def test_slash_and_help_are_not_routed_into_search(tmp_path: Path):
    # `/` focuses search without inserting a slash; `?` opens help, not a search.
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.query_one("#documents", OptionList).focus()
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        search = home.query_one("#search", Input)
        assert app.focused is search and search.value == ""


@pytest.mark.asyncio
async def test_down_from_search_steps_into_the_list_keeping_the_filter(tmp_path: Path):
    # Enter now opens the top match, so `↓` is the "browse the hits" move: it focuses
    # the documents pane while keeping the filter applied.
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        search = home.query_one("#search", Input)
        search.focus()
        search.value = "pass"
        await pilot.pause()
        await pilot.press("down")
        await pilot.pause()
        assert app.focused is home.query_one("#documents", OptionList)
        assert search.value == "pass"  # filter kept


@pytest.mark.asyncio
async def test_termux_opens_type_first(tmp_path: Path):
    # Touch/Termux launches with the search box focused (type-first) so a find can
    # start on the first keystroke.
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY, touch=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.focused is app.home.query_one("#search", Input)


@pytest.mark.asyncio
async def test_desktop_opens_on_the_list(tmp_path: Path):
    # Desktop keeps the list focused on launch — the router still routes the first
    # printable into search, so both find and browse are immediate.
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.focused is app.home.query_one("#documents", OptionList)


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
async def test_search_filters_in_place_keeping_columns(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.select_location("file")  # seed a scope so the All-snap is exercised
        await pilot.pause()
        home.query_one("#search", Input).value = "passport"
        await pilot.pause()
        await pilot.pause()  # let the searching class re-apply the stylesheet
        assert home.has_class("searching")
        assert home.query_one("#locations", OptionList).display  # columns stay put
        assert [d.id for d in home.documents_in_view()] == ["passport"]
        # root-wide results → the locations pane snaps to "All"
        assert home._selection == tui_home._ALL
        assert home.query_one("#locations", OptionList).highlighted == 0


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
    monkeypatch.setattr("dossier.tui.screens.open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("passport")
        await pilot.pause()
        home.action_open_file()
    assert opened == [tmp_path / "passport.pdf"]


@pytest.mark.asyncio
async def test_enter_activates_document_opens_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Find-fast verb: activating a document (Enter/tap) opens its file straight away.
    store, config = _setup(tmp_path)
    opened: list[Path] = []
    monkeypatch.setattr("dossier.tui.screens.open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.home._activate_doc("passport")
        await pilot.pause()
    assert opened == [tmp_path / "passport.pdf"]


@pytest.mark.asyncio
async def test_click_on_another_row_points_before_it_activates():
    """The mouse verb is two-part: click one moves the cursor, click two activates.

    Textual's OptionList does both in one click, which made a single click on the home
    show a document *and* launch its file. Driven here on a bare list so the assertion
    is about the widget's contract, not the home's row heights.
    """
    from textual.app import App, ComposeResult
    from textual.widgets.option_list import Option

    events: list[str] = []

    class _Harness(App[None]):
        def compose(self) -> ComposeResult:
            yield DocumentList(
                Option("first", id="first"), Option("second", id="second"), id="docs"
            )

        def on_document_list_previewed(self, event: DocumentList.Previewed) -> None:
            events.append(f"preview:{event.option_id}")

        def on_option_list_option_selected(
            self, event: OptionList.OptionSelected
        ) -> None:
            events.append(f"select:{event.option_id}")

    app = _Harness()
    async with app.run_test() as pilot:
        docs = app.query_one("#docs", DocumentList)
        docs.highlighted = 0  # cursor parked on the first row
        await pilot.pause()

        # y counts from the widget edge, and OptionList's default border is `tall`
        # — so row 0 sits at y=1 and the row we want at y=2.
        await pilot.click("#docs", offset=(2, 2))  # click the *other* row
        await pilot.pause()
        assert events == ["preview:second"], "a click on a new row activated it"
        assert docs.highlighted == 1, "the click did not move the cursor"

        await pilot.click("#docs", offset=(2, 2))  # click it again
        await pilot.pause()
        assert events == ["preview:second", "select:second"]

        # Enter is unaffected — the cursor is already where the user put it.
        await pilot.press("enter")
        await pilot.pause()
        assert events == ["preview:second", "select:second", "select:second"]


@pytest.mark.asyncio
async def test_first_click_shows_detail_and_second_opens_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # The home's half of the two-part mouse verb: point (detail), then activate (open).
    store, config = _setup(tmp_path)
    opened: list[Path] = []
    monkeypatch.setattr("dossier.tui.screens.open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        app.query_one("#search", Input).value = "passport"  # a single row
        await pilot.pause()
        documents = home.query_one("#documents", DocumentList)
        documents.highlighted = None  # nothing pointed at yet
        await pilot.pause()
        assert not home.has_class("show-detail")

        await pilot.click("#documents", offset=(2, 1))  # y=1: past the `tall` border
        await pilot.pause()
        assert home.has_class("show-detail"), "first click did not show detail"
        assert home._detail_id == "passport"
        assert opened == [], "first click opened the file"

        await pilot.click("#documents", offset=(2, 1))
        await pilot.pause()
        assert opened == [tmp_path / "passport.pdf"], "second click did not open"


@pytest.mark.asyncio
async def test_activate_physical_only_shows_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # A physical-only record has no file to open, so activating it shows its detail
    # (where the physical copy lives) with a notice — never a spurious opener call.
    store, config = _setup(tmp_path)
    opened: list[Path] = []
    monkeypatch.setattr("dossier.tui.screens.open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home._activate_doc("coc")  # coc has no digital rendition
        await pilot.pause()
        assert opened == []
        assert home.has_class("show-detail")
        assert any("no digital file" in n.message for n in app._notifications)


@pytest.mark.asyncio
async def test_enter_in_search_opens_top_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Enter from the search box opens the top match's file — cold-launch find-fast.
    store, config = _setup(tmp_path)
    opened: list[Path] = []
    monkeypatch.setattr("dossier.tui.screens.open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        search = app.home.query_one("#search", Input)
        search.focus()
        search.value = "passport"  # filters; top hit pinned to row 0
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert opened == [tmp_path / "passport.pdf"]


@pytest.mark.asyncio
async def test_open_progressively_drills_columns(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(50, 40)) as pilot:  # narrow: one pane at a time
        home = app.home
        home.query_one("#locations", OptionList).focus()
        await pilot.pause()
        # 1st Open drills locations → documents; no detail yet.
        home.action_open_file()
        await pilot.pause()
        docs = home.query_one("#documents", OptionList)
        assert app.focused is docs
        assert not home._show_detail
        docs.highlighted = 0
        await pilot.pause()
        # 2nd Open drills documents → detail (third column).
        home.action_open_file()
        await pilot.pause()
        assert home._show_detail


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
async def test_search_with_detail_open_previews_top_hit(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("passport")
        await pilot.pause()
        home.query_one("#search", Input).value = "card"  # filters passport out
        await pilot.pause()
        await pilot.pause()  # let the searching class re-apply the stylesheet
        assert home.has_class("searching")
        assert home.query_one("#detail").display  # detail stays open, previewing
        assert home._row_mode() is RowMode.COMPACT  # names-only beside the detail
        assert [d.id for d in home.documents_in_view()] == ["coc"]
        assert home._detail_id == "coc"  # preview followed the top hit
        # medium band + detail open still drops the locations column
        assert not home.query_one("#locations", OptionList).display


@pytest.mark.asyncio
async def test_escape_clears_search_in_place(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.query_one("#search", Input).value = "passport"
        await pilot.pause()
        assert home.has_class("searching")
        home.action_escape()
        await pilot.pause()
        assert not home.has_class("searching")
        assert home.query_one("#locations", OptionList).display
        assert {d.id for d in home.documents_in_view()} == {"passport", "coc"}
        assert app.focused is home.query_one("#documents", OptionList)


@pytest.mark.asyncio
async def test_narrow_search_fronts_documents_and_drills_to_detail(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(50, 40)) as pilot:  # narrow band
        home = app.home
        assert home.has_class("-narrow")
        home.query_one("#search", Input).value = "passport"
        await pilot.pause()
        await pilot.pause()
        assert home.query_one("#documents", OptionList).display  # results fronted
        assert not home.query_one("#locations", OptionList).display
        home.open_detail("passport")  # drilling a result → detail full-screen
        await pilot.pause()
        assert home.query_one("#detail").display
        assert not home.query_one("#documents", OptionList).display  # order wins


@pytest.mark.asyncio
async def test_expiring_toggle_keeps_columns(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.select_location("file")
        await pilot.pause()
        home.action_toggle_expiring()
        await pilot.pause()
        assert home.has_class("searching")
        assert home.query_one("#locations", OptionList).display  # columns stay put
        assert home._selection == tui_home._ALL


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
    monkeypatch.setattr("dossier.tui.screens.open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test():
        app.home.open_document("passport")
    assert opened == [tmp_path / "passport.pdf"]


@pytest.mark.asyncio
async def test_detail_pane_edits_dates_and_notes(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        pane = await _enter_edit(pilot, home, "coc")
        pane.query_one("#f-issue", Input).value = "2020-01-15"
        pane.query_one("#f-expiry", Input).value = "2026-09-01"
        pane.query_one("#f-notes", TextArea).text = "renewed early"
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()

    reloaded = store.load("coc")
    assert reloaded.issue_date == date(2020, 1, 15)
    assert reloaded.expiry_date == date(2026, 9, 1)
    assert reloaded.notes == "renewed early"


@pytest.mark.asyncio
async def test_review_integrity_tab_lists_findings(tmp_path: Path):
    store, config = _setup(tmp_path)
    store.save(Document(id="amb", name="Cert 21-08-23", expiry_date=date(2023, 8, 21)))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = await _open_review(pilot)
        options = screen.query_one("#integrity", OptionList)
        # Deferred: on mount the tab only shows a placeholder and the check has not
        # run (count still None), so opening Review never pays the doctor cost.
        assert options.option_count == 1
        assert "open" in str(options.get_option_at_index(0).prompt)
        assert screen._integrity_count is None
        # Activating the tab runs the check in a worker; wait for it, then assert.
        screen.query_one(TabbedContent).active = "tab-integrity"
        await _settle(
            pilot, lambda: options.option_count > 1, what="integrity findings"
        )
        # An ambiguous-date finding for "amb" is listed (with a group header row).
        assert options.option_count > 1
        assert any("amb" in str(o.prompt) for o in options.options)


@pytest.mark.asyncio
async def test_review_integrity_edit_opens_the_flagged_doc(tmp_path: Path):
    # `e` on an Integrity finding dismisses with an edit request for that doc.
    store, config = _setup(tmp_path)
    store.save(Document(id="amb", name="Cert 21-08-23", expiry_date=date(2023, 8, 21)))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.action_review()
        pane = await _await_review_load(pilot)
        pane.query_one(TabbedContent).active = "tab-integrity"
        await _settle(
            pilot, lambda: pane._integrity_count is not None, what="the integrity check"
        )
        options = pane.query_one("#integrity", OptionList)
        # Highlight the finding row (skip the group-header row, which has no id).
        options.highlighted = next(
            i for i, o in enumerate(options.options) if o.id is not None
        )
        pane.action_edit()
        await pilot.pause()
        await pilot.pause()  # let the dismiss callback open + start_edit settle
        assert app.screen is home  # review dismissed
        assert home.has_class("show-detail")  # the flagged doc opened
        assert home.editing  # …in edit mode


@pytest.mark.asyncio
async def test_review_doc_write_invalidates_cached_integrity(tmp_path: Path):
    # Slice-2 contract: the screen shares one doc snapshot across tabs, but a
    # document write drops the cached integrity result so the next open re-checks
    # against fresh data (never a stale copy).
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    store.save(  # a dead rendition (Missing tab) on an ambiguous-date doc (Integrity)
        Document(
            id="d",
            name="Doc 21-08-23",
            has_digital=True,
            files=[Rendition("x", "gone.pdf", primary=True)],
        )
    )
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = await _open_review(pilot)
        assert screen._docs is not None  # snapshot loaded once on mount
        # Run integrity so there's a cached result to invalidate.
        screen.query_one(TabbedContent).active = "tab-integrity"
        await _settle(
            pilot,
            lambda: screen._integrity_count is not None,
            what="the integrity check",
        )
        assert screen._integrity_count is not None
        # Unlink the dead rendition — a document write.
        screen.query_one(TabbedContent).active = "tab-missing"
        screen.query_one("#missing", OptionList).highlighted = 0
        await pilot.pause()
        screen.action_unlink()
        await pilot.pause()
        assert screen._report is not None
        assert not screen._report.missing  # _refresh used a fresh snapshot
        assert screen._integrity_count is None  # cached integrity dropped
        assert not screen._integrity_started  # …and will re-run on next open


@pytest.mark.asyncio
async def test_new_document_creates(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.action_new()  # inline: opens the pane in new-document edit mode
        await pilot.pause()
        pane = home.query_one("#detail", DetailPane)
        assert pane.editing
        pane.query_one("#f-name", Input).value = "New Passport 2026"
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
        home = app.home
        home.open_detail("z")
        await pilot.pause()
        home.action_move()  # inline: start_edit focused on the location field
        await pilot.pause()
        pane = home.query_one("#detail", DetailPane)
        pane.query_one("#f-perm", Input).value = "file"
        pane.query_one("#f-perm-slot", Input).value = "1"
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
async def test_inline_bundle_creates_and_assigns(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("coc")
        await pilot.pause()
        home.action_bundle()  # inline: start_edit focused on the bundles field
        await pilot.pause()
        pane = home.query_one("#detail", DetailPane)
        new = pane.query_one("#f-new-bundle", Input)
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
async def test_touch_focus_drives_soft_keyboard_no_kbd_button(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY, touch=True)
    reporting: list[bool] = []
    async with app.run_test(size=(50, 80)) as pilot:  # portrait phone
        home = app.home
        assert home.has_class("touch")
        assert home.query_one("#actionbar").display
        # No dedicated ⌨ button any more — focus itself drives the keyboard.
        assert not home.query("#act-kbd")
        monkeypatch.setattr(app, "set_mouse_reporting", reporting.append)
        # Focusing the search field drops mouse reporting so the Termux IME can
        # appear; focusing a non-text pane restores it so taps land again. (Touch
        # launches with search focused, so step onto the list first for a clean
        # transition.)
        home.query_one("#documents", OptionList).focus()
        await pilot.pause()
        assert reporting[-1] is True
        home.query_one("#search", Input).focus()
        await pilot.pause()
        assert reporting[-1] is False
        home.query_one("#documents", OptionList).focus()
        await pilot.pause()
        assert reporting[-1] is True


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
async def test_watch_enter_opens_file_and_right_opens_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # The app-wide verb reaches the watch too: Enter opens the document's file,
    # `→` hands the id back so the home shows its detail record.
    store, config = _setup(tmp_path)
    tracked = store.load("passport")  # has a digital file; give it an expiry
    tracked.expiry_date = date(2026, 9, 1)
    store.save(tracked)
    opened: list[Path] = []
    monkeypatch.setattr("dossier.tui.screens.open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = WatchScreen(store, config, today=TODAY)
        dismissed: list[str | None] = []
        app.push_screen(screen, dismissed.append)
        await pilot.pause()
        options = screen.query_one("#watch", OptionList)
        options.focus()
        options.highlighted = next(
            i
            for i in range(options.option_count)
            if options.get_option_at_index(i).id == "passport"
        )
        await pilot.pause()
        await pilot.press("enter")  # activate → open the file, screen stays up
        await pilot.pause()
        assert opened == [tmp_path / "passport.pdf"]
        assert dismissed == []

        await pilot.press("right")  # → hands the doc to the home for its detail
        await pilot.pause()
        assert dismissed == ["passport"]


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
        screen = await _open_review(pilot)
        folders = [
            str(node.label) for node in screen.query_one("#orphans", Tree).root.children
        ]
        assert any("Marine" in label for label in folders)  # orphan grouped by folder
        missing = screen.query_one("#missing", OptionList)
        # composite id: doc id + NUL + path (so two dead renditions can't collide)
        assert missing.get_option_at_index(0).id == "d\x00Marine/gone.pdf"


@pytest.mark.asyncio
async def test_default_tab_applies_even_if_the_load_beats_the_tab_bar(tmp_path: Path):
    """The load can land before TabbedContent has an active tab, and did on CI.

    Review is mounted lazily into a live screen now, so its worker races the tab
    bar's own mount. An unsettled TabbedContent reports `active == ""`, which is
    "untouched" just as much as "tab-conflicts" is — reading only the latter as
    untouched skipped the re-target and left review on the wrong tab.
    """
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    (tmp_path / "loose.pdf").write_bytes(b"x")  # an orphan → Orphans is the default
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        pane = await _open_review(pilot)
        tabs = pane.query_one(TabbedContent)
        assert pane._report is not None

        # Replay the pre-settle state the worker can observe, then re-apply in the
        # same breath. No pause in between: an empty active tab is a transient the
        # TabbedContent never settles into on its own, so awaiting it (rather than
        # letting _apply_load correct it synchronously) times the screen out — which
        # is a test artifact, not the behaviour under test.
        tabs.active = ""
        pane._apply_load(
            pane._state, pane._docs or [], pane._report, pane._readings, pane._plans
        )
        await pilot.pause()
        assert tabs.active == "tab-orphans"


@pytest.mark.asyncio
async def test_reconcile_opens_on_orphans_and_cycles_tabs(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    (tmp_path / "loose.pdf").write_bytes(b"x")  # an orphan on disk
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = await _open_review(pilot)
        # Opens on Orphans (actionable) — not the empty, scan-only Duplicates tab.
        tabs = screen.query_one(TabbedContent)
        assert tabs.active == "tab-orphans"
        # Tab / Shift+Tab (real key presses) cycle the tabs, beating focus-traversal.
        await pilot.press("tab")
        await pilot.pause()
        assert tabs.active == "tab-missing"
        await pilot.press("shift+tab")
        await pilot.pause()
        assert tabs.active == "tab-orphans"
        # `?` toggles the keybind help panel (discoverability).
        from textual.widgets import HelpPanel

        # The panel mounts on the *screen*, not on the pane that binds `?`.
        assert not app.screen.query(HelpPanel)
        await pilot.press("question_mark")
        await pilot.pause()
        assert app.screen.query(HelpPanel)
        await pilot.press("question_mark")
        await pilot.pause()
        assert not app.screen.query(HelpPanel)


@pytest.mark.asyncio
async def test_quick_accept_applies_and_saves_top_suggestion(tmp_path: Path):
    store, config = _setup(tmp_path)
    store.save(Document(id="visa", name="US Visa 12-Jan-2026", perm_location="wallet"))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("visa")
        await pilot.pause()
        await pilot.pause()  # let show_document populate the read-view suggestions
        assert home._detail_pane.has_pending_suggestion()
        home.action_accept_suggestion()  # the `a` binding, from the documents pane
        await pilot.pause()
        await pilot.pause()
        assert store.load("visa").issue_date == date(2026, 1, 12)
        assert not home._detail_pane.has_pending_suggestion()  # satisfied → gone


@pytest.mark.asyncio
async def test_help_panel_toggles_with_question_mark(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        assert not app.screen.query(HelpPanel)
        await pilot.press("question_mark")
        await pilot.pause()
        assert app.screen.query(HelpPanel)  # shown
        await pilot.press("question_mark")
        await pilot.pause()
        assert not app.screen.query(HelpPanel)  # same key hides it again


@pytest.mark.asyncio
async def test_reconcile_open_file_opens_orphan_under_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import dossier.tui.review as tui_review

    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    (tmp_path / "Wallpapers").mkdir()
    (tmp_path / "Wallpapers" / "bg.jpg").write_bytes(b"x")
    opened: list[Path] = []
    monkeypatch.setattr(tui_review, "open_file", opened.append)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = await _open_review(pilot)
        tree = screen.query_one("#orphans", Tree)
        screen.query_one(TabbedContent).active = "tab-orphans"
        folder = next(n for n in tree.root.children if n.data == "Wallpapers")
        folder.expand()
        await pilot.pause()
        tree.move_cursor(folder.children[0])
        screen.action_open_file()
        await pilot.pause()
    assert opened == [tmp_path / "Wallpapers" / "bg.jpg"]


@pytest.mark.asyncio
async def test_reconcile_dismiss_orphan_persists_and_hides(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    (tmp_path / "Wallpapers").mkdir()
    (tmp_path / "Wallpapers" / "bg.jpg").write_bytes(b"x")  # a non-document orphan
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = await _open_review(pilot)
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
        screen = await _open_review(pilot)
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
        screen = await _open_review(pilot)
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
        screen = await _open_review(pilot)
        await _cursor_on_orphan(pilot, screen, "Marine")
        screen.action_adopt()  # creates the doc immediately, then dismisses
        await pilot.pause()
        doc = store.load("new-cert")  # slug of the file-stem name
        assert doc.name == "New Cert"
        assert [r.path for r in doc.files] == ["Marine/New Cert.pdf"]
        assert doc.has_digital
        assert app.screen is app.home  # reconcile dismissed, back to the home screen


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
        screen = await _open_review(pilot)
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
        screen = await _open_review(pilot)
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
async def test_integrity_enter_opens_the_file_and_right_opens_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Integrity joins the app-wide verb: Enter opens the file, `→` shows the record.
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    store.save(
        Document(
            id="passport",
            name="Passport",
            perm_location="nowhere",  # unknown slug -> a location-ref finding
            has_digital=True,
            files=[Rendition(label="d", path="passport.pdf", primary=True)],
        )
    )
    (tmp_path / "passport.pdf").write_bytes(b"x")
    opened: list[Path] = []
    monkeypatch.setattr("dossier.tui.review.open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(120, 32)) as pilot:
        home = app.home
        home.action_review()
        pane = await _open_review_tab(pilot, "tab-integrity")
        options = pane.query_one("#integrity", OptionList)
        options.highlighted = next(
            i
            for i in range(options.option_count)
            if (options.get_option_at_index(i).id or "").startswith("passport")
        )
        options.focus()
        await pilot.pause()

        await pilot.press("enter")  # activate → the file
        await pilot.pause()
        assert opened == [tmp_path / "passport.pdf"]
        assert not home.has_class("show-detail"), "Enter should not open the record"

        await pilot.press("right")  # → the record, where a sidecar finding is fixed
        await pilot.pause()
        assert home.has_class("show-detail")
        assert home._detail_id == "passport"


@pytest.mark.asyncio
async def test_succession_o_opens_both_sides_older_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A succession asks a comparison question, so one file cannot answer it."""
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    for doc_id, rel in (("old-coc", "old.pdf"), ("new-coc", "new.pdf")):
        store.save(
            Document(
                id=doc_id,
                name=doc_id,
                has_digital=True,
                files=[Rendition(label="d", path=rel, primary=True)],
            )
        )
        (tmp_path / rel).write_bytes(b"x")
    opened: list[Path] = []
    monkeypatch.setattr("dossier.tui.review.open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        pane = await _open_review(pilot)
        proposal = succession.Succession(
            newer="new-coc",
            older="old-coc",
            document_type="CoC",
            confidence=0.9,
            rationale="test",
        )
        monkeypatch.setattr(pane, "_highlighted_succession", lambda: proposal)
        pane.query_one(TabbedContent).active = "tab-succession"
        await pilot.pause()
        pane.action_open_file()
        await pilot.pause()
        # Older first, so the renewal you are judging ends up frontmost.
        assert opened == [tmp_path / "old.pdf", tmp_path / "new.pdf"]

        # A paper-only predecessor opens the side that exists rather than neither.
        opened.clear()
        store.save(replace(store.load("old-coc"), files=[], has_digital=False))
        pane._docs = None  # drop the snapshot so the change is seen
        pane.action_open_file()
        await pilot.pause()
        assert opened == [tmp_path / "new.pdf"]


@pytest.mark.asyncio
async def test_copy_path_and_reveal_act_on_the_home_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config = _setup(tmp_path)
    copied: list[Path] = []
    revealed: list[Path] = []
    monkeypatch.setattr(tui_home, "copy_path", lambda p: copied.append(p))
    monkeypatch.setattr(tui_home, "reveal_file", lambda p: revealed.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("passport")
        await pilot.pause()
        home.action_copy_path()
        await pilot.pause()
        # OSC 52 goes out too, so the path also reaches the *terminal's* clipboard —
        # the only route to a local clipboard over SSH.
        assert app.clipboard == str(tmp_path / "passport.pdf")
        home.action_reveal_file()
        await pilot.pause()
    assert copied == [tmp_path / "passport.pdf"]
    assert revealed == [tmp_path / "passport.pdf"]


@pytest.mark.asyncio
async def test_copy_path_still_reports_success_when_no_local_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A missing local clipboard tool is no longer fatal — OSC 52 carried it.

    On a headless or restricted box there may be no xclip/wl-copy, but the OSC 52
    escape still reaches whatever terminal is on the other end. So a shell-out that
    can't find a tool must not surface as an error.
    """
    from dossier.platform_open import OpenError as _OpenError

    store, config = _setup(tmp_path)

    def _no_tool(_p: Path) -> None:
        raise _OpenError("no clipboard tool found")

    monkeypatch.setattr(tui_home, "copy_path", _no_tool)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("passport")
        await pilot.pause()
        home.action_copy_path()
        await pilot.pause()
        assert app.clipboard == str(tmp_path / "passport.pdf")
        assert not any(n.severity == "error" for n in app._notifications)
        assert any("copied" in n.message for n in app._notifications)


@pytest.mark.asyncio
async def test_reveal_targets_the_open_record_not_reviews_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An open record wins over review's list — it is what's being looked at.

    Matters most on a phone, where the record *replaces* review: acting on the
    hidden list's cursor would act on something the user cannot see.
    """
    store, config = _setup(tmp_path)
    (tmp_path / "loose.pdf").write_bytes(b"x")  # an orphan, so review's cursor differs
    revealed: list[Path] = []
    monkeypatch.setattr(tui_home, "reveal_file", lambda p: revealed.append(p) or None)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(120, 32)) as pilot:
        home = app.home
        home.action_review()
        await _await_review_load(pilot)
        home.open_detail("passport")  # a record from a finding
        await pilot.pause()
        assert home.has_class("review-mode") and home.has_class("show-detail")

        home.action_reveal_file()
        await pilot.pause()
    assert revealed == [tmp_path / "passport.pdf"]


@pytest.mark.asyncio
async def test_copy_path_in_review_asks_which_side_of_a_succession(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Two files under one cursor: opening both is meaningful, copying both isn't.

    Succession rows straddle the older and newer documents, so `o` opens the pair —
    but a path can only go on the clipboard one at a time, hence the ask.
    """
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    for doc_id, rel in (("old-coc", "old.pdf"), ("new-coc", "new.pdf")):
        store.save(
            Document(
                id=doc_id,
                name=doc_id,
                has_digital=True,
                files=[Rendition(label="d", path=rel, primary=True)],
            )
        )
        (tmp_path / rel).write_bytes(b"x")
    copied: list[Path] = []
    monkeypatch.setattr(tui_home, "copy_path", lambda p: copied.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(120, 32)) as pilot:
        home = app.home
        home.action_review()
        pane = await _await_review_load(pilot)
        proposal = succession.Succession(
            newer="new-coc",
            older="old-coc",
            document_type="CoC",
            confidence=0.9,
            rationale="test",
        )
        monkeypatch.setattr(pane, "_highlighted_succession", lambda: proposal)
        pane.query_one(TabbedContent).active = "tab-succession"
        await pilot.pause()

        home.action_copy_path()
        await pilot.pause()
        assert isinstance(app.screen, ChoiceScreen), "two files should prompt"
        assert copied == [], "nothing copied before the user chose"

        choices = app.screen.query_one("#cchoices", OptionList)
        labels = [str(choices.get_option_at_index(i).prompt) for i in range(2)]
        assert labels[0].startswith("older") and labels[1].startswith("newer")

        choices.highlighted = 1  # the newer side
        await pilot.press("enter")
        await pilot.pause()
        assert copied == [tmp_path / "new.pdf"]


@pytest.mark.asyncio
async def test_dups_x_dismisses_a_false_positive_cluster(tmp_path: Path):
    """`x` on Duplicates — the verdict the tab had no way to record.

    dedup matches on visual similarity, so it will sometimes cluster two genuinely
    different documents (same template, adjacent pages). Every other tab could say
    *no*; this one could only fold, which asserts the opposite.
    """
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        pane = await _open_review(pilot)
        pane.query_one(TabbedContent).active = "tab-dups"
        await pilot.pause()
        pane._pages = {"form-p1.pdf": [1, 2], "form-p2.pdf": [1]}
        pane._populate_dups(
            [
                dedup.DupGroup(
                    files=["form-p2.pdf", "form-p1.pdf"],
                    keep="form-p1.pdf",
                    subsets=["form-p2.pdf"],
                    ambiguous=False,
                )
            ]
        )
        await pilot.pause()
        pane.query_one("#dups", OptionList).highlighted = 0
        pane.query_one("#dups", OptionList).focus()
        await pilot.press("x")  # the real key: it was gated off this tab before
        await pilot.pause()

        state = store.load_reconcile()
        assert state.dup_dismissed == {"form-p1.pdf": {"form-p2.pdf"}}
        assert state.folded == {}, "dismissing must not claim they are duplicates"
        assert pane._dups_count == 0, "the cluster should stop resurfacing"
        # …and the copy stays adoptable, which folding would have prevented.
        assert "form-p2.pdf" not in state.suppressed_orphans()


@pytest.mark.asyncio
async def test_reconcile_ignore_glob_adds_and_hides(tmp_path: Path):
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    store = Store(config)
    store.ensure_layout()
    (tmp_path / "Wallpapers").mkdir()
    (tmp_path / "Wallpapers" / "bg.jpg").write_bytes(b"x")
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = await _open_review(pilot)
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


async def _enter_edit(pilot, home, doc_id: str) -> DetailPane:
    home.open_detail(doc_id)
    await pilot.pause()
    home.action_edit()
    await pilot.pause()
    return home.query_one("#detail", DetailPane)


@pytest.mark.asyncio
async def test_detail_pane_edit_saves_scalars_and_flags(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        pane = await _enter_edit(pilot, home, "coc")
        assert pane.editing and home.editing
        pane.query_one("#f-name", Input).value = "CoC Card Renewed"
        pane.query_one("#f-physical", Checkbox).value = True  # new capability
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not pane.editing and not home.editing
        saved = store.load("coc")
        assert saved.name == "CoC Card Renewed"
        assert saved.has_physical is True


@pytest.mark.asyncio
async def test_edit_close_edit_remounts_without_duplicate_ids(tmp_path: Path):
    # Regression: re-entering edit re-mounts the rendition/suggestion rows; the
    # async remove_children must finish before the re-mount or the reused row ids
    # collide (DuplicateIds). passport has a rendition, so a row is remounted.
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        pane = await _enter_edit(pilot, home, "passport")
        assert len(list(pane.query(".df-rend-row"))) == 1
        # Poll rather than pausing twice: `remove_children` is async, and "two
        # pumps" is a guess at how many turns it needs — one that holds on an idle
        # machine and not on a loaded one. This test was seen to fail exactly once,
        # early in the session, and pass on retry.
        pane.action_cancel_edit()
        await _settle(pilot, lambda: not pane.editing, what="the edit to close")
        home.action_edit()  # second edit — must not raise
        await _settle(pilot, lambda: pane.editing, what="the edit to reopen")
        assert len(list(pane.query(".df-rend-row"))) == 1  # exactly one, not two


@pytest.mark.asyncio
async def test_ctrl_z_restores_the_previous_save_and_toggles_back(tmp_path: Path):
    """`ctrl+z` is a toggle, not a stack — and that is the honest reading.

    A restore is itself a save, so the version it displaces is archived in turn;
    pressing again therefore brings back what was just undone. Arbitrary depth is
    the History picker's job, not hidden per-session state.
    """
    store, config = _setup(tmp_path)
    doc = store.load("passport")
    doc.notes = "second"
    store.save(doc)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("passport")
        await pilot.pause()
        pane = home.query_one("#detail", DetailPane)
        pane.focus()
        await pilot.pause()

        await pilot.press("ctrl+z")
        await _settle(pilot, lambda: store.load("passport").notes != "second")
        assert store.load("passport").notes == ""  # back to the original

        await pilot.press("ctrl+z")  # undoing the undo
        await _settle(pilot, lambda: store.load("passport").notes == "second")


@pytest.mark.asyncio
async def test_ctrl_z_reaches_undo_while_browsing(tmp_path: Path):
    """Undo must work from the documents list, which is where focus actually sits.

    The binding lived only on DetailPane, but in the wide layout the pane is *shown*
    without being *focused* — so ctrl+z reached nothing. Caught by driving the real
    key in a terminal; the unit test had called pane.focus() first and so set up a
    condition that does not hold in practice.
    """
    store, config = _setup(tmp_path)
    doc = store.load("passport")
    doc.notes = "second"
    store.save(doc)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(110, 34)) as pilot:  # wide: pane shown, not focused
        home = app.home
        home.open_detail("passport")
        await pilot.pause()
        home.query_one("#documents", OptionList).focus()
        await pilot.pause()
        assert app.focused is home.query_one("#documents", OptionList)

        await pilot.press("ctrl+z")
        await _settle(pilot, lambda: store.load("passport").notes == "")


@pytest.mark.asyncio
async def test_ctrl_z_says_so_when_there_is_nothing_to_undo(tmp_path: Path):
    store, config = _setup(tmp_path)  # passport saved once — nothing displaced yet
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("passport")
        await pilot.pause()
        pane = home.query_one("#detail", DetailPane)
        pane.action_undo()
        await pilot.pause()
        assert any("no earlier version" in n.message for n in app._notifications)


@pytest.mark.asyncio
async def test_history_picker_restores_the_chosen_version(tmp_path: Path):
    store, config = _setup(tmp_path)
    for note in ("second", "third"):
        doc = store.load("passport")
        doc.notes = note
        store.save(doc)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("passport")
        await pilot.pause()
        home.action_history()
        await pilot.pause()
        assert isinstance(app.screen, ChoiceScreen)

        choices = app.screen.query_one("#cchoices", OptionList)
        assert choices.option_count == 2  # two versions were displaced
        choices.highlighted = 1  # the older of the two: the original, empty notes
        await pilot.press("enter")
        await _settle(pilot, lambda: store.load("passport").notes == "")


@pytest.mark.asyncio
async def test_focused_detail_pane_takes_single_key_verbs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """When the detail pane holds focus, letters are verbs again.

    That is the phone flow: `→` drills in and focuses the pane, and the find-fast
    router only claims letters while a *column* is focused — so on the pane they
    are free to mean edit / open / undo, recovering the one-key ergonomics the home
    gave up without touching its type-to-search contract.
    """
    store, config = _setup(tmp_path)
    opened: list[Path] = []
    monkeypatch.setattr("dossier.tui.screens.open_file", lambda p: opened.append(p))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(50, 24)) as pilot:  # narrow: drilling focuses pane
        home = app.home
        home.query_one("#documents", DocumentList).focus()
        home.query_one("#documents", DocumentList).highlighted = 0  # passport
        await pilot.pause()
        await pilot.press("right")  # drill into detail
        await pilot.pause()
        pane = home.query_one("#detail", DetailPane)
        assert app.focused is pane, "drilling in should focus the pane on narrow"

        await pilot.press("o")  # open the file
        await pilot.pause()
        assert opened == [tmp_path / "passport.pdf"]

        await pilot.press("e")  # edit
        await pilot.pause()
        assert pane.editing, "`e` did not start an edit"


@pytest.mark.asyncio
async def test_document_list_letters_stay_search_not_verbs(tmp_path: Path):
    """The mirror: while a column is focused, `e` is search, never edit.

    The single-key verbs live only on the focused pane; letting them fire from the
    list too would break find-fast — the whole point of which is that the first
    keystroke is always the start of a find.
    """
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(120, 34)) as pilot:  # wide: list keeps focus
        home = app.home
        home.query_one("#documents", DocumentList).focus()
        await pilot.pause()
        assert "e" not in home.active_bindings, "the list must not expose `e` as a verb"
        await pilot.press("e")
        await pilot.pause()
        assert app.query_one("#search", Input).value == "e", "`e` should be search text"
        assert not home.query_one("#detail", DetailPane).editing


@pytest.mark.asyncio
async def test_edit_mode_suppresses_home_bindings(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        pane = await _enter_edit(pilot, home, "coc")
        # Focus a Checkbox (unlike an Input, it won't swallow a bare letter/arrow).
        pane.query_one("#f-physical", Checkbox).focus()
        await pilot.pause()
        await pilot.press("b")  # would push BundleScreen if not gated
        await pilot.press("left")  # would drill_out / close the detail if not gated
        await pilot.pause()
        assert len(app.screen_stack) == 1  # no modal pushed
        assert pane.editing and home.has_class("show-detail")


@pytest.mark.asyncio
async def test_edit_discard_requires_double_escape_when_dirty(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        pane = await _enter_edit(pilot, home, "coc")
        pane.query_one("#f-name", Input).value = "Changed"
        await pilot.pause()
        await pilot.press("escape")  # dirty → arm, stay editing
        await pilot.pause()
        assert pane.editing
        await pilot.press("escape")  # confirm → discard
        await pilot.pause()
        assert not pane.editing
        assert store.load("coc").name == "CoC Card"  # nothing written


@pytest.mark.asyncio
async def test_edit_stale_write_refused_then_reloaded(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        pane = await _enter_edit(pilot, home, "coc")
        pane.query_one("#f-name", Input).value = "Mine"
        await pilot.pause()
        other = store.load("coc")  # someone edits the file out-of-band
        other.name = "Theirs"
        store.save(other)
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert pane.editing  # save refused on the stale hash
        assert store.load("coc").name == "Theirs"  # our write didn't land
        await pilot.press("ctrl+r")
        await pilot.pause()
        assert pane.query_one("#f-name", Input).value == "Theirs"  # form reloaded
        assert pane.editing


@pytest.mark.asyncio
async def test_failed_save_leaves_in_memory_doc_untouched(tmp_path: Path):
    # A refused save must not mutate the Document shared with the home list —
    # otherwise a later discard would show the unsaved edit as if it were saved.
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        pane = await _enter_edit(pilot, home, "coc")
        pane.query_one("#f-name", Input).value = "Mine"
        await pilot.pause()
        other = store.load("coc")  # out-of-band change → next save is stale
        other.name = "Theirs"
        store.save(other)
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert pane.editing  # refused
        doc = home._doc_by_id("coc")
        assert doc is not None
        assert doc.name == "CoC Card"  # in-memory list still the original, not "Mine"


@pytest.mark.asyncio
async def test_inline_rendition_add_and_set_primary(tmp_path: Path):
    store, config = _setup(tmp_path)  # passport: 1 rendition (passport.pdf, primary)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        pane = await _enter_edit(pilot, home, "passport")
        pane.query_one("#rend-add", Button).press()  # append a blank file row
        await pilot.pause()
        rows = list(pane.query(".df-rend-row").results(Horizontal))
        assert len(rows) == 2
        rows[1].query_one(".df-rlabel", Input).value = "back"
        rows[1].query_one(".df-rpath", Input).value = "passport-back.pdf"
        rows[1].query_one(".df-rprimary", Checkbox).value = True  # radio clears row 0
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
    files = {(r.path, r.primary) for r in store.load("passport").files}
    assert files == {("passport.pdf", False), ("passport-back.pdf", True)}


@pytest.mark.asyncio
async def test_inline_rendition_remove(tmp_path: Path):
    store, config = _setup(tmp_path)  # passport: 1 rendition
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        pane = await _enter_edit(pilot, home, "passport")
        pane.query_one(".df-rremove", Button).press()  # drop the only file row
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert store.load("passport").files == []


@pytest.mark.asyncio
async def test_suggestion_accept_prefills_and_saves(tmp_path: Path):
    store, config = _setup(tmp_path)
    store.save(Document(id="dated", name="Some Doc 2023-08-15"))  # bare date → issue
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        pane = await _enter_edit(pilot, home, "dated")
        assert len(pane._suggestions) == 1  # one issue-date suggestion
        pane.query_one("#sg-accept-0-0", Button).press()  # accept the reading
        await pilot.pause()
        await pilot.pause()
        assert pane.query_one("#f-issue", Input).value == "2023-08-15"
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert store.load("dated").issue_date == date(2023, 8, 15)


@pytest.mark.asyncio
async def test_suggestion_dismiss_persists_without_writing_the_doc(tmp_path: Path):
    store, config = _setup(tmp_path)
    store.save(Document(id="dated", name="Some Doc 2023-08-15"))
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        pane = await _enter_edit(pilot, home, "dated")
        dismissed = pane._suggestions[0]
        pane.query_one("#sg-dismiss-0", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert not list(pane.query(".sg-row").results(Horizontal))  # row gone
    assert dismissed.key in store.load_suggestions().dismissed
    assert store.load("dated").issue_date is None  # dismiss never wrote the doc


@pytest.mark.asyncio
async def test_bundles_screen_filters_home_to_a_bundle(tmp_path: Path):
    store, config = _setup(tmp_path)  # passport, coc
    store.save_bundles(
        {"travel/india-2024": Bundle(slug="travel/india-2024", title="India 2024")}
    )
    coc = store.load("coc")  # give coc a bundle membership
    coc.bundles = ["travel/india-2024"]
    store.save(coc)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.action_bundles()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, BundlesScreen)
        # a group header (travel ▸) plus the bundle row
        options = screen.query_one("#bundle-list", OptionList)
        assert any(
            options.get_option_at_index(i).id == "travel/india-2024"
            for i in range(options.option_count)
        )
        screen.dismiss("travel/india-2024")  # Enter on the bundle
        await pilot.pause()
        assert home._bundle_filter == "travel/india-2024"
        assert [d.id for d in home.documents_in_view()] == ["coc"]  # scoped
        home.action_escape()
        await pilot.pause()
        assert home._bundle_filter is None  # Esc clears the bundle scope


@pytest.mark.asyncio
async def test_bundles_screen_accepts_folder_suggestion(tmp_path: Path):
    store, config = _setup(tmp_path)
    # two docs sharing a hint folder → one folder-bundle suggestion
    for i in (1, 2):
        store.save(
            Document(
                id=f"trip{i}",
                name=f"Trip Doc {i}",
                files=[Rendition("d", f"Travel Documents/India 2024/{i}.pdf", True)],
            )
        )
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.action_bundles()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, BundlesScreen)
        assert len(screen._suggested) == 1
        assert screen._suggested[0].slug == "travel/india-2024"
        # land the cursor on the suggestion row and accept it
        options = screen.query_one("#bundle-list", OptionList)
        for i in range(options.option_count):
            if (options.get_option_at_index(i).id or "").startswith(screen._SUGGESTED):
                options.highlighted = i
                break
        screen.action_accept()
        await pilot.pause()
    assert "travel/india-2024" in store.load_bundles()  # bundle created
    assert "travel/india-2024" in store.load("trip1").bundles  # members assigned
    assert "travel/india-2024" in store.load("trip2").bundles


@pytest.mark.asyncio
async def test_bundles_enter_on_suggestion_does_not_accept(tmp_path: Path):
    # Canon (Phase 3): Enter opens, `a` accepts. Enter on a *suggestion* must be a
    # no-op — it isn't a bundle until accepted — not a silent create-on-Enter.
    store, config = _setup(tmp_path)
    for i in (1, 2):
        store.save(
            Document(
                id=f"trip{i}",
                name=f"Trip Doc {i}",
                files=[Rendition("d", f"Travel Documents/India 2024/{i}.pdf", True)],
            )
        )
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.home.action_bundles()
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, BundlesScreen)
        options = screen.query_one("#bundle-list", OptionList)
        options.focus()
        options.highlighted = next(
            i
            for i in range(options.option_count)
            if (options.get_option_at_index(i).id or "").startswith(screen._SUGGESTED)
        )
        await pilot.press("enter")
        await pilot.pause()
        assert "travel/india-2024" not in store.load_bundles()  # NOT created
        assert app.screen is screen  # still on bundles — Enter did nothing


def test_linux_driver_exposes_mouse_toggle():
    # Pin the private methods the keyboard trick relies on, so a Textual rename
    # fails here loudly instead of silently breaking the Termux keyboard. The
    # Linux driver is Unix-only (imports termios), so skip off Unix.
    pytest.importorskip("termios")
    from textual.drivers.linux_driver import LinuxDriver

    assert hasattr(LinuxDriver, "_enable_mouse_support")
    assert hasattr(LinuxDriver, "_disable_mouse_support")


@pytest.mark.asyncio
async def test_scan_doc_reads_and_persists_the_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from dossier import scan as scan_mod

    store, config = _setup(tmp_path)  # passport links a real passport.pdf
    reading = scan_mod.ScanReading.from_payload(
        {
            "document_type": "Passport",
            "expiry_date_text": "01 Jan 2030",
            "is_validity_period": True,
            "confidence": 0.9,
        },
        model="m",
    )
    monkeypatch.setattr(scan_mod, "extract", lambda path, cfg: reading)  # no VLM
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.open_detail("passport")
        await pilot.pause()
        home.action_scan_doc()  # starts the @work(thread=True) vision worker
        await _settle(pilot, lambda: "passport" in store.load_scans(), what="the scan")
    saved = store.load_scans()
    assert "passport" in saved  # the reading was persisted
    assert saved["passport"].expiry_date_text == "01 Jan 2030"
    assert saved["passport"].fingerprint  # fingerprinted so a re-scan skips it


@pytest.mark.asyncio
async def test_settings_screen_saves_and_persists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from dossier.tui.screens import SettingsScreen

    store, config = _setup(tmp_path)
    device = tmp_path / "device.toml"
    device.write_text(f'syncthing_root = "{tmp_path.as_posix()}"\nglyphs = "nerd"\n')
    monkeypatch.setattr("dossier.config.per_device_config_path", lambda: device)
    monkeypatch.setattr("dossier.tui.screens.scan.list_models", lambda cfg: [])
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        screen = SettingsScreen(config)
        app.push_screen(screen)
        await pilot.pause()
        screen.query_one("#set-threshold", Input).value = "45"
        screen.query_one("#set-url", Input).value = "http://box:9000/v1"
        screen.action_save()
        await pilot.pause()
    assert config.expiry_threshold_days == 45  # live config mutated
    assert config.scan_base_url == "http://box:9000/v1"
    import tomllib

    saved = tomllib.loads(device.read_text())
    assert saved["scan_base_url"] == "http://box:9000/v1"  # persisted per-device
    assert saved["syncthing_root"] == tmp_path.as_posix()  # preserved


@pytest.mark.asyncio
async def test_command_palette_routes_to_home_actions(tmp_path: Path):
    from dossier.tui.app import DossierCommands

    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test():
        provider = DossierCommands(app.screen)
        # Every advertised command names an existing home action, and its runner
        # is that bound method — guards against an action-name typo drifting from
        # the HomeScreen binding it delegates to.
        # A fuzzy query surfaces the matching commands as palette hits.
        hits = [hit async for hit in provider.search("scan")]
        titles = {hit.text for hit in hits}
        # …and `discover` offers *everything* before a character is typed. Without
        # it the palette opened showing only Textual's own Theme/Quit/Screenshot,
        # so you had to already know a command existed in order to find it.
        found = {hit.text async for hit in provider.discover()}
        assert found == {entry.title for entry in ENTRIES}
    assert "Scan current document (vision)" in titles
    assert "Settings" not in titles  # unrelated command doesn't match "scan"


@pytest.mark.asyncio
async def test_palette_respects_the_same_gate_as_the_keys(tmp_path: Path):
    """The palette used to call actions the keys were forbidden to run.

    `check_action` gates keys, but the provider returned the bound action and
    called it directly — so mid-edit "Edit document" wiped the in-progress form,
    and in review-mode the document verbs acted on the *hidden* documents cursor.
    """
    from dossier.tui.app import DossierCommands

    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        provider = DossierCommands(app.screen)
        pane = await _enter_edit(pilot, home, "passport")
        pane.query_one("#f-name", Input).value = "Half-typed name"
        await pilot.pause()

        provider._runner("edit")()  # would have re-populated the form under us
        await pilot.pause()
        assert pane.editing, "the edit was torn down"
        assert pane.query_one("#f-name", Input).value == "Half-typed name"
        assert any("not available" in n.message for n in app._notifications)


def _inbox_setup(tmp_path: Path, name: str) -> tuple[Store, Config]:
    store, config = _setup(tmp_path)  # passport.pdf is linked; excluded from intake
    config.intake_inbox = "Inbox"
    (tmp_path / "Inbox").mkdir()
    (tmp_path / "Inbox" / name).write_bytes(b"x")
    return store, config


@pytest.mark.asyncio
async def test_intake_screen_reads_and_files_a_dropped_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from dossier import scan as scan_mod
    from dossier.tui.intake import IntakeScreen

    store, config = _inbox_setup(tmp_path, "new.pdf")
    reading = scan_mod.ScanReading.from_payload(
        {"document_type": "Driving Licence", "confidence": 0.9}, model="m"
    )
    monkeypatch.setattr(scan_mod, "extract", lambda p, c: reading)  # no VLM
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.push_screen(IntakeScreen(store, config))
        await pilot.pause()
        await _settle(pilot)  # the background VLM read
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, IntakeScreen)
        assert screen._proposal is not None
        assert screen._proposal.doc.name == "Driving Licence"
        screen.action_accept()  # apply is synchronous
        await pilot.pause()

    filed = [d for d in store.load_all() if d.name == "Driving Licence"]
    assert filed  # a record was created
    assert (tmp_path / "Filed" / "driving-licence.pdf").exists()  # moved out of inbox
    assert not (tmp_path / "Inbox" / "new.pdf").exists()


@pytest.mark.asyncio
async def test_intake_screen_reject_dismisses_without_filing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from dossier import (
        intake,
        scan as scan_mod,
    )
    from dossier.tui.intake import IntakeScreen

    store, config = _inbox_setup(tmp_path, "junk.pdf")
    reading = scan_mod.ScanReading.from_payload({"document_type": "X"}, model="m")
    monkeypatch.setattr(scan_mod, "extract", lambda p, c: reading)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.push_screen(IntakeScreen(store, config))
        await pilot.pause()
        await _settle(pilot)
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, IntakeScreen)
        screen.action_reject()
        await pilot.pause()

    assert "Inbox/junk.pdf" in store.load_reconcile().dismissed  # marked not-a-doc
    assert (tmp_path / "Inbox" / "junk.pdf").exists()  # never moved or deleted
    assert intake.pending_files(store, config) == []  # no longer pending


@pytest.mark.asyncio
async def test_readiness_screen_shows_gathered_and_missing(tmp_path: Path):
    from dossier.model import Requirement, Template
    from dossier.tui.screens import ReadinessScreen

    store, config = _setup(tmp_path)
    passport = store.load("passport")  # name "Passport"
    passport.bundles = ["trip"]
    passport.expiry_date = date(2030, 1, 1)  # valid well past the event
    store.save(passport)
    bundle = Bundle(slug="trip", title="India Trip", date=date(2026, 12, 1))
    template = Template(
        slug="trip",
        title="Trip pack",
        requires=(
            Requirement("passport", ("passport",)),
            Requirement("photo", ("photo",)),  # no member matches → missing
        ),
    )
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.push_screen(
            ReadinessScreen(
                store, config, bundle=bundle, template=template, today=TODAY
            )
        )
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, ReadinessScreen)
        options = screen.query_one("#readiness", OptionList)
        prompts = " | ".join(
            str(options.get_option_at_index(i).prompt)
            for i in range(options.option_count)
        )
    assert "Passport" in prompts  # the gathered passport
    assert "missing" in prompts  # the missing photo requirement


@pytest.mark.asyncio
async def test_ctrl_t_toggles_transcript_content_search(tmp_path: Path):
    from dossier.scan import ScanReading

    store, config = _setup(tmp_path)  # passport, coc
    store.save_scans(
        {
            "passport": ScanReading.from_payload(
                {"document_type": "Passport", "transcript": "the INDoS number is 09MU"},
                model="m",
            )
        }
    )
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.query_one("#search", Input).value = "indos"
        await pilot.pause()
        # Default: the transcript body is NOT in the `/` haystack → no match.
        assert "passport" not in {d.id for d in home.visible_docs()}
        home.action_toggle_search_content()  # ctrl+t
        await pilot.pause()
        assert home._search_content is True
        # Now the transcript is searched → the passport is found.
        assert "passport" in {d.id for d in home.visible_docs()}


@pytest.mark.asyncio
async def test_sync_conflicts_show_in_the_footer_not_a_toast(tmp_path: Path):
    # The conflict count rides in the dim footer segment (never a toast over the
    # search box) so it can't block the find path.
    store, config = _setup(tmp_path)
    conflict = store.document_path("passport").with_name(
        "passport.sync-conflict-20260101-120000-AAAAAAA.md"
    )
    conflict.write_bytes(store.serialize(Document(id="passport", name="X")).encode())
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        await pilot.pause()
        from textual.widgets import Static

        assert app.home._conflict_count == 1
        chip = app.home.query_one("#attn-conflicts", Static)
        assert chip.display and "1 conflict" in str(chip.render())
        # …and no conflict toast was raised over the search box.
        assert not any("sync conflict" in n.message for n in app._notifications)


@pytest.mark.asyncio
async def test_attention_chips_route_to_their_surfaces(tmp_path: Path):
    """Each attention count is a shortcut to the surface it summarises.

    The app's top actionable signal used to be pure text; a click on it now jumps
    to where you act on it — conflicts → Review, inbox → Intake, expiring → Watch.
    """
    from textual.widgets import Static

    store, config = _setup(tmp_path)  # coc expires soon → an expiring count
    config.intake_inbox = "Inbox"
    (tmp_path / "Inbox").mkdir()
    (tmp_path / "Inbox" / "drop.pdf").write_bytes(b"x")
    conflict = store.document_path("passport").with_name(
        "passport.sync-conflict-20260101-120000-AAAAAAA.md"
    )
    conflict.write_bytes(store.serialize(Document(id="passport", name="X")).encode())
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(120, 34)) as pilot:
        home = app.home
        await pilot.pause()
        # All three chips are showing (each count is non-zero).
        for sel in ("#attn-expiring", "#attn-conflicts", "#attn-inbox"):
            assert home.query_one(sel, Static).display, sel
        # …and actually *on screen*, not stacked at x=0 under the full-width footer
        # (the bug that hid the attention counts for their whole existence). Docked
        # right, so the segment ends at the footrow's right edge.
        seg = home.query_one("#attention")
        row_w = home.query_one("#footrow").size.width
        assert seg.region.x > 0 and seg.region.right == row_w

        await pilot.click("#attn-conflicts")
        await _await_review_load(pilot)
        assert home.has_class("review-mode"), "conflicts chip did not open review"
        await pilot.press("escape")
        await pilot.pause()

        from dossier.tui.intake import IntakeScreen

        await pilot.click("#attn-inbox")
        await pilot.pause()
        assert isinstance(app.screen, IntakeScreen), "inbox chip did not open intake"


@pytest.mark.asyncio
async def test_attention_chips_look_tappable_only_when_taps_land(tmp_path: Path):
    """The interactive cue tracks mouse reporting, so it never lies on Termux.

    On the desktop taps always land, so the `taps-land` class is always on. On
    touch it toggles with focus (the search box drops reporting for the keyboard),
    and the chips must fall back to plain text when a tap would do nothing.
    """
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY, touch=True)
    async with app.run_test() as pilot:
        home = app.home
        await pilot.pause()
        home._set_mouse_reporting(False)  # search-focused on a phone
        await pilot.pause()
        assert not home.has_class("taps-land"), "chips would look tappable when dead"
        home._set_mouse_reporting(True)  # a list took focus
        await pilot.pause()
        assert home.has_class("taps-land")

    # Desktop: taps always land, so the cue is on from the start.
    store2, config2 = _setup(tmp_path / "d")
    app2 = DossierApp(store2, config2, today=TODAY)
    async with app2.run_test() as pilot:
        await pilot.pause()
        assert app2.home.has_class("taps-land")


@pytest.mark.asyncio
async def test_review_conflicts_tab_lists_and_merges(tmp_path: Path):
    store, config = _setup(tmp_path)
    live = store.document_path("passport")
    conflict = live.with_name("passport.sync-conflict-20260101-120000-AAAAAAA.md")
    theirs = Document(id="passport", name="Passport", tags=["identity", "travel"])
    conflict.write_bytes(store.serialize(theirs).encode())

    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.home.action_review()  # conflicts are a Review tab now
        pane = await _await_review_load(pilot)
        # A pending conflict makes Conflicts the default tab.
        assert pane.query_one(TabbedContent).active == "tab-conflicts"
        assert pane.query_one("#conflicts", OptionList).option_count == 1

        await pilot.press("A")  # merge all
        await pilot.pause()
        assert not conflict.exists()  # conflict cleared
        assert "travel" in store.load("passport").tags  # tags unioned in


@pytest.mark.asyncio
async def test_esc_closes_a_columns_detail_back_to_the_columns(tmp_path: Path):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home

        # A normal (columns) detail closes back to the columns on Esc.
        home.open_detail("passport")
        await pilot.pause()
        assert home.has_class("show-detail")
        home.action_escape()
        await pilot.pause()
        assert not home.has_class("show-detail")
        assert app.screen is home  # stayed on the home columns


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("size", "review_shares_with_detail"),
    [((120, 32), True), ((80, 24), False), ((50, 30), False)],
)
async def test_review_shares_with_detail_only_where_there_is_room(
    tmp_path: Path, size: tuple[int, int], review_shares_with_detail: bool
):
    # Wide shows review and the record side by side; narrow and medium swap to the
    # record (hiding is not teardown — review keeps its tab and cursor underneath).
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=size) as pilot:
        home = app.home
        home.action_review()
        pane = await _await_review_load(pilot)
        assert pane.display, "review is not visible in review-mode"

        home.open_detail("passport")
        await pilot.pause()
        assert home.query_one("#detail").display, "the record never appeared"
        assert pane.display is review_shares_with_detail
        # Either way the pane is only hidden, never unmounted.
        assert pane.is_mounted


@pytest.mark.asyncio
async def test_each_review_tab_advertises_only_its_own_verbs(tmp_path: Path):
    """Hidden, not greyed — the footer and `?` panel become per-tab documentation.

    Returning None from check_action left every tab listing every other tab's
    verbs, greyed; a key that is *shown* but dead reads as broken rather than
    inapplicable ("why doesn't x dismiss this duplicate?").
    """
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.home.action_review()
        pane = await _await_review_load(pilot)
        tabs = pane.query_one(TabbedContent)

        expected = {
            "tab-conflicts": {"accept", "accept_all"},
            "tab-orphans": {"open_file", "reject", "link", "accept", "ignore_glob"},
            "tab-missing": {"reject", "unlink"},
            "tab-dups": {"open_file", "scan_dups", "fold", "reject"},
            "tab-succession": {"open_file", "reject", "accept"},
            "tab-integrity": {"open_file", "edit"},
        }
        every = set().union(*expected.values())
        for tab, live in expected.items():
            tabs.active = tab
            await pilot.pause()
            enabled = {a for a in every if pane.check_action(a, ()) is not False}
            assert enabled == live, f"{tab} advertises the wrong verbs"


@pytest.mark.asyncio
async def test_review_reloads_only_when_something_outside_it_changed(tmp_path: Path):
    """The pane is long-lived, so it must catch outside writes — but only those.

    Reloading on every return would put back the cost the old modal paid every
    time; never reloading would show stale findings after an edit in column 3.
    """
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(120, 32)) as pilot:
        home = app.home
        home.action_review()
        pane = await _await_review_load(pilot)
        baseline = pane._loads

        # A reload review itself caused must not restale it.
        home._reload(restale_review=False)
        await pilot.pause()
        assert not pane._stale

        # A write from anywhere else does.
        home._reload()
        await pilot.pause()
        assert pane._stale
        assert pane._loads == baseline, "marking stale must not reload immediately"

        # …and it is paid for on the next return into review, once.
        home.open_detail("passport")
        await pilot.pause()
        await pilot.press("escape")
        await _await_review_load(pilot)
        assert not pane._stale
        assert pane._loads == baseline + 1


@pytest.mark.asyncio
async def test_review_mode_disables_the_home_arrow_drills(tmp_path: Path):
    """`→` must not reach the home's drill while review holds the columns.

    As a modal, review swallowed bare arrows at the screen boundary. As a widget
    they bubble, and `→` would open the detail for whatever the *hidden* documents
    cursor happens to sit on.
    """
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        assert home.check_action("drill_in", ()) is True  # normal browsing

        home.action_review()
        await _await_review_load(pilot)
        assert home.check_action("drill_in", ()) is False
        assert home.check_action("focus_search", ()) is False
        assert home.check_action("drill_out", ()) is False

        # With a record open, `←` gets its meaning back: close it, back to review.
        home.open_detail("passport")
        await pilot.pause()
        assert home.check_action("drill_out", ()) is True
        assert home.check_action("drill_in", ()) is False


@pytest.mark.asyncio
async def test_esc_from_a_review_detail_keeps_review_exactly_as_left(tmp_path: Path):
    """The point of the whole refactor: acting on a finding costs you nothing.

    Review used to be a modal, so opening a finding's record dismissed it — losing
    the tab and the cursor — and Esc rebuilt it from scratch on a fresh load. Now
    the record opens in column 3 beside the finding, and Esc peels only that.
    """
    store, config = _setup(tmp_path)
    (tmp_path / "loose.pdf").write_bytes(b"x")  # an orphan, so Orphans has rows
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(120, 32)) as pilot:  # wide: review + detail at once
        home = app.home
        home.action_review()
        pane = await _await_review_load(pilot)
        assert home.has_class("review-mode")

        # Park somewhere specific, so "exactly as left" has something to mean.
        tabs = pane.query_one(TabbedContent)
        tabs.active = "tab-missing"
        await pilot.pause()
        loads_before = pane._loads

        home._review_open_document(ReviewPane.OpenDocument("passport"))
        await pilot.pause()
        assert home.has_class("show-detail")
        assert home.has_class("review-mode"), "review was torn down to show a record"
        assert home._detail_id == "passport"

        # Real keys, not action calls: which Esc fires depends on where focus sits
        # in the binding chain, and that cascade is the risky part of this change.
        await pilot.press("escape")  # focus is in the detail → home peels it
        await pilot.pause()
        assert not home.has_class("show-detail")
        assert home.has_class("review-mode"), "Esc left review instead of the detail"
        assert tabs.active == "tab-missing", "review lost the tab it was on"
        assert pane._loads == loads_before, "Esc re-ran the load it used to re-run"

        await pilot.press("escape")  # focus is back in the pane → review closes
        await pilot.pause()
        assert not home.has_class("review-mode")
        assert app.screen is home


@pytest.mark.asyncio
async def test_intake_card_detects_and_folds_a_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from textual.widgets import Label

    from dossier import (
        intake,
        scan as scan_mod,
    )
    from dossier.tui.intake import IntakeScreen

    store, config = _inbox_setup(tmp_path, "dup.pdf")  # passport.pdf is already linked
    reading = scan_mod.ScanReading.from_payload(
        {"document_type": "Passport", "confidence": 0.9}, model="m"
    )
    monkeypatch.setattr(scan_mod, "extract", lambda p, c: reading)
    # The drop is page-identical to the existing passport's rendition.
    monkeypatch.setattr(
        intake.dedup_cache,
        "cached_page_hashes",
        lambda paths, root: {"Inbox/dup.pdf": [1, 2, 3], "passport.pdf": [1, 2, 3]},
    )
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.push_screen(IntakeScreen(store, config))
        await pilot.pause()
        await _settle(pilot)  # the background read + hash
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, IntakeScreen)
        assert screen._proposal is not None
        assert screen._proposal.duplicate is not None
        assert screen._proposal.duplicate.doc_id == "passport"
        body = str(screen.query_one("#ibody", Label).render())
        assert "duplicate of" in body and "[f folds]" in body  # the card leads with it
        screen.action_fold()  # fold instead of filing a new record
        await pilot.pause()

    # No second Passport record; the copy became a rendition; the inbox is emptied.
    passports = [d for d in store.load_all() if d.name == "Passport"]
    assert [d.id for d in passports] == ["passport"]
    assert any(r.label == "duplicate" for r in store.load("passport").files)
    assert not (tmp_path / "Inbox" / "dup.pdf").exists()


@pytest.mark.asyncio
async def test_intake_card_retargets_the_succession_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from dossier import (
        intake,
        scan as scan_mod,
    )
    from dossier.tui.intake import IntakeScreen
    from dossier.tui.screens import DocPickerScreen

    store, config = _inbox_setup(tmp_path, "new.pdf")
    reading = scan_mod.ScanReading.from_payload(
        {"document_type": "Medical", "confidence": 0.9}, model="m"
    )
    monkeypatch.setattr(scan_mod, "extract", lambda p, c: reading)
    monkeypatch.setattr(
        intake.dedup_cache, "cached_page_hashes", lambda paths, root: {}
    )
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        app.push_screen(IntakeScreen(store, config))
        await pilot.pause()
        await _settle(pilot)
        await pilot.pause()
        screen = app.screen
        assert isinstance(screen, IntakeScreen)
        assert screen._proposal is not None
        assert screen._proposal.doc.supersedes is None  # nothing auto-proposed

        screen.action_retarget()  # open the picker
        await pilot.pause()
        assert isinstance(app.screen, DocPickerScreen)
        app.screen.dismiss("coc")  # pick an existing document to renew
        await pilot.pause()
        assert screen._proposal.doc.supersedes == "coc"  # link set by hand


@pytest.mark.asyncio
async def test_contextual_commands_hide_when_they_cannot_act(tmp_path: Path):
    """A command that would no-op should not be offered.

    Both of these used to sit in the list permanently: "Cancel vision scan"
    cheerfully notified "cancelling…" with nothing running, and "Accept top
    suggestion" returned silently with no detail pane open. check_action gates the
    palette now, so gating them here removes them from the list itself.
    """
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        await pilot.pause()
        assert home.check_action("cancel_scan", ()) is False, "nothing is scanning"
        assert home.check_action("accept_suggestion", ()) is False, "no detail open"
        # A command with no precondition is unaffected.
        assert home.check_action("review", ()) is True


@pytest.mark.asyncio
async def test_help_panel_indexes_the_commands_without_burying_the_keys(tmp_path: Path):
    """`?` gains the *shape* of the command surface, not its contents.

    Textual's panel is a 30–60 column split and the keybindings already fill most
    of it — listing all 19 commands there would push out the keys the panel exists
    to show. Counts per group answer "how much else is there"; ctrl+p answers
    "which one".
    """
    from textual.widgets import HelpPanel, KeyPanel

    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test(size=(110, 34)) as pilot:
        await pilot.press("question_mark")
        await _settle(pilot, lambda: bool(app.screen.query("#help-commands")))
        panel = app.screen.query_one(HelpPanel)
        index = panel.query_one("#help-commands")
        # It sits *above* the key list, which is `height: 1fr` and would otherwise
        # leave it no room at all — mounting it after was why it never appeared.
        children = list(panel.children)
        assert children.index(index) < children.index(panel.query_one(KeyPanel))

    # The content itself is a pure function, so assert on that rather than on
    # widget internals (Static exposes no stable `.renderable` here).
    rendered = _command_index_text().plain
    assert f"{len(ENTRIES)} commands" in rendered
    assert "Current document" in rendered
    # Counts, never the titles — that is what keeps it from crowding out the keys.
    assert "Settings" not in rendered


def test_palette_covers_the_occasional_actions():
    from dossier.tui.home import HomeScreen

    actions = {entry.action for entry in ENTRIES}
    # The actions that lost their dedicated letter now live in the palette.
    assert {
        "review",
        "bundles",
        "watch",
        "toggle_expiring",
        "intake",
        "supersede",
        "settings",
        "toggle_search_content",
        "quit",  # Quit is a catalog command now, delegating to app.exit()
    } <= actions
    # Every palette command maps to a real HomeScreen action.
    for action in actions:
        assert callable(getattr(HomeScreen, f"action_{action}", None))
    # "Move" and "Add to bundle" were the same _open_and_edit call as "Edit" with a
    # different starting field, so they are one entry now — but still findable by
    # the words people think in, which is what keeps the merge from costing anything.
    assert "move" not in actions
    haystacks = {entry.haystack.lower() for entry in ENTRIES}
    for word in ("move", "bundle", "rename"):
        assert any(word in hay for hay in haystacks), word


@pytest.mark.asyncio
async def test_quit_command_delegates_to_app_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Quit is a catalog command backed by a thin ``action_quit`` → ``app.exit()``.

    It stands in for the system Quit that retiring Textual's modal palette removes;
    ``ctrl+q`` still quits too. Asserting the delegate directly keeps this valid once
    the palette (and its provider) is gone.
    """
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        called: list[bool] = []
        monkeypatch.setattr(app, "exit", lambda *a, **k: called.append(True))
        app.home.action_quit()
        await pilot.pause()
        assert called, "Quit did not reach app.exit()"


def _highlighted_command(home) -> str | None:
    """The action id highlighted in the `:` command list (None on empty/header)."""
    commands = home.query_one("#commands", OptionList)
    idx = commands.highlighted
    return commands.get_option_at_index(idx).id if idx is not None else None


@pytest.mark.asyncio
async def test_colon_enters_command_mode_filters_and_runs(tmp_path: Path):
    """`:` turns the persistent bar into a command bar — the palette's replacement.

    A separate list takes the columns, typing fuzzy-filters the catalog and Enter
    runs the top hit, all without ever engaging the document search.
    """
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.query_one("#documents", OptionList).focus()
        await pilot.pause()
        await pilot.press("colon")  # find-fast router appends ":" and focuses search
        await _settle(pilot, lambda: home.has_class("command-mode"))

        search = home.query_one("#search", Input)
        assert app.focused is search and search.value == ":"
        assert home.query_one("#commands", OptionList).display
        assert not home.query_one("#documents", OptionList).display
        # The command branch never touches the document filter.
        assert home._filter_text == "" and not home.has_class("searching")
        # Empty query → grouped catalog; the cursor lands on a real entry, not a header.
        assert _highlighted_command(home) is not None

        for ch in "expiring":
            await pilot.press(ch)
        await _settle(pilot, lambda: _highlighted_command(home) == "toggle_expiring")
        await pilot.press("enter")
        await _settle(pilot, lambda: home._expiring_only)
        assert not home.has_class("command-mode") and search.value == ""


@pytest.mark.asyncio
async def test_command_mode_escape_preserves_the_expiring_filter(tmp_path: Path):
    """A `:` peek must not cost an active filter — Esc from command mode restores it.

    (Esc from a *search* deliberately clears filters so it can't get stuck; command
    mode gets its own exit that leaves them alone, like vim's Esc-from-`:`.)
    """
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.action_toggle_expiring()
        await _settle(pilot, lambda: home.has_class("searching"))
        assert home._expiring_only

        home.query_one("#documents", OptionList).focus()
        await pilot.pause()
        await pilot.press("colon")
        await _settle(pilot, lambda: home.has_class("command-mode"))
        assert not home.has_class("searching")  # dropped while the bar is a command bar

        await pilot.press("escape")
        await _settle(pilot, lambda: not home.has_class("command-mode"))
        assert home._expiring_only  # the filter survived the peek…
        assert home.has_class("searching")  # …and its view is restored


@pytest.mark.asyncio
async def test_command_mode_enter_with_no_match_opens_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`:` + a query matching nothing + Enter must not fall through to find-fast's
    Enter (which opens a document) — it reports no match and stays put."""
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        opened: list[str] = []
        monkeypatch.setattr(home, "open_document", lambda doc_id: opened.append(doc_id))
        home.query_one("#documents", OptionList).focus()
        await pilot.pause()
        await pilot.press("colon")
        await _settle(pilot, lambda: home.has_class("command-mode"))
        for ch in "zzzzz":
            await pilot.press(ch)
        await _settle(pilot, lambda: _highlighted_command(home) is None)

        await pilot.press("enter")
        await pilot.pause()
        assert not opened, "Enter fell through to open a document"
        assert not home._show_detail
        assert any("no matching command" in n.message for n in app._notifications)


@pytest.mark.asyncio
async def test_down_from_command_bar_lands_on_a_real_command(tmp_path: Path):
    """↓ from the `:` bar steps into the command list (not the documents pane), and
    the cursor never rests on a header — headers are disabled, not merely id-less."""
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY)
    async with app.run_test() as pilot:
        home = app.home
        home.query_one("#documents", OptionList).focus()
        await pilot.pause()
        await pilot.press("colon")
        await _settle(pilot, lambda: home.has_class("command-mode"))

        commands = home.query_one("#commands", OptionList)
        await pilot.press("down")
        await _settle(pilot, lambda: app.focused is commands)
        assert _highlighted_command(home) is not None  # a runnable entry, not a header
        for _ in range(6):
            await pilot.press("down")
            await pilot.pause()
            idx = commands.highlighted
            assert idx is not None
            assert not commands.get_option_at_index(idx).disabled


@pytest.mark.asyncio
async def test_commands_button_opens_the_palette(tmp_path: Path):
    from textual.command import CommandPalette

    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY, touch=True)  # touch shows the buttons
    async with app.run_test() as pilot:
        assert not isinstance(app.screen, CommandPalette)
        await pilot.click("#act-commands")
        await pilot.pause()
        assert isinstance(app.screen, CommandPalette)


@pytest.mark.asyncio
async def test_opening_palette_on_touch_raises_the_soft_keyboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    store, config = _setup(tmp_path)
    app = DossierApp(store, config, today=TODAY, touch=True)
    reports: list[bool] = []
    async with app.run_test() as pilot:
        monkeypatch.setattr(app, "set_mouse_reporting", reports.append)
        reports.clear()
        app.action_command_palette()  # its search box gets focus on mount
        await pilot.pause()
        await pilot.pause()
        # Focusing the palette input dropped mouse reporting so the IME can appear.
        assert reports and reports[-1] is False
