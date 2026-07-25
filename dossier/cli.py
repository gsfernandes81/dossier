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

Bare ``ds`` launches the TUI (``--mobile``/``--desktop`` force the touch vs desktop
UI); ``ds init`` bootstraps a device. The subcommands span setup and maintenance —
``migrate``, ``doctor``, ``reset``, ``reconcile``, ``resolve``, ``organize``,
``export`` — and the vision/intake pipeline — ``scan``, ``intake``, ``import`` —
plus the quick lookups ``expiring``, ``ask``, ``open`` and the ``service`` /
``profile`` utilities. Command machinery is imported lazily per subcommand so a bare
launch stays fast (guarded by ``test_cli_import_stays_lean``).
"""

from __future__ import annotations

import argparse
import functools
import json
import sys
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

# Only the modules dispatch always needs are imported eagerly. The rest are
# imported inside the handful of commands that use them (see each cmd_*), so a
# quick command like `ds expiring` or a bare `ds` no longer pays to import the
# dedup/intake/service machinery it never touches. `migrate`/`scan` stay because
# `reconcile`/`store` pull them anyway. TYPE_CHECKING holds the deferred modules
# used only in annotations (strings under `from __future__ import annotations`).
from dossier import doctor, migrate, query, reconcile, resolve, scan
from dossier.config import Config, per_device_config_path
from dossier.errors import ConfigError, IntakeError, ScanError
from dossier.merge import FieldDecision
from dossier.model import Document

if TYPE_CHECKING:
    from dossier import intake, preparedness, service_install
from dossier.platform_open import OpenError, is_termux, open_file
from dossier.store import Store


def _load_config() -> Config | None:
    """Load the device config, printing a clean error (and returning None) if unset."""
    try:
        return Config.load()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return None


def cmd_init(args: argparse.Namespace) -> int:
    """Bootstrap this device — a conversational per-device config + ``.dossier``
    layout. Deferred import keeps the engine (and its lazy Textual glyph peek) off
    the hot CLI-startup path."""
    from dossier import init

    assume_yes = args.yes or args.force  # --force is a deprecated alias for --yes
    io = init.InitIO(
        ask=lambda prompt, _default: input(prompt),
        say=print,
        interactive=sys.stdin.isatty() and not assume_yes,
        assume_yes=assume_yes,
    )
    return init.run(init.InitOptions(root=args.root, glyphs=args.glyphs), io)


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
    """Default action: launch the TUI (walking `ds init` first on an unset device)."""
    try:
        config = Config.load()
    except ConfigError as exc:
        # First contact: no device config at all, and a terminal to walk it → run init
        # then launch, so cold-start-to-usable stays one command. Only the *missing
        # config* case hands off; a bad root / missing .dossier keeps its loud pointer.
        if not per_device_config_path().is_file() and sys.stdin.isatty():
            print("dossier isn't set up on this device yet — let's fix that.\n")
            from dossier import init

            io = init.InitIO(
                ask=lambda prompt, _default: input(prompt),
                say=print,
                interactive=True,
            )
            if init.run(init.InitOptions(), io) != 0:
                return 1
            config = Config.load()
        else:
            print(f"error: {exc}", file=sys.stderr)
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
    from dossier import reset

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
    warnings = [f for f in report.findings if f.severity != "info"]
    notes = [f for f in report.findings if f.severity == "info"]
    if warnings:
        print(f"doctor: {len(warnings)} finding(s)\n")
        _print_findings(warnings)
    elif not notes:
        print("doctor: all clear.")
    if notes:  # advisory / skipped — kept apart so they never read as failures
        print("doctor: notes\n")
        _print_findings(notes)
    _print_icon_note(config)
    return 0


def _print_findings(findings: list[doctor.Finding]) -> None:
    """Print findings grouped by check, each group with its recovery hint."""
    grouped: dict[str, list[doctor.Finding]] = {}
    for finding in findings:
        grouped.setdefault(finding.check, []).append(finding)
    for check in sorted(grouped):
        items = grouped[check]
        print(f"{check} ({len(items)}):")
        for finding in items:
            print(f"  {finding.subject}: {finding.detail}")
        hint = doctor.CHECK_HINTS.get(check)
        if hint:
            print(f"  → {hint}")
        print()


def _print_icon_note(config: Config) -> None:
    print(
        f"icons: {config.glyphs} style — 'nerd' needs a Nerd Font installed; set "
        f'glyphs = "ascii" in {per_device_config_path()} if icons show as boxes.'
    )


def cmd_reconcile(args: argparse.Namespace) -> int:
    """List orphan files, missing files, and (with --dedup) duplicate clusters."""
    config = _load_config()
    if config is None:
        return 1

    store = Store(config)
    state = store.load_reconcile()

    pages = None
    if args.dedup:
        from dossier import dedup_cache, dedup_hash  # only the --dedup path needs these

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


def cmd_resolve(args: argparse.Namespace) -> int:
    """Field-merge Syncthing conflict files back into the live copies.

    Dry-run by default (prints what would merge); ``--apply`` performs the merges.
    Every merge is recoverable — the losing conflict copy and the pre-merge live
    copy are stashed to local history first.
    """
    config = _load_config()
    if config is None:
        return 1
    store = Store(config)
    items = resolve.find_conflicts(store)
    if not items:
        print("no sync conflicts to resolve.")
        return 0

    if args.apply and not _confirm(
        f"merge {len(items)} conflict(s) into the live copies?", args.yes
    ):
        return 1

    report = resolve.resolve_all(store, apply=args.apply)
    _print_resolve(report, apply=args.apply, verbose=args.verbose)
    if args.apply:
        return 1 if report.skipped else 0
    return 0


def _print_resolve(
    report: resolve.ResolveReport, *, apply: bool, verbose: bool
) -> None:
    verb = "merged" if apply else "would merge"
    print(f"{verb} {len(report.resolutions)} conflict(s):")
    for res in report.resolutions:
        flag = "  ⚠ whole-file replace" if res.loud else ""
        clean = "" if res.changed else "  (identical copy — cleared)"
        print(f"  {res.kind:11} {res.name}{flag}{clean}")
        shown = res.decisions if verbose else res.contested
        for decision in shown:
            print(f"      {_decision_line(decision)}")
    if report.skipped:
        print(f"\n{len(report.skipped)} left for a retry (changed mid-resolve):")
        for res in report.skipped:
            print(f"  {res.kind:11} {res.name}")
    if not apply:
        print("\n(dry run — nothing written; re-run with --apply to merge.)")


def _decision_line(decision: FieldDecision) -> str:
    winner = decision.winner.value if decision.winner else "ours"
    if decision.action in ("lww", "tie"):
        return (
            f"~ {decision.field}: {decision.ours!r} vs {decision.theirs!r} "
            f"→ kept {winner} ({decision.action})"
        )
    if decision.action == "fill":
        return f"+ {decision.field}: filled from {winner}"
    if decision.action == "union":
        return f"∪ {decision.field}: merged both"
    return f"= {decision.field}: agreed"


def cmd_export(args: argparse.Namespace) -> int:
    """Copy (or symlink) a bundle's files into an external folder."""
    from dossier import export

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
    from dossier import organize

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
    """``raw`` as a POSIX path relative to the syncthing root, or None if outside it.

    An absolute path is used as-is. A **relative** path is resolved against the
    shell's current directory first (the usual shell meaning) and only then against
    the root — so ``ds import Marine`` still targets the store's ``Marine`` folder
    from anywhere, but ``ds import ./Docs`` isn't doubled onto the root when the root
    already *is* ``…/Docs`` (running it from the parent no longer yields a phantom
    ``Docs/Docs``).
    """
    root = config.syncthing_root.resolve()
    path = Path(raw).expanduser()
    if path.is_absolute():
        candidates = [path]
    else:
        candidates = [Path.cwd() / path, config.syncthing_root / path]
    for candidate in candidates:
        try:
            rel = candidate.resolve().relative_to(root)
        except ValueError:
            continue  # not inside the root — try the next interpretation
        return rel.as_posix()
    return None


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
    from dossier import intake

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
    # A progress bar with an ETA for the slow (VLM) read pass; disabled off a TTY
    # so piped/redirected output stays plain (and tests stay clean).
    progress = _intake_progress()
    with progress:
        task = progress.add_task("reading", total=len(pending))
        write = functools.partial(progress.console.print, soft_wrap=True, markup=False)
        for rel in pending:
            progress.update(task, description=_progress_name(rel))
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
                write(f"  skip  {rel}  (scan failed: {exc})")
                progress.advance(task)
                continue
            if cache.get(rel) is not prior:  # fresh reading — persist for a resume
                store.save_intake_cache(cache)
            proposals.append(proposal)
            _print_proposal(proposal, write)
            progress.advance(task)

    if not proposals:
        return 1
    if not apply:
        print(f"\n{len(proposals)} proposal(s) (dry run; pass --apply to file).")
        return 0
    if not _confirm(f"file these {len(proposals)} document(s)?", yes):
        return 1

    filed = 0
    progress = _intake_progress()
    with progress:
        task = progress.add_task("filing", total=len(proposals))
        write = functools.partial(progress.console.print, soft_wrap=True, markup=False)
        for proposal in proposals:
            progress.update(task, description=_progress_name(proposal.dst_rel))
            try:
                doc, errors = intake.apply_proposal(proposal, store, config)
            except IntakeError as exc:
                write(f"  error {proposal.name}: {exc}")
                progress.advance(task)
                continue
            for message in errors:
                write(f"  warn  {message}")
            if cache.pop(proposal.src_rel, None) is not None:  # now in scans.toml
                store.save_intake_cache(cache)
            write(f"  filed {doc.id}  ({proposal.dst_rel})")
            filed += 1
            progress.advance(task)
    print(f"\nfiled {filed} document(s).")
    return 0


