# dossier v3 — Rewrite Plan (Rust core + journal store)

**Status:** Approved plan, ready for implementation. Produced from a design session with
a Fable advisor (2026-08-16) and hardened by an independent adversarial review (15
findings — HLC ordering, torn-tail repair, writer locking, truncation detection,
exporter/cutover sequencing — all incorporated below); decisions are **user-confirmed**
— do not re-litigate them during implementation.
**Supersedes:** `DESIGN.md` and `ROADMAP.md` describe the *current Python app* (v2).
They remain the authoritative record of v2's behavior and rationale — this plan cites
them as the port spec — but where this document contradicts them, **this document wins**.
**Implementer note:** build in independently shippable, CI-green slices (repo standing
workflow). Phase R-UI below is a **gate**: a separate TUI-layout design plan must be
produced and user-approved before Phase R3 implementation begins.

---

## 0. Why (one paragraph)

Snappiness is now the top priority, above features. The measured/estimated cost centers
are CPython + Textual cold start and FUSE syscall storms on Android — both floored in
the seconds on the phone at the store's real scale (~948 docs), and neither fixable by
further Python tuning (the cheap wins — libyaml, parallel FUSE reads, lean CLI imports —
are already spent; see `docs/dev/project-context.md`). The fix is a native rewrite of
the daily-driver core, paired with the storage redesign that makes sync conflicts
structurally impossible and makes a polyglot split free.

## 1. Decisions record (user-confirmed — binding)

| # | Decision |
|---|---|
| D1 | **Track B**: Rust core + Python enrichment satellite, communicating only through the journal store. From-scratch core; not weighted by the existing codebase. |
| D2 | **Rust + Ratatui** for the core. The codebase doubles as the user's **Rust learning material** — see §5.6 commenting requirements. |
| D3 | **PyPy is off the table** (wrong axis: JIT helps hot loops, not startup; no Android story). |
| D4 | **Big-bang cutover** (no prolonged side-by-side); the exporter + parity check are still mandatory. |
| D5 | **Perf budget: < 100 ms to usable on the phone (target), 150 ms acceptable.** Enforced, not aspirational (§9). |
| D6 | Phase R0 (measure) and the R0 spike are confirmed first steps. |
| D7 | Storage = **Q3 journals**: per-writer append-only files synced by Syncthing; single writer per file; state = deterministic fold of the union. Hand-editability of per-doc `.md` files is **dropped** (Obsidian-as-vault goal dies with it). |
| D8 | **Desktop VLM stays; no VLM on the phone, ever** (standing constraint). The Python satellite keeps all VLM/enrichment code. |
| D9 | **Renditions: keep the capability (multi-file docs + `primary`), drop the word** everywhere user-facing. |
| D10 | CLI consolidates to the **porcelain-5** + maintenance tier (§4.1); the feature disposition table (§8) is authoritative — anything not marked *Port* is out of scope. |
| D11 | Backlog filing: visible unfiled counter; **triage by exception** (review cards only for ambiguous cases). **Auto-filing high-confidence proposals is deferred** — desktop-only if ever; never on mobile. |
| D12 | **The Miller layout is dropped.** A simpler TUI layout will be designed in a **separate follow-up plan** before Phase R3 (see §6, Phase R-UI). This plan intentionally specifies only layout-independent behavior. |

## 2. Architecture

```
┌────────────────────────────┐        ┌─────────────────────────────────┐
│  ds  (Rust, single binary) │        │  dossier-lab (Python, desktop)  │
│  Windows + Termux/Android  │        │  VLM scan · transcribe · intake │
│  TUI · open · status ·     │        │  proposals · dedup hashing ·    │
│  file · export · maint.    │        │  power-gated service            │
└─────────────┬──────────────┘        └───────────────┬─────────────────┘
              │ append own journal · fold all         │ append own journal · fold all
              ▼                                       ▼
      <syncthing_root>/.dossier/journal/   ← synced by Syncthing (unchanged transport)
      <syncthing_root>/… real PDFs/scans … ← plain files, synced, untouched
```

- **No IPC, no FFI, no daemon.** The journal directory *is* the interface between the
  Rust core, the Python satellite, and the other device. Syncthing remains the only
  transport (connectivity, relays, Android app, versioning — all kept for free).
- Real documents stay plain files opened by platform openers (`os.startfile` /
  `termux-open` semantics port as-is, including opener-existence verification —
  DESIGN §7).
- Per-device config stays a small TOML file in the platform config dir (only
  `syncthing_root`, syncthing API key, scan endpoint for the satellite). **Synced
  settings move into the journal as ops** (uniformity; no whole-file-LWW special case).

## 3. Journal store — the contract (freeze in Phase R1)

This section is the contract between the Rust core and the Python satellite. Both
implement it; shared golden test vectors (§10) keep them identical. Format details
below are the design intent — R1 may refine mechanics, but any change must update this
section and the golden vectors in the same slice.

### 3.1 Files & writers

- Directory: `<syncthing_root>/.dossier/journal/`.
- Two namespaces, so the hot startup fold never parses transcripts:
  - `meta/<writer>.jsonl` — documents, locations, bundles, settings, review state
    (dismissals/acks/folds), succession, suggestions-state.
  - `enrich/<writer>.jsonl` — scan readings, transcripts, intake proposals, dedup
    clusters. Loaded lazily (search-in-scans, suggestion display, `ds file`).
- **Writer id** = `<device>-<component>`, e.g. `desk-core`, `desk-lab`, `phone-core`.
  Device part comes from per-device config (set by `ds init`); a writer appends **only
  to its own file**. Discovery is a directory glob — no registry.
