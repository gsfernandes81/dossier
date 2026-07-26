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

Every path is split into a measured bucket — stat vs read vs parse, the parallel
``load_all``, the reconcile walk, and the per-keystroke search + row render — and
the numbers feed a **ranked recommendations** section that names *which* fix to
implement (parse cache, libyaml, paint-first load, low-power mode, lazy panes) with
each one's estimated payoff. So a single ``ds profile`` is enough to decide.

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

# Mirrors ReviewPane._INTEGRITY_SKIP: skip the conflict/missing tabs' checks and the
# network `syncthing` group so profiling the Integrity tab stays offline.
_INTEGRITY_SKIP = frozenset({"sync-conflict", "missing-file", "syncthing"})


@dataclass
class StoreTimings:
    """In-process timings for the data the home + Review screens load."""

    documents_dir: str
    doc_count: int
    total_kib: float
    read_ms: float  # read every doc's bytes — I/O only, no parse (coldest pass)
    stat_ms: float  # os.stat every doc file — the floor a mtime-keyed cache pays
    parse_ms: float  # YAML-parse already-read bytes — isolates parse from the read
    load_all_ms: float  # read + YAML-parse every doc (cache warm after read_ms)
    load_all_again_ms: float  # a second load_all — stable warm parse cost
    load_one_ms: float | None  # a single document parse
    scan_files_ms: float  # the reconcile orphan-scan folder walk
    scan_files_count: int
    reconcile_ms: float  # reconcile.run given the already-loaded docs
    doctor_ms: float  # the Integrity check given the already-loaded docs
    # Per-keystroke interactive cost on the home documents pane (None if unmeasured):
    search_ms: float | None  # a representative live-search filter over the store
    render_ms: float | None  # build the document rows a keystroke re-renders
    render_rows: int  # how many rows render_ms built (capped like the real pane)


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

    # Stat every file — the floor a mtime-keyed parse cache would pay on a reload
    # where nothing changed (check freshness, parse nothing). Compared against
    # load_all below, the gap is exactly what such a cache could save.
    start = time.perf_counter()
    for path in paths:
        with contextlib.suppress(OSError):
            path.stat()
    stat_ms = (time.perf_counter() - start) * 1000

    # Cold-ish byte read (I/O only) — also warms the OS page cache for the parses.
    # Keep the bytes so parse_ms below times parsing *without* re-paying the read.
    start = time.perf_counter()
    total = 0
    raws: list[tuple[Path, bytes]] = []
    for path in paths:
        with contextlib.suppress(OSError):
            data = path.read_bytes()
            total += len(data)
            raws.append((path, data))
    read_ms = (time.perf_counter() - start) * 1000

    # Parse the already-read bytes — isolates the YAML/front-matter cost from I/O.
    # This is the share a faster parser (libyaml) or a parse cache would remove.
    start = time.perf_counter()
    for path, data in raws:
        with contextlib.suppress(Exception):
            store._parse(path, data)
    parse_ms = (time.perf_counter() - start) * 1000

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

    search_ms, render_ms, render_rows = _profile_keystroke(config, docs)

    return StoreTimings(
        documents_dir=str(config.documents_dir),
        doc_count=len(paths),
        total_kib=total / 1024,
        read_ms=read_ms,
        stat_ms=stat_ms,
        parse_ms=parse_ms,
        load_all_ms=load_all_ms,
        load_all_again_ms=load_all_again_ms,
        load_one_ms=load_one_ms,
        scan_files_ms=scan_files_ms,
        scan_files_count=len(scan),
        reconcile_ms=reconcile_ms,
        doctor_ms=doctor_ms,
        search_ms=search_ms,
        render_ms=render_ms,
        render_rows=render_rows,
    )


def _profile_keystroke(
    config: Config, docs: list
) -> tuple[float | None, float | None, int]:
    """Time the work one keystroke in the documents pane does: filter the store,
    then rebuild the (capped) visible rows. Both are pure functions, so this runs
    without a live Textual app. Returns ``(search_ms, render_ms, rows)`` — the
    search/render halves are ``None`` if their (optional TUI) imports are absent.
    """
    from datetime import date

    from dossier import query

    threshold = config.expiry_threshold_days
    today = date.today()
    # A short, lowercase query like a real second-or-third keystroke.
    seed = next((d.name for d in docs if getattr(d, "name", "")), "doc")
    needle = "".join(c for c in seed.lower() if c.isalnum())[:3] or "doc"

    search_ms: float | None = None
    with contextlib.suppress(Exception):
        flt = query.Filter(text=needle, expiry=(), bundles=())
        start = time.perf_counter()
        query.search(docs, flt, today=today, threshold_days=threshold)
        search_ms = (time.perf_counter() - start) * 1000

    # The pane caps rendered rows (see HomeScreen._MAX_ROWS); mirror that so the
    # measurement matches what a keystroke actually rebuilds.
    render_ms: float | None = None
    shown = docs[:200]
    with contextlib.suppress(Exception):
        from dossier.tui import (
            glyphs as glyphset,
            rows as row_mod,
        )

        views = query.views(
            docs, root=config.syncthing_root, today=today, threshold_days=threshold
        )[:200]
        glyphs = glyphset.resolve(config.glyphs)
        start = time.perf_counter()
        for v in views:
            row_mod.doc_row(v, glyphs=glyphs)
        render_ms = (time.perf_counter() - start) * 1000

    return search_ms, render_ms, len(shown)


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
    out.append(f"  stat all files      : {_fmt(t.stat_ms)}   (mtime-cache floor)")
    out.append(f"  read bytes (serial) : {_fmt(t.read_ms)}   (I/O only)")
    out.append(f"  parse bytes (serial): {_fmt(t.parse_ms)}   (YAML only, no I/O)")
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
    out.append(f"  search / keystroke  : {_fmt(t.search_ms)}   (live filter)")
    out.append(
        f"  render {t.render_rows:>3} rows      : {_fmt(t.render_ms)}   (per keystroke)"
    )

    tui = next((ms for label, ms in imports if label.startswith("dossier.tui")), None)
    textual = next((ms for label, ms in imports if "textual" in label), None)
    out.append("\nrecommendations  (ranked by estimated payoff)")
    recs = _recommendations(
        t,
        backend_slow=not HAS_LIBYAML,
        fuse=_looks_fuse(t.documents_dir),
        bytecode_warm=_bytecode_state().startswith("warm"),
        floor_ms=floor,
        textual_ms=textual,
        tui_ms=tui,
    )
    for line in recs:
        out.append(f"  {line}")

    print("\n".join(out))
    return 0


