# dossier — Design (v2)

**Status:** Design settled, pre-implementation
**Date:** 2026-07-21
**Revision:** v2 — revised after an independent adversarial design review (see §15 changelog).
**Author:** gsfernandes81 (with Claude)

A cross-platform TUI for tracking personal documents — physical and digital — replacing an
elaborate Notion setup. Runs on Windows and Android (Termux). Finds documents by name/tags,
tracks physical storage location, links to the Syncthing-synced soft copies, and opens them
with the platform's native opener.

---

## 1. Goals & non-goals

### Goals
- Replace the Notion "Documents" system entirely; local files become the source of truth.
- Fast search by **name** and **tags**; sort/group by **physical location**.
- Preserve Notion's key behavior — the **permanent/temporary location override** (a doc has a
  permanent home but may currently be elsewhere).
- **Direct links to soft copies** in a Syncthing folder, openable from the TUI.
- New capabilities Notion lacked: **issue/expiry dates** with an expiring-soon view,
  **hierarchical tags**, **bundles** (application/trip sets that replace file duplication),
  and **file⇄record reconciliation**.
- **Durability**: survive the three real ways this data gets damaged — sync conflicts, stale
  writes, and accidental deletion (§6).
- One pure-Python codebase on Windows and Termux.

### Non-goals
- No bidirectional Notion sync; migration is a one-time cutover.
- No cloud service or server. Sync is delegated entirely to Syncthing.
- No GUI (Obsidian may optionally open `.dossier/` as a vault, but is not required).

---

## 2. Background — the current Notion system

Two related databases: **📁 Documents** (137 rows) and **🗄️ Document Storage** (9 locations).

Documents schema migrated from: `Name` (title, ≈ filename), `Permanent/Temp Storage` (relation),
`Permanent/Temp Slot` (number, e.g. `1.3`), `Storage`/`Slot` (formulas = effective location/slot,
temp overriding permanent), `Carried to India` (checkbox), `Notes`.

Locations: `Backpack (Carlton)`, `Blue Pouch`, `Cert File #2048`, `Destroyed`,
`File #4096 @ Home`, `KTM RC125 File`, `Leather #1024`, `Ship's Folder`, `Softcopy Only`.

**Preserve:** the effective-location override, and grouped/sorted-by-location tables.
**Fix:** locations overloaded as state (`Softcopy Only`, `Destroyed`); dates trapped in names
(`ENG-1 Med Cert Expires 10-07-26`); no tags; no link to the actual soft-copy files.

### The soft-copy tree (`Official Documents/`, ~900 files, nesting up to 8 deep)
```
Marine/ (573)            → Applications, Joining Documents, Sea Service Testimonials,
                           Safety Course Certs, Medicals, CDC Scans, Certificate of Competency…
Identity Documents/ (198)→ Passports, Driving Licenses, BRPs, Passport Photos
Travel Documents/ (69)   → "2026-04-18 London, Goa, Mumbai, Singapore trip", …
Visas/ (48)              → "US Visa Application", "2026-04-12 Indian Visa Application", …
```
Folders do two jobs: **category folders** (`Marine`, `Passports`) say *what a doc is* → **tags**;
**bundle folders** (`US Visa Application`, trips) say *what a doc was gathered for* and are the
source of file duplication → **bundles**. These become two distinct concepts (§5).

---

## 3. Data model

### Document
Stored as one Markdown file per doc. **The filename (minus `.md`) is the `id`** — the single
source of truth; there is no `id` frontmatter field.

| Field | Type | Notes |
|---|---|---|
| `name` | str | title; need not equal the filename |
| `tags` | list[str] | hierarchical, e.g. `marine`, `marine/coc`; a parent filter includes children |
| `bundles` | list[slug] | bundle membership (see §5) |
| `issue_date` | date? | ISO 8601 |
| `expiry_date` | date? | ISO 8601 |
| `has_physical` | bool | whether a physical copy exists |
| `has_digital` | bool | whether a soft copy exists |
| `files` | list[Rendition] | digital renditions of this one logical doc |
| `perm_location` | slug? | references a Location **by slug** |
| `perm_slot` / `perm_subslot` | int? | subslot optional |
| `temp_location` | slug? | override |
| `temp_slot` / `temp_subslot` | int? | override |
| `notes` | markdown | free body text |

