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
from dataclasses import replace
from datetime import date
from pathlib import Path

import tomli_w

from dossier import (
    dedup_cache,
    dedup_hash,
    doctor,
    export,
    intake,
    migrate,
    organize,
    preparedness,
    query,
    reconcile,
    reset,
    scan,
)
from dossier.config import DEFAULT_GLYPHS, Config, per_device_config_path
from dossier.errors import ConfigError, IntakeError, ScanError
from dossier.model import Document
from dossier.platform_open import is_termux, termux_preconditions
from dossier.store import Store, atomic_write_bytes


def _load_config() -> Config | None:
    """Load the device config, printing a clean error (and returning None) if unset."""
    try:
        return Config.load()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


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


def _resolve_touch(args: argparse.Namespace) -> bool:
    """Whether to use the touch/mobile UI.

    ``--mobile``/``--desktop`` force it either way (so the touch UI can be driven
    on a desktop terminal via the ``tools/`` PTY harness); otherwise it follows
    the platform, i.e. on by default only under Termux.
    """
    if getattr(args, "mobile", False):
        return True
    if getattr(args, "desktop", False):
        return False
    return is_termux()


def cmd_tui(args: argparse.Namespace) -> int:
    """Default action: launch the TUI."""
    config = _load_config()
    if config is None:
        return 1

    from dossier.tui import DossierApp

    DossierApp(Store(config), config, touch=_resolve_touch(args)).run()
    return 0