# Felt-latency thresholds: below these a path isn't worth optimising; above, it is.
_FELT_RELOAD_MS = 150.0  # a reload/mount that blocks the UI longer than this is felt
_FELT_KEYSTROKE_MS = 60.0  # per-keystroke work above this makes fast typing lag
_WORTH_MS = 80.0  # a saving smaller than this isn't worth the change


def _recommendations(
    t: StoreTimings | None,
    *,
    backend_slow: bool,
    fuse: bool,
    bytecode_warm: bool,
    floor_ms: float,
    textual_ms: float | None,
    tui_ms: float | None,
) -> list[str]:
    """Turn the measurements into ranked, actionable recommendations — each tagged
    with its estimated payoff — so ``ds profile`` answers *which* fix to implement.

    Pure (no measuring, no I/O): the caller passes the timings and environment
    facts, so the mapping is unit-tested with synthetic numbers. Opening Review
    costs ≈ ``load_all`` + ``reconcile.run``; the home mount blocks on ``load_all``.
    """
    ranked: list[tuple[float, str]] = []  # (estimated ms saved, recommendation)
    if t is not None:
        cache_saving = t.load_all_ms - t.stat_ms
        if cache_saving > _WORTH_MS:
            ranked.append(
                (
                    cache_saving,
                    f"parse cache — a no-change reload could drop"
                    f" ~{t.load_all_ms:.0f}→{t.stat_ms:.0f} ms: an mtime-keyed cache"
                    " skips re-parsing unchanged files",
                )
            )
        if backend_slow and t.parse_ms > _WORTH_MS:
            ranked.append(
                (
                    t.parse_ms,
                    f"install libyaml (`pkg install libyaml`) — drops"
                    f" ~{t.parse_ms:.0f} ms of pure-Python YAML parse per cold load",
                )
            )
        if t.load_all_ms > _FELT_RELOAD_MS:
            ranked.append(
                (
                    t.load_all_ms,
                    f"paint-first load — the ~{t.load_all_ms:.0f} ms mount/reload"
                    " runs on the UI thread; move it to a worker (shell paints first)",
                )
            )
        keystroke = (t.search_ms or 0.0) + (t.render_ms or 0.0)
        if keystroke > _FELT_KEYSTROKE_MS:
            ranked.append(
                (
                    keystroke,
                    f"low-power mode — each keystroke ~{keystroke:.0f} ms (search"
                    f" {t.search_ms or 0:.0f} + render {t.render_ms or 0:.0f}); a"
                    " debounce + per-doc view cache smooth fast typing",
                )
            )
    if textual_ms is not None and tui_ms is not None and tui_ms - textual_ms > 120:
        ranked.append(
            (
                tui_ms - textual_ms,
                f"lazy-import the mode panes — ~{tui_ms - textual_ms:.0f} ms of"
                " TUI-launch import beyond Textual could defer to first use",
            )
        )
    ranked.sort(key=lambda r: r[0], reverse=True)

    notes: list[str] = []
    if not bytecode_warm:
        notes.append(
            "warm the bytecode cache (compileall on install) — a cold `.pyc` set"
            " recompiles the whole import on the next launch"
        )
    if fuse:
        notes.append(
            "store is on Android shared storage (FUSE): reads and stats pay syscall"
            " overhead, so the cache and paint-first wins above land hardest here"
        )

    lines = [f"[~{saving:>4.0f} ms] {text}" for saving, text in ranked]
    lines += [f"[note]     {text}" for text in notes]
    if not lines:
        lines.append(
            "[ok]       nothing stands out — every profiled path is under the"
            f" felt-latency thresholds ({_FELT_KEYSTROKE_MS:.0f} ms/keystroke,"
            f" {_FELT_RELOAD_MS:.0f} ms/reload) on this machine"
        )
    return lines
