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

"""Opt-in real-terminal integration test — drives the app through a live PTY.

Not part of the default suite (lives outside ``testpaths``, needs the ``driver``
dependency group). Run explicitly:

    uv run --group driver python -m pytest tools/test_terminal_integration.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("pyte")
if sys.platform == "win32":
    pytest.importorskip("winpty")

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from ptyterm import PtyTerm  # noqa: E402

LAUNCH = str(HERE / "run_tui_temp.py")


def test_drives_real_app_in_a_real_terminal(tmp_path: Path):
    term = PtyTerm([sys.executable, LAUNCH, str(tmp_path)], cols=100, rows=30)
    try:
        assert term.wait_for("dossier"), "home never rendered"
        assert term.wait_for("Passport"), "documents pane never populated"

        # Find-fast launch: typing a printable with NO `/` first routes into the
        # search box and filters live; Esc clears it again.
        assert term.wait_for("5 / 5"), "document count never settled"
        # One character only: the router's job is to route the *first* keystroke, and
        # a multi-char burst races the focus switch on a slow runner (multi-char
        # typing is covered by test_typing_from_a_column_routes_into_search).
        # "p" matches Passport + CoC "Competency" in the sample store.
        term.send("p", settle=0.6)
        assert term.wait_for("2 / 5", timeout=6), "typing did not filter (router)"
        term.send("esc", settle=0.4)
        assert term.wait_for("5 / 5"), "esc did not clear the typed filter"

        # Drill: ↓ highlights a doc, → opens its detail pane (progressive Open).
        term.send("down")
        term.wait_for("dossier")
        term.send("right")
        assert term.wait_for("Location:"), "detail pane did not open"

        # The expiring filter narrows the list (2 of the 5 sample docs expire soon).
        # It's a command now (no keybind), driven through the persistent bar's `:`
        # command mode — which also exercises that mode in a real terminal.
        term.send("\x10")  # Ctrl+P now opens the `:` command bar (the modal is retired)
        # Wait for the command list to render before typing: until it is up, the
        # home's type-to-search router owns the keystrokes and would swallow
        # "expiring" into the document filter — a fixed sleep is a bet on load. The
        # "Current document" group heading tops the (empty-query) command list, so
        # it's an unambiguous "the list is up" marker that is always on screen (later
        # groups scroll off), and it never appears on the home screen.
        assert term.wait_for("Current document"), "command mode never opened"
        term.send("expiring")
        assert term.wait_for("Toggle expiring", timeout=6), "command missing"
        term.send("enter", settle=0.5)
        assert term.wait_for("2 / 5"), "expiring filter did not apply"

        # Esc clears the filter; the bordered search box brightens on focus —
        # assert it via real cell colours (something an SVG export can't give).
        term.send("esc")
        assert term.wait_for("5 / 5"), "esc did not clear the expiring filter"
        row = next(
            i
            for i, line in enumerate(term.text().splitlines())
            if "Search name" in line
        )
        unfocused = term.cell(row - 1, 1)[1]  # fg of the ╭── border, unfocused
        term.send("/")
        # Poll the colour itself. Waiting on "Search name" proved nothing — the
        # placeholder is on screen focused or not, so that wait returned instantly
        # and the cell could be sampled before the focus repaint.
        assert term.wait_until(lambda: term.cell(row - 1, 1)[1] != unfocused), (
            "search border colour did not change on focus"
        )

        term.send("esc", settle=0.4)  # leave the search box
        term.send("\x11", settle=0.5)  # ctrl+q quits (bare `q` is a search char now)
        assert term.wait_for_exit(), "app did not quit on ctrl+q"
    finally:
        term.close()


def test_review_takes_the_columns_and_keeps_its_place(tmp_path: Path):
    """Review is a widget on the home now, so its keys share one binding chain.

    That is precisely what a headless test can't vouch for: which surface claims
    Tab, whether a letter reaches review or leaks into the type-to-search router,
    and whether the footer follows focus. Drive it for real.
    """
    term = PtyTerm([sys.executable, LAUNCH, str(tmp_path)], cols=100, rows=30)
    try:
        assert term.wait_for("dossier"), "home never rendered"
        assert term.wait_for("Passport"), "documents pane never populated"

        term.send("\x10")  # ctrl+p → the `:` command bar (the modal palette is retired)
        assert term.wait_for("Current document"), "command mode never opened"
        term.send("review")
        assert term.wait_for("Review —", timeout=6), "command missing"
        term.send("enter")
        assert term.wait_for("Conflicts", timeout=10), "review never opened"
        assert term.wait_for("Duplicates"), "the tab bar is missing"
        # Wait for the threaded load to settle before pressing Tab. review re-targets
        # to its default tab *after* the load lands, and that re-target only fires
        # while the tab is still the freshly-composed Conflicts — so a Tab pressed
        # first (as this test used to) moves off Conflicts, the re-target is skipped,
        # and every later tab-count is off by one: green on a fast runner, red on a
        # slow one. The two-line summary appears only once the load is applied, so it
        # is the signal that the default tab has settled.
        assert term.wait_for("Tab/Shift+Tab switch tabs", timeout=10), (
            "review load never settled"
        )

        # Tab belongs to review while focus is inside it — not to focus-traversal.
        # Cycle to Duplicates by its *body*, never a fixed tab count: the default tab
        # depends on what the store has pending, so counting from it is fragile.
        # "press s to scan" renders only while Duplicates is the active tab
        # (TabbedContent shows just the active pane), making it an unambiguous "we're
        # on Duplicates" marker — the tab-bar titles are always on screen and prove
        # nothing about which tab is active. Reaching it proves Tab cycled (the
        # default tab is never Duplicates).
        def on_dups() -> bool:
            return "press  s  to scan" in term.text()

        for _ in range(8):  # more than the six tabs — one full cycle always suffices
            if on_dups():
                break
            term.send("tab")
            term.wait_until(on_dups, timeout=2)  # let the switch land, then re-check
        assert on_dups(), f"Tab never reached the Duplicates tab:\n{term.text()}"

        # The footer follows the active tab, offering this tab's verbs and no other
        # tab's. It repaints on refresh_bindings, not on the tab switch, so poll for
        # the effect. Assert on the whole screen, not a fixed line: the app renders at
        # whatever size Textual perceives (80×24 under pytest on some hosts, 100×30 on
        # CI), so the footer's row isn't fixed. "Find duplicates" (capitalised) is a
        # footer verb label only — the tab's body says lowercase "scan for duplicates"
        # — so a screen-wide match still pins it to the footer. Likewise the
        # capitalised "Link"/"Unlink" (the summary's "1 linked" is lowercase).
        assert term.wait_until(lambda: "Find duplicates" in term.text()), (
            f"Duplicates' footer verb never appeared:\n{term.text()}"
        )
        screen = term.text()
        assert "Unlink" not in screen, "footer shows Missing's verb on Duplicates"
        assert "Link" not in screen, "footer shows Orphans' verb on Duplicates"

        # Esc leaves review and gives the columns back.
        term.send("esc", settle=0.6)
        assert term.wait_for("Passport"), "Esc did not return to the columns"

        term.send("\x11", settle=0.5)
        assert term.wait_for_exit(), "app did not quit on ctrl+q"
    finally:
        term.close()


def test_ctrl_z_does_not_suspend_the_app(tmp_path: Path):
    """ctrl+z is bound to undo, and ctrl+z is also SIGTSTP.

    Textual keeps the terminal in raw mode with ISIG off, so the 0x1a byte reaches
    the app instead of suspending it — but that is a per-platform claim about the
    line discipline, so the ubuntu CI leg is where it earns its keep (SIGTSTP is a
    no-op on Windows). If a future change dropped raw mode, ctrl+z would background
    the process and this asserts it does not.
    """
    term = PtyTerm([sys.executable, LAUNCH, str(tmp_path)], cols=100, rows=30)
    try:
        assert term.wait_for("dossier"), "home never rendered"
        assert term.wait_for("Passport"), "documents pane never populated"

        # Open a document so undo has a target (it acts on the shown detail).
        term.send("down")
        term.send("right")
        assert term.wait_for("Location:"), "detail pane did not open"

        term.send("\x1a", settle=0.5)  # ctrl+z — must reach undo, never suspend
        assert term.alive(), "ctrl+z suspended the app instead of reaching undo"
        # It reached action_undo: the sample doc has been saved only once, so it has
        # no archived version — the notice is how we know the key arrived.
        assert term.wait_for("no earlier version"), "ctrl+z did not reach undo"

        term.send("\x11", settle=0.5)
        assert term.wait_for_exit(), "app did not quit on ctrl+q"
    finally:
        term.close()