def _intake_progress():
    """A Rich progress bar (bar · count · % · elapsed · ETA) for a long intake pass.

    Disabled when stdout isn't a TTY, so ``ds import`` piped to a file or a test
    harness emits only the plain per-file lines, no live-render control codes.
    """
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TaskProgressColumn,
        TextColumn,
        TimeElapsedColumn,
        TimeRemainingColumn,
    )

    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TaskProgressColumn(),
        TextColumn("· elapsed"),
        TimeElapsedColumn(),
        TextColumn("· eta"),
        TimeRemainingColumn(),
        disable=not sys.stdout.isatty(),
    )


def _progress_name(rel: str, width: int = 40) -> str:
    """The file's basename, truncated, for the progress bar's description column."""
    name = rel.rsplit("/", 1)[-1]
    return name if len(name) <= width else name[: width - 1] + "…"


def _print_proposal(
    p: intake.IntakeProposal, write: Callable[[str], None] = print
) -> None:
    write(f"\n{p.src_rel}")
    if p.duplicate is not None:
        kind = "exact duplicate of" if p.duplicate.exact else "subset of"
        write(f"  copy    {kind} {p.duplicate.doc_id}  (fold in the TUI, or file new)")
    write(f"  name    {p.doc.name}  (id {p.doc.id})")
    if p.doc.tags:
        write(f"  tags    {' '.join(p.doc.tags)}")
    if p.doc.issue_date:
        write(f"  issue   {p.doc.issue_date}")
    if p.doc.expiry_date:
        write(f"  expiry  {p.doc.expiry_date}")
    if p.doc.notes:
        write(f"  notes   {p.doc.notes.splitlines()[-1]}")
    if p.succession is not None:
        conf = p.succession.confidence
        write(f"  renews  {p.succession.older}  (conf {conf:.2f})")
    dst = p.dst_rel + (f"  [{','.join(p.notes)}]" if p.notes else "")
    write(f"  file    {p.src_rel}  {'->' if p.moves else '= (in place)'}  {dst}")
    for q in p.open_questions:
        write(f"  ?       {q.field.value}: {' / '.join(q.values)}  (pick in the pane)")
    write(f"  read    conf {p.reading.confidence:.2f}, model {p.reading.model}")