def cmd_migrate(args: argparse.Namespace) -> int:
    """Transform a Notion export into the store (dry-run unless ``--apply``)."""
    config = _load_config()
    if config is None:
        return 1

    export_path: Path = args.notion_export.expanduser()
    try:
        export_data = json.loads(export_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read export {export_path}: {exc}", file=sys.stderr)
        return 1
    if not isinstance(export_data, dict):
        print("error: the export JSON must be an object", file=sys.stderr)
        return 1

    plan = migrate.build_plan(export_data, migrate.build_file_index(config))
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
        root = args.root.expanduser().resolve()
        if not root.is_dir():
            print(f"error: not a directory: {root}", file=sys.stderr)
            return 1
        config = Config(syncthing_root=root)
    else:
        config = _load_config()
        if config is None:
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
    config = _load_config()
    if config is None:
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
    config = _load_config()
    if config is None:
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


def cmd_export(args: argparse.Namespace) -> int:
    """Copy (or symlink) a bundle's files into an external folder."""
    config = _load_config()
    if config is None:
        return 1

    store = Store(config)
    bundles = store.load_bundles()
    if args.bundle not in bundles:
        available = ", ".join(sorted(bundles)) or "(none)"
        print(
            f"error: unknown bundle '{args.bundle}'. Known: {available}",
            file=sys.stderr,
        )
        return 1

    dest_raw = args.to or bundles[args.bundle].export_dir
    if not dest_raw:
        print(
            "error: no destination — pass --to DIR or set the bundle's export_dir",
            file=sys.stderr,
        )
        return 1
    dest = Path(dest_raw).expanduser()

    plan = export.build_export_plan(
        store.load_all(),
        args.bundle,
        root=config.syncthing_root,
        dest=dest,
        force=args.force,
    )
    print(f"export {args.bundle} → {dest}")
    for item in plan.problems:
        print(f"  skip  {item.name}  ({item.problem})")
    if args.dry_run:
        print(f"\n{len(plan.ready)} file(s) would be exported (dry run).")
        return 1 if plan.problems else 0  # match the real run's exit code

    exported, errors = export.apply_export_plan(plan, symlink=args.symlink)
    for message in errors:
        print(f"  error {message}", file=sys.stderr)
    print(f"\nexported {exported} file(s); {len(plan.problems)} skipped.")
    return 1 if errors or plan.problems else 0


def cmd_organize(args: argparse.Namespace) -> int:
    """Rename linked files to canonical names (dry-run unless ``--apply``)."""
    config = _load_config()
    if config is None:
        return 1

    store = Store(config)
    docs = store.load_all()
    if args.doc_id:
        docs = [d for d in docs if d.id == args.doc_id]
        if not docs:
            print(f"error: unknown document '{args.doc_id}'", file=sys.stderr)
            return 1
    elif args.bundle:
        docs = [d for d in docs if args.bundle in d.bundles]
        if not docs:
            print(f"error: no documents in bundle '{args.bundle}'", file=sys.stderr)
            return 1

    plan = organize.build_organize_plan(
        docs,
        root=config.syncthing_root,
        to_folders=args.to_folders,
        folder_map=config.organize_folders,
    )
    label = "organize --to-folders" if args.to_folders else "organize"
    print(
        f"{label}: {len(plan.ready)} rename(s), "
        f"{len(plan.problems)} skip(s), {len(plan.already)} already ok"
    )
    for item in plan.ready:
        note = f"  [{item.note}]" if item.note else ""
        print(f"  rename  {item.src_rel}  ->  {item.dst_rel}{note}")
    for item in plan.problems:
        print(f"  skip    {item.name}  ({item.problem})")
    if args.verbose:
        for item in plan.already:
            print(f"  ok      {item.src_rel}")

    if not args.apply:
        print(f"\n{len(plan.ready)} file(s) would be renamed (dry run; pass --apply).")
        return 1 if plan.problems else 0

    renamed, errors = organize.apply_organize_plan(
        plan, store, root=config.syncthing_root
    )
    for message in errors:
        print(f"  error {message}", file=sys.stderr)
    print(f"\nrenamed {renamed} file(s); {len(plan.problems)} skipped.")
    return 1 if errors or plan.problems else 0


def cmd_intake(args: argparse.Namespace) -> int:
    """Propose records for dropped files, and (``--apply``) file them."""
    config = _load_config()
    if config is None:
        return 1
    store = Store(config)
    from_dir, in_place = None, False
    if args.from_dir:
        from_dir = _relative_to_root(config, args.from_dir)
        if from_dir is None:
            print("error: --from must be inside the syncthing root", file=sys.stderr)
            return 1
        in_place = True
    return _intake_run(
        config,
        store,
        from_dir=from_dir,
        in_place=in_place,
        limit=args.limit,
        apply=args.apply,
        yes=args.yes,
    )


def cmd_import(args: argparse.Namespace) -> int:
    """Bulk-import a folder: intake every unfiled file, renamed in place."""
    config = _load_config()
    if config is None:
        return 1
    store = Store(config)
    from_dir = _relative_to_root(config, args.directory)
    if from_dir is None:
        print("error: the directory must be inside the syncthing root", file=sys.stderr)
        return 1
    return _intake_run(
        config,
        store,
        from_dir=from_dir,
        in_place=True,
        limit=args.limit,
        apply=args.apply,
        yes=args.yes,
    )


def _relative_to_root(config: Config, raw: str) -> str | None:
    """``raw`` (absolute or root-relative) as a root-relative POSIX path, or None
    if it falls outside the syncthing root."""
    path = Path(raw).expanduser()
    abs_path = path if path.is_absolute() else config.syncthing_root / path
    try:
        rel = abs_path.resolve().relative_to(config.syncthing_root.resolve())
    except ValueError:
        return None
    return rel.as_posix()


def _intake_run(
    config: Config,
    store: Store,
    *,
    from_dir: str | None,
    in_place: bool,
    limit: int,
    apply: bool,
    yes: bool,
) -> int:
    """Shared intake loop for ``ds intake`` and ``ds import`` — propose, then file.

    Reuses a reading cache (``.dossier/intake.toml``) so re-running a big sweep
    doesn't re-scan: a fresh read is persisted immediately (resumable), and a filed
    file's entry is dropped (it now lives in ``scans.toml`` under the new id).
    """
    pending = intake.pending_files(store, config, from_dir=from_dir)
    if limit > 0:
        pending = pending[:limit]
    if not pending:
        where = from_dir or config.intake_inbox or "(no [intake] inbox configured)"
        print(f"no files to intake in {where}.")
        return 0

    docs = store.load_all()
    readings = store.load_scans()
    cache = store.load_intake_cache()
    proposals: list[intake.IntakeProposal] = []
    for rel in pending:
        prior = cache.get(rel)
        try:
            proposal = intake.build_proposal(
                rel,
                store,
                config,
                docs=docs,
                readings=readings,
                in_place=in_place,
                cache=cache,
            )
        except ScanError as exc:
            print(f"  skip  {rel}  (scan failed: {exc})", file=sys.stderr)
            continue
        if cache.get(rel) is not prior:  # a fresh reading — persist so a re-run resumes
            store.save_intake_cache(cache)
        proposals.append(proposal)
        _print_proposal(proposal)

    if not proposals:
        return 1
    if not apply:
        print(f"\n{len(proposals)} proposal(s) (dry run; pass --apply to file).")
        return 0
    if not _confirm(f"file these {len(proposals)} document(s)?", yes):
        return 1

    filed = 0
    for proposal in proposals:
        try:
            doc, errors = intake.apply_proposal(proposal, store, config)
        except IntakeError as exc:
            print(f"  error {proposal.name}: {exc}", file=sys.stderr)
            continue
        for message in errors:
            print(f"  warn  {message}", file=sys.stderr)
        if cache.pop(proposal.src_rel, None) is not None:  # now in scans.toml
            store.save_intake_cache(cache)
        print(f"  filed {doc.id}  ({proposal.dst_rel})")
        filed += 1
    print(f"\nfiled {filed} document(s).")
    return 0


def _print_proposal(p: intake.IntakeProposal) -> None:
    print(f"\n{p.src_rel}")
    print(f"  name    {p.doc.name}  (id {p.doc.id})")
    if p.doc.tags:
        print(f"  tags    {' '.join(p.doc.tags)}")
    if p.doc.issue_date:
        print(f"  issue   {p.doc.issue_date}")
    if p.doc.expiry_date:
        print(f"  expiry  {p.doc.expiry_date}")
    if p.doc.notes:
        print(f"  notes   {p.doc.notes.splitlines()[-1]}")
    if p.succession is not None:
        conf = p.succession.confidence
        print(f"  renews  {p.succession.older}  (conf {conf:.2f})")
    dst = p.dst_rel + (f"  [{','.join(p.notes)}]" if p.notes else "")
    print(f"  file    {p.src_rel}  {'->' if p.moves else '= (in place)'}  {dst}")
    for q in p.open_questions:
        print(f"  ?       {q.field.value}: {' / '.join(q.values)}  (pick in the pane)")
    print(f"  read    conf {p.reading.confidence:.2f}, model {p.reading.model}")


def cmd_expiring(args: argparse.Namespace) -> int:
    """List documents needing attention — plain text for a scheduled reminder.

    Empty stdout when nothing is due (so a cron/Task-Scheduler notification is
    clean). Exit 0 = clean · 1 = at least one line · 2 = error, so a scheduled job
    can tell "nag me" from "the tool is broken".
    """
    config = _load_config()
    if config is None:
        return 2

    store = Store(config)
    docs = store.load_all()
    threshold = args.days if args.days is not None else config.expiry_threshold_days
    bundles = store.load_bundles()
    if args.bundle is not None:
        if args.bundle not in bundles:
            print(f"error: unknown bundle '{args.bundle}'", file=sys.stderr)
            return 2
        docs = [d for d in docs if args.bundle in d.bundles]

    today = date.today()
    flags: dict[str, list[preparedness.EventFlag]] = {}
    if not args.no_events:
        flags = preparedness.event_flags(
            docs, bundles.values(), today=today, margin_days=threshold
        )
    return _print_expiring(docs, flags, today=today, threshold=threshold)


def _print_expiring(
    docs: list[Document],
    flags: dict[str, list[preparedness.EventFlag]],
    *,
    today: date,
    threshold: int,
) -> int:
    tracked = query.tracked(docs, today=today)  # excludes ignored/superseded
    soon = query.expiring(tracked, today=today, threshold_days=threshold)
    soon_ids = {doc.id for doc in soon}

    rows: list[tuple[date, str, str, preparedness.EventFlag | None]] = []
    for doc in soon:
        assert doc.expiry_date is not None  # tracked + expiring ⇒ dated
        label = "expired" if doc.expiry_date < today else "expiring"
        headline = flags.get(doc.id)
        rows.append(
            (doc.expiry_date, label, doc.name, headline[0] if headline else None)
        )
    for doc in tracked:  # OK today, but lapses before an event it's needed for
        if doc.id in soon_ids or doc.id not in flags:
            continue
        assert doc.expiry_date is not None
        rows.append((doc.expiry_date, "event", doc.name, flags[doc.id][0]))

    rows.sort(key=lambda row: row[0])  # soonest expiry first
    for expiry, label, name, flag in rows:
        line = f"{expiry}  {label:8}  {name}"
        if flag is not None:
            line += f"  · needed {flag.event} for {flag.bundle_slug}"
        print(line)
    return 1 if rows else 0


def _print_models(config: Config) -> int:
    """List the router's models (``ds scan --list-models``), vision ones flagged."""
    try:
        models = scan.list_models(config)
    except ScanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if not models:
        print("no models reported by the router.")
        return 0
    print(f"models at {config.scan_base_url}  (scan uses: {config.scan_model})")
    for model in models:
        kind = "vision" if model.vision else "text  "
        current = "  <- current" if model.id == config.scan_model else ""
        print(f"  [{kind}] {model.id}{current}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    """Read linked scans with the vision model into the readings sidecar.

    Skips files whose fingerprint is unchanged since the last scan (``--force``
    overrides). Readings feed the suggestions layer and the succession matcher.
    """
    config = _load_config()
    if config is None:
        return 1
    if args.list_models:
        return _print_models(config)
    if args.model:
        config.scan_model = args.model  # override for this run
    store = Store(config)
    existing = store.load_scans()
    readings = dict(existing)
    linked = [doc for doc in store.load_all() if doc.primary_rendition() is not None]
    scanned = skipped = missing = failed = 0
    for doc in linked:
        rendition = doc.primary_rendition()
        assert rendition is not None
        path = query.resolve_path(config.syncthing_root, rendition.path)
        if not path.exists():
            missing += 1
            continue
        fingerprint = scan.file_fingerprint(path)
        if (
            not args.force
            and doc.id in existing
            and existing[doc.id].fingerprint == fingerprint
        ):
            skipped += 1
            continue
        if args.limit and scanned >= args.limit:
            break
        try:
            reading = scan.extract(path, config)
        except ScanError as exc:
            print(f"  ! {doc.id}: {exc}", file=sys.stderr)
            failed += 1
            continue
        readings[doc.id] = replace(reading, fingerprint=fingerprint)
        scanned += 1
        dates = " to ".join(
            d for d in (reading.issue_date_text, reading.expiry_date_text) if d
        )
        tail = f"  [{dates}]" if dates else ""
        print(f"  + {doc.id}: {reading.document_type}{tail}")
    store.save_scans(readings)
    print(
        f"scanned {scanned}, skipped {skipped} (unchanged), "
        f"{missing} missing on disk, {failed} failed"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dossier",
        description="Track personal documents — physical and digital.",
    )
    # Bare `ds` launches the TUI; these force its touch vs desktop UI (else the
    # platform decides). Forcing lets the touch UI be driven on a desktop
    # terminal via the tools/ PTY harness.
    view = parser.add_mutually_exclusive_group()
    view.add_argument(
        "--mobile",
        action="store_true",
        help="force the touch/mobile UI, overriding platform auto-detection",
    )
    view.add_argument(
        "--desktop",
        action="store_true",
        help="force the desktop UI, overriding platform auto-detection",
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

    export_p = sub.add_parser(
        "export",
        help="copy (or symlink) a bundle's files to an external folder",
    )
    export_p.add_argument("bundle", help="the bundle slug to export")
    export_p.add_argument(
        "--to", metavar="DIR", help="destination folder (else the bundle's export_dir)"
    )
    export_p.add_argument(
        "--symlink",
        action="store_true",
        help="symlink instead of copy (needs Developer Mode on Windows)",
    )
    export_p.add_argument(
        "--force",
        action="store_true",
        help="overwrite files already in the destination",
    )
    export_p.add_argument(
        "--dry-run", action="store_true", help="print the plan without writing"
    )
    export_p.set_defaults(func=cmd_export)

    organize_p = sub.add_parser(
        "organize",
        help="rename linked files to canonical names (dry-run unless --apply)",
    )
    organize_p.add_argument(
        "doc_id", nargs="?", help="organize just this document id (else all linked)"
    )
    organize_p.add_argument(
        "--bundle", metavar="SLUG", help="limit to a bundle's documents"
    )
    organize_p.add_argument(
        "--to-folders",
        action="store_true",
        dest="to_folders",
        help="also move each file into the folder its primary tag maps to",
    )
    organize_p.add_argument(
        "--apply", action="store_true", help="perform the renames (default: dry run)"
    )
    organize_p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also list files already at their canonical name",
    )
    organize_p.set_defaults(func=cmd_organize)

    intake_p = sub.add_parser(
        "intake",
        help="file dropped documents from the inbox (dry-run unless --apply)",
    )
    intake_p.add_argument(
        "--from",
        dest="from_dir",
        metavar="DIR",
        help="source folder (files renamed in place) instead of the configured inbox",
    )
    intake_p.add_argument(
        "--limit", type=int, default=0, help="process at most N files (0 = all)"
    )
    intake_p.add_argument(
        "--apply", action="store_true", help="file the proposals (default: dry run)"
    )
    intake_p.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt (with --apply)"
    )
    intake_p.set_defaults(func=cmd_intake)

    import_p = sub.add_parser(
        "import",
        help="bulk-import a folder's unfiled files in place (dry-run unless --apply)",
    )
    import_p.add_argument(
        "directory", help="folder to import (inside the syncthing root)"
    )
    import_p.add_argument(
        "--limit", type=int, default=0, help="process at most N files (0 = all)"
    )
    import_p.add_argument(
        "--apply", action="store_true", help="file the proposals (default: dry run)"
    )
    import_p.add_argument(
        "--yes", action="store_true", help="skip the confirmation prompt (with --apply)"
    )
    import_p.set_defaults(func=cmd_import)

    expiring_p = sub.add_parser(
        "expiring",
        help="list documents needing attention (plain text, for a scheduled reminder)",
    )
    expiring_p.add_argument(
        "--days",
        type=int,
        default=None,
        metavar="N",
        help="warn window in days (default: the synced expiry threshold)",
    )
    expiring_p.add_argument(
        "--bundle", metavar="SLUG", help="limit to a bundle's members"
    )
    expiring_p.add_argument(
        "--no-events",
        action="store_true",
        dest="no_events",
        help="skip event-date checks (today-relative expiry only)",
    )
    expiring_p.set_defaults(func=cmd_expiring)

    scan_p = sub.add_parser(
        "scan",
        help="read linked scans with the vision model into readings (needs [scan])",
    )
    scan_p.add_argument(
        "--force", action="store_true", help="re-read even unchanged files"
    )
    scan_p.add_argument(
        "--limit", type=int, default=0, help="scan at most N new files (0 = all)"
    )
    scan_p.add_argument(
        "--model", metavar="NAME", help="override the scan model for this run"
    )
    scan_p.add_argument(
        "--list-models",
        action="store_true",
        help="list the router's models (vision-capable flagged) and exit",
    )
    scan_p.set_defaults(func=cmd_scan)

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
