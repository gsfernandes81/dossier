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
        assert not term.alive(), "app did not quit on ctrl+q"
    finally:
        term.close()