def cmd_expiring(args: argparse.Namespace) -> int:
    """List documents needing attention — plain text for a scheduled reminder.

    Empty stdout when nothing is due (so a cron/Task-Scheduler notification is
    clean). Exit 0 = clean · 1 = at least one line · 2 = error, so a scheduled job
    can tell "nag me" from "the tool is broken".
    """
    from dossier import preparedness

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


def cmd_ask(args: argparse.Namespace) -> int:
    """Answer a question from the records — retrieval-first, no model (Phase 11)."""
    from dossier import answers

    config = _load_config()
    if config is None:
        return 2
    question = " ".join(args.question).strip()
    if not question:
        print("error: ask what? give a question", file=sys.stderr)
        return 2
    store = Store(config)
    result = answers.answer(
        question,
        store.load_all(),
        store.load_scans(),
        store.load_locations(),
        today=date.today(),
        k=args.limit,
    )
    for line in result.lines:
        print(line)
    return 0 if result.answered else 1


def cmd_open(args: argparse.Namespace) -> int:
    """Open the document that best matches a query (or list ties)."""
    from dossier import answers

    config = _load_config()
    if config is None:
        return 2
    term = " ".join(args.query).strip()
    if not term:
        print("error: open what? give a search term", file=sys.stderr)
        return 2
    store = Store(config)
    docs = store.load_all()
    by_id = {d.id: d for d in docs}
    corpus = answers.build_corpus(docs, store.load_scans())
    ranked = answers.rank(corpus, answers.residue(term), k=5)
    if not ranked:
        print(f"no match for '{term}'.", file=sys.stderr)
        return 1

    top_id, top_score = ranked[0]
    ambiguous = len(ranked) > 1 and ranked[1][1] >= 0.8 * top_score
    if ambiguous:
        print(f"'{term}' is ambiguous — {len(ranked)} matches:")
        for doc_id, score in ranked:
            doc = by_id.get(doc_id)
            if doc is not None:
                print(f"  {doc.id}  {doc.name or doc.id}  ({score:.1f})")
        return 1

    doc = by_id[top_id]
    rendition = doc.primary_rendition()
    if rendition is None:
        print(f"{doc.id}: no linked file to open", file=sys.stderr)
        return 1
    path = query.resolve_path(config.syncthing_root, rendition.path)
    if args.dry_run:
        print(f"{doc.id}  {doc.name or doc.id}\n  {path}")
        return 0
    try:
        open_file(path)
    except OpenError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"opened {doc.id}  ({doc.name or doc.id})")
    return 0


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
    if args.transcribe:
        return _transcribe_pass(store, config, force=args.force, limit=args.limit)
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


