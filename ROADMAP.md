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
store. **Phase 8 (organize, #4) is next.**

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

## Phase 8 — Organize: metadata-driven filenames  *(planned; #4, no vision)*
- [ ] **`ds organize`** (M) — propose a canonical filename (and optional category folder)
  for each *linked* file from its document record (category / name / date), as a
  review → apply plan (like `ds export`: plan, `--dry-run`, apply). On apply, renames /
  moves the real file and updates the rendition path; never auto-moves. The thing that
  actually tidies a messy `Official Documents/` tree. Formalizes the old "Organize mode"
  note. (Subset-elimination, idea #1, is already **Phase 3 dedup** — install
  `dossier[dedup]`, `ds reconcile` → Duplicates → `d` scan → `f` fold.)

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
