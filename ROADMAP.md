# dossier roadmap

**Where we are (2026-07-22).** The Miller-columns home shipped (PRs #15–#31):
browse · detail pane · bottom-bar search · the full action set · touch/Termux ·
Nerd-Font icons · spacing/gutter polish. **Expiries now come from the Notion
_Marine Documents_ table** (#32). Since then: `ds reset` (#38), the **real
migration applied** on `…/Official Documents`, the **expiry-watch surface** (#39),
the **dedup engine** — perceptual page-hashing + subset/superset clustering with a
per-device cache (#40–#41), and the **reconcile view** with its full metadata-only
action set — dismiss/ack/link/adopt/unlink/fold/ignore-glob backed by a
`reconcile.toml` sidecar (#42–#45).

**Phases 4–6 are done too** (#46–#60): the **editable detail pane** (every field
inline; the DetailScreen/Move/Bundle modals retired) + **search as an in-place
Miller filter**; the **dismissable suggestions** layer (name-dates demoted to
accept/dismiss suggestions in the pane); and **bundles & export** (hierarchical
slugs, dates + chronological surface, folder→bundle suggestions, `ds export`).
Phase 7 (vision) is **done** — `ds scan` reads linked scans with a local VLM into
grounded readings that drive content-based succession (a reconcile tab), expiry/issue
suggestions (the detail pane), and per-run model selection; all verified on the real
store. **Phase 8 (organize, #4) is done** — `ds organize` gives every linked file a
canonical name (plan → `--apply`), the first surface that touches the real files;
verified read-only on the store (55 in-place renames, 2 shared-file flags, nothing
applied). **Phase 9 (intake) is essentially done** — the **inbox flow** (proposal engine + `ds intake`
CLI + the `IntakeScreen` review card) and the **full-tree scale-up** (`ds import` + a
resumable reading cache) have landed, reusing `organize`/`suggest`/`succession`. The one
remaining item, **phone sync-back, is deferred to Phase 13** — its automatic trigger *is*
the scan service, and the manual loop already works over synced `scans.toml`. Phases 10–13
sketch the **long horizon** — preparedness, answers, durability, platform hardening —
turning the catalogue into a living system.

Effort: **S** ≈ a few hours · **M** ≈ 1–2 slices · **L** ≈ several slices.
Per-item rationale lives in `DESIGN.md` §14.

## Phase 1 — Live on your real data
- [x] **Expiries from the Notion Marine Documents table** (authoritative) — #32
- [x] **`ds reset`** (S) — folder-data reset (clears `.dossier/` only, never the real
  files) + `--global` config reset. The safety net that makes re-importing painless. #38
- [x] **Applied** on `…/Official Documents` — 137 docs, 5 marine expiries, 57 files
  auto-linked. Old store backed up by `ds reset`.
- [x] **Reconcile / orphan view** — `ds reconcile` (CLI, #40) + the TUI reconcile
  screen (#42): orphan files (per-folder), docs whose file is missing, and duplicate
  clusters.
- [x] **Reconcile actions** — the full set, all metadata-only (never moves/deletes a
  real file), persisted in a `.dossier/reconcile.toml` sidecar:
  - `x` dismiss orphan / ack missing (#43) · `l` link · `a` adopt orphan→new doc ·
    `u` unlink dead rendition (#44) · `f` fold duplicate cluster · `g` ignore-glob (#45).

## Phase 2 — Expiry watch  ✅
- [x] **`ignore_expiry` toggle** (S) — a keypress to drop residual old CDCs from the watch.
- [x] **Expiry-watch surface** (M) — tracked docs soonest-first, "N tracked · M red"
  header, open / ignore from the list. #39

## Phase 3 — Dedup by visual similarity  ✅ *(engine)*
- [x] **Near-duplicate detection** — perceptual **dHash** per page (`dedup_hash`),
  fuzzy Hamming **containment** clustering that folds subsets under their superset
  (`dedup.group_files`), and a per-device page-hash **cache** keyed by size+mtime
  (`dedup_cache`). Surfaced in the reconcile view; folding is review-only (Phase 1
  reconcile actions), never automatic. #40–#41
  - *Deferred enhancers:* image **embeddings** (CLIP/SigLIP/DINOv2 or Qwen3-VL's
    encoder) for harder near-dupes, and a generative **VLM confirmer** on borderline
    pairs only. Baseline pHash already catches most re-scans GPU-free.

## Phase 4 — Editing & search ergonomics  *(pulled up, per priority)*
- [x] **Editable detail pane (col 3)** — the pane edits every parameter inline (name,
  dates, location + slot + subslot with neighbour-shift, tags, bundles, copy flags,
  renditions, `ignore_expiry`, notes); `e`/`n`/`m`/`b` all open it and the doctor jump
  edits inline. The DetailScreen / MoveScreen / BundleScreen modals are retired; the
  only editing surface left is the pane. Supersession stays a picker (`s`). ctrl+s
  save · double-Esc discard · ctrl+r StaleWrite reload; home bindings are gated while
  editing. (#46–#51). Gains an "accept suggestion" affordance once Phase 5 lands.
- [x] **Search as an in-place Miller filter** — the three columns stay put during
  search; the documents pane filters root-wide (locations snaps to "All"), the detail
  preview follows the highlighted top hit. `x` (expiring) rides the same path (#52).
  *Deferred polish:* per-location match counts + disabling zero-match rows.

## Phase 5 — Dismissable suggestions  ✅  (replaced the name-based date system)
- [x] **Suggestions framework** — `dossier/suggest.py` pure engine (`for_document` /
  `live`) + `Suggestion`/`SuggestionState` model + a `.dossier/suggestions.toml`
  sidecar. Per-document field suggestions (issue/expiry/notes); accept pre-fills the
  field in the detail pane (then ctrl+s), dismiss persists forever and never writes.
  Ambiguous numeric dates offer every reading as a picker (#53, #55).
- [x] **Demote name parsing → suggestions** — `migrate.build_plan` no longer writes
  issue dates or the "guessed expiry" from names; that intelligence now lives only in
  `suggest`. A date **range** becomes a *notes* period suggestion (never issue/expiry)
  unless the authoritative expiry confirms it as a validity window — so sea-service
  testimonials get their span in notes, no issue/expiry. CBT-type expiries arrive as
  keyword-driven suggestions to accept (#54).
  - *Verified on the real store:* 56/137 docs surface suggestions (23 period, 19 issue,
    14 expiry).

## Phase 6 — Bundles & export  ✅
- [x] **Bundle grouping** — **hierarchical slugs** (`travel/india-2024`) via
  `slugify_path` (#56); bundle **`date` + `created`** stamp with chronological
  `sort_bundles`/`group_bundles` (#57); a **bundles surface** (`B`) grouped by
  category, sorted by date, Enter filters the home via `Filter.bundles`, `d` sets a
  date (#58); **folder → bundle suggestions** — `suggest.bundles_from_folders` +
  `live_bundles` surfaced in the bundles screen with accept/dismiss, sharing the
  suggestions sidecar (#60). Verified: 3 sensible folder suggestions on the real store.
- [x] **`ds export`** — `dossier/export.py` plan/apply; copy (or `--symlink`) a
  bundle's member files into a folder, named by doc id, with problem flags
  (no-file/missing/exists) and `--dry-run`/`--force` (#59).

## Phase 7 — Vision & content suggestions  ✅
- [x] **`ds scan` extraction engine + CLI** — rasterize the first page (pypdfium2) and
  read a **structured, grounded** `ScanReading` from a local VLM over an
  OpenAI-compatible endpoint (a llama.cpp router; model + URL per-device config).
  Dates are extracted **verbatim** (never the model's own reordering); all-required
  schema + temp 0.1 make reads deterministic. Readings persist to `.dossier/scans.toml`
  (synced, so a phone benefits from a desktop scan); a size:mtime fingerprint skips
  unchanged files. Verified on the real store — 57/57 read, 0 low-confidence.
- [x] **Content-based succession** (#2) — `dossier/succession.py` clusters same-credential
  documents (shared document number, or type-core + issuer + holder) and proposes
  renewals; a fourth **reconcile "Succession" tab** accepts (`s`, sets `supersedes`) /
  dismisses (`x`). Verified on the real store: recovers the CoC-card, ENG-1 medical,
  and BRP chains, matching a filename-only inference.
- [x] **Expiry / issue suggestions from readings** (#3) — `suggest.from_reading` turns a
  reading's verbatim dates into issue/expiry suggestions (source `scan`), routed through
  `suggest.live` into the detail pane's accept/dismiss layer (deduped against name
  suggestions; a scan expiry drops out when the doc already has an authoritative one). A
  VLM-confirmed validity *window* gives issue+expiry; a non-window date pair (a
  sea-service period) becomes a notes span, not a spurious expiry. Verified on the real
  store: 11 expiry + 19 issue + 4 period suggestions surfaced.
- [x] **Model selection** (S) — `ds scan --list-models` lists the router's `/v1/models`
  (vision-capable ones flagged + sorted first, current one marked); `ds scan --model NAME`
  overrides the model for a run (URL/model are already per-device config).
  - *Stack in use:* Qwen3-VL-8B-Instruct (Q4) via a llama.cpp router behind the OpenAI
    `/v1/chat/completions` endpoint with a JSON schema; pypdfium2 @ ~170 DPI;
    desktop-only (an 8B VLM isn't viable on the phone).
- [x] **In-TUI scan + settings** (M) — scanning is no longer CLI-only: `v` scans the
  current document, a cancellable background worker (progress in the sub-title,
  save-after-each) scans all linked docs, and a `SettingsScreen` (`,`) edits icons +
  the scan endpoint/model/temp/DPI (model via a live `/v1/models` `Select`) + the synced
  expiry threshold, written back read-modify-write so unknown keys survive. All reachable
  from the `ctrl+p` command palette (`DossierCommands` provider). Verified live in a real
  terminal via the PTY driver.

## Phase 8 — Organize: metadata-driven filenames  ✅  *(#4, no vision)*
- [x] **`ds organize`** (M) — `dossier/organize.py` gives every *linked* file a canonical
  name from its record: `slugify(name)`, ISO `issue_date` prefix **gated** on the name not
  already embedding a date (`suggest.name_has_date`), lowercase ext. Pure plan (`OrganizeItem`
  /`OrganizePlan`, problem codes `shared-file`/`missing-file`/`exists`/`no-label`) → `--apply`,
  like `ds export` but **dry-run by default** since it mutates real files. Apply moves the file
  (`os.rename`, EXDEV→`shutil.move`) **then** rewrites the rendition path, rolling the move back
  if the save fails; never overwrites, never deletes, idempotent (`src==dst` → no-op). In place
  by default; `--to-folders` maps a doc's **primary tag** → a category folder via
  `[organize.folders]`. `canonical_stem` is the per-doc hook **Phase 9 intake** reuses.
  - *Verified read-only on the real store:* 57 linked → 55 in-place renames, 0 missing,
    0 occupied, **2 shared-file flags** (two records point at one PDF — surfaced, not resolved),
    0 date-prefixes (every dated doc still carries the date in its name — the gate proves
    itself). The review also surfaced genuine record-vs-file date drift to reconcile by hand.
    Nothing applied — awaiting the go-ahead to rename real files.
  - *Not category folders on day one:* tags were never written at migration (DESIGN §10 planned
    it; only the detail pane writes tags), so all 137 docs are untagged and `--to-folders`
    degrades to in-place with a `no-folder` note — dormant-but-correct until tags exist.
  - (Subset-elimination, idea #1, is already **Phase 3 dedup** — install `dossier[dedup]`,
    `ds reconcile` → Duplicates → `d` scan → `f` fold.)

## Phase 9 — Intake: zero-friction capture  *(long horizon)*
The highest-friction moment today is the most common future event: a new document
arrives, and filing it means scan → drop the file → reconcile → adopt → hand-fill
every field. Every piece needed to automate that already exists — this phase composes
them, and changes the app's economics: near-zero marginal cost per document is what
keeps the catalogue accurate in three years, and what makes owning the *whole* tree
(not just the curated 137) affordable.
- [x] **Inbox flow** (L) — *engine + CLI + TUI card landed; verified on the real store.*
  - [x] **Proposal engine + `ds intake`** — `dossier/intake.py` composes the record for a
    dropped file: `build_proposal` reads it with the VLM (injectable), names it from the
    reading's `document_type` (filename fallback), derives issue/expiry via `suggest`
    (ambiguous dates deferred to the pane as `open_questions`), finds a succession link via
    `succession`, and picks the canonical destination via `organize` (`--to-folders` with a
    `fallback_folder` so an untagged scan files into `Filed/`). `apply_proposal` never
    auto-applies: save record (still at the inbox path) → persist reading → move via
    `organize`'s rollback-safe rename. `ds intake [--from DIR] [--limit N] [--apply] [--yes]`
    is dry-run by default. Synced `[intake]` config (inbox / filed / a keyword→tag map —
    intake is the first surface that sets tags). Enabling touches: `unique_id` lifted to
    `store.py`; `fallback_folder` added to `organize`.
  - [x] **TUI review card** — `dossier/tui/intake.py` `IntakeScreen` (`I` + palette entry),
    one proposal at a time, each read by a background VLM worker: `a` file · `e` file+edit
    (hands to the detail pane) · `n` rename (re-slugs id + destination) · `s` toggle
    succession · `k` skip · `x` not-a-document · `o` open. Reuses the detail pane for edits.
  - *Verified read-only on the real store:* `ds intake --from "…/Official Documents"
    --limit 10` → 7 grounded proposals (name/date/canonical destination) + 3 graceful
    non-document skips (`.txt`/`.xlsx`), images read directly by the VLM, nothing written.
- [x] **Scale to the full tree** (M) — `ds import DIR [--limit N] [--apply]` bulk-imports a
  folder's unfiled files in place, sharing one loop with `ds intake`. A synced path-keyed
  reading cache (`.dossier/intake.toml`) reuses an unchanged file's reading (fingerprint
  match) instead of re-running the VLM, so a 900-file sweep is resumable and cheap — a
  fresh read persists immediately, a filed file's entry moves to `scans.toml`. *Verified on
  the real store:* a re-`import` of the same file served from cache in **1s vs 6s cold**, no
  re-scan. (Running the full ~900-file apply is the user's to do — a real-file mutation.)
- [→] **Phone intake rides sync-back — no on-phone VLM** (S) — **deferred to Phase 13**
  (the scan service). The plumbing already exists: `[intake]` config and `scans.toml` are
  synced, and the VLM never runs on the phone by construction — so a **manual** loop works
  *today* (drop a photo in the synced inbox on the phone → run `ds intake` on the desktop →
  the filed record + reading sync back). The only missing piece is the **automatic** desktop
  trigger, which *is* the Phase 13 `ds scan` service — so it lands there, not as separate
  Phase 9 code. When the desktop is reachable the phone's `ds scan` may instead point at its
  llama-server (already per-device URL config); vision inference never runs on the phone.

## Phase 10 — Preparedness: checklists, event-aware validity, reminders  *(in progress)*
Bundles are the app's real job (gather → check → submit, DESIGN §5) but today they are
passive labels. This phase makes dossier answer "am I ready?" — and warn *before* it
matters, against the date you need the document, not just today. *Event-aware validity +
reminders landed (slice 1); bundle templates next (slice 2).*
- [x] **Event-aware validity** (M) — `dossier/preparedness.py`, a pure engine: `event_status`
  reuses `Document.expiry_status` against a bundle's **event date** (plus an optional
  `min_valid_days` floor for "passport valid ≥ 6 months past the trip"), no new rule engine;
  `event_flags` flags members of a *future* dated bundle that lapse by then (superseded /
  ignored never nag). Surfaced in the expiry watch (a dim "· needed <date> for <slug>" note).
- [x] **Proactive reminders** (S) — `ds expiring [--days N] [--bundle SLUG] [--no-events]`:
  plain text, one line per doc needing attention (`expired` / `expiring` / `event`), **empty
  stdout when clean** so a Task-Scheduler / Termux-cron notification is quiet, exit **0/1/2**
  so a job can tell "nag me" from "the tool is broken". Event-aware by default. Verified
  read-only on the real store (3 marine expiries, soonest-first).
- [ ] **Bundle templates** (M) — a template (`.dossier/templates.toml`, a `Bundle.template`
  field) lists required document *types* as **match aliases** over name + tags + scan
  `document_type` (works today — names are typed — and sharpens as intake writes tags), with
  optional `count` / `min_valid_days` / `optional`. `preparedness.check_bundle` →
  gathered / problem / missing; surfaced as a `ReadinessScreen` checklist off the bundles
  screen (`t` attach template, `c` open checklist). *(Deferred: "add to bundle" from the
  checklist, template suggestions, a global `[validity.rules]` table.)*

## Phase 11 — Answers: content search & ask  *(long horizon)*
`.dossier/scans.toml` already holds structured, grounded readings of every linked scan
— currently used only for date suggestions and succession. Make the corpus queryable:
find documents by *what they say*, not what they were named. Guiding split: **vision
is enrichment at scan time (desktop-only); queries are text, on every device.** A
query never needs a VLM, so ask stays fast, cool, and battery-cheap on the phone.
- [ ] **Reading transcripts** (S) — extend `ScanReading` with a full-text transcript
  (+ keywords) emitted by the desktop VLM during the pass it already makes; synced via
  `scans.toml`, so every device gets richer search material for free.
- [ ] **Content search** (M) — `/` (and `ds open`) also matches reading text/
  transcripts, so "the doc with my INDoS number" is findable when name/tags don't
  mention it. Pure text search — no model, instant everywhere.
- [ ] **`ds ask` — retrieval-first** (M) — tiered: **Tier 0, no model** — structured
  field lookups ("when does my ENG-1 expire?") and BM25 over readings answer most
  questions deterministically. **Tier 1, small text-only model** — a 1–2B Q4 text
  model (e.g. Qwen3-1.7B) composes an answer from the retrieved snippets (~1–2k
  tokens of context): seconds on a phone CPU, offline. The VLM is never in the query
  path.

## Phase 12 — Bulletproof sync conflicts  *(long horizon)*
Today's handling (DESIGN §6) *contains* conflicts — the loader excludes
`.sync-conflict-*`, the TUI shows a banner, `ds doctor` lists them with a field-diff.
The bar for this phase: **no Syncthing conflict, on any sidecar, can silently lose an
edit — through any means necessary.**
- [ ] **Field-level three-way merge** (M) — when a conflict's changes don't overlap
  (different frontmatter fields / different docs in a toml), merge automatically and
  archive the conflict file; only overlapping edits need a human.
- [ ] **TUI resolve surface** (M) — a per-field pick UI for genuine overlaps (ours /
  theirs / edit), replacing "go run doctor and hand-edit"; resolved conflict files are
  archived to the local history dir, never deleted outright.
- [ ] **Cover every sidecar** (M) — documents get the attention today; extend
  detection + merge to `reconcile.toml`, `suggestions.toml`, `scans.toml`,
  `bundles.toml`, `locations.toml`, and synced `config.toml`.
- [ ] **Fault-injection tests** (M) — simulate concurrent edits, Syncthing conflict
  renames, stale writes, and partial syncs in the test suite; prove the no-silent-loss
  guarantee instead of asserting it.

## Phase 13 — Platform hardening & the scan service  *(long horizon)*
- [ ] **Cross-platform test matrix** (M) — CI proves Windows, Linux, and Termux, not
  just "pure Python so probably fine": platform-gated tests for `platform_open`,
  path/case handling, atomic writes (`EXDEV`), and the PTY driver on each OS.
  Investigate desktop Termux testing (qemu / Android emulator / proot-distro) for the
  Termux leg — **drop it if the maintenance cost outweighs the coverage**; a documented
  on-device smoke checklist is the fallback.
- [ ] **`ds scan` service install** (M) — one command installs the auto-scan as a
  Windows Scheduled Task / systemd user timer that scans new files in the background
  (what Phase 9's sync-back intake rides on). **Battery-aware by requirement**: never
  runs on battery or when any low-power/power-saver mode is active — plugged-in and
  idle only; skips cleanly (exit 0, logged) when gated.

## Notes
- **Quick wins:** `ds reset` (S), the `ignore_expiry` toggle (S).
- **Dependencies:** the editable detail pane (Phase 4) is the home for accepting
  suggestions — it ships with direct editing first and gains the accept affordance when
  the suggestions framework (Phase 5) lands. Phase 7 (vision) needs Phase 5 to land its
  proposals into.
- **Reconcile follow-ups** *(deferred, low priority)* — doctor checks for a document
  that links a *folded* duplicate copy and for stale sidecar entries (a `dismissed`
  path or `folded` keep that no longer exists on disk); a "show dismissed (N)" toggle
  to review/undo suppressions from the TUI (undo today = hand-edit `reconcile.toml`).
- **Open from reconcile / doctor** — `o` already opens the *file* under the cursor
  (orphans / duplicates / succession in reconcile; the finding's doc in doctor).
  Follow-up: also open the full **detail view** for a row's document, **reusing** the
  home detail pane rather than standing up a second surface. Worth a fable-advisor
  pass on the cleanest seam (the reconcile/doctor modals currently *dismiss* with a
  doc id for the home to open — a shared "open detail for id" path could serve both)
  before building.
- **Someday:** `createdTime` year-plausibility + "issued X expires Y" range parsing
  (fold into suggestions quality), slug finalization, Obsidian-vault confirmation.