def _transcribe_pass(store: Store, config: Config, *, force: bool, limit: int) -> int:
    """Add a full-text transcript to each linked doc's reading (Phase 11 slice C).

    A batch enrichment: interactive scans stay fast (extract only); this second VLM
    pass fills in transcript + keywords for content search. Persists after each so a
    big backfill is resumable. Docs without a reading yet are skipped (run `ds scan`).
    """
    readings = store.load_scans()
    linked = [d for d in store.load_all() if d.primary_rendition() is not None]
    done = skipped = missing = no_reading = failed = 0
    for doc in linked:
        reading = readings.get(doc.id)
        if reading is None:
            no_reading += 1
            continue
        if reading.transcript and not force:
            skipped += 1
            continue
        rendition = doc.primary_rendition()
        assert rendition is not None
        path = query.resolve_path(config.syncthing_root, rendition.path)
        if not path.exists():
            missing += 1
            continue
        if limit and done >= limit:
            break
        try:
            transcript, keywords = scan.transcribe(path, config)
        except ScanError as exc:
            print(f"  ! {doc.id}: {exc}", file=sys.stderr)
            failed += 1
            continue
        readings[doc.id] = replace(reading, transcript=transcript, keywords=keywords)
        store.save_scans(readings)  # persist after each (resumable)
        done += 1
        print(f"  + {doc.id}: {len(transcript)} chars, {len(keywords)} keywords")
    print(
        f"transcribed {done}, skipped {skipped} (have transcript), "
        f"{no_reading} without a reading, {missing} missing, {failed} failed"
    )
    return 0


