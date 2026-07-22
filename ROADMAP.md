# dossier roadmap

**Where we are (2026-07-22).** The Miller-columns home shipped across PRs #15–#30:
browse · detail pane · bottom-bar search · the full action set (`⏎ o i b e n m s
d x`) · touch/Termux · Nerd-Font icons. The durable flat-file store, the Notion
migration engine, and `ds doctor` predate that. 91 tests, CI green.

What's left is below, in the order that gets the most value soonest. Effort:
**S** ≈ a few hours · **M** ≈ 1–2 slices · **L** ≈ several slices. Per-item
rationale lives in `DESIGN.md` §14; this file is the ordering.

---

## Phase 1 — Live on your real data
*Goal: stop building against fixtures — get the 137 documents in and use it daily.*
This is the biggest unlock; everything after is easier to judge once you're dogfooding.

1. **`ds reset`** (S) — folder-data reset (clears `.dossier/` only; **never** the
   real soft-copy files) + `--global` config reset. Do this **first**: it's the
   safety net that makes re-importing painless while you tune the migration.
2. *(optional, before the import — for a cleaner first pass)* **Migration date +
   slug wins** (S–M): thread Notion `createdTime` to rule out implausible year
   readings (auto-resolves most of the ~33 ambiguous dates); parse "issued X
   expires Y" ranges; finalize the slug algorithm (transliteration, year-suffix
   disambiguation for the four `BRP Expires …` files, reserved-name guard) and the
   2-digit-year century pivot. Skippable — `ds doctor` + edit already fix these
   in-app afterwards.
3. **Apply the migration** *(milestone)* — `ds init` on the real Syncthing folder,
   `ds migrate --apply`, review. 51/137 files auto-link on the dry run.
4. **Fuzzy-suggestion review flow** (M) — a TUI screen to accept/reject the ~47
   suggested file matches the migration wouldn't auto-link. The suggestions
   already come out of `migrate.build_plan`; this surfaces them for one-key triage.

## Phase 2 — Finish the expiry watch
*Goal: the marquee thing Notion did poorly — "which marine / motorcycle doc needs renewing?"*
The logic (`query.tracked`, opt-out; supersession) is already done — this is surfacing it.

5. **`ignore_expiry` toggle** (S) — a keypress (on the watch list and/or detail)
   that drops residual old CDCs from tracking. Minimal home now; Phase 3 makes it a
   proper field.
6. **Expiry-watch surface** (M) — the mockup's 5th screen: tracked docs
   soonest-expiry-first, an "N tracked · M red (≤ threshold)" header, open / ignore
   from the list.

## Phase 3 — Editing & search ergonomics
*Goal: make everyday editing and searching frictionless.*

7. **Editable detail pane (col 3)** (L) — edit *every* parameter inline in the
   third column (name, dates, location/slot, tags, bundles, flags, renditions,
   `ignore_expiry`, supersession). Absorbs the `e` edit modal and folds
   `b`/`s`/`m`/ignore into inline fields, freeing column 2 for navigation. Drill
   `→` to edit, `Esc`/`←` back; keyboard-first.
8. **Search as an in-place Miller filter** (M) — keep the three columns during
   search (don't hide the detail); filter the documents pane in place (root-wide),
   detail preview following the highlight. Decide how location scoping reads while
   filtering (only-matching-locations vs. a forced "All").

## Phase 4 — Bundles & export
*Goal: gather a set of documents for an application (US visa, OCI, …).*

9. **`ds export`** (M) — export a bundle's files to an external folder (copy or
   symlink), the original "export all the files for this application" goal. Bundles
   themselves already exist (model + the `b` action).

## Phase 5 — Nice-to-have / research
10. **Vision date extraction** (L) — a local `llama.cpp` image model to read
    issue/expiry dates straight off the scans instead of inferring from filenames.
    A research spike; lowest priority.
11. **Obsidian vault confirmation** (S, research) — verify Obsidian opens a
    dot-prefixed folder as a vault root. Only matters if you ever want to lean on
    Obsidian over the `.dossier/` store; otherwise drop it.

---

## Reading the order
- **Quick wins, pull forward anytime:** `ds reset` (S), the `ignore_expiry` toggle
  (S), and the range-parsing date win (S).
- **Why data-first:** the app is feature-complete enough that the highest-value
  next step is putting *your* documents in it — real data will tell you which of
  Phases 2–4 actually matters most day to day, and may reshuffle the rest.
- **The one real dependency:** the editable detail pane (Phase 3) re-homes the
  `ignore_expiry` toggle and the edit modal — so Phase 2 gives `ignore_expiry` a
  minimal keypress home, and Phase 3 turns it into a proper field. Nothing else is
  blocked; phases can be reordered to taste.
