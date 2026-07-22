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

"""Drive the real DossierApp in a real terminal and print each screen as text.

Run: ``uv run --group driver python tools/drive_tui.py [--touch]``. A manual
smoke/exploration tool — spawns the app on a throwaway store and walks it.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from ptyterm import PtyTerm  # noqa: E402

LAUNCH = str(HERE / "run_tui_temp.py")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    touch = "--touch" in sys.argv
    argv = [sys.executable, LAUNCH, tempfile.mkdtemp(prefix="ds-drive-")]
    if touch:
        argv.append("--touch")
    cols, rows = (50, 40) if touch else (100, 30)
    t = PtyTerm(argv, cols=cols, rows=rows)
    try:
        t.wait_for("dossier")
        t.show("home")
        t.send("down")
        t.wait_for("dossier")
        t.show("after Down")
        t.send("right")
        t.wait_for("Location:")
        t.show("after Right (open detail)")
        t.send("x")
        t.wait_for("2 / 5")
        t.show("after x (expiring filter → 2/5)")
        t.send("esc")
        t.send("q", settle=0.4)
        print("\nquit clean:", not t.alive())
    finally:
        t.close()


if __name__ == "__main__":
    main()