Derived at runtime (never stored):
- `effective_location` / `effective_slot` = temp if `temp_location` set, else permanent.
- `expiry_status` ∈ {`expired`, `expiring` (≤ synced threshold, default 90d), `ok`, `none`}.
- `file_status` ∈ {`ok`, `missing`, `none`}.

### Rendition
```
label:   str        # "complete", "front-and-back", "default"
path:    str        # POSIX, relative to the device's syncthing_root
primary: bool       # opened/exported by default; no per-bundle pinning
```
Handles passport-complete vs passport-front-and-back: **one document, multiple renditions.**

### Location — in `locations.toml`, keyed by slug
```toml
[cert-file-2048]
title = "Cert File #2048"
notes = ""
```
No `kind`/state field — `Destroyed`/`Softcopy Only` are represented by the `has_physical` /
`has_digital` flags, not by pseudo-locations.

### Bundle — in `bundles.toml`, keyed by slug (all fields optional)
```toml
[us-visa]
title = "US Visa Application"
export_dir = "~/Desktop/US Visa Submission"
```
Membership lives in each Document's `bundles:` list. A slug that appears only in doc frontmatter
is a **valid bundle with a default title** — so a missing `bundles.toml` entry degrades to
"missing metadata," never an orphaned reference.

---

## 4. Storage format & layout

Flat files, **one Markdown file per document** with YAML frontmatter. Chosen because Syncthing
does file-level sync (no merge): per-file edits only ever conflict on the *same* doc, never the
whole DB. Human-readable and greppable without the tool.

```
<syncthing_root>/                 ← differs per device; NOT itself synced
├─ .dossier/
│  ├─ documents/*.md              ← one per doc (id = filename)
│  ├─ locations.toml
│  ├─ bundles.toml
│  ├─ config.toml                 ← SYNCED shared settings
│  └─ migration/                  ← raw Notion export archived at cutover
└─ … the actual PDFs / scans …
```

`.dossier/` is dot-prefixed (idiomatic beside the existing `.stfolder`/`.stignore`, hidden from
Android browsers, keeps the root tidy). Obsidian can open `.dossier/` **directly** as a vault
*(still to be confirmed — §14)*.

### Serialization rules (mandatory — see §6)
- **Always quote string scalars.** (`perm_location: cert-file-2048` is safe as a slug, but
  values like `Cert File #2048` as a *title* MUST be quoted — an unquoted ` #` starts a YAML
  comment and silently truncates the value.)
- **Byte-stable output**: fixed key order, stable quoting, trailing newline — so an edit
  produces a minimal diff and conflict resolution/history stay legible.
- **Atomic writes**: temp file **in the same directory** as the target (not `$TMPDIR` — a
  different filesystem on Termux, which makes `os.replace` fail with `EXDEV`), then `os.replace`.
- **POSIX path separators** stored always; resolve via `PurePosixPath` against the local root.

### Example document (`documents/coc-card-2025.md`)
```markdown
---
name: "Certificate of Competency (CoC) Card 10-02-25 to 28-09-26"
tags: [marine, marine/coc]
bundles: [us-visa, india-trip-2026]
issue_date: 2025-02-10
expiry_date: 2026-09-28
has_physical: true
has_digital: true
files:
  - {label: "default", path: "Official Documents/Marine/Certificate of Competency/CoC Card.pdf", primary: true}
perm_location: cert-file-2048
perm_slot: 8
temp_location:
temp_slot:
---
Free-form notes in Markdown.
```

### Config
- **Synced** — `.dossier/config.toml`:
  ```toml
  expiry_threshold_days = 90            # single shared value; no per-device override
  include = ["Official Documents/**"]   # scope for reconcile/orphan detection
  ignore  = ["desktop.ini", "**/.ipynb_checkpoints/**", "$Temp/**", "**/Exclude - *"]
  ```
