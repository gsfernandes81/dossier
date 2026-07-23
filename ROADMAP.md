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
the scan service, and the manual loop already works over synced `scans.toml`. **Phase 10
(preparedness) is done** — event-aware validity, `ds expiring` reminders, and bundle
templates with a readiness checklist. **Phase 11 (answers) is done** — content search,
`ds ask`/`ds open`, and scan transcripts. **Phase 12 (bulletproof sync conflicts) is
done** — a field-level 2-way merge with mtime last-writer-wins, `ds resolve` + an in-app
ResolveScreen, every sidecar covered, and fault-injection tests proving no silent loss.
**Phase 13 (platform hardening & the scan service) is done** — a Windows + Linux CI matrix
with platform-gated tests, and a battery-aware background scan service (`ds service run` +
a build-but-don't-run installer) that closes the phone sync-back loop. **Phases 1–13 are
complete.** Phases 14–15 are next: **find-fast UX** (launch optimized for the urgent
lookup, undo, init/empty-state polish, typo-tolerant search) and **Syncthing integration**.

**Next up — "Review in the miller view"** (first open item in Phase 14). Review stops being
a modal that must be destroyed to act on a row; it takes columns 1–2 and the detail pane
becomes column 3. It carries four fixes found in real use: per-tab keys, dismissing a
false-positive duplicate, Integrity taking the app-wide verb, and opening both sides of a
succession.

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
- [x] **Phone intake rides sync-back — no on-phone VLM** (S) — **closed in Phase 13**.
  The automatic desktop trigger is now `ds service run`: a photo dropped in the synced inbox
  on the phone is read by the desktop service and its reading synced back via `intake.toml` /
  `scans.toml`, filed from either device's review card — the VLM never runs on the phone. See
  the "Background scan & phone intake" section of the README (config + a `termux-url-opener`
  snippet). Needed no new phone-side code, only the service + docs.

## Phase 10 — Preparedness: checklists, event-aware validity, reminders  ✅
Bundles are the app's real job (gather → check → submit, DESIGN §5) but today they are
passive labels. This phase makes dossier answer "am I ready?" — and warn *before* it
matters, against the date you need the document, not just today. **Done** — event-aware
validity + reminders (slice 1) and bundle templates + the readiness checklist (slice 2).
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
- [x] **Bundle templates** (M) — a template (`.dossier/templates.toml`, a `Bundle.template`
  field) lists required document *types* as **match aliases** over name + tags + scan
  `document_type` (works today — names are typed — and sharpens as intake writes tags), with
  optional `count` / `min_valid_days` / `optional`. `preparedness.check_bundle` →
  gathered / problem / missing; surfaced as a `ReadinessScreen` checklist off the bundles
  screen (`t` attach template, `c` open checklist), with a per-bundle readiness summary on
  its row. *(Deferred: "add to bundle" from the checklist — engine `candidates` field ships
  dark; template suggestions; a global `[validity.rules]` table; a `ds ready` CLI twin.)*

## Phase 11 — Answers: content search & ask  ✅
`.dossier/scans.toml` already holds structured, grounded readings of every linked scan
— currently used only for date suggestions and succession. Make the corpus queryable:
find documents by *what they say*, not what they were named. Guiding split: **vision
is enrichment at scan time (desktop-only); queries are text, on every device.** A
query never needs a VLM, so ask stays fast, cool, and battery-cheap on the phone.
**Done** — content search, `ds ask`/`ds open`, and the transcribe pass all shipped.
- [x] **Content search** (M) — `query.reading_text` + an optional `readings` map on
  `search`/`matches`, so the `/` filter (and `ds open`) match a doc by its scan's fields.
  Verified on the real store: "bernhard" finds 4 testimonials by issuer, "ENG10166083"
  the medical by number — invisible to name search. Query stays import-light.
- [x] **`ds ask` — retrieval-first, Tier 0** (M) — `dossier/answers.py`: intent routing
  (expiry/issue/number/location) → the authoritative record field, target doc found by
  stdlib **Okapi BM25** over name/tags/notes + reading (superseded excluded; date intents
  prefer the record carrying the structured date, latest first); unknown questions fall
  back to ranked retrieval. `ds ask "…"` (0/1/2) + `ds open QUERY [-n]`. No model, offline.
  - *Known limit (data, not code):* a document whose expiry lives only in its name/scan
    text (e.g. the ENG-1 records — no structured `expiry_date`, no accepted succession)
    is answered from the top-ranked scan text; accepting the succession / setting the
    expiry fixes it. Tier 1 (a small text model composing prose, `--compose`) is deferred.
- [x] **Reading transcripts** (S) — a second `scan.transcribe()` VLM call (own schema/prompt,
  4096-token budget) adds a full-text transcript + keywords to `ScanReading` (byte-stable for
  legacy readings); `ds scan --transcribe` batch-backfills (interactive scans stay fast).
  Body content is now findable — `SUNTECH` (a transcript-only name) matched 2 extra docs on
  the real store. **Search inclusion is opt-in:** the `/` filter excludes transcript body by
  default; **ctrl+t** toggles "search inside scans" (placeholder cue + notify + `?` help).
  `ds ask` always uses the full content. *(Real-store backfill grinds in the background — the
  57-doc VLM pass is ~15 min, chunked/resumable; the code + toggle don't depend on it.)*

## Phase 12 — Bulletproof sync conflicts  ✅
The bar for this phase — **no Syncthing conflict, on any sidecar, can silently lose an
edit** — is met. `dossier/merge.py` is a pure field-merge engine; `dossier/resolve.py`
discovers `.sync-conflict-*` files, plans a merge, and applies it crash-safely.
- [x] **Field-level merge** (M) — a **2-way** merge (there is no reliable common
  ancestor, so 3-way was dropped): agreed→keep, one-side-empty→fill, collections→union,
  and genuinely contested scalars→**last-writer-wins by file mtime** (the confirmed
  policy). Non-overlapping edits merge with no human; the losing copy is archived, so
  even an LWW verdict is recoverable, never a silent loss.
- [x] **Resolve surface** (M) — `ds resolve` (dry-run by default, `--apply`) and an
  in-app **ResolveScreen** (shift+R): both preview each conflict's contested fields +
  verdict, then merge; conflict copies are archived to the local history dir, never
  deleted outright. *(A manual per-field ours/theirs/edit override is a possible future
  enhancement; the LWW auto-policy + recoverability covers the confirmed requirement.)*
- [x] **Cover every sidecar** (M) — documents, `reconcile.toml`, `suggestions.toml`
  (union-only), `scans.toml`/`intake.toml` (transcript-preferring), `bundles.toml`,
  `locations.toml`, and `templates.toml`/`config.toml` (whole-file LWW, surfaced loudly).
- [x] **Fault-injection tests** (M) — `test_merge.py` + `test_resolve.py` inject a fault
  at each apply step (write fails · unlink fails · a concurrent write races us) and a
  compare-and-swap on the live copy; they *prove* the no-silent-loss guarantee.

## Phase 13 — Platform hardening & the scan service  ✅
- [x] **Cross-platform test matrix** (M) — CI is three jobs: `check` (lint/type, once),
  `test` and `driver` both over a **Windows + Linux** matrix (macOS dropped — unsupported;
  Termux CI dropped for an on-device smoke checklist, per the escape hatch). Platform-gated
  tests: per-OS `platform_open` opener argv + a real Windows `os.startfile` path, the
  same-directory-temp invariant (the EXDEV defense — `atomic_write_bytes` needs no copy
  fallback by construction), a real-pypdfium2 rasterize on the extras leg, and the PTY driver
  per OS. Fixed a real `unique_id` case-fold bug the Linux leg now guards.
- [x] **`ds scan` service** (M) — `ds service run` performs one power-gated, single-instance
  pass (scan + transcribe + intake), **never on battery / power-saver / unknown AC** (pure
  `power.decide` over injectable readings), exit 0 when gated. `ds service install` generates a
  Windows Scheduled Task / systemd user timer pointing at it and **prints the plan without
  touching the system** unless `--yes` is given; `status`/`uninstall` round it out. Closes the
  Phase 9 phone sync-back (config + README docs; no on-phone VLM).

## Phase 14 — Find-fast UX: launch, undo, first-run, fuzzy search
This store is write-few read-fewer — but when a find is required (you're looking for a
document *urgently*), it is required **now**. Launch optimizes for that, and the rest of
the phase builds confidence: edits are reversible, first contact explains itself, and
search forgives phone-keyboard typos.
- [x] **Find-fast launch** (S/M) — **done.** A screen-level `on_key` routes any printable
  typed in the columns straight into search (first character kept), so a find never needs a
  mode key; `Enter` opens the top match's **file** and `→` drills to **detail** (a verb now
  applied consistently). For typing to always mean "search" the home keeps **no letter
  bindings** — `o`/`e`/`n`/`b`/`a` and bare `q` moved to the command palette and the touch
  buttons (`ctrl+q` quits). The same verb was swept across the other **document**-listing
  surface, the expiry **watch** (`Enter` opens the file, `→` details); bundles and review keep
  their own activate targets, which list bundles and records rather than documents. Termux
  launches **type-first** (search focused); desktop keeps the
  list focused since the router lands the first key in search anyway. Attention counts
  (expiring · conflicts · inbox) now ride dim **beside the footer**, replacing a toast that
  overlapped the search box. Budget met: cold start → `pass` + `Enter` → opened = 5 keystrokes.
- [ ] **Review in the miller view** (M/L) — **next.** Review is a modal, so acting on a row
  *destroys* it: activating an Integrity finding dismisses the screen to show the record,
  losing the finding, the tab and the cursor — and Esc back runs `action_review()`, which
  builds a **fresh** ReviewScreen and re-runs the entire load. The round trip is lossy, and
  on the phone slow. Instead let review **replace columns 1 and 2** of the miller view and
  let the home's existing detail pane serve as **column 3**, appearing as needed without ever
  tearing review down. It deletes rather than adds: the `ReviewResult` dismiss protocol and
  the `_detail_origin == "review"` Esc special-case both go away. Feasible because
  `DetailPane` already talks to its host purely by message (`EditingChanged` / `Saved` /
  `ReloadRequested`) — it never needed to be the home's. Three seams to watch: the home's
  type-to-search `on_key` router must not fire while review holds the columns, `escape` means
  something to both, and the narrow/Termux collapse needs its own rule. Carries four fixes
  found in real use, each cheap once the layout is right:
  - **Per-tab keys** — `check_action` returns `None` for an inapplicable action, and Textual
    reads `None` as disabled-but-**visible** (`False` is disabled+hidden). So every tab
    advertises every tab's verbs, greyed — which is why "how do I dismiss a duplicate?" and
    "accept vs fold vs link?" both came up in use. Return `False` and the footer and `?` panel
    become per-tab for free, with no help content to author or keep in sync; the
    `refresh_bindings()` on `TabActivated` is already wired (its comment is simply not true
    yet). Check nothing depends on a greyed key still firing before flipping it.
  - **Dismiss a false-positive duplicate** — `dedup` matches on visual similarity, so it will
    sometimes cluster two genuinely different documents. Duplicates is the only tab with no
    way to say *no*: `ReconcileState` suppresses orphans (`dismissed`), missing (`missing_ok`)
    and succession (`succession_dismissed`), but for duplicates only `folded`, which asserts
    the opposite. Folding a false positive is actively wrong — `suppressed_orphans()` hides
    every folded subset, so a genuinely different document that is still an orphan vanishes
    from the list that would have prompted you to adopt it. Add `dup_dismissed` keyed by keep
    + subsets exactly as `folded` is, reusing `covers()` so a **new** copy resurfaces the
    cluster for a fresh decision, then wire `x` — after which `f` affirms and `x` rejects,
    like everywhere else.
  - **Integrity takes the app-wide verb** — `Enter` opens the file, `→` focuses the record, so
    the tab reads like the home and the watch. The record is the more useful target for most
    findings (date-order, supersession, location-ref and round-trip are sidecar problems a PDF
    cannot fix), which is precisely why keeping it *beside* the finding is the win here.
  - **Open both sides of a succession** — `o` opens only the newer rendition
    (`_succession_rendition_path`), but "does this renewal really replace that one?" is a
    comparison, so one file cannot answer it. Open older then newer so the renewal lands
    frontmost, and open whichever side exists when the other is paper-only. Termux degrades
    gracefully: sequential `termux-open` intents share a viewer, so the phone shows the
    newer — today's behavior, not a regression.
- [ ] **Undo / history restore** (M) — every save already writes the prior version to the
  local history dir; surface it. `ctrl+z` in the detail pane restores the last saved
  version of the current doc; a palette "History…" lists a doc's saved versions to
  restore from. Restores are themselves saves (history'd), so undo is always undoable.
- [ ] **First-run & empty states** (S) — `ds init` walks root pick → glyph check → Termux
  preconditions conversationally; every empty surface (no config, empty store, empty
  inbox, no matches) says exactly what to do next instead of rendering blank.
- [ ] **Typo-tolerant search** (M) — `/`, `ds open`, and `ds ask` retrieval forgive small
  edit distances ("cerificate" → Certificate), rank exact > prefix > fuzzy so precision
  never suffers, and stay fast on Termux (the corpus is small — no index needed).

## Phase 15 — Syncthing integration: orchestrate, don't own
Syncthing is the transport (the PC folder also lives in Proton Drive for an opportunistic
cloud copy — meaning a Proton revert can *propagate* via Syncthing, which makes Syncthing's
own versioning the recovery net and verifying it non-negotiable). Talk to Syncthing's REST
API; never bundle, spawn, or reimplement it.
- [ ] **Doctor checks** (S/M) — via the REST API on localhost: Syncthing reachable ·
  resolve which Syncthing folder *contains* the store (the synced folder is an
  **ancestor** of `syncthing_root` — `.stfolder` lives at the synced parent, not the
  store root, so match the store path against the REST folder list, never expect a
  marker in the store) · that folder shared and not paused · **file versioning
  enabled** (the net against a propagated Proton revert) · device
  connectivity/last-seen. Degrade gracefully to "Syncthing not reachable — checks
  skipped" rather than failing doctor.
- [ ] **Sync-aware service + TUI** (M) — the scan service waits for sync-idle before batch
  writes (don't race an incoming sync); a footer glyph on the home shows sync state
  (idle / syncing / conflict / unreachable).
- [ ] **Termux feasibility investigation** (S, first) — *open question for a future agent:*
  how to reach the API from Termux. Syncthing-Fork on Android exposes the same REST API on
  127.0.0.1:8384 (API key in its settings/config.xml) — verify reachability from Termux,
  where the key can live per-device, and what the Play-vs-F-Droid build differences are.
  If the phone leg proves brittle, ship desktop-only checks and keep the phone read-only.

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
- **Finish the Enter/→ verb sweep** *(deferred — come back to this)* — Phase 14.1 settled
  the app-wide verb (**Enter opens the file, `→` opens detail**) and applied it to the home
  and the expiry watch, the two surfaces that list *documents*. **Bundles** and **review**
  were left alone on purpose, not forgotten: bundles lists bundles (its `Enter` scopes the
  home to one — already the right activate for that object, and there is no file), and
  review lists reconciliation *records*, keeping the Phase-3 map (`o` = file, `Enter` =
  record) — on its Missing tab the file is gone by definition, so Enter-opens-file would be
  dead on the tab that most needs a verb. Worth revisiting both, because the rows are not
  all the same kind of thing: review's **duplicates** and **succession** rows *are*
  documents and could take the verb, while **orphans** rows are files with no document yet;
  bundles could plausibly use `→` for "show me what's in it". Do it as one deliberate pass
  (fable-advisor first) rather than per-surface drift — the point of the verb is that it is
  predictable. **Partly settled:** *Integrity* adopts the verb as part of "Review in the
  miller view" above, which is also what makes `→` cheap everywhere else in review — with
  the detail pane sitting in column 3, "show the record" stops meaning "tear the screen
  down". Related: `open_doc_file()` in `tui/screens.py` is already the shared open-the-file
  seam; once review is a column, the home's `open_detail()` is the matching seam for `→`,
  and the "dismiss with a doc id" protocol it replaces goes away.
- **Someday:** `createdTime` year-plausibility + "issued X expires Y" range parsing
  (fold into suggestions quality), slug finalization, Obsidian-vault confirmation.
