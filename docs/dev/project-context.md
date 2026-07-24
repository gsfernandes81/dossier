<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# Project context

Durable facts about dossier that aren't obvious from the code or git history — the
things a fresh contributor (human or agent) should know before touching it. Status
by phase lives in [`ROADMAP.md`](../../ROADMAP.md); the design rationale in
[`DESIGN.md`](../../DESIGN.md); conventions and the dev-container in
[`CLAUDE.md`](../../CLAUDE.md). This file is the "why is it like this" companion.

## What it is

A cross-platform (Windows + Termux/Android) Python **Textual** TUI that replaces a
Notion "Documents" system with local, **Syncthing-synced** Markdown + TOML sidecar
files — one Markdown file per document, no database. Private GitHub repo
`gsfernandes81/dossier`, AGPL-3.0.

Every capability is a **pure engine module + thin CLI/TUI wrappers**
(organize / intake / preparedness / answers / merge / resolve / power / service are
pure-ish, with `ds …` and TUI faces). Keep that split when adding features.

## Sync topology (load-bearing, user-confirmed)

- The store **is** Syncthing-synced, but the synced folder is a **parent/ancestor**
  of `syncthing_root` — so `.stfolder` is **not** in the store root. Do **not**
  conclude "no Syncthing" from its absence.
- On the PC, Proton Drive additionally mirrors the store as an opportunistic cloud
  copy. This means a Proton revert can **propagate through Syncthing**, which is why
  verifying Syncthing's own file-versioning is the key Phase 15 doctor check.
- Proton Drive's cloud-sync FS can silently revert *sustained rapid* atomic writes;
  short/chunked runs persist reliably. Matters for big backfills (`ds scan --transcribe`).

## Hard product constraints

- **No VLM on the phone, ever** (user decision). The phone drops photos into the
  synced `[intake] inbox`; the desktop `ds service run` reads them and syncs the
  reading back via `intake.toml` / `scans.toml`. Queries are text-only on every device.
- **The scan service is built but NOT registered.** `ds service install` prints the
  plan and touches nothing; only `ds service install --yes` writes artifacts and
  registers a Scheduled Task / systemd timer. Registering is a system change the
  **user** performs — never run `--yes` autonomously.
- **Real-store operations are read-only unless the user explicitly green-lights a
  mutation.** On the primary Windows host the live store is at
  `…\Proton Drive\…\Official Documents` (~137 docs migrated from Notion, ~948 on the
  phone); per-device config at `%LOCALAPPDATA%\dossier\config.toml`. On the remote dev
  container no real store is mounted — tests use `tmp_path`.
- A full **native Android app was considered and rejected**; Termux glue is the phone
  story, at most a thin share-target/widget companion someday.

## Performance decisions that must not be undone

These were measured; reversing them regresses the phone or reintroduces file churn.

- **Don't reintroduce ruamel.** The store uses PyYAML (`CSafeLoader`/`SafeLoader` read,
  a private `_Dumper` with a `_Quoted` str + None→empty representers write). Output is
  **byte-identical** to the old ruamel output, verified against all 948 real docs — a
  C-vs-pure test plus the parity gate protect Syncthing from spurious churn. Keep
  `serialize` byte-identical. **libyaml** is the C backend: desktop wheels bundle it;
  Termux needs `pkg install libyaml` *before* the dep sync (pure-Python fallback still
  works, ~10× slower parse). `store.libyaml_hint()` self-resolves to `None` once active.
- **`load_all` reads in parallel then parses serially** — `ThreadPoolExecutor(16)` for
  the I/O (reads release the GIL and overlap ~5× on FUSE), serial parse (GIL-bound).
  The store lives on `/storage/emulated/0` (Android shared storage, FUSE) on the phone;
  relocating off FUSE was **rejected** for UX reasons.
- **The Review surface loads docs once** (`_snapshot()`) and threads that list through
  `reconcile.run(..., docs=)` and `doctor.run(..., docs=)`. **Don't add another
  `store.load_all` to that screen — reuse `_snapshot()`.**
- **Integrity (in-app `ds doctor`) is deferred** — it runs in a thread worker on first
  tab-open, never as the default tab; a doc write invalidates it.
- **`cli.py` keeps lean top-level imports** — command modules import inside their
  `cmd_*` handlers; `scan.py` imports `urllib` lazily. `test_cli_import_stays_lean`
  (a subprocess `sys.modules` check) guards it. **Don't re-add eager engine imports to
  `cli.py`'s top.**
- **`ds profile`** (`dossier/profiling.py`) is a read-only timing harness — re-run it
  after any perf change.

## The TUI, at a glance

One screen, every mode a CSS class toggle (see `dossier/tui/home.py` docstring and
DESIGN §14). **Find-fast**: the home binds no letters — a printable is always the start
of a search, routed by a screen-level `on_key`. Occasional actions live in one shared
catalog (`dossier/tui/commands.py`) reached through the command palette. **Review** is
not a modal — it lives as columns 1–2 of the home's miller view (`dossier/tui/review.py`),
with the detail pane as column 3; acting on a finding shows the record beside it. Watch /
Bundles / Intake / Settings are still `ModalScreen`s. The `check_action` gate is the app's
one answer to "is this actionable right now"; every command surface must respect it.

## Standing workflow

- Design each substantial phase with a **Fable advisor** first (Agent tool,
  `model:"fable"`, `subagent_type:"Plan"`, run in background), then build in
  independently shippable, CI-green slices.
- **Run the full local CI gate before every push, and read the conclusion** — see
  [`ci-gate.md`](ci-gate.md). TUI tests must poll for effects, never sleep-then-assert —
  see [`testing.md`](testing.md).
- Merge to `main` and push per slice. `main` tracks `origin/main`; **fetch before
  pushing** — other machines/agents occasionally land commits on `main`.
