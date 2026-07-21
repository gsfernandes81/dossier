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

"""Command-line interface for the ``dossier`` / ``ds`` commands.

Bare ``ds`` launches the TUI (not built yet); ``ds init`` bootstraps a device.
Further subcommands (open, export, migrate, doctor, …) land in later slices.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

import tomli_w

from dossier.config import Config, per_device_config_path
from dossier.platform_open import is_termux, termux_preconditions
from dossier.store import Store, atomic_write_bytes


def cmd_init(args: argparse.Namespace) -> int:
    """Bootstrap this device: per-device config + the ``.dossier`` layout."""
    root: Path | None = args.root
    if root is None:
        root = _prompt_for_root()
    if root is None:
        print(
            "error: --root is required (no interactive terminal to prompt).",
            file=sys.stderr,
        )
        return 2

    root = root.expanduser().resolve()
    if not root.is_dir():
        print(
            f"error: syncthing root does not exist or is not a directory: {root}",
            file=sys.stderr,
        )
        return 1

    device_path = per_device_config_path()
    if device_path.is_file() and not args.force:
        print(f"already configured: {device_path}")
        existing = _existing_root(device_path)
        if existing:
            print(f"  syncthing_root = {existing}")
        print("re-run with --force to point this device at a different root.")
        return 0

    config = Config(syncthing_root=root)
    Store(config).ensure_layout()
    atomic_write_bytes(
        device_path, tomli_w.dumps({"syncthing_root": str(root)}).encode("utf-8")
    )

    print("dossier initialised.")
    print(f"  device config : {device_path}")
    print(f"  data folder   : {config.meta_dir}")
    if is_termux():
        problems = termux_preconditions()
        if problems:
            print("\nTermux setup still needed:")
            for problem in problems:
                print(f"  - {problem}")
    print("\nNext: add documents, then run `ds` to open the TUI (coming soon).")
    return 0


def cmd_tui(_args: argparse.Namespace) -> int:
    """Default action: launch the TUI (placeholder until it is built)."""
    print("dossier: the TUI is not implemented yet — see DESIGN.md.")
    print("Available now: `ds init`. Run `ds --help` for options.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dossier",
        description="Track personal documents — physical and digital.",
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    init_p = sub.add_parser(
        "init",
        help="set up this device (per-device config + .dossier layout)",
    )
    init_p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="the Syncthing root folder that holds (or will hold) .dossier/",
    )
    init_p.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing per-device config",
    )
    init_p.set_defaults(func=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        return cmd_tui(args)
    return func(args)


def _prompt_for_root() -> Path | None:
    if not sys.stdin.isatty():
        return None
    raw = input("Syncthing root (the folder that holds .dossier/): ").strip()
    return Path(raw) if raw else None


def _existing_root(device_path: Path) -> str | None:
    try:
        with device_path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = data.get("syncthing_root")
    return str(value) if value else None
