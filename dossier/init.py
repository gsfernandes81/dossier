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

"""Conversational first-run bootstrap — the engine behind ``ds init``.

Pure step functions with injected I/O (:class:`InitIO`) so the walk is testable
without a real terminal. Writes the per-device config through
:func:`config.update_per_device` — a **merge**, never a replace, so scan/service
keys a re-run inherits are preserved (the old hand-written TOML dropped them).
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from dossier.config import Config, per_device_config_path, update_per_device
from dossier.platform_open import is_termux, termux_preconditions
from dossier.store import Store, libyaml_hint


@dataclass
class InitIO:
    """Injected terminal I/O so the conversation is unit-testable.

    ``ask(prompt, default)`` returns the raw answer (tests script it); ``say``
    prints a line; ``interactive`` is False for ``--yes`` / non-TTY runs — no
    question is asked then, every choice takes its default.
    """

    ask: Callable[[str, str], str]
    say: Callable[[str], None]
    interactive: bool
    assume_yes: bool = False  # --yes: non-interactive, but *do* take affirmative
    #                           defaults (e.g. create a missing root). A plain non-TTY
    #                           run without --yes stays conservative.


@dataclass
class InitOptions:
    root: Path | None = None
    glyphs: str | None = None  # forced via --glyphs (skips the icon question)


def run(options: InitOptions, io: InitIO) -> int:
    """Walk the bootstrap; return a process exit code (0 ok · 1 failed step · 2 not
    enough info non-interactively)."""
    device_path = per_device_config_path()
    existing_root = _existing_root(device_path)

    # Already configured, and nothing new to point it at → report and stop. Passing
    # --root is the non-interactive reconfigure path (it skips this branch).
    if existing_root is not None and options.root is None:
        io.say("dossier is already set up on this device.")
        io.say(f"  syncthing_root = {existing_root}")
        if not io.interactive or not _yes(io, "Reconfigure this device?", False):
            io.say("Nothing changed.")
            return 0

    root = _pick_root(options, io, existing_root)
    if root is None:
        io.say("error: a root folder is required — pass --root or run in a terminal.")
        return 2
    root = root.expanduser().resolve()
    if not root.exists():
        # Interactive: ask (default yes). Non-interactive: only --yes may create a
        # folder — a bare `ds init --root <typo>` in a script must not make a stray dir.
        create = (
            _yes(io, f"{root} does not exist — create it?", True)
            if io.interactive
            else io.assume_yes
        )
        if not create:
            io.say(f"error: root folder does not exist: {root}")
            return 1
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            io.say(f"error: could not create {root}: {exc}")
            return 1
    if not root.is_dir():
        io.say(f"error: not a directory: {root}")
        return 1

    config = Config(syncthing_root=root)
    n_docs = _count_documents(config)
    if n_docs is None:
        io.say(f"A new {config.meta_dir.name}/ data folder will be created here.")
    else:
        io.say(f"Found an existing dossier store here — {n_docs} document(s).")

    glyphs = _pick_glyphs(options, io)

    Store(config).ensure_layout()
    changes: dict[str, object] = {"syncthing_root": str(root)}
    if glyphs is not None:  # skipped → leave any existing value (default is nerd)
        changes["glyphs"] = glyphs
    update_per_device(changes)

    _report_termux(io)

    io.say("")
    io.say("dossier initialised.")
    io.say(f"  device config : {device_path}")
    io.say(f"  data folder   : {config.meta_dir}")
    io.say("Next: run `ds migrate` to import from Notion, or `ds` to open the TUI.")
    return 0


def _yes(io: InitIO, prompt: str, default: bool) -> bool:
    if not io.interactive:
        return default
    answer = io.ask(f"{prompt} [{'Y/n' if default else 'y/N'}] ", "").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _pick_root(options: InitOptions, io: InitIO, existing: str | None) -> Path | None:
    if options.root is not None:
        return options.root
    if not io.interactive:
        return Path(existing) if existing else None
    default = existing or ""
    raw = io.ask("Syncthing root (the folder that holds .dossier/): ", default).strip()
    raw = raw or default
    return Path(raw) if raw else None


def _pick_glyphs(options: InitOptions, io: InitIO) -> str | None:
    if options.glyphs:
        return options.glyphs
    if not io.interactive:
        return None  # leave any existing value; a fresh config defaults to nerd
    from dossier.tui import glyphs as gset  # lazy: keeps init off the Textual import

    sample = "  ".join(
        (
            gset.NERD.folder,
            gset.NERD.calendar,
            gset.NERD.tag,
            gset.NERD.ok,
            gset.NERD.expired,
        )
    )
    io.say("Icon check — these should look like little pictures, not boxes:")
    io.say(f"    {sample}")
    return "nerd" if _yes(io, "Do they render as icons?", True) else "ascii"


def _report_termux(io: InitIO) -> None:
    if not is_termux():
        return
    problems = list(termux_preconditions())
    hint = libyaml_hint()
    if hint:
        problems.append(hint)
    if problems:
        io.say("")
        io.say("Termux setup still needed:")
        for problem in problems:
            io.say(f"  - {problem}")
    io.say("")
    io.say(
        "Tip: add `hide-soft-keyboard-on-startup=true` to ~/.termux/termux.properties "
        "so the keyboard stays down; tap the on-screen ⌨ button to raise it."
    )


def _existing_root(device_path: Path) -> str | None:
    try:
        with device_path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = data.get("syncthing_root")
    return str(value) if value else None


def _count_documents(config: Config) -> int | None:
    docs = config.documents_dir
    if not docs.is_dir():
        return None
    return sum(1 for _ in docs.glob("*.md"))
