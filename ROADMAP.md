# dossier roadmap

**Where we are (2026-07-22).** The Miller-columns home shipped (PRs #15–#31):
browse · detail pane · bottom-bar search · the full action set · touch/Termux ·
Nerd-Font icons · spacing/gutter polish. **Expiries now come from the Notion
_Marine Documents_ table** (#32). Since then: `ds reset` (#38), the **real
migration applied** on `…/Official Documents`, the **expiry-watch surface** (#39),
the **dedup engine** — perceptual page-hashing + subset/superset clustering with a
per-device cache (#40–#41), and the **reconcile view** with its full metadata-only
action set — dismiss/ack/link/adopt/unlink/fold/ignore-glob backed by a
`reconcile.toml` sidecar (#42–#45). 129 tests, CI green.

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

## Phase 5 — Dismissable suggestions  (replaces the name-based date system)
- [ ] **Suggestions framework** (M) — per-document suggestions for fields (esp.
  issue/expiry): accept, or **dismiss individually**; dismissals persist; never
  auto-write. (Accepted in the editable pane from Phase 4.)
- [ ] **Demote name parsing → suggestions** (M) — the current name-based date parsing
  stops being an authority and feeds the suggestions layer instead. We don't rely on
  filenames to decide whether a doc even has an expiry. Period-docs (sea-service
  testimonials, voyage records): `expiry = None`, span → `notes`, and **do not** take
  the issue date from the range's start.
  - *Motorcycle expiries* (CBT, etc.) aren't in the Marine table — they arrive here as
    name suggestions to accept, or via manual entry, until/unless a structured source
    exists.

## Phase 6 — Bundles & export
- [ ] **Bundle grouping** (M) — several joining-docs / travel-docs bundles want
  structure:
  - **Hierarchical bundle slugs** like tags (`joining/mv-ship-2024`, `travel/india-2024`,
    `visa/us-2025`); group by the top segment in a bundles pane (*joining ▸ / travel ▸*).
  - An optional **`date`** on a bundle (the joining/trip date) + a **`created`** stamp →
    **sort bundles chronologically** (date, else creation order).
  - A **bundles surface** grouped by category + sorted by date (sibling of the watch).
  - **Folder → bundle suggestions** — the `Travel Documents/…` / joining folders become
    suggested bundles to accept.
- [ ] **`ds export`** (M) — export a bundle's files to an external folder (copy or
  symlink), the original "gather the files for this application" goal.

## Phase 7 — Vision suggestions  *(deferred)*
- [ ] **`ds scan`** (L) — a local VLM (llama.cpp) reads linked scans and **suggests**
  issue/expiry dates + an expires-vs-period classification into the suggestions layer,
  with grounding + validation (quote the source text, cross-check, low temperature).
  Review only, never auto-apply.
- [ ] **Model selection in settings** (S) — pull the available models from your
  llama.cpp **router** (`/v1/models`) and choose per run.
  - *Researched stack:* Qwen3-VL-8B-Instruct Q4 (or Qwen2.5-VL-7B fallback) served by
    `llama-server --mmproj` behind the OpenAI `/v1/chat/completions` endpoint with a
    JSON grammar; PyMuPDF @ ~250 DPI; desktop-only (an 8B VLM isn't viable on the phone).

## Notes
- **Quick wins:** `ds reset` (S), the `ignore_expiry` toggle (S).
- **Dependencies:** the editable detail pane (Phase 4) is the home for accepting
  suggestions — it ships with direct editing first and gains the accept affordance when
  the suggestions framework (Phase 5) lands. Phase 7 (vision) needs Phase 5 to land its
  proposals into.
- **Organize mode** *(feature creep, opt-in, later)* — propose a canonical folder +
  filename for each *linked* file from its document record (category / name / date), as
  a plan you review and apply. Never auto-moves. The thing that would actually *sort* a
  messy `Official Documents/` tree.
- **Reconcile follow-ups** *(deferred, low priority)* — doctor checks for a document
  that links a *folded* duplicate copy and for stale sidecar entries (a `dismissed`
  path or `folded` keep that no longer exists on disk); a "show dismissed (N)" toggle
  to review/undo suppressions from the TUI (undo today = hand-edit `reconcile.toml`).
- **Someday:** `createdTime` year-plausibility + "issued X expires Y" range parsing
  (fold into suggestions quality), slug finalization, Obsidian-vault confirmation.
