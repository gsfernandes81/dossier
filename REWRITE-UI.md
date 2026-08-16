# dossier v3 — TUI Layout Plan (R-UI)

**Status:** Approved (2026-08-16). This document satisfies the **Phase R-UI gate** in
[`REWRITE.md`](REWRITE.md) — the layout decisions below were made by the user; do not
re-litigate them. It specifies *layout and navigation only*; the behavioral
invariants in REWRITE.md §4.5 remain binding and this plan must never contradict
them. Where v2 (`DESIGN.md` §8/§14) is cited, it is cited as interaction spec — the
Miller three-column implementation itself is **not** ported (REWRITE.md D12).

## U-decisions (user-confirmed)

| # | Decision |
|---|---|
| U1 | **Single list + drill-down stack.** One full-width searchable list is the base surface; every other surface pushes onto a back-stack and `Esc` pops. No persistent multi-pane home. |
| U2 | **No location group headers.** The list is flat; location is per-row data and a *filter*, never a header row or a pane. |
| U3 | **Detail is a sticky toggle.** `→` opens detail; on wide terminals it splits beside the list and follows the cursor until closed; on narrow it is a full-screen push. |
| U4 | **Command drill-down, very limited hotkeys.** Secondary surfaces are reached via `:` commands; letter hotkeys exist only for the most common tasks, and never on the base list (which is type-to-search). |

## 1. The base surface: Find

The app *is* a finder that happens to have management surfaces behind it.

```
┌ dossier ──────────────── ! 3 expiring · 612 unfiled ┐
│ ▸ CoC Card                    cert-file 8   ! 09-26 │
│   Passport (IN)               file-4096 1.2 ⚠ 01-27 │
│   ENG-1 Medical               cert-file 3     11-26 │
│   Sea Service Testimonial     softcopy         ·    │
│   …                                                 │
├─────────────────────────────────────────────────────┤
│ > pass_                                      12/948 │
│ ⏎ open  → detail  : commands  ? help                │
└─────────────────────────────────────────────────────┘
```

- **Rows** (single line when the pane is ≥ ~70 cols): name left, truncated with `…`;
  right-aligned: dim `location slot`, expiry (status-colored, `!`/`~`/`⚠` ASCII
  markers per v2), `·` when none. Below ~70 cols rows go **two-line** (v2 pattern):
  line 1 name + status, line 2 dim `location · slot · tags`. Alignment via
  cell-width-aware truncation (unicode-width), never `len()`.
- **Default order**: location → slot → subslot → name (explicit tiebreakers, v2
  rule) — physical shelf order survives U2 as *sort*, not headers. The list is
  virtualized (948+ rows).
- **Search bar docked at the bottom** (thumb-reachable — a v2 lesson that stands):
  any printable typed anywhere on this surface lands in it (find-fast, invariant 1);
  fzf-style `matched/total` count; exact-pass-then-fuzzy exactly as v2's `fuzz`
  contract; `ctrl+t` toggles search-inside-scans. Filters render as dim chips in the
  bar row (`loc:cert-file`, `bundle:us-visa`, `expiring`).
- **Header**: title + attention counts (expiring · unfiled · journal anomalies).
  Each count names its `:` command; on touch, tapping a count jumps there.
- **Footer**: 3–5 hints for *this* surface, `?` for the full reference. Per-surface
  hints only — never another surface's verbs (v2's `check_action` lesson).
- **Verbs**: `Enter` opens the file, `→` opens detail (invariant 2). No letter
  bindings on this surface at all — the v2 find-fast rule, which also satisfies U4.

## 2. Detail (U3)

- `→` opens detail for the highlighted row. **Wide (≥ ~100 cols)**: a right split
  (~45%), list keeps focus, detail follows the cursor; `Esc` (or `←`) closes it.
  **Narrow**: full-screen push; `Esc` pops back to the list, cursor preserved.
- Detail is the **only editing surface** (v2 Phase 4 conclusion stands): every field
  inline — name, dates, location/slot with neighbour-shift, tags (flat), bundle
  membership, copy flags, files list + primary, ignore-expiry, notes. Save/discard
  semantics as v2: explicit save, double-`Esc` discards an edit in progress.