- **Per-device** (via `platformdirs`; `%APPDATA%\dossier\config.toml` / `~/.config/dossier/config.toml`):
  ```toml
  syncthing_root = "…"   # the ONLY device-specific setting
  ```

---

## 5. Tags vs Bundles

- **Tags** — *what a document is*. Hierarchical (`marine/coc`), multi-valued, auto-derived from
  category-folder paths at migration. Filtering a parent includes children. ~15 tags total, so
  the `marine/coc` syntax is a one-line `startswith` filter — **no tag-tree UI**.
- **Bundles** — *what a document is gathered for*. A named set assembled for an application or
  trip. **Adding a doc to a bundle copies nothing** — it is a membership label. The bundle
  becomes real files only on export. Generalizes the old "Carried to India" flag (→ a trip
  bundle). A doc sits in many bundles with zero duplication.

### Day-to-day flow (e.g. a US visa)
1. Gather: in the TUI, select each needed doc → add to bundle `us-visa` (created on first use).
   No file copying, no folder navigation.
2. Check: filter to bundle `us-visa` → the checklist of what's gathered, with expiry warnings
   surfaced from the real date fields.
3. Submit: `ds export us-visa "<dest>"` → a folder of the **current** PDFs + a `manifest.txt`
   (bundle, date, source paths + hashes). Zip/upload that.
4. Update a doc → re-export → always current. No stale duplicates.
5. After: the bundle + its export manifest remain as a record of exactly what was submitted, when.

### Export
- **Copy-based by default** — the only cross-platform-reliable option (symlinks fail on Android
  `sdcardfs`; need Developer Mode/admin on Windows) and what you actually upload/zip.
- Each export writes a **manifest** so exports are never flagged as orphans by reconcile, and
  drift (source changed since export) can be reported. Two intents are recognized: **archival**
  (drift expected — a submitted record) vs **working** (drift = re-export). No sync-back.
- Uses each member's `primary` rendition (prompts if multiple and none marked primary).
- Referential integrity is enforced by `ds doctor`; `ds bundle rename` rewrites all members atomically.

---

## 6. Data integrity & durability

The metadata is curated and effectively irreplaceable once Notion is gone. Three damage vectors,
each explicitly handled:

1. **Sync conflicts.** Syncthing produces `.sync-conflict-*` files (this folder already contains
   real ones from Obsidian). The loader **excludes `.sync-conflict-*`** (`.md` and `.toml`); the
   TUI shows a conflict-count banner; `ds doctor` lists them and offers a frontmatter field-diff
   to resolve.
2. **Stale writes** (the likeliest loss): a long-open TUI overwrites a file that Syncthing updated
   underneath it, leaving *no* conflict file. Mitigation: **optimistic concurrency** — record each
   file's mtime+hash at load, re-check immediately before `os.replace`, and on mismatch reload &
   prompt instead of clobbering.
3. **Accidental deletion / bad bulk edit** propagating to both devices. Mitigation: (a) enable
   **Syncthing staggered file versioning** on this folder; (b) dossier writes the **prior version
   to a local, non-synced history dir** (platformdirs data dir) on every save.

Supporting measures: `ds doctor` round-trip-lints every file (serialize→parse→diff) to catch the
YAML-quoting truncation class; validates referential integrity (location/bundle/tag slugs exist,
rendition paths exist with **exact case** — NTFS and Android FUSE are case-insensitive and will
hide wrong-case rot); checks id/filename consistency, slug collisions, and Windows reserved names
(`aux`/`con`/`nul`/`prn`…). `.stignore` covers the atomic-write temp pattern and `.dossier/.obsidian/`
(whose `workspace.json` churns and would generate conflicts). A **loud root-sanity check** on
startup (is `<root>/.dossier` present?) prevents rendering all docs as "missing" on a misconfigured root.

---

## 7. Platform integration