def cmd_service_run(_args: argparse.Namespace) -> int:
    """Run one background scan pass now — power-gated, single-instance locked.

    Exits 0 on a clean pass *and* on any gated/locked skip (so a scheduler never
    nags), 1 if items failed, 2 on a config error. This is what the installed
    Scheduled Task / systemd timer invokes.
    """
    from dossier import service

    config = _load_config()
    if config is None:
        return 2
    result = service.run_service(Store(config), config)
    print(result.summary())
    _append_service_log(result.summary())
    return result.exit_code


def _append_service_log(line: str) -> None:
    """Best-effort append of a run summary to the per-device service log."""
    import platformdirs

    try:
        log_dir = Path(platformdirs.user_log_dir("dossier", appauthor=False))
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).isoformat(timespec="seconds")
        with (log_dir / "scan-service.log").open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {line}\n")
    except OSError:
        pass


def cmd_service_install(args: argparse.Namespace) -> int:
    """Show how to install the scan service; register it only with ``--yes``.

    Build-but-don't-run: the default prints the resolved command, the full
    generated artifact(s), and the exact registration commands — and changes
    nothing. ``--yes`` writes the artifacts and registers the task/timer.
    """
    from dossier import service_install

    if _load_config() is None:
        return 2
    plan = service_install.plan_install()
    if not plan.supported:
        print(f"the scan service is not available here: {plan.note}", file=sys.stderr)
        return 2
    _print_install_plan(plan, verb="install")
    return _apply_or_dry_run(plan, apply=args.yes, verb="installed")


def cmd_service_uninstall(args: argparse.Namespace) -> int:
    """Show how to remove the scan service; do it only with ``--yes``."""
    from dossier import service_install

    if _load_config() is None:
        return 2
    plan = service_install.plan_uninstall()
    if not plan.supported:
        print("nothing to uninstall (the service is desktop-only).", file=sys.stderr)
        return 2
    _print_install_plan(plan, verb="uninstall")
    return _apply_or_dry_run(plan, apply=args.yes, verb="uninstalled")


def _print_install_plan(plan: service_install.InstallPlan, *, verb: str) -> None:
    if plan.run_command:
        print(f"run command : {' '.join(plan.run_command)}")
    for artifact in plan.artifacts:
        print(f"\n--- {artifact.path} ---")
        print(artifact.content.rstrip("\n"))
    for target in plan.removes:
        print(f"remove file : {target}")
    if plan.commands:
        print(f"\ncommands ({verb}):")
        for argv in plan.commands:
            print(f"  {' '.join(argv)}")
    if plan.note:
        print(f"\nnote: {plan.note}")


def _apply_or_dry_run(
    plan: service_install.InstallPlan, *, apply: bool, verb: str
) -> int:
    from dossier import service_install

    if not apply:
        print(
            "\n(dry run — nothing written or registered; re-run with --yes, "
            "or run the commands above yourself.)"
        )
        return 0
    for line in service_install.apply(plan):
        print(f"  {line}")
    print(f"\n{verb}.")
    return 0


def cmd_service_status(_args: argparse.Namespace) -> int:
    """Report the live power decision, artifact presence, and registration state."""
    from dossier import power, service_install

    config = _load_config()
    if config is None:
        return 2
    sample = power.read_sample()
    decision = power.decide(sample, assume_ac=config.service_assume_ac)
    verdict = "would run" if decision.run else "would skip"
    print(
        f"power   : {sample.source} · on_ac={sample.on_ac} saver={sample.saver} "
        f"→ {verdict} ({decision.reason})"
    )
    plan = service_install.plan_install()
    for artifact in plan.artifacts:
        state = "present" if artifact.path.exists() else "absent"
        print(f"artifact: {artifact.path} ({state})")
    query_cmd = service_install.status_query_command()
    if query_cmd is not None:
        import subprocess

        try:
            result = subprocess.run(
                query_cmd, capture_output=True, text=True, check=False
            )
            registered = "registered" if result.returncode == 0 else "not registered"
        except OSError as exc:
            registered = f"could not query ({exc})"
        print(f"schedule: {registered}  ({' '.join(query_cmd)})")
    return 0