- Because detail is not a search surface, it may bind letters (U4's "common tasks"):
  `s` supersede picker, `b` bundle picker, `u` undo (inverse op). All also `: `
  commands.

## 3. Secondary surfaces (U4)

Reached by `:` command (each pushed on the stack, `Esc` pops); the base list stays
letter-free. Command mode lives in the same bottom bar (`:` or `>` switches it, v2
style); prefix-matched with completion.

| Surface | Command | Content |
|---|---|---|
| Review | `:review` | the five tabs (orphans · missing · duplicates · succession · integrity), `[`/`]` or `tab` cycles; row verbs per REWRITE.md invariant 2 |
| File | `:file` | the filing queue — proposal cards, exception triage, unfiled counter |
| Bundles | `:bundles` | bundle list (dated, chronological), Enter scopes the Find list to the bundle; export from here |
| Expiring | `:expiring` (hotkey `ctrl+x`) | the Find list with the expiring filter + `N tracked · M red` header — a filter, not a mode |
| Settings | `:settings` | per-device + synced settings |
| Help | `?` | full key/command reference |

Quit: `ctrl+q`, or Esc-Esc from the base state (armed, v2 semantics). `ctrl+c`
always quits cleanly (never bound over).

## 4. Responsive plan

Exactly **two** layout states (the Miller collapse ladder is gone):

- **Split-capable (≥ ~100 cols)**: list + detail side by side when detail is open;
  otherwise the list is full-width.
- **Single-pane (< ~100 cols)**: everything full-screen, stack navigation. This is
  the phone (≈ 45×~28 portrait Termux) and a 60-col tmux split alike.
- **Floor**: below ~38×12, render a "terminal too small (need ≥ 38×12)" notice
  instead of glitching. Resize re-layouts (SIGWINCH), debounced.

## 5. Touch / Termux

The v2 findings port as-is (REWRITE.md invariant 6): SGR mouse on; tap selects,
tap-on-selected opens; **tapping the search bar** drops mouse reporting so the
next tap raises the IME, restored on the next keypress; the app owns scrolling.
Touch mode shows a one-row action bar above the search bar — the only extra
chrome touch gets — carrying this surface's four verbs (Open · Detail · Expiry ·
Scans). Below it the **search bar is two rows on a touch layout** (query, then
count + chips + the hints the buttons do not carry), sitting against the bottom
edge: the whole block is the keyboard target, and one terminal row is too small
a thing to ask a thumb to hit when the row above it opens files. It costs no
document rows — the second row is the hint line the action bar had already made
redundant. On a keyboard layout the bar stays one row with its own hint line
below.

**Amended 2026-08-16, from the device.** The action bar's fourth quarter was a
`⌨ Keys` button. Termux's own extra-keys row can carry a keyboard toggle, so a
second button for it wasted a quarter of the only touch chrome there is. The
keyboard affordance is now the search bar itself — which is what the sentence
above always said, and what a thumb does anyway when it wants to type — marked
with a dim `⌨` beside the count. The freed quarter went to `^t Scans`: with
`^x Expiry` beside it, the two touch buttons are exactly the verbs whose keys are
modifier combinations, which are the ones a phone keyboard is least reliable at
delivering.

## 6. Visual language

- **Semantic color tokens** only (`status.expired`, `text.muted`, `accent`), mapped
  to terminal ANSI colors by default so the user's terminal theme carries the
  palette; `NO_COLOR` honored; every color signal paired with a glyph/letter
  (`!`/`~`/`⚠`, ASCII-first with optional Nerd-Font icons per v2's glyphs toggle).
- **No borders inside surfaces** — the split uses a single vertical rule; selection
  is reverse-video/background, never an indent shift (v2 rule). One frame max
  between terminal edge and content.
- Usable in monochrome by construction (weight + reverse + markers).

## 7. What this deletes relative to v2

The locations pane and any location browser; the three-tier Miller responsive
collapse; the watch *mode* (now `:expiring`, a filter); per-surface modal screens
(everything is a stack push); the header ⭘ icon and the touch Commands button's
special-casing (the bar is always present).

## 7a. Mockups — the reference to match (approved 2026-08-16)

Three published pages render this plan as real terminal screens on an exact
character grid: **the Find surface**, **the surfaces behind `:`**, and **the
interaction model**. Sources and links in
[`docs/dev/mockups/`](docs/dev/mockups/); they are kept so the finished TUI can
be compared against them and refined where it does not match.

User calls from that review, binding on R3–R5:

- **Two-line phone rows confirmed** — 35 columns of name, location and tags
  underneath, twelve documents visible at 45×28. The trade against density is
  accepted.
- **Detail density confirmed for now** — notes, files, bundles and succession on
  one phone screen.
- **Filing card approved, with one item deferred:** it should also be able to
  *reverse* the succession relationship — marking the proposal as the **older**
  document the existing one supersedes, rather than the newer. **Not to be
  implemented until the user confirms it is a real pain point.**
- Possible follow-up: ship a **Termux colour theme**, if the default palette
  fights the semantic tokens in practice.
- **Diverges from the mockups, deliberately (2026-08-16, first device runs of
  the real app):** the touch action bar's fourth quarter is `^t Scans`, not
  `⌨ Keys`; the keyboard affordance moved to the search bar; and that bar is
  **two rows** on touch, having absorbed the hint line — see §5. Both changes
  came from the phone: a redundant button, then a target too small for a thumb.
  The published mockup pages still show the old one-row bar with `⌨ Keys`;
  everything else on them still holds.

## 8. Acceptance checklist (R3–R5 must satisfy)

1. Every REWRITE.md §4.5 invariant, verified per surface.
2. Cold start → type → `Enter` → file open ≤ 5 keystrokes; zero letter bindings on
   the Find surface.
3. All surfaces reachable and fully operable at 45×28 (portrait Termux) and 80×24;
   split appears only ≥ ~100 cols; too-small notice below the floor.
4. `Esc` peels exactly one layer per press through: edit → command mode → search →
   detail/surface pop → arm → quit.
5. Every letter verb has a `:` command equivalent; footer hints are per-surface.
6. Monochrome + `NO_COLOR` legible; ASCII fallback complete.
7. List virtualized; no I/O on the render thread (walks, syncthing polls, folds on
   workers).