- **Open a file:** Windows → `os.startfile`; Termux → `termux-open`. If a doc has >1 rendition,
  prompt which to open. **Verify the opener exists and surface failures** — don't trust exit codes
  (a Play-Store/F-Droid install mismatch makes `termux-open` a silent no-op that exits 0).
- **Termux preconditions** (documented, checked by `ds init`/`ds doctor`): `termux-setup-storage`
  (without it Termux cannot see `/storage/emulated/0` at all); `pkg install termux-api` **and** the
  Termux:API app from the **same source** as Termux.
- **Platform detection:** `$PREFIX`/`com.termux` in env or `shutil.which("termux-open")` → Termux;
  else desktop/Windows.
- Relative paths + per-device `syncthing_root` resolve the differing absolute roots correctly.

---

## 8. TUI (Textual) — designed for narrow screens

Must fit portrait Termux (~40–60 cols), not just a desktop terminal.

- **Main list**: **two-line rows** (not a 6-wide table). Line 1: name + expiry/phys/digital status;
  line 2: location · slot · tags. Columns collapse by priority as width shrinks. **ASCII status
  fallbacks** (`!`/`~` for expired/expiring) alongside optional emoji — emoji cell widths misalign
  in many terminals.
- Grouped by effective location; sort key **location → slot → subslot → name** (explicit
  tiebreakers; slotless/locationless docs sort last) so order never jitters between renders.
- `/` live search across name + tags + notes; hotkeys filter by tag / bundle / location / expiry.
- `Enter` opens the file (rendition picker if >1).
- **Views:** Expiring (certs first) · Reconcile (missing files ⇄ in-scope orphans) · Bundles
  (browse members + export) · Detail modal (all fields + notes + file/conflict status; add/edit).
- **Slot op:** "insert at slot N and shift" is first-class (renumbering neighbors is the correct
  physical semantics; ~15 docs/location so the write fan-out is trivial).
- **Startup:** parsing 137 frontmatter files on a phone is acceptable now; add a per-device
  mtime-keyed cache before growing toward ~900. Document the Termux extra-keys row (Esc/arrows).

---

## 9. CLI surface

Two console-script entry points (`dossier` and `ds`) installed identically on both platforms via
`[project.scripts]` — no per-shell alias setup.

| Command | Action |
|---|---|
| `dossier` / `ds` | launch the TUI (default) |
| `ds init` | create per-device config, set `syncthing_root`, scaffold `.dossier/`, check Termux preconditions |
| `ds open <query>` | resolve + open a file from the shell (picker on multiple matches) |
| `ds export <bundle> <dest>` | materialize a bundle to a folder (copies + manifest) |
| `ds bundle rename <old> <new>` | rename a bundle, rewriting all members atomically |
| `ds add` | add a document |
| `ds migrate` | Notion → local migration (dry-run + review report first) |
| `ds doctor` | conflicts, referential integrity, round-trip lint, case/id/reserved-name checks, orphans |

`ds import <folder>` (bulk folder ingest) is **deferred post-v1** — the schema stays import-ready,
but it is not built for v1. Duplicate detection (by hash) is deferred with it.

---

## 10. Migration (one-time, dry-run first)

Sources merged: **Notion** (137 docs + 9 locations; authoritative for physical slots/locations/
notes) and **the file tree** (what digital files exist; folder→tag mapping).

1. **Archive the raw Notion export** (per-row JSON) into `.dossier/migration/` before anything —
   the only insurance for fields that lived nowhere but Notion.
2. Pull docs + locations via the Notion connector.
3. **Match** each doc to a file by name (fuzzy, tolerant of Windows-illegal characters). **Rank
   category-folder paths above application/trip-folder copies** so a doc never binds to a
   duplicate it's meant to obsolete. Any multi-match → review report, never auto-resolved.
4. **Parse issue/expiry from names** with **`dayfirst=True`** as the house rule (ranges self-prove
   it: `28-09-26`). Auto-accept only unambiguous parses (named month, or day > 12); **flag every
   two-digit-year all-numeric date** and define the century pivot explicitly.
