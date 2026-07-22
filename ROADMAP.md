# dossier roadmap

**Where we are (2026-07-22).** The Miller-columns home shipped (PRs #15–#31):
browse · detail pane · bottom-bar search · the full action set · touch/Termux ·
Nerd-Font icons · spacing/gutter polish. **Expiries now come from the Notion
_Marine Documents_ table** (#32) — the authoritative source, not document names.
92 tests, CI green.

Effort: **S** ≈ a few hours · **M** ≈ 1–2 slices · **L** ≈ several slices.
Per-item rationale lives in `DESIGN.md` §14.

## Phase 1 — Live on your real data
- [x] **Expiries from the Notion Marine Documents table** (authoritative) — #32
- [ ] **`ds reset`** (S) — folder-data reset (clears `.dossier/` only, never the real
  files) + `--global` config reset. The safety net that makes re-importing painless.
- [ ] **Apply the migration** *(milestone)* — the one-time real import: `ds init` on the
  real Syncthing folder, `ds migrate` (dry-run → review → `--apply`). Writes the 137
  document records — marine expiries + file links + locations — into `.dossier/`, so
  the TUI holds your actual documents instead of fixtures. Dogfooding starts here.
- [ ] **Fuzzy file-suggestion review flow** (M) — a TUI screen to accept/reject the
  ~47 suggested file matches the migration couldn't auto-link.

## Phase 2 — Expiry watch (now backed by real data)
- [ ] **`ignore_expiry` toggle** (S) — a keypress to drop residual old CDCs from the watch.
- [ ] **Expiry-watch surface** (M) — the mockup's 5th screen: tracked docs
  soonest-first, an "N tracked · M red (≤ threshold)" header, open / ignore from the
  list. (`query.tracked` logic is already done.)

## Phase 3 — Dedup by visual similarity  *(sooner rather than later, not first)*
- [ ] **Near-duplicate detection** (M–L) — the same document scanned more than once
  from different sources at different times. **Propose merges** (keep one rendition,
  fold the rest); review only, never automatic.
  - **No VLM required.** Baseline: **perceptual hashing** (`imagehash` over the
    rasterized page) — cheap, GPU-free, catches most re-scans. *Benefits from* an image
    **embedding** model (CLIP/SigLIP/DINOv2, or Qwen3-VL's vision encoder via llama.cpp)
    for harder near-dupes (different source/crop/quality), and a generative **VLM only as
    an optional confirmer** on the few borderline candidate pairs (running it on every
    pair is O(n²), infeasible). Pipeline: pHash → optional embeddings → optional VLM
    confirm.

## Phase 4 — Editing & search ergonomics  *(pulled up, per priority)*
- [ ] **Editable detail pane (col 3)** (L) — edit every parameter inline (name, dates,
  location/slot, tags, bundles, flags, renditions, `ignore_expiry`, supersession).
  Absorbs the `e` edit modal and folds `b`/`s`/`m` into inline fields, freeing column 2
  for navigation. (Gains an "accept suggestion" affordance once Phase 5 lands.)
- [ ] **Search as an in-place Miller filter** (M) — keep the three columns during
  search; filter the documents pane in place (root-wide), detail preview following the
  highlight.

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
- **Someday:** `createdTime` year-plausibility + "issued X expires Y" range parsing
  (fold into suggestions quality), slug finalization, Obsidian-vault confirmation.
