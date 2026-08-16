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