5. **Map state locations to flags**: `Softcopy Only` → `has_physical:false`; `Destroyed` →
   `has_physical:false, location:null`; "no soft copy" note → `has_digital:false`.
6. **Auto-derive tags** from category-folder paths (flagged for review). **Bundles are NOT
   auto-created** — the migration only **suggests** candidate bundles from application/trip folders
   in the review report; the user opts in. Historical `Attempt 2/`, `Uploaded documents/` folders
   are archives, not bundles, and are ignored.
7. Emit a **review report**: uncertain dates, unmatched docs, in-scope orphan files, name/slug
   collisions, suggested bundles. **Nothing is written until approved.**

Scope: curated ~137 docs for v1.

---

## 11. Dependencies

Pure-Python / Termux-friendly: `textual`, a YAML lib with quote control (`ruamel.yaml` or
`python-frontmatter` + explicit dumper), `platformdirs`, `python-dateutil`, `tomllib` (stdlib).
Termux also needs `termux-api` + the Termux:API app for `termux-open`.

---

## 12. Module layout
```
dossier/
├─ model.py          # Document, Rendition, Location, Bundle
├─ config.py         # per-device + synced config; syncthing_root resolution & sanity check
├─ store.py          # load/save (quote-safe, byte-stable, atomic same-dir); conflict exclusion; history
├─ query.py          # search/filter/sort; effective location; expiry & file status
├─ platform_open.py  # cross-platform open + detection + opener verification
├─ export.py         # bundle → folder (copies + manifest)
├─ migrate.py        # Notion + tree → .md files + review report + raw archive
├─ doctor.py         # integrity/durability checks
└─ tui/              # Textual app: narrow-first list, detail modal, expiring/reconcile/bundle views
```

---

## 13. Slots
Two ints (`slot`, `subslot`), sorted `(slot, subslot)` — mirror physical positions. Inserting
physically shifts neighbors, so **"insert at N and shift"** is a first-class op (§8). No fractional
indexing (CRDT-flavored overkill for a physical folder).

---

## 14. Open items
- Confirm Obsidian opens a dot-prefixed folder as a vault root (blocks any Obsidian reliance).
- Finalize the slug algorithm: transliteration, year-suffix disambiguation (four `BRP Expires …`
  files), reserved-name guard.
- Define the two-digit-year century pivot.

### Date disambiguation (partly built in `ds doctor`)
- **Done:** `doctor` flags numeric 2-digit-year dates ambiguous on either axis — day/month order
  (`DD-MM-YY` vs `MM-DD-YY`) **and** year position (`DD-MM-YY` vs `YY-MM-DD`, e.g. `21-08-23` →
  2023-08-21 or 2021-08-23). Resolved when a component > 12 pins one axis, or an `issue < expiry`
  span is self-consistent. On the real 137-doc data this leaves ~33 to review by hand.
- **Year-plausibility vs Notion `createdTime`:** thread the record's creation timestamp through the
  migration and use it to rule out implausible readings (a doc created in 2024 can't have been
  *issued* in 2015) — would auto-resolve most of the ~33.
- **"issued X expires Y"** (no `to`) range parsing: currently only the expiry is captured.
- **Vision-model date extraction (nice-to-have):** interface with a local `llama.cpp` image model to
  read issue/expiry dates directly off the scanned document, instead of inferring from the name.

### Supersession & the expiry watch
Renewals *replace* rather than accumulate — a new passport / MOT / cert supersedes the old.
- **Model:** a `supersedes` link on a document (the id of the doc it replaces), set explicitly via
  the TUI `s` action when filing a renewal. The superseded doc is **kept but marked**, and is
  excluded from every expiry calculation and from the watch.
- **Expiry watch — opt-out, not opt-in.** Most documents have no expiry at all; of those that do,
  the ones that actually matter are **marine certs + motorcycle docs**, and supersession already
  removes the renewed-and-replaced noise. So expiry tracking is **on by default** for any document
  that has an `expiry_date` and is neither superseded nor explicitly ignored. An **`ignore_expiry`**
  flag opts a document out (the residual noise is old CDCs no longer in use). A tracked doc turns
  **red only within 9 months** of expiry — no per-day countdown. *(This replaces an earlier opt-in
  "star" idea: with an opt-out default a renewal is tracked automatically, no re-starring.)*
