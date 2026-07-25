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

"""Drive a real terminal program and read its screen as text.

A pseudo-terminal (ConPTY on Windows via ``pywinpty``, the stdlib ``pty`` on
POSIX/Termux) runs the child in a *real* terminal; ``pyte`` is a VT100 emulator
that turns the child's ANSI byte-stream into a screen grid we read as plain text
(:meth:`PtyTerm.text`) or per-cell (:meth:`PtyTerm.cell` → char + colours). This
is the actual terminal framebuffer — no SVG export, no column-width guessing.

The catch a real terminal handles for free: ConPTY and Textual both *query* the
terminal (Device Attributes, cursor position, sync-output mode…) and stall until
answered. ``pyte`` doesn't reply, so this harness answers those queries itself
(:meth:`PtyTerm._respond`) — without it, nothing renders.

Dev-only (see ``tools/README.md``); needs the ``driver`` dependency group.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time

import pyte

IS_WINDOWS = os.name == "nt"

# Escape sequences a terminal sends for special keys (xterm, cursor-keys normal).
KEYS = {
    "enter": "\r",
    "tab": "\t",
    "shift+tab": "\x1b[Z",
    "esc": "\x1b",
    "space": " ",
    "backspace": "\x7f",
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    "home": "\x1b[H",
    "end": "\x1b[F",
    "pageup": "\x1b[5~",
    "pagedown": "\x1b[6~",
}


class PtyTerm:
    """Spawn ``argv`` in a pseudo-terminal and mirror its screen with pyte."""

    def __init__(self, argv, cols=100, rows=30, env=None):
        self.cols, self.rows = cols, rows
        self.screen = pyte.Screen(cols, rows)
        self.stream = pyte.ByteStream(self.screen)
        self._lock = threading.Lock()
        self._alive = True
        self._tail = ""  # last few chars, so a query split across reads still matches
        self._open(argv, cols, rows, env)
        self._reader = threading.Thread(target=self._drain, daemon=True)
        self._reader.start()

    # -- pty backend (Windows ConPTY / POSIX pty) ----------------------------

    def _open(self, argv, cols, rows, env):
        if IS_WINDOWS:
            from winpty import PtyProcess

            self._proc = PtyProcess.spawn(argv, dimensions=(rows, cols), env=env)
            return
        import fcntl
        import pty
        import struct
        import subprocess
        import termios

        self._master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self._popen = subprocess.Popen(
            argv, stdin=slave, stdout=slave, stderr=slave, env=env, close_fds=True
        )
        os.close(slave)

    def _read(self):
        if IS_WINDOWS:
            return self._proc.read(65536)  # str
        return os.read(self._master, 65536)  # bytes

    def _write(self, s: str):
        if IS_WINDOWS:
            self._proc.write(s)
        else:
            os.write(self._master, s.encode("utf-8"))

    def _child_alive(self):
        if IS_WINDOWS:
            return self._proc.isalive()
        return self._popen.poll() is None

    def _terminate(self):
        if IS_WINDOWS:
            self._proc.terminate(force=True)
        else:
            self._popen.terminate()

    # -- read loop + terminal query responder --------------------------------

    def _drain(self):
        while self._alive:
            try:
                data = self._read()
            except (EOFError, OSError):
                break
            if not data:
                if not IS_WINDOWS:  # POSIX read returns b"" at EOF
                    break
                continue
            raw = data if isinstance(data, bytes) else data.encode("utf-8", "replace")
            text = data if isinstance(data, str) else data.decode("utf-8", "replace")
            with self._lock:
                self.stream.feed(raw)
            self._respond(text)
        self._alive = False

    def _respond(self, text):
        scan = self._tail + text
        self._tail = scan[-8:]
        if "\x1b[c" in scan or "\x1b[0c" in scan:
            self._write("\x1b[?1;2c")  # primary Device Attributes
        if "\x1b[>c" in scan or "\x1b[>0c" in scan:
            self._write("\x1b[>0;10;1c")  # secondary DA
        if "\x1b[5n" in scan:
            self._write("\x1b[0n")  # status OK
        if "\x1b[6n" in scan:  # cursor position report
            with self._lock:
                y, x = self.screen.cursor.y + 1, self.screen.cursor.x + 1
            self._write(f"\x1b[{y};{x}R")
        if "\x1b[?2026$p" in scan:
            self._write("\x1b[?2026;2$y")  # synchronized-output mode: unsupported

    # -- public API ----------------------------------------------------------

    def send(self, *keys, settle=0.15):
        """Send keys (named tokens from :data:`KEYS`, or literal text), then settle."""
        for k in keys:
            self._write(KEYS.get(k, k))
        time.sleep(settle)

    def wait_for(self, needle, timeout=6.0, settle=0.12):
        """Block until ``needle`` is on screen; True, or False on timeout."""
        end = time.time() + timeout
        while time.time() < end:
            if needle in self.text():
                time.sleep(settle)  # let the frame settle
                return True
            time.sleep(0.04)
        return False

    def wait_until(self, predicate, timeout=6.0, settle=0.12):
        """Block until ``predicate()`` is true; True, or False on timeout.

        For conditions no substring captures — a cell's colour, a footer's contents.
        Prefer this to a fixed ``settle``: sleeping a guessed number of milliseconds
        and then asserting is a bet on the machine's load, and it is the bet the
        driver tests kept losing.
        """
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                time.sleep(settle)
                return True
            time.sleep(0.04)
        return False

    def wait_for_exit(self, timeout=6.0):
        """Block until the child process exits; True, or False on timeout.

        Sampling :meth:`alive` once right after sending a quit key is a race: the
        app may still be finishing whatever the *previous* keystroke started (a
        store reload, say), so the quit is merely queued rather than refused.
        """
        end = time.time() + timeout
        while time.time() < end:
            if not self.alive():
                return True
            time.sleep(0.05)
        return False

    def text(self):
        with self._lock:
            return "\n".join(self.screen.display)

    def cell(self, row, col):
        """``(char, fg, bg, bold, reverse)`` for one cell — lets tests assert colour."""
        with self._lock:
            c = self.screen.buffer[row][col]
        return (c.data, c.fg, c.bg, c.bold, c.reverse)

    def show(self, label=""):
        print(f"──── {label} " + "─" * max(0, 62 - len(label)))
        print(self.text())

    def alive(self):
        return self._alive and self._child_alive()

    def close(self):
        self._alive = False
        with contextlib.suppress(Exception):
            self._terminate()
