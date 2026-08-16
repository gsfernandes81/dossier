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

"""Run the v2 → v3 journal export against a real store, and gate it on parity.

Dev-only (`tools/` is not shipped): this is the Phase R2 rehearsal, and it dies
with the cutover. Read-only with respect to the store — it never writes a
document, never touches the file tree, and never modifies `.dossier/`.

    uv run python tools/export_journal.py --dest ~/journal-rehearsal
    uv run python tools/export_journal.py --dest ~/journal-rehearsal --root /path/to/store

Two guards are enforced here rather than left to care:

1. **The destination may not be inside the Syncthing root.** Anything created
   there syncs by default (REWRITE.md §6 R2, §7), and a half-built journal
   reaching the phone before cutover is exactly the accident the plan is written
   to avoid. The check refuses rather than warns.
2. **Parity is the exit code.** Any field-level mismatch is a hard stop (§7), so
   a rehearsal that "mostly worked" cannot be mistaken for a green one — the
   script exits 1 and prints every difference.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dossier.config import Config  # noqa: E402
from dossier.export_journal import check_parity, export  # noqa: E402
from dossier.journal import fold, parse_body  # noqa: E402
from dossier.store import Store  # noqa: E402


def _inside(child: Path, parent: Path) -> bool:
    """Whether `child` is `parent` or lives under it, symlinks resolved."""
    try:
        child.resolve().relative_to(parent.resolve())
    except (ValueError, OSError):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--dest",
        required=True,
        type=Path,
        help="where to write the journal (must be OUTSIDE the Syncthing root)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=None,
        help="store root; defaults to this device's configured syncthing_root",
    )
    parser.add_argument("--writer", default="desk-core", help="meta writer id")
    parser.add_argument("--lab-writer", default="desk-lab", help="enrich writer id")
    args = parser.parse_args(argv)

    config = Config.load() if args.root is None else Config(syncthing_root=args.root)
    store = Store(config)

    if _inside(args.dest, config.syncthing_root):
        print(
            f"refusing to write inside the Syncthing root ({config.syncthing_root}).\n"
            "Journals must not exist in the synced tree before cutover — anything "
            "there syncs by default, and a half-built journal must never reach the "
            "phone early (REWRITE.md §6 R2, §7). Pick a destination outside it.",
            file=sys.stderr,
        )
        return 2

    exported = export(store, writer=args.writer, lab_writer=args.lab_writer)

    meta_dir = args.dest / "meta"
    enrich_dir = args.dest / "enrich"
    meta_dir.mkdir(parents=True, exist_ok=True)
    enrich_dir.mkdir(parents=True, exist_ok=True)
    meta_path = meta_dir / f"{args.writer}.jsonl"
    enrich_path = enrich_dir / f"{args.lab_writer}.jsonl"
    meta_path.write_text(exported.meta_body, encoding="utf-8")
    enrich_path.write_text(exported.enrich_body, encoding="utf-8")

    # Fold what was actually written, not what was held in memory: the bytes on
    # disk are what the Rust core will read.
    lines, torn = parse_body(meta_path.read_text(encoding="utf-8"))
    enrich_lines, enrich_torn = parse_body(enrich_path.read_text(encoding="utf-8"))
    folded = fold(lines + enrich_lines)

    print(f"wrote {meta_path} ({len(exported.meta)} ops, {meta_path.stat().st_size:,} bytes)")
    print(
        f"wrote {enrich_path} ({len(exported.enrich)} ops, "
        f"{enrich_path.stat().st_size:,} bytes)"
    )
    docs = sum(1 for ent, _ in folded.entities if ent == "doc")
    print(f"folded: {docs} documents, {folded.stats.folded} ops")

    if torn or enrich_torn:
        print("FAIL: the export produced a torn line — this is a bug", file=sys.stderr)
        return 1
    if folded.stats.has_anomalies:
        print(
            f"FAIL: the export folds with anomalies "
            f"(malformed {folded.stats.malformed}, orphaned {folded.stats.orphaned}, "
            f"duplicate keys {folded.stats.duplicate_keys})",
            file=sys.stderr,
        )
        return 1

    problems = check_parity(store, folded)
    if problems:
        print(f"\nFAIL: {len(problems)} parity mismatch(es) — cutover is blocked:")
        for problem in problems[:50]:
            print(f"  {problem}")
        if len(problems) > 50:
            print(f"  … and {len(problems) - 50} more")
        return 1

    print("parity: OK — every field round-trips through the journal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