- The watch surface lists tracked docs sorted by soonest expiry; ignored + superseded are hidden.

### TUI direction (from the mockup review)
- **Home = Miller × tree hybrid + a bottom command bar** (the standalone indented-tree option is
  **dropped** — the hybrid's rich rows already *are* the tree row). Panes drill *location → documents
  → detail*; the documents pane renders *rich rows* (colour, issue/exp date via an `i` issue⇄expiry
  toggle, tag, emoji, and a **permanent** ⚠ on expired-and-not-superseded rows). Selecting a doc /
  `⏎` opens the detail pane. Selection is a background highlight — it **never shifts the indent**.
- **Responsive collapse** — the focused (rightmost) pane always gets room; panes drop by terminal
  width: **wide (≥ ~100 cols)** shows all three; **medium (~60–100)** shows two, and opening detail
  swaps to `documents │ detail` (locations drops off — this is "move col 2 over col 1 when space is
  low"); **portrait (< ~60)** is one pane, `⏎` → detail full-screen, `→`/`←` drill. *(Optional,
  low-prio: gradual horizontal scroll instead of snapping — desktop-only, since Termux maps touch
  drags to vertical wheel, so there is no horizontal touch scroll.)*
- **Documents rows:** single-line when the pane is wide (name left, exp + emoji **right-aligned**,
  name truncated with `…`). When detail opens, the date/tags move to the detail pane but each row
  **keeps a one-char ⚠ / expiry-colour cue** so expired items still stand out while scanning. Rows go
  multi-line in narrow panes and portrait.
- **Command bar docked at the bottom** (thumb-reachable): `/` or typing runs a **root-wide** search
  that collapses the panes to a flat rich results list; `Esc` returns to the Miller view where you
  were. The bottom bar **doubles as the keyboard affordance** — tapping it focuses search and raises
  the IME via the mouse-mode-drop trick, restoring mouse mode on submit/`Esc` (so no separate ⌨
  button is needed on this surface).
- **Actions, in priority order:** `⏎` open (most common) · `b` add-to-bundle · `n` new / `s` supersede.
  Keys are consistent across widths: `←`/`→` move panes, `Esc` closes detail / clears search.
- **Touch / Termux (researched, 2026).** Textual enables SGR mouse reporting, and Termux's
  `onSingleTapUp` only raises the soft-keyboard when mouse tracking is *off* — so with mouse mode on,
  **taps arrive as clicks and the keyboard stays down automatically**, no config needed. That makes
  the app tap-navigable out of the box (tap a row to select/open; on-screen action bar Open / Bundle
  / New / ⌨). The app **cannot itself summon the IME** — no `termux-api` command, escape sequence, or
  property exists (termux-app #27 / #3135 / #3733 are open feature requests). The workable "show
  keyboard" affordance: the `⌨` / search control **momentarily disables mouse mode** so the next tap
  on the input raises the keyboard, then re-enables it on submit/blur. Belt-and-braces: ship a
  `~/.termux/termux.properties` with `hide-soft-keyboard-on-startup=true` (optionally
  `soft-keyboard-toggle-behaviour=enable/disable`). Design around three known Termux quirks: mouse
  mode **blocks terminal scrollback** (#4302 — so the TUI must own scrolling), OSC-8 link clicks
  don't work, and Termux may present a numeric keyboard variant (#1255). Termux:GUI is the only route
  to real programmatic IME control, but it means abandoning the TUI — not worth it.

### Reset / teardown (`ds reset`)
A safe way to undo a setup, complementing `ds init`. Two independent scopes:
- **`ds reset` (folder data)** — clears a root's `.dossier/` metadata (document records, locations,
  bundles, synced `config.toml`) so the folder can be re-`init`ed or re-`migrate`d from clean.
  **Hard guarantee: it never deletes anything outside `.dossier/` — the real soft-copy files in the
  Syncthing tree are NEVER touched.** Confirm before acting; back the `.dossier/` up to the local
  history dir first (recoverable). Default target is the configured `syncthing_root`; `--root <path>`
  targets a specific folder.
- **`ds reset --global`** (a.k.a. `--config`) — removes only this device's per-device config
  (`syncthing_root` etc.), un-configuring the device. Touches no `.dossier/` data and no documents.
- Guardrails: refuse without an explicit confirmation (or `--yes`); print exactly what will be
  removed first; never follow the config into the file tree to delete documents.

### Backlog — after the home build
The Miller home (locations │ documents │ detail), its actions, touch/Termux, and Nerd-Font icons
shipped in PRs #15–#25. Still open:
- **Keep the detail column while searching.** Search currently switches to a flat, root-wide list
  and hides the locations *and* detail panes (`HomeScreen.searching` in `tui/home.py`). Instead the
  **detail (third) column should stay visible during search** so a highlighted result still previews.
  **Better still: make search an in-place filter on the Miller view** rather than a separate mode —
  keep the three columns and just narrow the documents pane to matches (root-wide scope), with the
  detail preview following the highlight as usual. Decide how location scoping reads while filtering
  (show only locations with matches, or force an "All" scope).
- **Dedicated expiry-watch surface** — the mockup's 5th screen: tracked docs soonest-expiry-first
  with an ignore toggle and an "N tracked · M red (≤ threshold)" header. The *logic* is done
  (`query.tracked`, opt-out; §"Supersession & the expiry watch") and `x` filters the documents pane,
  but there is no standalone watch screen yet.
- **A way to set `ignore_expiry`** — the model flag exists and drops a doc from the watch, but nothing
  toggles it from the UI. Intended for the detail / edit flow (e.g. an `x`/checkbox in the edit modal,
  or a key on the detail pane).
- **Editable detail pane (col 3).** Let the third column edit *all* of a document's parameters inline
  (name, dates, location/slot, tags, bundles, flags, renditions, `ignore_expiry`, supersession),
  rather than only previewing. This can **absorb several of the documents-pane keybinds** — the `e`
  edit modal folds into col 3, and `b` bundle / `s` supersede / `m` move / the `ignore_expiry` toggle
  can become inline fields there — leaving col 2 for navigation. Drill `→` into the detail to edit,
  `Esc`/`←` back; keep it keyboard-first.

---

## 15. Changelog — v1 → v2 (from the adversarial review)
- **Slug-based references** for locations & bundles (was display-name); **always-quote + byte-stable
  serializer** — fixes the ` #`-in-YAML silent-truncation bug (`Cert File #2048` → `Cert File`).
- **New durability layer** (§6): exclude/ surface `.sync-conflict-*`; optimistic-concurrency stale-write
  guard; Syncthing versioning + local history backups; `ds doctor` integrity checks; loud root-sanity check.
- **`id` = filename**, single source of truth (dropped the frontmatter `id`); slug-collision & reserved-name rules.
- **Dropped state pseudo-locations** (`kind`) — folded into `has_physical`/`has_digital`.
- **TUI redesigned narrow-first** (two-line rows, ASCII fallbacks, explicit sort tiebreakers, startup cache).
- **Migration hardened**: raw Notion archive first; `dayfirst=True`; flag all 2-digit numeric dates;
  category-folder match ranking; **bundles suggested, not auto-created**.
- **Export writes a manifest**; archival-vs-working drift distinction named.
- **Config split**: synced `.dossier/config.toml` (shared `expiry_threshold_days`, scope globs) vs
  per-device `syncthing_root` only.
- **Added** `ds init`, `ds bundle rename`, Termux preconditions & opener verification.
- **Cut/deferred**: per-bundle rendition pinning, Windows hardlink export, building `ds import` in v1,
  hash dedup, tag-tree UI, fractional slot indexing.
- **`.stignore`** the atomic temp pattern and `.dossier/.obsidian/`.