- **Filename grammar (frozen)**: writer files match `^[a-z0-9][a-z0-9-]*\.jsonl$` and
  nothing else is folded. Both implementations **exclude** anything containing
  `.sync-conflict-`, the `.stversions/` dir, and rewrite temps. Compaction temps are
  named `<writer>.jsonl.tmp-<pid>` (does not match the glob) and the pattern
  `*.jsonl.tmp-*` goes into `.stignore` on **both devices before any journal exists
  in the synced tree** (Phase R7 pre-step; `.stignore` is per-device and never syncs).
- **One process per writer id**: a writer takes an OS advisory lock on a lock file in
  the platform-**local** data dir (never on FUSE / never synced) before appending or
  compacting. A second process that fails the lock runs **read-only** with a visible
  notice (browse/open/status still work — `ds status --quiet` from cron is read-only
  by design). This closes the v2-supported "TUI open + cron job" concurrency without
  duplicate `(ts, w)` keys or append-vs-compaction races.
- Single-writer-per-file means Syncthing never sees two versions of one file to
  reconcile: **conflicts are structurally impossible**, not merely handled. The v2
  merge/resolve machinery (Phase 12) is not ported. `.sync-conflict-*` files, should
  they ever appear (e.g. versioning restore), are surfaced by `ds status` as a loud
  anomaly, never silently read.

### 3.2 Op format (JSONL, one op per line)

```json
{"v":1,"ts":1755300000123,"w":"desk-core","op":"set","ent":"doc","id":"coc-card-2025","f":"expiry_date","val":"2026-09-28"}
```

- `v` — format version (unknown-versioned or unknown-`op` lines are **preserved
  verbatim** through compaction; forward compatibility).
- `ts` is a **hybrid logical clock, not raw wall time**: on every append,
  `ts = max(now_ms, own_last_ts + 1)`, where `own_last_ts` initializes on startup
  from the max `ts` seen across **all** journals. Per-writer `ts` is therefore
  strictly monotonic — a backwards clock jump (NTP fix between sessions) can never
  reorder a writer against itself — while staying human-meaningful as a timestamp.
  Total order for LWW is lexicographic `(ts, w)`, which is unique because a writer
  never repeats a `ts`. All numbers in the format are **integers** (no floats, ever;
  dates are ISO strings) — this matters for §10's canonical comparison.
- Ops (final list frozen in R1): `create` / `delete` (tombstone; retained forever) /
  `set` / `unset` for entity fields; `state` ops for review/suggestion entries as
  **per-key LWW** (`dismissed`/`active`, newest op wins) — *not* a monotone union,
  because v2 ships restore verbs (`h` un-dismisses an orphan) that a union could
  never express; `reading` / `proposal` in `enrich`.
- **Field-level LWW** on scalars *and* whole-list values (tags, files). Rationale:
  single user, two devices; concurrent same-field edits within a sync window are
  vanishingly rare, and LWW's loser is still in the journal (recoverable), never
  silently gone. Do not build OR-sets — documented simplicity trade, revisit only on
  evidence.
- Doc `id` stays the slug (v2 rules: reserved-name guard, collision suffixing). An id
  rename = `create` new + copy fields + **reference fixups** + `delete` old, emitted
  as consecutive ops from one writer. Fixups are part of the contract: rewrite every
  inbound `supersedes` pointing at the old id, and re-emit the effective
  review/suggestion `state` under the new id (stale old-id state is harmless after
  the tombstone but must not be *relied* on). `enrich` entries are keyed by file
  path/fingerprint, not doc id, so they are unaffected. A golden vector (§10) covers
  rename-with-inbound-references.

### 3.3 Fold, durability, compaction, history

- **Fold**: group ops by `(ent, id)`; apply in `(ts, w)` order; a tombstone wins over
  all older ops, and **ops newer than the tombstone are ignored unless a `create`
  newer than the tombstone precedes them** (no partial-doc resurrection from a stray
  `set`; a golden vector pins this); `state` entries are per-key LWW (§3.2). The fold
  is a pure function — **fold(A ∪ B) ≡ fold(B ∪ A)**, property-tested (§10).
  **R1 clarification (both implementations):** a `set`/`unset` for an entity that has
  no `create` at all is *also* ignored, not treated as an implicit create — a stray
  field write must never materialize a document — and each one is counted as an
  `orphaned` op so `ds status` can say a `create` went missing. Ops sharing a
  `(ts, w)` key are counted as `duplicate_keys`: impossible under the HLC + writer
  lock, so a non-zero count means two processes shared a writer id.
- **Append durability**: one op = one `write()` of a full line ending in `\n`;
  `fsync` after user-initiated saves (edits are rare; the cost is nothing). Torn
  final line (no trailing `\n` / parse failure): dropped with a warning on load, and
  — critically — **repaired before any append**: a writer opening its own file for
  append must first truncate a torn tail (it was never durable), otherwise the next
  append glues two ops into one unparseable line and destroys the *new* op.
  **Mid-file unparseable lines** (distinct from unknown-`op`/unknown-`v` lines, which
  fold as opaque and survive compaction): both implementations skip them, count them,
  surface the count as a `ds status` anomaly, and preserve them verbatim through
  compaction — never silently discard. Atomic same-directory temp + rename for any
  full-file rewrite (the v2 EXDEV lesson stands).
- **Truncation detection (the Proton-revert defense)**: a reverted, *shorter* journal
  propagates through Syncthing as an ordinary modification — no conflict file, still
  valid JSONL, silently missing recent ops. So each device persists, in **local**
  (non-synced) state, a high-water mark per journal file (max `ts` seen + byte
  length). **The damage signal is max-`ts` regression** — compaction can never
  regress it (it always preserves the newest ops, §3.3), while a revert by
  definition deletes them — so a max-`ts` regression triggers the loud `ds status`
  anomaly pointing at Syncthing versioning for recovery. A byte-length shrink with
  max `ts` preserved is ordinary compaction and must **not** alarm; length is a
  secondary corroborator only. "Conflicts are
  structurally impossible" does **not** mean "damage is impossible" — this check is
  what keeps the difference honest.
