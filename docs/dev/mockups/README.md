<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# R-UI mockups — the reference the finished TUI is measured against

Three published pages showing what the v3 TUI looks like, generated from
[`REWRITE-UI.md`](../../../REWRITE-UI.md). **User-reviewed and approved
(2026-08-16)**; kept so the finished product can be compared against them, and
refined if it does not match.

| # | Page | What it shows |
|---|---|---|
| 1 | [The Find Surface](https://claude.ai/code/artifact/cf5729cf-5837-4c99-9bbc-c8e60f0eace5) | cold start, typed search, the record at both widths, desktop full-width list |
| 2 | [Behind the Colon](https://claude.ai/code/artifact/ac07dc0f-f936-44c3-a4bc-258bf32cd098) | command mode, `:expiring`, the filing card, review tabs, bundles, `ds status` |
| 3 | [How It Answers](https://claude.ai/code/artifact/61cc6e74-9bd4-4bea-a886-e86c09f41792) | verb pair, the Esc peel chain, the Termux keyboard dance, glyph/colour language, the floor |

## Review outcome

- **Two-line phone rows: confirmed.** 35 columns for the name, location and tags
  underneath. Twelve documents visible at 45×28 rather than twenty-four, and
  that trade is accepted.
- **Detail density: confirmed for now** — notes, files, bundles and succession on
  one phone screen.
- **Filing card: approved, with one deferred request.** The card should also be
  able to *reverse* the succession relationship (mark the proposal as the
  **older** document that the existing one supersedes, rather than the newer).
  **Do not implement until the user confirms it is a real pain point** — the
  guess is that reversing is rare enough to belong in the record surface.
- Possible follow-up: a **Termux colour theme** shipped with the app, if the
  default terminal palette turns out to fight the semantic tokens.

## Regenerating

Screens are rendered on an exact character grid — every line padded to the
pane's true column count using **display widths**, so a 45-column phone screen
really is 45 columns. Hand-drawn ASCII lies about precisely the thing that is
hardest to get right, and the generator refuses a line that is one column too
wide.

```bash
uv run --group driver python screens.py   # writes screens.json (needs wcwidth)
uv run --group driver python inject.py    # splices screens + CSS into the pages
```

`*.src.html` are the sources; `find.html`, `surfaces.html` and
`interaction.html` are generated and **not** checked in. Markup inside
`screens.py` is `«class|text»`; the class names match the terminal semantic
tokens in `style.css`.

The panes deliberately follow the viewer's light/dark theme — that demonstrates
REWRITE-UI.md §6's claim rather than asserting it: semantic tokens map to ANSI,
so the user's own terminal theme carries the palette.

## Known divergence

Three changes came out of running the real binary on the phone, and these pages
predate all of them:

1. The touch action bar reads `⏎ Open · → Detail · ^x Expiry · ^t Scans`. The
   `⌨ Keys` button is gone — Termux's own extra-keys row can carry a keyboard
   toggle, so the affordance moved to the search bar.
2. The search bar is **two rows** on a touch layout (query, then count + chips +
   hints) and sits against the bottom edge, because one row is too small a tap
   target. It absorbed the separate hint line, so the document count is
   unchanged at twelve.
3. The buttons are **filled cells on a fixed tiling** and the query row is an
   **underlined field**, settled in `searchbar.html` and `searchbar-merge.html`
   below. Reverse video means pressable, underline means typable, dim means
   information — and all three are attributes, so `NO_COLOR` loses nothing.

REWRITE-UI.md §5 and §7a record the reasoning. Nothing else on these pages has
been superseded.

## The follow-up pages

- `searchbar.py` → **The Bar, Four Ways** — four treatments for a bar that read
  as indistinct on the device, with what each costs in document rows.
- `searchbar-merge.py` → **Press and Type** — merging the two the user liked,
  which meant deciding what each texture means, then fixing the button row's
  geometry: `gutter 1 + (cell + gutter) × 4`, remainder at the right, renderer
  and hit test sharing one function.

Both are self-contained: each reads `style.css`, renders its own screens on the
exact character grid, and writes its finished page. Run them with
`uv run --with wcwidth python searchbar.py`.

## The verb audit and the bar directions (2026-08-17)

After `⏎ Open` turned out to be a button for a gesture that already existed, the
whole verb surface was audited with a Fable design advisor:

- `verbs.py` → **The Verb Audit** — every verb, how it is reached by key and by
  thumb, where it is taught, and the four kinds of fault that turns up. The
  specimens are reproductions of what the shipped binary renders, checked in a
  PTY at 45×28 rather than imagined.
- `bottombars.py` → **Six Bottom Bars** — six directions for the bar and the
  field together, since they compete for the same four rows.

Findings worth carrying whatever ships: hints must degrade item by item rather
than vanishing whole (two filters currently erase the entire hint line); the
Find chrome must go inert while a pushed record covers the list; the keyboard
layout's query row is missing its underline and `ctrl+t` appears in no desktop
hint; and **`Esc` has no touch affordance at all** — the peel machinery the whole
interaction model rests on is keyboard-only.
