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

"""Platform detection and native file opening (Windows + Termux, with fallbacks)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from dossier.errors import DossierError

TERMUX_API_HINT = (
    "run `pkg install termux-api` and install the Termux:API app (from the same "
    "source as Termux; an F-Droid/Play-Store mismatch makes termux-open a no-op)"
)


class OpenError(DossierError):
    """A file could not be opened with the platform's opener."""


def is_termux() -> bool:
    """True when running under Termux on Android."""
    prefix = os.environ.get("PREFIX", "")
    return "com.termux" in prefix or bool(os.environ.get("TERMUX_VERSION"))


def open_file(path: Path) -> None:
    """Open ``path`` with the platform's default application.

    Raises :class:`OpenError` with actionable guidance rather than trusting exit
    codes blindly (a mismatched Termux:API install exits 0 while doing nothing).
    """
    target = str(path)
    if is_termux():
        _run_opener("termux-open", target, missing_hint=TERMUX_API_HINT)
        return
    if sys.platform.startswith("win"):
        os.startfile(target)  # Windows-only; see ty.toml override
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    _run_opener(opener, target, missing_hint=f"no '{opener}' on PATH")


def termux_preconditions() -> list[str]:
    """Return a list of Termux setup problems (empty when everything is ready)."""
    problems: list[str] = []
    if shutil.which("termux-open") is None:
        problems.append(f"termux-open not found — {TERMUX_API_HINT}")
    if not (Path.home() / "storage").exists():
        problems.append("~/storage not set up — run `termux-setup-storage`")
    return problems


def _run_opener(name: str, target: str, *, missing_hint: str) -> None:
    exe = shutil.which(name)
    if exe is None:
        raise OpenError(f"{name} not found — {missing_hint}")
    result = subprocess.run([exe, target], capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise OpenError(f"{name} failed to open {target}: {detail}")
