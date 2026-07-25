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

"""Integrity and review diagnostics for the store (drives ``ds doctor``).

Checks: Syncthing conflict files, location-slug referential integrity,
supersession integrity (dangling / self / cyclic ``supersedes`` links),
round-trip lint (files that would change on next save), missing rendition files,
ambiguous dates (2-digit-year dates whose day/month order can't be pinned down),
reconcile-sidecar consistency (a doc linking a folded duplicate; stale
suppressions), and Syncthing health over the REST API (advisory).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dossier import query, resolve
from dossier.config import Config
from dossier.errors import StoreError
from dossier.model import Document, Location
from dossier.store import Store, _hash

# Short recovery hints per check, shown under a group in the CLI/TUI doctor output.
# Kept here (not in the UI layers) so both surfaces stay in sync, and so the guidance
# reaches a device — e.g. a phone on Termux — that may not have the repo/docs cloned.
CHECK_HINTS: dict[str, str] = {
    "sync-conflict": (
        "Syncthing kept a `*.sync-conflict-*` copy after two devices edited the same "
        "record. dossier never loads these. To recover: diff the conflict file against "
        "the live one, merge anything you want to keep into the live file, then delete "
        "the conflict copy. (Prior versions are also backed up under the local history "
        "dir on save.) See docs/guide/sync-conflicts.md."
    ),
    "syncthing-unconfigured": (
        "dossier can't find Syncthing's REST API. Add a `[syncthing]` table to the "
        'per-device config with `apikey = "…"` (Syncthing GUI → Actions → Settings → '
        "API Key; on Android: Syncthing-Fork → Web GUI → Settings → API Key). Desktop "
        "usually autodetects from Syncthing's own config.xml. These checks are "
        "skipped, not failed."
    ),
    "syncthing-unreachable": (
        "Syncthing isn't answering, so these checks are skipped. Start Syncthing, or "
        "fix `address` in the `[syncthing]` config. Only a problem if you expected it "
        "to be running."
    ),
    "syncthing-auth": (
        "Syncthing answered but rejected the API key. Re-copy `apikey` from the GUI "
        "settings — it changes if Syncthing's config is reset."
    ),
    "syncthing-folder": (
        "No synced Syncthing folder contains the store. The synced folder should be an "
        "ancestor of the store (its `.stfolder` marker sits at the synced parent, not "
        "the store root). Share a folder that covers the store, or fix "
        "`syncthing_root`."
    ),
    "syncthing-paused": (
        "The Syncthing folder holding the store is paused — edits won't propagate. "
        "Resume it in Syncthing."
    ),
    "syncthing-versioning": (
        "File versioning is OFF on the Syncthing folder holding the store. Versioning "
        "is dossier's recovery net: it's what lets you undo a bad change that syncs "
        "between devices — including a Proton Drive revert arriving via Syncthing. "
        "Enable Staggered File Versioning on this folder (Folder → Edit → File "
        "Versioning), on every device."
    ),
    "syncthing-unshared": (
        "The Syncthing folder holding the store isn't shared with any other device, so "
        "nothing is actually syncing. Share it with your other device(s)."
    ),
    "syncthing-connectivity": (
        "No sync devices are currently connected. Sync resumes when both ends are "
        "online — only a last-seen of days really deserves a look."
    ),
    "reconcile-folded-link": (
        "A document links a file you folded as a duplicate in the reconcile screen. "
        "The copy still exists (folding never deletes), but the document points at "
        "the redundant one — re-link the rendition to the kept file, or unfold the "
        "cluster if they aren't actually the same file."
    ),
    "reconcile-stale": (
        "A `.dossier/reconcile.toml` suppression points at a file that's no longer on "
        "disk — a harmless leftover from a moved or deleted file. Prune it by "
        "re-running the relevant reconcile decision, or hand-edit reconcile.toml to "
        "drop the entry."
    ),
}


@dataclass(frozen=True)
class Finding:
    check: str
    subject: str  # a document id, or a file path for conflict findings
    detail: str
    # "warn" = a problem to act on (the default, so every existing finding keeps its
    # meaning); "info" = advisory/skipped (e.g. Syncthing not configured) — surfaced
    # apart so it never drowns out real warnings or reads as a failure.
    severity: str = "warn"


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def by_check(self) -> dict[str, list[Finding]]:
        grouped: dict[str, list[Finding]] = {}
        for finding in self.findings:
            grouped.setdefault(finding.check, []).append(finding)
        return grouped


def run(
    store: Store,
    config: Config,
    *,
    skip: frozenset[str] = frozenset(),
    docs: list[Document] | None = None,
) -> Report:
    """Run every integrity check and collect the findings.

    ``skip`` drops findings of the named checks (see :class:`Finding.check`). The
    two heaviest — ``sync-conflict`` and ``missing-file`` — are also short-circuited
    when skipped so they never run; a final filter honours any other skipped kind.
    The Review screen's Integrity tab skips those two because its Conflicts and
    Missing tabs already own them, and skips ``syncthing`` so the tab stays offline
    (the home's live sync glyph is the TUI's window on it). ``docs`` reuses an
    already-loaded document list
    (findings then describe those docs *as loaded*) instead of a fresh
    :meth:`Store.load_all`; omit it — as the CLI does — to check the live store.
    """
    report = Report()
    if "sync-conflict" not in skip:
        report.findings += _check_conflicts(store)

    docs = store.load_all() if docs is None else docs
    locations = store.load_locations()
    report.findings += _check_location_refs(docs, locations)
    report.findings += _check_supersession(docs)
    report.findings += _check_round_trip(store, docs)
    if "missing-file" not in skip:
        report.findings += _check_files(docs, config.syncthing_root)
    report.findings += _check_dates(docs)
    report.findings += _check_reconcile(store, config, docs)
    if "syncthing" not in skip:  # a network group; the Review tab skips it wholesale
        report.findings += _check_syncthing(config)
    if skip:
        report.findings = [f for f in report.findings if f.check not in skip]
    return report


def _check_syncthing(config: Config) -> list[Finding]:
    """Syncthing REST diagnostics (Phase 15).

    Reachability problems are advisory (``info``): a device with no Syncthing
    configured, or one that just isn't running, is not a broken store — doctor says
    so and moves on. The real warnings are about the *synced folder* holding the
    store, chiefly file versioning being off (the recovery net).
    """
    from dossier import syncthing

    status = syncthing.query_status(config)
    state = status.state
    if state is syncthing.SyncState.UNCONFIGURED:
        running = syncthing.probe_health(
            "http://" + syncthing.DEFAULT_ADDRESS
        ) or syncthing.probe_health("https://" + syncthing.DEFAULT_ADDRESS)
        detail = (
            f"Syncthing is running at {syncthing.DEFAULT_ADDRESS} but dossier has no "
            "API key"
            if running
            else "no Syncthing REST API configured"
        )
        return [Finding("syncthing-unconfigured", "syncthing", detail, "info")]
    if state is syncthing.SyncState.UNREACHABLE:
        detail = status.error or "not answering"
        return [Finding("syncthing-unreachable", "syncthing", detail, "info")]
    if state is syncthing.SyncState.UNAUTHORIZED:
        detail = status.error or "API key rejected"
        return [Finding("syncthing-auth", "syncthing", detail, "warn")]

    out: list[Finding] = []
    folder = status.store_folder
    if folder is None:
        out.append(
            Finding(
                "syncthing-folder",
                str(config.syncthing_root),
                "no synced folder contains the store",
            )
        )
    else:
        name = folder.label or folder.id
        if folder.paused:
            out.append(Finding("syncthing-paused", name, "folder is paused"))
        if not folder.versioning:
            out.append(Finding("syncthing-versioning", name, "file versioning is off"))
        if folder.shared_with == 0:
            out.append(
                Finding("syncthing-unshared", name, "not shared with any other device")
            )
    if status.total_devices > 0 and status.connected_devices == 0:
        out.append(
            Finding(
                "syncthing-connectivity",
                "syncthing",
                f"0 of {status.total_devices} device(s) connected",
                "info",
            )
        )
    return out


def _check_conflicts(store: Store) -> list[Finding]:
    """Preview what ``ds resolve`` would do with each Syncthing conflict file.

    Read-only: plans the merge (no writes) so the finding names the contested
    fields — or says it auto-merges cleanly — rather than just flagging a file.
    """
    out: list[Finding] = []
    items = resolve.find_conflicts(store)
    handled = {item.conflict_path for item in items}
    for item in items:
        try:
            planned = resolve.plan(store, item)
        except StoreError as exc:
            out.append(
                Finding("sync-conflict", item.conflict_path.name, f"unreadable: {exc}")
            )
            continue
        if planned.loud:
            detail = f"{item.name}: whole-file replace on resolve"
        elif planned.contested:
            fields = ", ".join(d.field for d in planned.contested)
            count = len(planned.contested)
            detail = f"{item.name}: {count} contested field(s): {fields}"
        else:
            detail = f"{item.name}: auto-merges cleanly — run `ds resolve`"
        out.append(Finding("sync-conflict", item.conflict_path.name, detail))
    for path in store.list_conflicts():  # anything we can't classify, still surface
        if path not in handled:
            out.append(
                Finding(
                    "sync-conflict",
                    path.name,
                    f"{path} (unrecognised — resolve by hand)",
                )
            )
    return out


def _check_location_refs(
    docs: list[Document], locations: dict[str, Location]
) -> list[Finding]:
    out: list[Finding] = []
    for doc in docs:
        for kind, slug in (("perm", doc.perm_location), ("temp", doc.temp_location)):
            if slug and slug not in locations:
                out.append(
                    Finding(
                        "location-ref",
                        doc.id,
                        f"{kind}_location {slug!r} is not a known location",
                    )
                )
    return out


def _check_supersession(docs: list[Document]) -> list[Finding]:
    """Referential integrity of ``supersedes`` links: dangling, self, cyclic.

    Each document supersedes at most one other, so the graph is functional and
    cycles are cheap to find by walking each link chain to its end.
    """
    ids = {doc.id for doc in docs}
    by_id = {doc.id: doc for doc in docs}
    out: list[Finding] = []

    for doc in docs:
        target = doc.supersedes
        if target and target != doc.id and target not in ids:
            out.append(
                Finding(
                    "supersession",
                    doc.id,
                    f"supersedes {target!r}, which is not a known document",
                )
            )

    reported: set[str] = set()
    for start in docs:
        if start.id in reported:
            continue
        path: list[str] = []
        current: str | None = start.id
        while current is not None and current in by_id and current not in path:
            path.append(current)
            current = by_id[current].supersedes
        if current is None or current not in path:
            continue  # chain ended or ran into a dangling link — no cycle
        cycle = path[path.index(current) :]
        reported.update(cycle)
        if len(cycle) == 1:
            out.append(Finding("supersession", cycle[0], "document supersedes itself"))
        else:
            joined = " -> ".join(cycle)
            out.append(
                Finding(
                    "supersession",
                    cycle[0],
                    f"supersession cycle: {joined} -> {cycle[0]}",
                )
            )
    return out


def _check_round_trip(store: Store, docs: list[Document]) -> list[Finding]:
    # Compare each doc's canonical serialisation against ``source_hash`` — the hash
    # of the bytes it was parsed from, captured at load (store._read). That answers
    # "would this file change on the next save?" without re-reading it: on a synced
    # network store the per-doc read was the whole cost. ``_hash`` is imported (not
    # duplicated) so this stays in lockstep with how the store computes source_hash.
    out: list[Finding] = []
    for doc in docs:
        if doc.source_hash is None:
            continue  # never loaded/saved — nothing to compare against
        if _hash(store.serialize(doc).encode("utf-8")) != doc.source_hash:
            out.append(
                Finding(
                    "round-trip",
                    doc.id,
                    "file would change on next save (hand-edited or legacy format)",
                )
            )
    return out


def _check_files(docs: list[Document], root: Path) -> list[Finding]:
    out: list[Finding] = []
    for doc in docs:
        for rendition in doc.files:
            if not query.resolve_path(root, rendition.path).exists():
                out.append(
                    Finding(
                        "missing-file",
                        doc.id,
                        f"linked file not found: {rendition.path}",
                    )
                )
    return out


def _check_reconcile(
    store: Store, config: Config, docs: list[Document]
) -> list[Finding]:
    """Reconcile-sidecar consistency (``.dossier/reconcile.toml``).

    Two low-noise checks: a document that still links a file the user *folded* as a
    duplicate (it should point at the kept copy instead — a ``warn``), and sidecar
    suppressions whose file has since left the disk (harmless cruft to prune —
    ``info``). Both are read-only; folding never deletes a real file.
    """
    state = store.load_reconcile()
    root = config.syncthing_root
    out: list[Finding] = []

    # Each folded subset path → the kept (superset) path it was folded under.
    folded_under: dict[str, str] = {}
    for keep, subsets in state.folded.items():
        for subset in subsets:
            folded_under.setdefault(subset, keep)
    for doc in docs:
        for rendition in doc.files:
            keep = folded_under.get(rendition.path)
            if keep is not None:
                out.append(
                    Finding(
                        "reconcile-folded-link",
                        doc.id,
                        f"links {rendition.path}, folded as a duplicate of {keep}",
                    )
                )

    # Stale suppressions: a dismissed orphan or a folded keep no longer on disk.
    for path in sorted(state.dismissed):
        if not query.resolve_path(root, path).exists():
            out.append(
                Finding(
                    "reconcile-stale",
                    path,
                    "dismissed orphan is gone from disk",
                    "info",
                )
            )
    for keep in sorted(state.folded):
        if not query.resolve_path(root, keep).exists():
            out.append(
                Finding(
                    "reconcile-stale", keep, "folded keep is gone from disk", "info"
                )
            )
    return out


# -- ambiguous dates ---------------------------------------------------------

# An all-numeric 3-part date with a 2-digit year at the end position.
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2})\b")


def _readings(a: int, b: int, c: int) -> set[date]:
    """Distinct valid dates for a numeric token across plausible orderings.

    Guards both axes: day/month order (``DD-MM-YY`` vs ``MM-DD-YY``) AND year
    position (``21-08-23`` is 2023-08-21 as ``DD-MM-YY`` but 2021-08-23 as
    ``YY-MM-DD``). More than one distinct valid reading ⇒ ambiguous.
    """
    out: set[date] = set()
    for year, month, day in (
        (2000 + c, b, a),  # DD-MM-YY
        (2000 + c, a, b),  # MM-DD-YY
        (2000 + a, b, c),  # YY-MM-DD
    ):
        try:
            out.add(date(year, month, day))
        except ValueError:
            continue
    return out


def _ambiguous_tokens(name: str) -> list[str]:
    """Numeric 2-digit-year tokens with more than one plausible reading."""
    return [token for token, _ in candidate_readings(name)]


def candidate_readings(name: str) -> list[tuple[str, list[date]]]:
    """Ambiguous numeric date tokens in ``name`` with their sorted readings.

    Used by the TUI to offer the candidate dates when resolving an ambiguity.
    """
    out: list[tuple[str, list[date]]] = []
    for match in _NUMERIC_DATE.finditer(name):
        a, b, c = int(match.group(1)), int(match.group(2)), int(match.group(3))
        readings = sorted(_readings(a, b, c))
        if len(readings) >= 2:
            out.append((match.group(0), readings))
    return out


def _span_resolves_order(doc: Document) -> bool:
    # A well-ordered issue < expiry pair means the day/month reading is
    # self-consistent, so we don't flag it for order review.
    return (
        doc.issue_date is not None
        and doc.expiry_date is not None
        and doc.issue_date < doc.expiry_date
    )


def _check_dates(docs: list[Document]) -> list[Finding]:
    out: list[Finding] = []
    for doc in docs:
        if (
            doc.issue_date is not None
            and doc.expiry_date is not None
            and doc.issue_date > doc.expiry_date
        ):
            out.append(
                Finding(
                    "date-order",
                    doc.id,
                    f"issue {doc.issue_date} is after expiry {doc.expiry_date}",
                )
            )
        tokens = _ambiguous_tokens(doc.name)
        if tokens and not _span_resolves_order(doc):
            out.append(
                Finding(
                    "ambiguous-date",
                    doc.id,
                    f"2-digit-year date order unclear: {', '.join(tokens)}",
                )
            )
    return out