def cmd_service(_args: argparse.Namespace) -> int:
    """Bare ``ds service`` — point at the subcommands."""
    print("usage: ds service {run | install | uninstall | status}")
    return 2


def cmd_profile(args: argparse.Namespace) -> int:
    """Time startup + data-load to locate performance bottlenecks (read-only)."""
    from dossier import profiling

    try:
        config: Config | None = Config.load()
    except ConfigError:
        config = None  # imports/environment section still works without a store
    return profiling.run(config, runs=args.runs, importtime=args.importtime)


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
        "--glyphs",
        choices=("nerd", "ascii"),
        default=None,
        help="icon set (skips the icon question)",
    )
    init_p.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="accept defaults and ask nothing (for scripts)",
    )
    init_p.add_argument("--force", action="store_true", help=argparse.SUPPRESS)
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
        help="find orphan files, missing files, and duplicate clusters (--dedup)",
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

    resolve_p = sub.add_parser(
        "resolve",
        help="merge Syncthing conflict files back into the live copies",
    )
    resolve_p.add_argument(
        "--apply",
        action="store_true",
        help="perform the merges (default: a dry-run report)",
    )
    resolve_p.add_argument(
        "--yes",
        action="store_true",
        help="skip the confirmation prompt (with --apply)",
    )
    resolve_p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="show every field decision, not just contested ones",
    )
    resolve_p.set_defaults(func=cmd_resolve)

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

    ask_p = sub.add_parser(
        "ask", help="answer a question from the records (retrieval-first, no model)"
    )
    ask_p.add_argument("question", nargs="+", help="the question (any words)")
    ask_p.add_argument(
        "--limit", type=int, default=3, metavar="N", help="top matches to consider"
    )
    ask_p.set_defaults(func=cmd_ask)

    open_p = sub.add_parser(
        "open", help="open the document best matching a query (content-aware)"
    )
    open_p.add_argument("query", nargs="+", help="search term (name / tags / scan)")
    open_p.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="print the match + path without opening",
    )
    open_p.set_defaults(func=cmd_open)

    scan_p = sub.add_parser(
        "scan",
        help="read linked scans with the vision model into readings (needs [scan])",
    )
    scan_p.add_argument(
        "--force", action="store_true", help="re-read even unchanged files"
    )
    scan_p.add_argument(
        "--transcribe",
        action="store_true",
        help="batch-add full-text transcripts to existing readings (content search)",
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

    service_p = sub.add_parser(
        "service",
        help="the background scan service (desktop only; power-gated)",
    )
    service_p.set_defaults(func=cmd_service)
    service_sub = service_p.add_subparsers(
        dest="service_command", metavar="<subcommand>"
    )
    service_run_p = service_sub.add_parser(
        "run",
        help="run one batch pass now (scan + transcribe + intake; power-gated, locked)",
    )
    service_run_p.set_defaults(func=cmd_service_run)
    service_install_p = service_sub.add_parser(
        "install",
        help="show how to install the auto-scan task/timer (registers only with --yes)",
    )
    service_install_p.add_argument(
        "--yes",
        action="store_true",
        help="actually write the artifacts and register (default: print the plan)",
    )
    service_install_p.set_defaults(func=cmd_service_install)
    service_uninstall_p = service_sub.add_parser(
        "uninstall", help="show how to remove the auto-scan task/timer (--yes to do it)"
    )
    service_uninstall_p.add_argument(
        "--yes",
        action="store_true",
        help="actually unregister and remove the artifacts",
    )
    service_uninstall_p.set_defaults(func=cmd_service_uninstall)
    service_status_p = service_sub.add_parser(
        "status", help="show the live power decision, artifacts, and registration state"
    )
    service_status_p.set_defaults(func=cmd_service_status)

    profile_p = sub.add_parser(
        "profile",
        help="time startup + data-load to find performance bottlenecks (read-only)",
    )
    profile_p.add_argument(
        "--runs", type=int, default=3, help="best-of-N runs for the import timings"
    )
    profile_p.add_argument(
        "--importtime",
        action="store_true",
        help="also print the per-module import cost breakdown",
    )
    profile_p.set_defaults(func=cmd_profile)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        return cmd_tui(args)
    return func(args)
