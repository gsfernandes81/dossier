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

"""Drive the **Rust** R0.2 spike binary in a real terminal and print each screen.

The same `PtyTerm` harness that drives the Python TUI works unchanged against a
native binary — which is the point: the terminal is the interface, not the
language. This is how the spike gets looked at on a machine with no phone
attached, and it is the prototype of the PTY smoke test REWRITE.md §10 wants for
the real `ds`.

Run (after `cargo build --release` in `spike/`)::

    uv run --group driver python tools/drive_spike.py            # 100x30 desktop
    uv run --group driver python tools/drive_spike.py --touch    # 45x28 phone

Pass ``--bin PATH`` to point at another build (e.g. a cross-compiled one running
under an emulator).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from ptyterm import PtyTerm  # noqa: E402

DEFAULT_BIN = HERE.parent / "spike" / "target" / "release" / "ds-spike"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:]
    touch = "--touch" in argv
    binary = Path(argv[argv.index("--bin") + 1]) if "--bin" in argv else DEFAULT_BIN
    if not binary.exists():
        print(f"no spike binary at {binary} — run `cargo build --release` in spike/")
        return 1

    cols, rows = (45, 28) if touch else (100, 30)
    term = PtyTerm([str(binary)], cols=cols, rows=rows)
    try:
        term.wait_for("dossier")
        term.show(f"first paint ({cols}x{rows})")

        term.send("pass")
        term.wait_for("/1000")
        term.show("typed 'pass' — find-fast, no letter bindings")

        term.send("right")
        term.wait_for("location")
        term.show("detail (split when wide, full-screen when narrow)")

        term.send("esc")
        term.wait_for("/1000")
        term.send("f3")
        term.wait_for("glyph")
        term.show("F3 — glyph + width check (bars must line up)")

        term.send("f4")
        term.wait_for("diagnostics")
        term.show("F4 — diagnostics + budgets")

        # Esc peels: panel, then query, then arm, then quit.
        term.send("esc", "esc", "esc", "esc")
        term.close()
    finally:
        term.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
