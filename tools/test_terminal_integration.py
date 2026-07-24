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
        # It's a command-palette entry now (Phase 1 dropped the `x` keybind), so
        # drive it there — which also exercises the palette in a real terminal.
        term.send("\x10", settle=0.4)  # Ctrl+P opens the command palette
        term.send("expiring", settle=0.4)
        assert term.wait_for("Toggle expiring", timeout=6), "palette command missing"
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
        term.wait_for("Search name")
        focused = term.cell(row - 1, 1)[1]
        assert focused != unfocused, "search border colour did not change on focus"

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

        term.send("\x10", settle=0.4)  # ctrl+p
        term.send("review", settle=0.4)
        term.send("enter", settle=1.0)
        assert term.wait_for("Conflicts", timeout=10), "review never opened"
        assert term.wait_for("Duplicates"), "the tab bar is missing"

        # Tab belongs to review while focus is inside it — not to focus-traversal.
        term.send("tab", settle=0.5)
        term.send("tab", settle=0.5)
        assert term.wait_for("press  s  to scan", timeout=6), "Tab did not cycle tabs"

        # The footer follows the active tab, offering this tab's verbs and no other
        # tab's. (It still truncates at the right edge, so assert on an early entry.)
        footer = term.text().splitlines()[-1]
        assert "Find duplicates" in footer, f"Duplicates lost its verb: {footer!r}"
        assert "Unlink" not in footer, f"footer shows another tab's verb: {footer!r}"
        assert "Link" not in footer, f"footer shows another tab's verb: {footer!r}"

        # Esc leaves review and gives the columns back.
        term.send("esc", settle=0.6)
        assert term.wait_for("Passport"), "Esc did not return to the columns"

        term.send("\x11", settle=0.5)
        assert term.wait_for_exit(), "app did not quit on ctrl+q"
    finally:
        term.close()
