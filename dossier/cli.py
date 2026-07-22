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
import json
import sys
import tomllib
from pathlib import Path

import tomli_w

from dossier import (
    dedup_cache,
    dedup_hash,
    doctor,
    migrate,
    reconcile,
    reset,
)
from dossier.config import DEFAULT_GLYPHS, Config, per_device_config_path
from dossier.errors import ConfigError
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
    device_settings = {"syncthing_root": str(root), "glyphs": DEFAULT_GLYPHS}
    atomic_write_bytes(device_path, tomli_w.dumps(device_settings).encode("utf-8"))

    print("dossier initialised.")
    print(f"  device config : {device_path}")
    print(f"  data folder   : {config.meta_dir}")
    print(f"  icons         : {DEFAULT_GLYPHS} (needs a Nerd Font; set glyphs=ascii)")
    if is_termux():
        problems = termux_preconditions()
        if problems:
            print("\nTermux setup still needed:")
            for problem in problems:
                print(f"  - {problem}")
        print(
            "\nTip: add `hide-soft-keyboard-on-startup=true` to "
            "~/.termux/termux.properties so the keyboard stays down; tap the "
            "on-screen ⌨ button in the TUI to bring it up when you need to type."
        )
    print("\nNext: add documents, then run `ds` to open the TUI (coming soon).")
    return 0


