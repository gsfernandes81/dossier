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

"""The background scan service — one batch pass of `ds scan`, gated and locked.

`ds service run` performs a single pass: **power-gate** (never on battery or in a
power-saver mode — a hard requirement), **lock** (so two passes never overlap),
then scan → transcribe → intake, reusing the same engine seams as the interactive
commands. It exits **0** on a clean pass *and* on every gated/locked skip (so a
scheduler never pages the user for "not now"), **1** if items failed, **2** on a
config/environment error.

The installer that wires this into a Windows Scheduled Task / systemd timer is a
separate, explicit step (`ds service install`); this module is just the run-mode
it points at, so it's fully unit-testable with an injected power reading, a temp
lock dir, and a mocked VLM.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import platformdirs

from dossier import intake, power, query, scan
from dossier.config import APP_NAME, Config
from dossier.errors import IntakeError, ScanError
from dossier.power import PowerSample
from dossier.store import Store

_LOCK_NAME = "scan-service.lock"
# A lock older than this is treated as abandoned (a crashed run) and stolen. Well
# above any real pass — transcription is budgeted, so a pass is minutes, not hours.
_STALE_AFTER = timedelta(hours=6)


@dataclass(frozen=True)
class ServiceResult:
    """The outcome of one service pass — its ``summary()`` is the log line."""

    gate: str  # ok | battery | saver | unknown | locked | termux
    scanned: int = 0
    transcribed: int = 0
    intake: int = 0
    failed: int = 0
    exit_code: int = 0

    def summary(self) -> str:
        return (
            f"gate={self.gate} scanned={self.scanned} "
            f"transcribed={self.transcribed} intake={self.intake} "
            f"failed={self.failed} exit={self.exit_code}"
        )


def run_service(
    store: Store,
    config: Config,
    *,
    probe: Callable[[], PowerSample] = power.read_sample,
    now: Callable[[], datetime] | None = None,
    lock_dir: Path | None = None,
) -> ServiceResult:
    """One batch pass: power-gate → lock → scan + transcribe + intake.

    ``probe`` (the power reader), ``now`` (the clock, for lock staleness), and
    ``lock_dir`` are injectable so the whole thing is testable without hardware.
    """
    now = now or (lambda: datetime.now(UTC))
    from dossier.platform_open import is_termux

    if is_termux():
        return ServiceResult("termux")  # desktop-only by design; a no-op, exit 0

    decision = power.decide(probe(), assume_ac=config.service_assume_ac)
    if not decision.run:
        return ServiceResult(_gate_label(decision.reason))

    lock_path = (lock_dir or _default_lock_dir()) / _LOCK_NAME
    if not _acquire_lock(lock_path, now=now):
        return ServiceResult("locked")
    try:
        scanned, failed = _scan_pass(store, config)
        transcribed = intook = 0
        if _still_on_ac(probe, config):  # re-check before each expensive phase
            transcribed, tfailed = _transcribe_pass(store, config)
            failed += tfailed
            if _still_on_ac(probe, config):
                intook = _intake_pass(store, config)
        return ServiceResult(
            "ok", scanned, transcribed, intook, failed, 1 if failed else 0
        )
    finally:
        lock_path.unlink(missing_ok=True)


def _gate_label(reason: str) -> str:
    if "battery" in reason:
        return "battery"
    if "saver" in reason:
        return "saver"
    return "unknown"


def _still_on_ac(probe: Callable[[], PowerSample], config: Config) -> bool:
    """Re-sample mid-run so unplugging stops the pass gracefully (work persists)."""
    return power.decide(probe(), assume_ac=config.service_assume_ac).run


# -- the batch phases (reuse the interactive engine seams) -------------------


def _scan_pass(store: Store, config: Config) -> tuple[int, int]:
    """Extract readings for linked files whose fingerprint changed (like `ds scan`)."""
    existing = store.load_scans()
    readings = dict(existing)
    linked = [d for d in store.load_all() if d.primary_rendition() is not None]
    scanned = failed = 0
    for doc in linked:
        rendition = doc.primary_rendition()
        assert rendition is not None
        path = query.resolve_path(config.syncthing_root, rendition.path)
        if not path.exists():
            continue
        fingerprint = scan.file_fingerprint(path)
        if doc.id in existing and existing[doc.id].fingerprint == fingerprint:
            continue  # unchanged since the last pass
        try:
            reading = scan.extract(path, config)
        except ScanError:
            failed += 1
            continue
        readings[doc.id] = replace(reading, fingerprint=fingerprint)
        scanned += 1
    if scanned:
        store.save_scans(readings)
    return scanned, failed


def _transcribe_pass(store: Store, config: Config) -> tuple[int, int]:
    """Add transcripts to readings that lack one, up to the per-run budget."""
    limit = config.service_transcribe_limit
    readings = store.load_scans()
    linked = [d for d in store.load_all() if d.primary_rendition() is not None]
    done = failed = 0
    for doc in linked:
        if limit and done >= limit:
            break
        reading = readings.get(doc.id)
        if reading is None or reading.transcript:
            continue
        rendition = doc.primary_rendition()
        assert rendition is not None
        path = query.resolve_path(config.syncthing_root, rendition.path)
        if not path.exists():
            continue
        try:
            transcript, keywords = scan.transcribe(path, config)
        except ScanError:
            failed += 1
            continue
        readings[doc.id] = replace(reading, transcript=transcript, keywords=keywords)
        store.save_scans(readings)  # persist after each (resumable)
        done += 1
    return done, failed


def _intake_pass(store: Store, config: Config) -> int:
    """Compute (and, under ``intake = "file"``, file) proposals for inbox drops.

    Default ``propose`` policy computes the reading into the synced intake cache —
    the phone/other device then files it from the review card — so unattended
    filing stays opt-in, consistent with the app's dry-run-by-default spine.
    """
    if not config.intake_inbox:
        return 0
    pending = intake.pending_files(store, config)
    if not pending:
        return 0
    docs = store.load_all()
    readings = store.load_scans()
    cache = store.load_intake_cache()
    count = 0
    for rel in pending:
        try:
            proposal = intake.build_proposal(
                rel,
                store,
                config,
                docs=docs,
                readings=readings,
                in_place=True,
                cache=cache,
            )
        except ScanError:
            continue
        store.save_intake_cache(cache)  # persist the reading (resumable + synced)
        if config.service_intake == "file":
            try:
                intake.apply_proposal(proposal, store, config)
            except IntakeError:
                continue
            if cache.pop(proposal.src_rel, None) is not None:
                store.save_intake_cache(cache)
        count += 1
    return count


# -- the single-instance lock ------------------------------------------------


def _default_lock_dir() -> Path:
    """A local, non-synced directory for the lock (never inside `.dossier`)."""
    return Path(platformdirs.user_runtime_dir(APP_NAME, appauthor=False))


def _acquire_lock(path: Path, *, now: Callable[[], datetime]) -> bool:
    """Take the single-instance lock via ``O_EXCL``; steal it if abandoned.

    Returns True if we now hold the lock. A live lock (younger than
    :data:`_STALE_AFTER`) blocks; an older one is assumed crashed and stolen.
    """
    import os

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = f"{os.getpid()}\n{now().isoformat()}\n".encode()
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if not _lock_is_stale(path, now=now):
            return False
        path.unlink(missing_ok=True)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False  # lost a race to another starting instance
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
    return True


def _lock_is_stale(path: Path, *, now: Callable[[], datetime]) -> bool:
    """True if the lock's recorded timestamp is older than the stale window.

    An unreadable / unparseable lock is treated as stale — better to steal a
    corrupt lock than to wedge the service forever.
    """
    try:
        lines = path.read_text().splitlines()
        stamp = datetime.fromisoformat(lines[1])
    except (OSError, IndexError, ValueError):
        return True
    return now() - stamp > _STALE_AFTER
