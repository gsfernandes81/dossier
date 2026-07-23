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

"""Startup + data-load timing harness behind ``ds profile``.

The two slow paths users feel — launching the app and opening the Review screen —
are each a sum of very different costs: interpreter start, importing Textual and
the engine, reading document files, parsing their YAML front-matter, and walking
the folder tree. This splits them into measured buckets so a fix targets the real
cost, which on **Termux** differs wildly from a desktop (slow ARM imports, a store
on ``/sdcard`` behind Android's FUSE layer, or CPU-bound YAML parsing).

It is **read-only** — it times the real load paths but never writes to the store.
Import timings run in fresh subprocesses (a warm best-of-N) so they reflect a real
launch; the store timings run in-process. Run it with ``ds profile`` (add
``--importtime`` for the per-module import breakdown).
"""

from __future__ import annotations

import contextlib
import platform
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from dossier.config import Config

# (label, code) — timed in a fresh interpreter. `pass` is the floor (bare start);
# each import's own cost is roughly its line minus the `pass` line. dossier.tui is
# the one a bare `ds` pays to open the TUI.
_IMPORT_TARGETS = [
    ("bare interpreter (floor)", "pass"),
    ("+ PyYAML", "import yaml"),
    ("+ textual", "import textual.app"),
    ("dossier.store", "import dossier.store"),
    ("dossier.cli", "import dossier.cli"),
    ("dossier.tui  (TUI launch)", "import dossier.tui"),
]

_INTEGRITY_SKIP = frozenset({"sync-conflict", "missing-file"})


@dataclass
class StoreTimings:
    """In-process timings for the data the home + Review screens load."""

    documents_dir: str
    doc_count: int
    total_kib: float
    read_ms: float  # read every doc's bytes — I/O only, no parse (coldest pass)
    load_all_ms: float  # read + YAML-parse every doc (cache warm after read_ms)
    load_all_again_ms: float  # a second load_all — stable warm parse cost
    load_one_ms: float | None  # a single document parse
    scan_files_ms: float  # the reconcile orphan-scan folder walk
    scan_files_count: int
    reconcile_ms: float  # reconcile.run given the already-loaded docs
    doctor_ms: float  # the Integrity check given the already-loaded docs


# -- timing helpers ----------------------------------------------------------


def _fmt(ms: float | None) -> str:
    return "     n/a" if ms is None else f"{ms:8.1f} ms"


def _time_subprocess(code: str, runs: int) -> float | None:
    """Best-of-``runs`` wall-clock for ``python -c code`` in a fresh process."""
    best: float | None = None
    for _ in range(max(1, runs)):
        start = time.perf_counter()
        try:
            proc = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        elapsed = (time.perf_counter() - start) * 1000
        best = elapsed if best is None else min(best, elapsed)
    return best


def _importtime_top(n: int) -> list[tuple[float, str]]:
    """Top-``n`` cumulative-cost modules from ``-X importtime`` on the TUI import."""
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "importtime", "-c", "import dossier.tui"],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    rows: list[tuple[float, str]] = []
    for line in proc.stderr.splitlines():
        match = re.match(r"import time:\s+\d+ \|\s+(\d+) \|\s+(.*)", line)
        if match:
            rows.append((int(match.group(1)) / 1000, match.group(2).strip()))
    rows.sort(reverse=True)
    # Keep top-level-ish modules (≤1 dot) so the list reads as "who's expensive".
    return [row for row in rows if row[1].count(".") <= 1][:n]


def _profile_store(config: Config) -> StoreTimings | None:
    """Time the real read/parse/scan paths the screens use. Read-only."""
    from dossier import doctor, reconcile
    from dossier.store import Store

    store = Store(config)
    paths = list(store.iter_document_paths())
    if not paths:
        return None

    # Cold-ish byte read (I/O only) — also warms the OS page cache for the parses.
    start = time.perf_counter()
    total = 0
    for path in paths:
        with contextlib.suppress(OSError):
            total += len(path.read_bytes())
    read_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    docs = store.load_all()
    load_all_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    store.load_all()
    load_all_again_ms = (time.perf_counter() - start) * 1000

    load_one_ms: float | None = None
    try:
        start = time.perf_counter()
        store.load(paths[0].stem)
        load_one_ms = (time.perf_counter() - start) * 1000
    except Exception:
        load_one_ms = None

    start = time.perf_counter()
    scan = reconcile.scan_files(config)
    scan_files_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    reconcile.run(store, config, docs=docs)
    reconcile_ms = (time.perf_counter() - start) * 1000

    start = time.perf_counter()
    doctor.run(store, config, docs=docs, skip=_INTEGRITY_SKIP)
    doctor_ms = (time.perf_counter() - start) * 1000

    return StoreTimings(
        documents_dir=str(config.documents_dir),
        doc_count=len(paths),
        total_kib=total / 1024,
        read_ms=read_ms,
        load_all_ms=load_all_ms,
        load_all_again_ms=load_all_again_ms,
        load_one_ms=load_one_ms,
        scan_files_ms=scan_files_ms,
        scan_files_count=len(scan),
        reconcile_ms=reconcile_ms,
        doctor_ms=doctor_ms,
    )


# -- report ------------------------------------------------------------------


def _looks_fuse(path: str) -> bool:
    lowered = path.lower()
    return any(
        marker in lowered
        for marker in ("/sdcard", "/storage/emulated", "/mnt/media", "emulated/0")
    )