def cmd_tui(_args: argparse.Namespace) -> int:
    """Default action: launch the TUI."""
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    from dossier.tui import DossierApp

    DossierApp(Store(config), config, touch=is_termux()).run()
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    """Transform a Notion export into the store (dry-run unless ``--apply``)."""
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    export_path: Path = args.notion_export
    try:
        export = json.loads(export_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read export {export_path}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(export, dict):
        print("error: the export JSON must be an object", file=sys.stderr)
        return 1

    plan = migrate.build_plan(export, migrate.build_file_index(config))
    _print_migration_report(plan, verbose=args.verbose)

    if args.apply:
        written = migrate.apply_plan(Store(config), plan)
        print(f"\napplied {written} documents and {len(plan.locations)} locations.")
    else:
        print("\n(dry run — nothing written; re-run with --apply to write.)")
    return 0


def _print_migration_report(plan: migrate.MigrationPlan, *, verbose: bool) -> None:
    linked = sum(1 for doc in plan.documents if doc.files)
    print(f"documents: {len(plan.documents)}   locations: {len(plan.locations)}")
    print(f"file links matched: {linked}/{len(plan.documents)}")

    counts: dict[str, int] = {}
    for issue in plan.issues:
        counts[issue.kind] = counts.get(issue.kind, 0) + 1
    if counts:
        print("issues to review:")
        for kind, number in sorted(counts.items()):
            print(f"  {kind}: {number}")
    if plan.bundle_suggestions:
        print("suggested bundles (not created):")
        for slug, docs in sorted(plan.bundle_suggestions.items()):
            print(f"  {slug}: {len(docs)} docs")
    if verbose and plan.issues:
        print("\nissue detail:")
        for issue in plan.issues:
            print(f"  [{issue.kind}] {issue.doc}: {issue.detail}")


def cmd_reset(args: argparse.Namespace) -> int:
    """Clear a folder's ``.dossier`` data, or (``--global``) this device's config."""
    if args.global_config:
        path = per_device_config_path()
        if not path.is_file():
            print("no per-device config to remove.")
            return 0
        if not _confirm(f"remove this device's config at {path}?", args.yes):
            return 1
        removed = reset.reset_device_config()
        print(f"removed device config: {removed}")
        print("this device is no longer configured; run `ds init` to set it up again.")
        return 0

    if args.root is not None:
        config = Config(syncthing_root=args.root.expanduser().resolve())
    else:
        try:
            config = Config.load()
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    entries = reset.folder_reset_entries(config)
    if not entries:
        print(f"no .dossier data to reset at {config.meta_dir}.")
        return 0
    print(f"This will clear {config.meta_dir}")
    print(f"  backed up to {config.history_dir} first; your real files are untouched:")
    for name in entries:
        print(f"    {name}")
    if not _confirm("proceed?", args.yes):
        return 1
    backup = reset.reset_folder_data(config)
    print(f"reset complete. backup: {backup}")
    print("run `ds migrate ... --apply` (or `ds`) to repopulate.")
    return 0


def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print("refusing without --yes (no interactive terminal).", file=sys.stderr)
        return False
    return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")


def cmd_doctor(_args: argparse.Namespace) -> int:
    """Check the store for problems (conflicts, refs, dates, files)."""
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    report = doctor.run(Store(config), config)
    if report.findings:
        grouped = report.by_check()
        print(f"doctor: {len(report.findings)} finding(s)\n")
        for check in sorted(grouped):
            items = grouped[check]
            print(f"{check} ({len(items)}):")
            for finding in items:
                print(f"  {finding.subject}: {finding.detail}")
            print()
    else:
        print("doctor: all clear.")
    _print_icon_note(config)
    return 0


def _print_icon_note(config: Config) -> None:
    print(
        f"icons: {config.glyphs} style — 'nerd' needs a Nerd Font installed; set "
        f'glyphs = "ascii" in {per_device_config_path()} if icons show as boxes.'
    )


def cmd_reconcile(args: argparse.Namespace) -> int:
    """List orphan files, missing files (and, later, duplicates) in the folder."""
    try:
        config = Config.load()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    store = Store(config)
    state = store.load_reconcile()

    pages = None
    if args.dedup:
        root = config.syncthing_root
        candidates = [
            root / rel
            for rel in reconcile.scan_files(config, state.ignore)
            if Path(rel).suffix.lower() in dedup_hash.PAGE_SUFFIXES
        ]
        print(f"hashing {len(candidates)} page-bearing files (first run may be slow)…")
        try:
            pages = dedup_cache.cached_page_hashes(candidates, root)
        except dedup_hash.DedupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    report = reconcile.run(store, config, pages_by_file=pages, state=state)
    print(
        f"reconcile: {len(report.orphans)} orphan · {len(report.linked)} linked · "
        f"{len(report.missing)} missing"
    )
    ignore = [*config.ignore, *state.ignore]
    if config.include or ignore:
        print(f"  scope: include={config.include or ['*']}  ignore={ignore}")

    if report.groups is not None:
        print(f"\nduplicate clusters ({len(report.groups)}):")
        for group in report.groups[:40]:
            tag = " [ambiguous — review]" if group.ambiguous else ""
            print(f"  keep {group.keep}{tag}")
            for subset in group.subsets:
                print(f"       {subset}")

    suggested = sorted(
        (o for o in report.orphans if o.suggestion), key=lambda o: -o.score
    )
    if suggested:
        print(f"\nsuggested matches ({len(suggested)}):")
        for orphan in suggested[:30]:
            print(f"  {orphan.path}  ->  {orphan.suggestion} ({orphan.score:.2f})")

    if args.verbose:
        print(f"\norphans ({len(report.orphans)}):")
        for orphan in report.orphans:
            print(f"  {orphan.path}")
    elif report.orphans:
        by_folder: dict[str, int] = {}
        for orphan in report.orphans:
            folder = orphan.path.rsplit("/", 1)[0] if "/" in orphan.path else "."
            by_folder[folder] = by_folder.get(folder, 0) + 1
        print(f"\norphans by folder ({len(report.orphans)} total; -v lists all):")
        for folder, count in sorted(by_folder.items(), key=lambda item: -item[1])[:25]:
            print(f"  {count:>5}  {folder}")

    if report.missing:
        print(f"\nmissing files ({len(report.missing)}):")
        for missing in report.missing:
            print(f"  {missing.doc_id}: {missing.path}")
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

    migrate_p = sub.add_parser(
        "migrate",
        help="import a Notion export (dry-run unless --apply)",
    )
    migrate_p.add_argument(
        "--notion-export",
        type=Path,
        required=True,
        dest="notion_export",
        help="path to the Notion export JSON",
    )
    migrate_p.add_argument(
        "--apply",
        action="store_true",
        help="write the documents (default is a dry-run report)",
    )
    migrate_p.add_argument(
        "--verbose",
        action="store_true",
        help="list every issue in the report",
    )
    migrate_p.set_defaults(func=cmd_migrate)

    doctor_p = sub.add_parser(
        "doctor",
        help="check the store for problems (conflicts, refs, dates, files)",
    )
    doctor_p.set_defaults(func=cmd_doctor)

    reset_p = sub.add_parser(
        "reset",
        help="clear a folder's .dossier data (never real files), or --global config",
    )
    reset_p.add_argument(
        "--root",
        type=Path,
        default=None,
        help="folder whose .dossier data to clear (default: the configured root)",
    )
    reset_p.add_argument(
        "--global",
        "--config",
        dest="global_config",
        action="store_true",
        help="remove this device's config instead (un-configure this device)",
    )
    reset_p.add_argument(
        "--yes",
        action="store_true",
        help="skip the confirmation prompt",
    )
    reset_p.set_defaults(func=cmd_reset)

    reconcile_p = sub.add_parser(
        "reconcile",
        help="find orphan files and missing files (duplicates soon)",
    )
    reconcile_p.add_argument(
        "--dedup",
        action="store_true",
        help="also find duplicate/superset clusters (needs the [dedup] extra)",
    )
    reconcile_p.add_argument(
        "--verbose",
        action="store_true",
        help="list every orphan instead of per-folder counts",
    )
    reconcile_p.set_defaults(func=cmd_reconcile)

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
