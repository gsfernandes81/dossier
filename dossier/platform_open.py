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

    Raises :class:`OpenError` (with actionable guidance) on a missing opener, a
    non-zero exit, or a Windows open error — so the caller shows a clean message
    instead of an unhandled traceback.
    """
    target = str(path)
    if is_termux():
        _run_opener("termux-open", target, missing_hint=TERMUX_API_HINT)
        return
    if sys.platform.startswith("win"):
        try:
            os.startfile(target)  # Windows-only; see ty.toml override
        except OSError as exc:
            raise OpenError(f"could not open {target}: {exc}") from exc
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    _run_opener(opener, target, missing_hint=f"no '{opener}' on PATH")


def reveal_file(path: Path) -> None:
    """Show ``path`` in the platform's file manager.

    Raises :class:`OpenError` where the platform has no answer — notably Android,
    where scoped storage means apps address files by ``content://`` URI and there is
    no reliable "show this file" intent; the caller should offer :func:`copy_path`
    instead of pretending.
    """
    if is_termux():
        raise OpenError(
            "Android has no reliable reveal-in-file-manager — copy the path instead"
        )
    if sys.platform.startswith("win"):
        exe = shutil.which("explorer") or "explorer"
        # `/select,` with no space, and never check the exit code: explorer.exe
        # returns 1 even when it worked.
        subprocess.run([exe, f"/select,{path}"], capture_output=True)
        return
    if sys.platform == "darwin":
        _run_opener("open", str(path), missing_hint="no 'open' on PATH", args=["-R"])
        return
    # Freedesktop has no portable "select this file", so open its folder. (A
    # FileManager1 D-Bus call would highlight the file itself, on the desktops
    # that implement it — worth adding only if opening the folder proves too blunt.)
    _run_opener("xdg-open", str(path.parent), missing_hint="no 'xdg-open' on PATH")


def copy_path(path: Path) -> None:
    """Put ``path`` on the system clipboard.

    The one action that works everywhere, and the *primary* answer on Android where
    revealing cannot work.
    """
    text = str(path)
    if is_termux():
        _pipe_clipboard(["termux-clipboard-set"], text.encode("utf-8"), TERMUX_API_HINT)
        return
    if sys.platform.startswith("win"):
        # clip.exe reads the console codepage, which mangles non-ASCII; UTF-16-LE
        # *without* a BOM round-trips exactly (a BOM lands in the text as U+FEFF).
        _pipe_clipboard(["clip"], text.encode("utf-16-le"), "no 'clip' on PATH")
        return
    if sys.platform == "darwin":
        _pipe_clipboard(["pbcopy"], text.encode("utf-8"), "no 'pbcopy' on PATH")
        return
    for argv in (["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "-ib"]):
        if shutil.which(argv[0]) is not None:
            _pipe_clipboard(argv, text.encode("utf-8"), "")
            return
    raise OpenError("no clipboard tool found — install wl-clipboard, xclip or xsel")


def _pipe_clipboard(argv: list[str], data: bytes, missing_hint: str) -> None:
    exe = shutil.which(argv[0])
    if exe is None:
        raise OpenError(f"{argv[0]} not found — {missing_hint}")
    result = subprocess.run([exe, *argv[1:]], input=data, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="replace").strip() or "non-zero exit"
        raise OpenError(f"{argv[0]} failed: {detail}")


def termux_preconditions() -> list[str]:
    """Return a list of Termux setup problems (empty when everything is ready)."""
    problems: list[str] = []
    if shutil.which("termux-open") is None:
        problems.append(f"termux-open not found — {TERMUX_API_HINT}")
    if not (Path.home() / "storage").exists():
        problems.append("~/storage not set up — run `termux-setup-storage`")
    return problems


def _run_opener(
    name: str, target: str, *, missing_hint: str, args: list[str] | None = None
) -> None:
    exe = shutil.which(name)
    if exe is None:
        raise OpenError(f"{name} not found — {missing_hint}")
    argv = [exe, *(args or []), target]
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit code {result.returncode}"
        raise OpenError(f"{name} failed to open {target}: {detail}")