- **Compaction**: a writer compacts **only its own file** (single writer ⇒ no race):
  rewrite as the minimal op set reproducing its contribution to the fold, **keeping**
  (a) all tombstones, (b) all ops newer than **30 days** — which makes the journal
  itself the undo history with a durable 30-day horizon. Trigger: on clean exit when
  live-op ratio < 25%. The v2 local history dir is retired; **undo = append the
  inverse op** (previous value read from the fold/journal). Compaction must preserve
  every op a fold would still consult and all unknown lines.
- **Backstop**: Syncthing staggered versioning on the folder (already the Phase 15
  headline check) remains the disaster net; `ds status` keeps that check.
- **Sizing reality check**: ~1000 docs × ~15 fields ≈ tens of thousands of ops ≈ a few
  MB of `meta/` JSONL. serde_json folds that in single-digit ms; one sequential FUSE
  read replaces 948 opens. Startup cost is dominated by paint, not data.

## 4. The Rust core (`ds`)

### 4.1 CLI surface (porcelain-5 + maintenance)

Daily (what `ds --help` leads with):

| Verb | Behavior |
|---|---|
| `ds` | the TUI (find → open, the urgent lookup) |
| `ds open <query>` | shell-side find: exact-then-fuzzy match over names/tags/notes + reading text; opens the file (picker on ties). Absorbs v2 `ds ask` (the intent router is dropped; a simple ranked retrieval + one-line answer for question-shaped queries). |
| `ds file` | the one filing flow: unfiled files (inbox drops **and** in-scope orphans are the same concept) → review card queue; consumes satellite proposals when present, manual filing when not. Prints/updates the **unfiled counter**. |
| `ds status` | the router, git-status style: unfiled count · expiring/expired · missing files · duplicate clusters · syncthing health (reachable / folder shared / **versioning on**) · journal anomalies — each line naming the verb that fixes it. Absorbs v2 `ds doctor` + `ds reconcile` (CLI) + `ds syncthing status`. `--quiet` mode = v2 `ds expiring` contract **exactly**: exit 1 is driven by expiry/event findings *only* (other findings never flip a cron job), empty stdout when clean, exit 2 = tool broken; keeps `--days N` and `--bundle SLUG` so existing cron/Task-Scheduler jobs port with a rename. |
| `ds export <bundle> <dest>` | materialize a bundle (copies + manifest), v2 semantics. |

Maintenance (listed under a separate heading): `ds init` (conversational; sets device
id, root, termux checks, syncthing API key — absorbs v2 `ds syncthing key/address`),
`ds reset` (same hard guarantee: never touches anything outside `.dossier/`),
`ds organize` (canonical renames, plan → `--apply`). Bundle rename lives in the TUI
command line (`:bundle rename old new`), emitting the bundle-entity ops plus a
per-member `set bundles` op — the v2 "rewrites all members atomically" guarantee
becomes "emitted as one consecutive op run from one writer". Hidden: a self-timing
flag (§9).

Gone from the binary entirely: `migrate` (Notion cutover is history), `resolve`
(no conflicts to resolve), `profile` (replaced by built-in timing), `ask`, `scan`,
`service`, `import`/`intake` as separate verbs, and `ds add` (creation happens in
the TUI or via `ds file` — a bare CLI creator earned no keep).

### 4.2 Crate layout

Cargo **workspace** at the repo root — two crates, so the pure logic is a library the
TUI can't reach into and tests hammer directly (also the cleanest Rust-learning shape):

```
Cargo.toml            # workspace
crates/
├─ journal/           # lib: op model, append, fold, compaction, golden vectors
└─ ds/                # bin: CLI (clap), query, TUI (ratatui), platform open,
                      #      status, export, file, init/reset/organize
```

The Python package stays at `dossier/` and shrinks to the satellite (§5) —
both live in this repo; CI runs both toolchains (§10).

### 4.3 Dependencies (deliberately few, pinned)

`ratatui` + `crossterm` (TUI; crossterm speaks SGR mouse — the Termux touch story
ports), `serde`/`serde_json`, `clap`, `walkdir` (the reconcile tree walk — one
`DirEntry` pass kills the v2 520 ms FUSE stat storm), `thiserror`/`anyhow`, `dirs`,
`jiff` or `chrono` (dates), `ureq` + rustls (Syncthing REST, status only — **note:**
on Termux the API is HTTPS-only with a *self-signed* cert (Phase 15 finding; plain
http 307-redirects), so rustls needs a custom `ServerCertVerifier` pinned/permissive
for `127.0.0.1` only — budget for that shim in R3, never disable verification
globally). **No async runtime** — std threads + channels are sufficient at this
scale and far better learning material than tokio. Every added dependency needs a
sentence of justification in the PR.

### 4.4 Platform targets

- Windows: `x86_64-pc-windows-msvc` (native terminal via crossterm).
- Termux: **`aarch64-unknown-linux-musl`, fully static** — runs under Termux with no
  NDK and no bionic linkage; cross-compiled from the PC (cargo + musl toolchain or
  `cargo-zigbuild`; the R0 spike picks whichever builds cleanly). Distribution =
  copy one binary into `$PATH` (this *replaces* the python/uv/libyaml install chain
  on the phone — document it; a self-update verb is out of scope for v3.0).
- Linux (`x86_64-unknown-linux-gnu`) for CI and the dev container.

### 4.5 TUI — behavioral invariants only (layout deferred, D12)