def _bytecode_state() -> str:
    import dossier

    if sys.dont_write_bytecode:
        return "disabled (PYTHONDONTWRITEBYTECODE / -B) — recompiles every run"
    pkg = getattr(dossier, "__file__", None)
    if pkg is None:
        return "unknown"
    cache = Path(pkg).parent / "__pycache__"
    warm = cache.is_dir() and any(cache.glob("*.pyc"))
    return "warm (.pyc present)" if warm else "cold (no .pyc — first run compiles)"


def run(config: Config | None, *, runs: int = 3, importtime: bool = False) -> int:
    """Print the profile report. ``config`` None → import/environment section only."""
    from dossier.platform_open import is_termux

    # The report is ASCII, but a stray non-ASCII path shouldn't crash it on a
    # legacy Windows codepage; emit UTF-8 (Termux's default) and never hard-fail.
    # getattr: reconfigure exists on a real TextIOWrapper, not a redirected stream.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if callable(reconfigure):
        with contextlib.suppress(Exception):
            reconfigure(encoding="utf-8", errors="replace")

    out: list[str] = []
    out.append("dossier performance profile")
    out.append("=" * 52)

    from dossier.store import HAS_LIBYAML, libyaml_hint

    out.append("\nenvironment")
    out.append(f"  platform     : {platform.platform()}")
    out.append(f"  python       : {platform.python_version()}  ({sys.executable})")
    out.append(f"  termux       : {is_termux()}")
    out.append(f"  optimize flag: -O level {sys.flags.optimize}")
    out.append(f"  bytecode     : {_bytecode_state()}")
    backend = "libyaml (C, fast)" if HAS_LIBYAML else "pure Python (slow)"
    out.append(f"  yaml backend : {backend}")
    hint = libyaml_hint()
    if hint:
        out.append(f"\n  ! {hint}")

    out.append(f"\nstartup imports  (fresh process, best of {runs})")
    imports = [(label, _time_subprocess(code, runs)) for label, code in _IMPORT_TARGETS]
    floor = imports[0][1] or 0.0
    for label, ms in imports:
        delta = (
            "" if ms is None or label.startswith("bare") else f"  (+{ms - floor:.0f})"
        )
        out.append(f"  {label:26} {_fmt(ms)}{delta}")
    out.append("  (+N = cost above the bare-interpreter floor)")

    if importtime:
        out.append("\nimport breakdown  (cumulative, top modules of dossier.tui)")
        top = _importtime_top(12)
        if not top:
            out.append("  (unavailable)")
        for cum, name in top:
            out.append(f"  {cum:8.1f} ms  {name}")

    if config is None:
        out.append("\nstore: no device config found — run `ds init` to profile loads.")
        print("\n".join(out))
        return 0

    timings = _profile_store(config)
    if timings is None:
        out.append("\nstore: no documents found to profile.")
        print("\n".join(out))
        return 0

    t = timings
    out.append(f"\nstore data  ({t.doc_count} docs, {t.total_kib:.0f} KiB)")
    out.append(f"  location            : {t.documents_dir}")
    out.append(f"  read bytes (serial) : {_fmt(t.read_ms)}   (reference only)")
    out.append(
        f"  load_all (parallel) : {_fmt(t.load_all_ms)}   (16-thread read + parse)"
    )
    out.append(f"  load_all again      : {_fmt(t.load_all_again_ms)}")
    out.append(f"  load one document   : {_fmt(t.load_one_ms)}")
    out.append(
        f"  scan_files (walk)   : {_fmt(t.scan_files_ms)}  ({t.scan_files_count})"
    )
    out.append(f"  reconcile.run(docs=): {_fmt(t.reconcile_ms)}")
    out.append(f"  doctor.run(docs=)   : {_fmt(t.doctor_ms)}  (Integrity tab)")

    out.append("\ndiagnosis")
    for line in _diagnose(t, imports):
        out.append(f"  • {line}")

    print("\n".join(out))
    return 0


def _diagnose(t: StoreTimings, imports: list[tuple[str, float | None]]) -> list[str]:
    """A few heuristic pointers at the dominant cost, for a fix to target.

    Opening Review costs ≈ ``load_all`` + ``reconcile.run`` (which itself contains
    the ``scan_files`` orphan walk); the Integrity tab's ``doctor.run`` is deferred.
    """
    notes: list[str] = []
    if _looks_fuse(t.documents_dir):
        notes.append(
            "store is under Android shared storage (FUSE) — every open/stat pays "
            "syscall overhead; the folder walk below is the clearest victim."
        )
    if t.reconcile_ms > t.load_all_ms and t.reconcile_ms > 300:
        notes.append(
            f"reconcile.run ({t.reconcile_ms:.0f} ms), mostly its orphan folder-walk "
            f"(scan_files, {t.scan_files_ms:.0f} ms), now dominates the Review-open "
            "load. Deferring the orphan scan to the Orphans tab (like Integrity) or "
            "caching the file tree is the next lever."
        )
    if t.read_ms > t.load_all_ms * 1.5 and t.read_ms > 300:
        notes.append(
            f"the parallel load_all ({t.load_all_ms:.0f} ms) is already well under a "
            f"serial read ({t.read_ms:.0f} ms) — the read parallelism is doing its "
            "job; load_all is not the bottleneck."
        )
    tui = next((ms for label, ms in imports if label.startswith("dossier.tui")), None)
    floor = imports[0][1] or 0.0
    if tui is not None and tui - floor > 1500:
        notes.append(
            "importing the app is a large share of startup — most is Textual "
            "itself (a framework floor); confirm the bytecode cache is warm above."
        )
    if not notes:
        notes.append("no single dominant cost stood out — send this report to compare.")
    return notes