The **layout is not specified here.** The Miller three-pane home is dropped; a simpler
layout will be designed in the follow-up **TUI layout plan** (Phase R-UI gate) before
R3 begins. What *is* binding — the layout-independent interaction invariants v2 spent
~20 PRs getting right (port these from DESIGN §8/§14 as a spec, not as code):

1. **Find-fast**: a bare printable typed anywhere on the browse surface starts a
   search (first character kept); the surface binds **no letter keys**. Cold start →
   type → `Enter` → file open, ≤ 5 keystrokes.
2. **The verb pair**: `Enter` opens the **file**, `→` opens the **record/detail** —
   applied by row kind everywhere (doc / orphan / succession pair / other object per
   DESIGN §8); `Enter` never mutates and never dies (falls through to the record when
   there's no file).
3. **Esc peels exactly one layer per press**; at base state it arms, second
   consecutive Esc quits; any other key disarms. Termux's IME-dismiss Esc must never
   quit spuriously.
4. **Command line, not palette**: `:`/`>` for occasional commands; one shared command
   catalog; a `check_action`-style gate decides actionability.
5. **Narrow-first**: usable at ~40–60 cols portrait Termux; ASCII status fallbacks
   (`!`/`~`) alongside optional glyphs; sort keys with explicit tiebreakers so order
   never jitters.
6. **Touch/Termux**: SGR mouse on; first tap selects, tap-on-selected opens;
   the ⌨/search affordance momentarily drops mouse mode so the next tap raises the
   IME, restored on submit/blur. Mouse mode owns scrolling (Termux #4302).
7. **Never block the render thread**: tree walks, syncthing polls, journal compaction
   run on worker threads; the UI thread only folds messages. (The v2 rule "no blocking
   I/O in async paths" becomes "no blocking I/O on the render loop".)
8. **Surfaces** (however the layout arranges them): browse+detail, review queue
   (orphans / missing / duplicates / succession / integrity — **no conflicts tab**),
   bundles (+ export + readiness *counts* only), filing cards, settings. Expiry watch
   is a **filter with a header count**, not a mode (v2 watch-mode is not ported).

### 4.6 Rust-for-learning commenting standard (binding, D2)

The user will learn Rust from this codebase. Requirements:

- Every public item has a doc comment (`///`) saying what it is **and why it exists**;
  every module a `//!` header explaining its role and its key design decision.
- Where a Rust idiom would surprise a Python developer, add a short `// rust:` note at
  first use *in that module* — ownership/borrow choices at API boundaries, `?` error
  flow, lifetimes (avoid them in public APIs where an owned type is cheap), enums +
  exhaustive `match` as the state-machine tool, why a `&str` vs `String`, interior
  mutability if ever used. Explain the *chosen* pattern, don't tour alternatives.
- Idiomatic code over clever code: `clippy::pedantic` warnings triaged, not silenced;
  no `unsafe` (there is no need at this scale — a CI deny).
- Tests double as examples: each `journal` invariant test states the invariant in a
  sentence first.

## 5. The Python satellite (`dossier-lab`)

- **Keeps** (moves under a `service`-oriented CLI, e.g. `ds-lab`): VLM scan +
  transcribe (`scan.py`), intake **proposal engine** (`intake.py` — proposes *fields*:
  name, dates, document type, succession candidate), suggestion derivation from
  readings (`suggest.py` scan-sourced parts), succession clustering
  (`succession.py`), dedup hashing/clustering (`dedup*`), the power-gated service
  (`power.py`, `service*.py`). All desktop-only, latency-irrelevant, already verified
  against the real store — **do not rewrite these**.
- **Persistence adapter**: one new module replacing `store.py`'s sidecar I/O — fold
  `meta/`+`enrich/`, append to `enrich/<device>-lab.jsonl` (and nothing else).
  The satellite never writes `meta/` — filing/accepting is the core's job.
- **Division of authority** (drift-killer): the satellite proposes **fields**; the
  Rust core owns canonical naming and file moves (`organize` logic lives in Rust
  only). No naming logic duplicated across languages.
- **Deleted from the Python package** at cutover: the Textual TUI, `cli.py`'s user
  surface, `store.py` YAML machinery, `merge.py`, `resolve.py`, `migrate.py`,
  `answers.py`, `fuzz.py`, `profiling.py`, `preparedness.py` templates, `query.py`
  (satellite keeps only what its engines import). Expect the package to shrink by
  well over half.
- Name-based date suggestions (cheap, needed on the phone) are **ported to Rust**;
  the satellite contributes only reading-derived suggestions.

## 6. Phases & slices

Every phase lands in CI-green, conventional-commit slices; each phase ends with an
**on-phone measurement** against the budget (§9). Real-store operations stay read-only
until the cutover step the user personally green-lights.

- **R0 — Measure + spike (confirmed first, D6).**
  - R0.1: **done (2026-08-16).** The `DS_TIMING` probe + protocol live in
    `docs/dev/startup-timing.md`, with the real-phone baseline recorded there:
    **cold 1.43 s wall / 1053 ms usable; warm median ≈ 0.98 s / 670 ms** —
    6.5–9.5× the budget, CPU-bound, and structural (interpreter ~300 ms +
    framework imports ~320 ms + first paint ~230 ms; store load only ~95 ms).
    This is the number the rewrite must embarrass. Desktop/Windows numbers are
    optional nice-to-have.
  - R0.2: **DONE — the gate is GO (2026-08-16, measured on the phone).**
    Code in `spike/` (throwaway); protocol, full results and findings in
    **[`docs/dev/spike-r02.md`](docs/dev/spike-r02.md)**.
    - **Phone, Samsung S24U:** cold **6.2 ms to usable** (10.97 ms wall), warm
      **4.1 ms** (6.91 ms wall), **RSS 1.2 MB**, worst keystroke→frame **0.33 ms**.
      Against the R0.1 baseline of 1053 ms cold / ~670 ms warm on the same phone:
      **170× faster, and 16× inside the 100 ms target**. Nothing is marginal —
      frame times on the phone match the x86 dev box's, so this workload is
      nowhere near either machine's limits.
    - **Every touch/IME finding from DESIGN §14 still holds**, three years on and
      previously untested: taps arrive as SGR clicks, tap-then-tap opens, drags
      scroll, the `⌨` affordance raises the soft keyboard, typed text lands in the
      search bar, and an Esc that dismisses the IME does not quit the app.
    - **Toolchain: nothing extra is needed.** `rustup target add
      aarch64-unknown-linux-musl` + `rust-lld` with `+crt-static` /
      `link-self-contained=yes` yields a fully static **810 KB** binary. No NDK,
      no `musl-gcc`, no `cargo-zigbuild` — §4.4's "the spike picks whichever
      builds cleanly" resolves to *none of them*.
    - **Binding finding for R3: Termux has no function keys.** The spike bound
      its diagnostic panels to F2–F5 to keep every letter free for find-fast, and
      on the phone they turned out to be unreachable. So the `⌨` button in the
      touch action bar is the *only* route to the IME affordance (REWRITE-UI.md
      §5 keeps it — this is why), and **nothing in R3 may sit behind a function
      key**. REWRITE-UI.md §3 already routes secondary surfaces through `:`
      commands, so the plan is sound; the spike proved the failure mode rather
      than assuming it.
    - Ratatui 0.30 + crossterm 0.29 needed no workarounds; two dependencies
      total. The list is hand-virtualized, so frame cost tracks the viewport, not
      the store, and the event loop blocks on input so idle CPU is zero.
    - **The column arithmetic is right on the device**: every glyph row's
      right-hand rule lines up — CJK at two cells, emoji, Devanagari with
      zero-width combining marks, Cyrillic, combining `é`. Termux and the
      `unicode-width` crate agree because both follow the Unicode East Asian
      Width table. **Nerd Font glyphs are absent** on the phone's default font,
      so the optional icon set stays optional and the ASCII/Unicode set must
      carry every signal (§4.5.5 already requires this). Emoji get one cell
      despite painting wider — never put one in a width-sensitive column.
    - Open but non-blocking: Windows startup/interactive behaviour (CI already
      covers the renderer and the budget there).
- **R-UI — TUI layout plan (gate, D12).** **Done (2026-08-16): see
  [`REWRITE-UI.md`](REWRITE-UI.md)** — user-approved: single-list drill-down stack,
  flat list (no location headers; location = row data + filter), sticky-toggle
  detail, command drill-down with minimal hotkeys. The gate is satisfied; R3
  implements that document.
- **R1 — `journal` crate.** Op model, append, fold, compaction, torn-line tolerance;
  property tests (union-commutativity, compaction-preserves-fold, tombstone
  supremacy); **golden test vectors** checked in as JSON fixtures; synthetic perf
  test (fold 1k docs / 50k ops < 20 ms in CI with generous margin). Freeze §3.
  - **Slice 1 done (2026-08-16):** `crates/journal` with the op model
    (parse/classify/round-trip, torn tails, unknown-`v`/unknown-`op` preserved as
    opaque, malformed lines counted and kept), the fold with all three §3.3 rules,
    the frozen filename grammar, 9 golden vectors plus their cross-language schema
    (`crates/journal/tests/golden/README.md`), 6 property tests, and the perf gate.
    35 tests; `clippy::pedantic` denied and triaged in place.
  - **Measured (x86_64 dev box, release, 50k ops / 1k docs):** fold **16 ms**,
    parse **40 ms**, canonical serialization 2 ms. Two corrections to §3.3's sizing
    note, both worth carrying into R3:
    1. **Parse dominates, not fold** — 2.5× the fold, and it was not in the budget
       at all. The obvious "parse to `Value`, inspect, then deserialize" walks every
       line twice; a fast-path deserialize straight into `Op`, with a slow path only
       for lines that fail it, cut parse from 58 ms. `#[serde(flatten)]` for
       unknown-field preservation costs a further 18% (40 vs 32.7 ms) and is
       **kept** — 7 ms is a fair price for not silently deleting a future version's
       fields during compaction.
    2. **The fold's cost was allocation, not algorithm** — keying the working maps
       by `(&str, &str)` borrowed from the ops and materializing owned keys once at
       the end took it from 20 ms to 16 ms; it had been allocating three `String`s
       per op, 150k of them.
    At the *real* store size (~948 docs, ~15k ops) that is ≈17 ms parse+fold on this
    machine. The phone is slower, so R3's first on-device run should check it against
    the 100 ms budget before anything else joins the startup path.
  - **Slice 2 done (2026-08-16):** the reader and the truncation defense.
    `store.rs` discovers writer files per namespace (`meta` on every launch,
    `enrich` only when asked), reports per-file size/high-water/malformed counts,
    and turns every problem into an `Anomaly` rather than an error — one bad file
    costs that writer's contribution, never the store. `.sync-conflict-*` copies
    are **reported and never read**, which is what keeps "conflicts are
    structurally impossible" an honest claim rather than a hopeful one.
    `watermark.rs` implements the Proton-revert defense with its table pinned by
    tests in both directions: a `max_ts` regression or a vanished file is damage;
    a file that shrinks by 97% while keeping its newest op is compaction and must
    stay silent. Marks only ever climb, so a revert keeps being reported until the
    data is genuinely recovered (`accept` is the deliberate way out). An
    end-to-end test plays the whole scenario out on a real directory.
  - **Slice 3 done (2026-08-16):** `writer.rs` — the appending half. The hybrid
    logical clock (monotonic per writer even across a backwards NTP correction,
    seeded from the whole store so a slow-clocked device does not lose every LWW
    comparison), the one-process-per-writer advisory lock, and the torn-tail
    repair that must happen *before* the first append. Callers describe ops with
    a `Draft` and the writer stamps `v`/`ts`/`w`, so forging a timestamp or
    writing under another writer's id is not a mistake the API can make.
    `append_all` writes a consecutive run for the edits that are only correct
    together (id rename, bundle rename). **The lock needs no dependency**: std's
    `File::try_lock` has been stable since 1.89, and a failed lock is a
    recoverable `Error::Locked`, not a crash — the caller degrades to read-only,
    which is what keeps `ds status --quiet` from cron working while the TUI is
    open.
  - **Slice 4 done (2026-08-16) — R1 complete.** `compact.rs` rewrites a writer's
    own file as the minimal set reproducing its contribution: every `create` and
    `delete` (tombstones forever), the newest `set`/`unset` per field, the newest
    `state`/`reading`/`proposal` per key, **everything inside the 30-day undo
    horizon**, and every line this build could not read — preserved verbatim, since
    it is not compaction's place to discard what it did not understand. Two rules
    earn their own tests because they look wrong at first glance: an `unset`
    survives even when the `set` it cancelled is dropped (the *other* writer may
    have set that field earlier, and the unset is what keeps it removed), and ops
    older than their entity's newest tombstone are dropped as permanently
    unreachable. The rewrite is a same-directory temp + rename (EXDEV lesson), the
    temp name deliberately fails the journal grammar so a half-finished compaction
    is invisible to the next fold, and `Writer::compact` holds the writer lock
    throughout. The `compaction-preserves-fold` golden vector is in, and two
    property tests generalize it: compaction preserves the fold for *arbitrary*
    two-writer streams, and never lowers a file's high-water mark — the second
    being what keeps the truncation defense from firing on routine maintenance.
  - **§3 is frozen** as of this slice. 78 tests across the crate; the one contract
    clarification R1 added (orphaned `set` handling, duplicate-key counting) is
    written into §3.3 above.
- **R2 — Exporter + parity (Python).** One-shot v2-store → journal converter using
  the existing trusted `store.py` reader: docs + locations + bundles + reconcile +
  suggestions + scans + intake sidecars **and the synced `config.toml` → settings
  ops** (`expiry_threshold_days`, `include`/`ignore` scope globs, `[intake]`
  inbox/filed/keyword-map, `[organize.folders]` — losing these at cutover would
  silently reset scope and filing behavior). Parity harness: `fold(export(store)) ==
  store.load_all()` + settings, field-by-field across all ~948 real docs
  (read-only). Also: the Python fold for the satellite, validated against the R1
  golden vectors. **All R2–R6 test journals live *outside* the synced tree**
  (scratch/local dirs) — anything created inside the Syncthing folder syncs by
  default, and half-built journals must never reach the phone early.
  - **Slice 1 done (2026-08-16):** `dossier/journal.py` — the Python fold, written
    against the **same** fixture files the Rust crate runs
    (`crates/journal/tests/golden/`), not a copy of them. It parses and classifies
    lines identically (folded / opaque / malformed), folds by the three §3.3 rules,
    produces the canonical JSON, and implements the compaction plan the satellite
    needs for its own `enrich/` file. `dossier/tests/test_journal.py` runs all ten
    shared vectors plus both-file-orders, torn tails, health counters and
    compaction-preserves-fold. **All ten matched byte-for-byte on the first run** —
    which is the useful result: the canonical form (sorted keys, compact
    separators, `ensure_ascii=False`, integers only) really is reproducible across
    `serde_json` and `json.dumps`, so §10's cross-language comparison is sound
    rather than aspirational. The drift risk §11 lists is now closed by a test that
    fails in *both* languages the moment either fold changes.
  - **Slice 2 done (2026-08-16):** `dossier/export_journal.py` — the one-shot
    converter and the parity harness. Exports documents, locations, bundles, the
    synced settings, reconcile decisions (as per-key `review` state), suggestion
    dismissals, scans and intake proposals. Read-only w.r.t. the v2 store and
    **idempotent**, so a rehearsal costs nothing and a re-run after a fix is safe.
    The value mapping lives in one place and is used by *both* the exporter and the
    parity check, so parity tests the round trip through JSONL — where a migration
    actually loses things — instead of re-deriving the same mistake twice and
    agreeing with itself. `tools/export_journal.py` runs it against a real store
    and **refuses a destination inside the Syncthing root**, enforcing §7's
    "no journal in the synced tree before cutover" mechanically rather than by
    documentation; parity failure is its exit code.
  - **Cross-language cutover proof:** `cargo run -p journal --example fold_dir`
    folds a journal directory and prints its canonical JSON. Running it over a
    Python-exported store gives a **byte-identical** result to the Python fold —
    the golden vectors prove agreement on hand-written fixtures, this proves it on
    an exporter's real output, which is the case no fixture can cover. It is also
    the tool for R7's "confirm the phone folds it" step.
  - **One format finding, now binding:** v2's `ScanReading.confidence` is a
    **float**, and §3.2 bans floats (they would make the canonical comparison
    between the two folds unimplementable). It is exported as an integer
    `confidence_permille` (0–1000), *renamed* rather than rounded in place so
    nothing can read it as a fraction by accident. The satellite must read and
    write it that way from now on.
  - **Still to come in R2:** the parity run against the real ~948 documents. That
    is the user's to make — no real store exists in the dev container, and the
    standing rule keeps real-store operations read-only and user-initiated.

- **R3 — Read-only core** *(needs R-UI)*: browse + search (exact→fuzzy, ctrl+t
  content search) + open + `ds status` (counts, syncthing REST checks) + `ds open`.
  Daily-usable read-only against an exported copy of the real store.
  - **Slice 1 done (2026-08-16):** `crates/ds` as a library first — `doc.rs` turns
    a fold into documents (shelf order with every tiebreaker explicit, expiry
    standing, the file `Enter` opens, a pre-folded search haystack) and `search.rs`
    ports v2's fuzz contract unchanged (exact always wins; budget 0/1/2 edits by
    term length so a short query never fuzzes; OSA so a thumb transposition costs
    one; terms `AND`ed). No index: R0.2 measured 0.33 ms for filter-plus-repaint at
    store scale on the phone, so an index would be complexity bought with nothing.
  - **Slice 2 done (2026-08-16):** the Find surface, runnable. The Elm loop is
    fixed as REWRITE.md §11 prescribes — `app.rs` is `update` (message in, state
    changed, [`Effect`] out), `find.rs`/`detail.rs` are `view`, `input.rs` is the
    only module that knows crossterm exists. Every §4.5 invariant is a rule with a
    test beside it: find-fast with the first character kept, `Enter`-opens-file
    falling through to the record, one-layer `Esc` peeling (search → surface →
    filter → arm → quit), tap-then-tap, the mouse-drop IME affordance that
    restores itself on the next key. The list is hand-virtualized and every column
    is measured in cells. Rendering is checked against a `TestBackend` at 45×28 and
    100×26 — the mockups' own sizes — including that the status marker shares one
    column in every row and that `NO_COLOR` changes not one character of text.
    - **Two layout calls worth recording.** The two-line row switch keys off the
      *terminal* width, not the list pane's: when detail splits at ≥100 cols the
      list pane is ~55 columns but its rows stay single-line, which is what the
      approved split mockup shows. And the touch action bar's third quarter is
      `^x Expiry`, not `: Cmds`, until command mode exists — a button for
      something that does not work yet is exactly v2's `check_action` lesson.
    - **Measured (x86_64 dev container, release, 950 docs / 6,650 ops):**
      `read 4.5ms · fold 3.0ms · build 4.1ms · terminal 0.6ms · **usable 12.3ms**`
      via `DS_TIMING=exit`, which mirrors v2's probe and the spike's so the three
      are comparable on the same phone. The store build (fold → documents, sorted)
      costs about as much as the fold itself and is now on the startup path; the
      phone number is the one that decides, and R3's first on-device run should
      take it before anything else joins that path.
- **R4 — Editing**: detail editing via ops, undo (inverse ops), slots with
  insert-and-shift, supersession, bundle membership, settings ops, `ds init`/`reset`.
- **R5 — Review + file + export**: `walkdir` tree walk, review queue (five tabs),
  `ds file` (manual + proposal-consuming cards, unfiled counter, exception triage per
  D11), `ds export` with manifest, `ds organize`.
- **R6 — Satellite adaptation**: persistence adapter, `ds-lab` CLI, gut the Python
  package, service writes to `enrich/`, sync-idle wait kept.
- **R7 — Cutover (big-bang, D4) + polish.** Rehearse on a copy; then, in this order:
  **(1) install the phone binary and verify it launches** (the phone must never sit
  with a deleted v2 store and no app), **(2) `.stignore` the compaction-temp pattern
  on both devices** (per-device file, never syncs — §3.1), (3) stop edits → export →
  parity green, (4) archive the old `.dossier/` contents to the local data dir,
  (5) let the journal layout sync, (6) confirm the phone folds it. Rollback = the
  archived v2 store (additive, nothing destroyed). Then: README/CLAUDE.md rewrite,
  v2 code deletion, CI finalization, Termux install docs. **The user personally runs
  the cutover.**

Sequencing notes: R1 ∥ R2 overlap after §3 freezes; R-UI runs during R1/R2; R3–R5 are
strictly ordered; R6 can overlap R4/R5 once the adapter exists.

## 7. Cutover mechanics & data safety

- The exporter is idempotent and read-only w.r.t. the v2 store; parity failure on any
  field is a hard stop.
- The archived v2 store goes to the platform data dir (non-synced), not the trash.
- `.dossier/journal/` first exists inside the synced tree **at cutover, never
  before** — enforced by keeping all pre-cutover journals outside the Syncthing
  folder entirely (§6 R2), since anything created inside it syncs by default.
  Syncthing versioning verified on before cutover (existing status check).
- The real files tree is untouched by every phase except user-approved
  `ds organize --apply` / `ds file` moves — same v2 guarantee, same rollback-safe
  rename (move file, then op; roll back the move if the append fails).

## 8. Feature disposition (authoritative; anything not "Port" is out of scope)

| Feature (v2) | Disposition |
|---|---|
| Browse/search/open, detail editing, slots+shift, supersession | **Port** (Rust; layout per R-UI) |
| Expiry watch | **Port as filter** + header count; watch *mode* dropped |
| Review: orphans/missing/duplicates/succession/integrity | **Port** (five tabs; conflicts tab dropped) |
| Suggestions accept/dismiss | **Port**; name-parse source in Rust, reading source from satellite |
| Bundles + export + manifest | **Port**; readiness = counts only |
| Bundle rename (atomic member rewrite) | **Port** as a TUI command-line verb emitting per-member ops (§4.1) |
| `ds add` | **Drop** — creation via TUI / `ds file` |
| Undo/history | **Port, redesigned**: journal-as-history, inverse ops, 30-day horizon |
| `ds status` router | **New** (absorbs doctor/reconcile-CLI/syncthing-status/expiring) |
| `ds file` | **New** (absorbs intake + import + orphan-adopt; D11 triage) |
| Multi-file docs (`files` + `primary`) | **Port**; the word "rendition" is banned (D9) |
| VLM scan/transcribe, intake proposals, dedup engine, service | **Keep in Python satellite** (D8) |
| Fuzzy search (bounded OSA, exact-first) | **Port** (small, well-specified in v2) |
| Content search + BM25-ish ranking for `ds open` | **Port** (simple scorer; `ds ask` router dropped) |
| Syncthing REST checks + sync glyph | **Port** (subset: reachable, folder, versioning, paused) |
| Merge/resolve (Phase 12), conflict banner, stale-write guard | **Drop** — structurally obsolete under journals |
| Notion `migrate`, `profile`, `ask`, hand-editable `.md` store, Obsidian vault | **Drop** |
| Bundle templates / readiness checklists / `min_valid_days` rules | **Drop**; keep bundle `date` + event note in the expiry filter |
| Hierarchical tags | **Drop** — flat tags only |
| Miller layout | **Drop** (D12; replacement per R-UI plan) |
| Textual, PyYAML serializer, sidecar TOMLs | **Drop** at cutover |

## 9. Performance budget (binding, D5)

| Metric | Target | Acceptable | Where enforced |
|---|---|---|---|
| Phone: launch → usable (first interactive paint, list rendered from meta fold) | **< 100 ms** | 150 ms | on-phone protocol, every phase end |
| Desktop: launch → usable | < 50 ms | 100 ms | on-phone protocol + dev habit |
| Fold 50k ops / 1k docs | < 20 ms | 50 ms | CI (synthetic, generous margin) |
| Keystroke → frame | < 16 ms | 33 ms | CI TestBackend timing + on-phone feel |
| Full tree walk (~1k files, FUSE) | < 150 ms | 300 ms | on-phone; worker thread regardless |
| Binary RSS on phone | < 30 MB | 50 MB | on-phone protocol |

Instrumentation: the binary self-times startup milestones behind a hidden flag/env var
(`DS_TIMING=1` prints a one-line breakdown: exec→main, fold, first paint). CI asserts
the synthetic numbers; the *real* gate is the documented on-phone measurement run at
each phase end and recorded in the PR. A phase does not ship over-budget.

## 10. Testing & CI

- **journal crate**: property tests (proptest) for the §3.3 invariants; golden vector
  fixtures shared with the Python fold — both implementations serialize their folded
  state to **canonical JSON** (sorted keys, UTF-8, integers only — the format has no
  floats by construction, §3.2) and must match byte-for-byte; comparing raw
  serializer defaults is unimplementable (serde_json and `json.dumps` disagree on
  key order and escaping). Required vectors include: union-commutativity,
  compaction-preserves-fold, tombstone-then-newer-`set` (no resurrection),
  tombstone-then-newer-`create` (legitimate recreate), id-rename with inbound
  `supersedes`, per-key `state` LWW un-dismiss, torn tail, mid-file garbage line.
- **TUI**: ratatui `TestBackend` renders to an in-memory buffer (the analog of v2's
  `run_test`); same discipline as v2 — poll for effects, never sleep-then-assert. A
  thin PTY smoke test on real terminals (port the `tools/` driver idea; `expectrl` or
  the existing Python driver pointed at the binary).
- **CI matrix**: Rust jobs — `cargo fmt --check`, `clippy -D warnings` (pedantic
  triaged), `cargo test` on Linux + Windows, cross-build `aarch64-unknown-linux-musl`
  + artifact upload, synthetic perf gate. Python jobs — existing ruff/ty/pytest over
  the surviving satellite. The v2 local-gate discipline stands: mirror CI exactly,
  read per-job conclusions, never infer (docs/dev/ci-gate.md).
- License: AGPL-3.0 header block in every `.rs` file, same as `.py`.

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Ratatui immediate-mode state management sprawl | Elm-style update loop fixed at R3 start (msg → update → view); R-UI plan defines it; spike validates feel |
| Termux terminal quirks (mouse, IME, keyboard variants, scrollback) | R0.2 spike tests every trick from DESIGN §14 before anything else is built |
| Journal design flaw found late | §3 frozen in R1 behind property tests + golden vectors; `v` field enables evolution; unknown-op preservation |
| Two fold implementations drift (Rust/Python) | Shared golden vectors; CI runs both against the same fixtures |
| Feature-parity creep | §8 is authoritative; PRs may not port anything marked Drop |
| Clock skew reorders LWW between devices | `ts` is a hybrid logical clock (§3.2), strictly monotonic per writer; cross-writer worst case a field resolves to the older edit — still in the journal, recoverable; acceptable for 1 user / 2 devices |
| Journal damage without a conflict file (Proton revert, partial sync) | Local high-water marks per journal + loud `ds status` regression anomaly (§3.3); Syncthing staggered versioning as the restore path |
| Interaction polish regressions (Esc/verbs/find-fast) | §4.5 invariants are the acceptance checklist for R3–R5; each gets a test |
| Big-bang cutover surprise | Cutover *rehearsed on a copy* first; parity hard-stop; archived v2 store as rollback; user runs it personally |
| Learning-codebase pressure vs. shipping | Commenting standard (§4.6) is part of review, not an afterthought; slices stay small |

## 12. Out of scope for v3.0 (recorded, not forgotten)

Auto-filing high-confidence proposals (D11 — desktop-only, later, needs its own
design); a `ds self-update`; image-embedding dedup enhancers; any on-phone VLM
(never, D8); OR-set collection merging (only on evidence LWW loses real edits);
Obsidian integration (dead with D7).
