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
  underneath. Half the documents of a one-line row, and that trade is accepted.
  (The pages say "twelve at 45×28"; the device reports 47×45 browsing and 47×24
  typing, so it is really twenty-one and ten. See the 2026-08-20 note below.)
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
  thumb, where it is taught, and the faults that turns up. The specimens are
  reproductions of what the shipped binary renders, checked in a PTY at 45×28
  rather than imagined.
- `bottombars.py` → **Two Chips and a Quiet Row** — the bottom chrome, drawn
  against the phone's actual Termux key row.

Findings worth carrying whatever ships: hints must degrade item by item rather
than vanishing whole (two filters currently erase the entire hint line); the
Find chrome must go inert while a pushed record covers the list; the keyboard
layout's query row is missing its underline and `ctrl+t` appears in no desktop
hint; and **tap-to-open is taught nowhere**.

## Revised against the Termux key row (2026-08-19)

Both pages above were first written against a thumb with no keys. That thumb
does not exist — Termux pins an **extra-keys row above the terminal**, and the
phone this is built for carries `CTRL·ESC`, `ALT·TAB`, `◀▲▼▶` and
`KEYBOARD·ENTER` on it. **`CTRL` and `ALT` are sticky modifiers** — tap one and
it latches until the next key — and they compose with soft-keyboard letters and
with each other (device-verified), so `ctrl+x`, `ctrl+t` and `ctrl+alt+letter`
are two taps with one thumb.

A modifier still needs a letter, and the only letter source is the soft
keyboard — so the reachability question splits by keyboard state. Termux
*resizes* the terminal when the keyboard raises rather than covering it, which
makes these two layouts rather than one layout half-hidden: **browsing** at
47×45 with the keyboard down, **querying** at 47×24 with it up.

What that settles:

- **Three findings retracted.** `Esc` and quit were never orphans (swipe-up on
  `CTRL`, twice for quit). And `^t Scans` never needed a touch affordance:
  `app.rs:298` only widens the search into scan text when the query is
  non-empty, so the toggle is meaningless until you are typing — and while you
  are typing, the keyboard is up and `ctrl+t` works.
- **One new duplication.** `→ Detail` is `⏎ Open` again: a bar cell for `▶`.
- **One orphan survives.** `ctrl+x` with the keyboard down. "Show me what is
  expiring" is a browsing act, and browsing has no letters. One verb, one state.
- **So: no bar, and no pressable chips either.** `find.rs:349` emits
  `[expiring]` only when the filter is already on, so a chip can turn a filter
  off and never on. The affordance is the **tappable header count** —
  REWRITE-UI §1 already specifies it, `app.rs` does not implement it (row 0
  falls through to `Idle`), and it costs no rows. Deleting the bar does not add
  a document at either size — it buys the spare row that carries flashes and
  the armed-quit message.
- **A bug fell out of it.** `input.rs:71` guards only `CONTROL`, so on this
  phone `alt`+letter types into the query and `ctrl+alt+x` is
  indistinguishable from `ctrl+x`. Measured against the built binary.

`bottombars.html` carries the `termux.properties` rows for the install notes,
tagged by evidence tier — device-verified, measured here, or documentation that
still wants a look at the phone.

Confirmed on the device: the row is an Android view drawn in both keyboard
states; swipe-up sends the `popup:` key; raising the keyboard resizes the
terminal rather than covering it; **the latch reaches the row's own arrows**
(`CTRL` then `▶` is `ctrl+→`); and long press repeats on ordinary keys but
*holds* on latching ones, so a long press on `CTRL` is a momentary modifier.

Two consequences:

- **A modifier tier exists with the keyboard down** — ctrl/alt against the four
  arrows, twelve combinations, none bound to anything, none needing a letter.
  The one orphan verb could simply be bound there. It still ships as the header
  count first: `ctrl+↑` is reachable but not discoverable, and the tier is worth
  keeping in reserve for R4, which needs keyboard-down verbs far more than Find
  does. Binding anything there needs modifier guards on the arrow arms in
  `input.rs` — `ctrl+→` currently means plain `→`.
- **The pane is 47×45 browsing and 47×24 typing**, not 45×28 — both measured,
  keyboard down and up. That is **twenty-one documents at a glance and ten
  while you type**, not twelve. No code depends on it — `layout.rs` is
  width-driven and 45×28 appears only in tests and these mockups — so this was
  a promise to correct, and it has been swept through `REWRITE-UI.md`,
  `layout.rs`, `find.rs`, `screens.rs` and this file. Deleting the action bar
  is worth a real document at the browsing height: 41 list rows hold twenty
  two-line documents and waste one, 42 hold twenty-one.

## The built product (2026-08-20)

`built.py` → **Find at 47×45** — the finished screens with no history and no
argument: browsing, typing, a filter live, a record, the armed quit, the touch
map, the three textures, the key row and the desk layout, every one drawn at
the size the device reports. This is the page to compare the shipped binary
against.

## Where the verbs live (2026-08-20)

`leader.py` → **The Leader Key** — five ways to give dossier a verb surface,
read off Spacemacs, Magit and Neovim, which solved the same problem on the same
constraint: a surface where the keyboard is already spoken for and the verb set
keeps growing.

The enabling fact: **a query never usefully begins with a space**, so `Space` on
an *empty* query is free while mid-query it still types a space. That is
Spacemacs' normal-vs-insert split without modes — the query is the mode.

The recommendation is one object doing three jobs:

- **A leader sheet** (which-key) over the bottom of the list, groups named after
  nouns, delayed on a keyboard and immediate on touch. New verbs cost nothing in
  memorised keys.
- **Transient toggles inside it** (magit infixes) — filters drawn as checkboxes,
  which is the only shape that shows *off* as well as on. A status chip cannot:
  it does not exist until the filter is already on.
- **Typing turns it into the command picker** (Telescope / `M-x`), reusing the
  list widget Find already is, with each result showing the chord that would
  have run it. You either know the chord or you know the word.

`:` commands stay as the third tier. Three tiers, and the rule for sorting a
verb into one: a key for every session, a chord for weekly or for anything whose
state must be visible, a command for the rest — and for anything destructive,
which is safer spelled than chorded. A chord is always a shortcut for a command,
never a separate code path.

Modal (vim-style) is drawn and rejected: it frees the alphabet and costs the
premise, since find-fast means you open the app and type.

Open before building: does the sheet cover the list or shrink it, and does
`Space` still lead when the query is empty but a filter is live (I would say
yes — the query decides, not the filters).

### The leader's touch trigger

`leaderchip.py` → **Space Without a Spacebar** — five placements for the chip
that opens the sheet when the phone keyboard is down and there is no Space to
press. All five live inside the existing three chrome rows; a trigger that cost
a row would undo the argument that deleted the action bar.

Settled: **A+B+C from the leader page is the design** — sheet, transient
toggles, and typing turning it into the picker. The sheet **covers** the list
rather than shrinking it, and `Space` leads whenever the query is empty,
regardless of live filters.

The chip is **E — `SPC` replaces `⌨` at the right end of the query row**.
Termux has its own keyboard key and tapping the field already raises the IME, so
`⌨` was a hint rather than a control; with one chip left, the "targets at
opposite ends" argument for placement A goes with it, and the bottom-right
corner is where a thumb rests. The label is `SPC`, not a glyph: ASCII cannot
render as a box on an unverified font, and it teaches its own key, since the
sheet's breadcrumb reads `SPC` too.

Touch layouts only. A keyboard has a space bar, and drawing a button for a key
you are already holding is the `⏎ Open` mistake this whole line of work exists
to stop repeating.

### What the empty field says

`emptyfield.py` → **The Empty Field** — with `⌨` gone, nothing told a
first-time user that the bottom of the screen is where you type. The field says
it itself: **dim text inside the underline whenever the query is empty**, gone
the instant a character is typed. No stored state and no decay rule — the
condition is just "the query is empty", which is the same condition that makes
`Space` the leader.

Settled by the user, and better than any single phrase: **a pair**. `Type to
search` dim on the left, `For more, hit` dim on the right, and the reversed
`SPC` chip is the last word of the second sentence — `For more, hit` ends
exactly where the chip begins, so the prose and the button are one object. That
also fixes something the chip alone could not do: a bare reversed `SPC`
announces that it is pressable and nothing about what happens next.

Capitalised, narrowly: these are sentences, not labels, and every other dim
string on the surface is a label.

Costs, all accepted: the pairing forces the short invitation (`type any part of
a name` is 23 columns, and with the cursor and signpost that is 39 exactly, so
they would touch), which gives up teaching that the search matches anywhere in
a name — that lesson moves to the help sheet behind `?`. At the 38-column floor
the signpost sheds and the invitation stays, one at a time, the same rule as
the hint line. On a keyboard layout there is no chip, so no signpost either —
a keyboard has a space bar and the hint line already names `space`.

Two things worth keeping in mind when building it:

- The underline spans all 39 columns in every state. The placeholder changes
  what is drawn on the line, never how long it is, so the field's geometry is
  identical empty, half-typed and full.
- Dim under an underline is two independent SGR attributes on one cell — one
  Ratatui `Style` with two modifiers. The mockup markup could not express it,
  because `«class|text»` does not nest; `grid`-style pages now need a combined
  `uldim` class for this. **That constraint is the generator's, not the
  terminal's** — do not let it shape the design.

### The entry line

`minibuffer.py` → **The Minibuffer** — read against Emacs and Vim, which both
answer the same question the same way: **one line at the bottom that becomes
whatever is being asked**, with a prompt that names the question, and no box.

Four properties worth taking:

- **The prompt names the question.** `Find file:`, `M-x`, `:` — never a bare
  marker. Our `>` says nothing, which is exactly why the field needs a
  placeholder telling you to type; name the prompt and the invitation is
  redundant.
- **The prompt is the mode.** Vim's `:` / `/` / `?` are three prompts on one
  line. Find, command mode and picking-inside-the-sheet are three questions that
  already share our row.
- **It is also the echo area.** One place for what you type and what the program
  says. Our second row already carries flashes; that would become its identity.
- **Candidates go above it** (Vertico), which the leader sheet already does.

The one that does not transfer: **no box.** Our underline is the settled
*typable* texture and the visible boundary of the tap target that raises the
keyboard. Emacs never had a thumb to design for. Keep the box on touch; dropping
it is only defensible on the desktop layout.

Proposed: name the prompt, fold the match count in beside the input (Vertico's
move, freeing row two), and let the prompt change with the question. The prize
is that **command mode arrives for free** — when `:` lands there is nowhere new
to put it. Costs four typable columns. **Wording is the user's call**: `Find:`,
`Search:` or `Filter:`, with or without the colon.

### Marking the field — and a flaw in these mockups

`field.py` → **Marking the Field**. The underlined query field sits too high on
the device: a terminal draws `SGR 4` wherever the font's metric says, through
the descenders rather than under them, and nothing in the app can move it.

**The mockups were flattering it.** Every page here that recommended an
underlined field drew it with `text-underline-offset: 3px` — a property no
terminal has. The grid machinery makes sure a 45-column pane really is 45
columns; nothing was checking that a *texture* renders the way the device
renders it. Carry this: **these pages are honest about geometry and were never
honest about attributes.** Colour is hedged correctly (panes follow the viewer's
theme, every page has a NO_COLOR twin); underline, reverse and dim have been
drawn as a browser draws them. `field.src.html` has an `.asphone` class that
renders the same specimen with the offset removed — use it when a new texture is
proposed.

What Emacs does instead:

- **The minibuffer marks nothing.** A prompt in `minibuffer-prompt` face and a
  cursor. The prompt names the question, so nothing has to outline where the
  answer goes.
- **`widget-field` is a background face** — the field's extent as a coloured
  run, not a rule under it. And even that underline is contentious enough that
  theme authors patch it; a line under a field is a known problem, not a settled
  idiom.

**Built: the background, widened to the whole row** — the field *is* the row,
and a marking that stops where the characters stop is a box drawn around today's
text. Both ends pinned (ANSI 8 behind, ANSI 15 in front): a background alone is a
coin flip on theme polarity, since ANSI 8 is light grey on a dark theme and
near-black on a light one. `NO_COLOR` gets no band, which is this texture's
honest cost.

Also proposed, and still open: **drop the underline and let the named prompt
carry it.** The three
textures become two plus the input — reverse is pressable, dim is information,
and full brightness is what you typed. Immune to the font metric, costs nothing
in NO_COLOR, and it only works because the prompt got a name. Rejected: a
background tint (the only colour-dependent texture on the surface, and the
palette is the user's) and a short underline (keeps the fault, drops the
benefit). Kept in the drawer: dim delimiters, if an unmarked row reads as blank
on the device.

### Which row goes last, and which one is lit

`bottomrow.py` → **The Last Line** — two changes that only make sense as a pair.
The count-and-hints row moves **above** the entry line and keeps the band; the
entry line goes **last**, on the terminal's own background.

The band stops being a marking on the field and becomes **a lit rule between the
list and the thing you type into**. That is Vim's arrangement exactly:
`StatusLine` is a highlighted row carrying position and state, and `:` takes the
plain final line beneath it. It is also Emacs's, by a different route — with the
band off the entry row, nothing marks the field but a prompt and a cursor, which
is the minibuffer's own answer and what `field.py` recommended before the band
was asked for.

**Neither change works alone.** A divider has to be *between* the things it
divides, so the band has to move up and the entry line has to move down to be
under it. Swapping without moving the band is worse than what ships.

What it fixes, all of them consequences of putting the band on the row with your
text on it:

- Dim placeholder text over a lit row was the least legible combination on the
  screen; it goes back onto the plain background.
- The band had to pin both ends to survive theme polarity, so typed text was
  ANSI 15 rather than the terminal's own foreground. Now it is just your text.
- Nothing divided the list from the chrome. The band is now that boundary.
- **The bottom-right-cell caveat evaporates** — the last row is plain, so no
  background is painted into the final cell and there is no auto-margin scroll
  to check for.

What it makes worse, and the thing to look at on the device: the hints and count
are now *on* the band, so the dim-on-lit problem moves rather than disappearing.
It should read better, because the whole row is dim and the eye takes it as one
quiet strip rather than as text competing with a cursor — but they are the same
ingredients, and these pages have been wrong about how a texture renders before.

Unchanged: no row gained or lost (21 browsing, 10 typing), both bottom rows
still raise the keyboard, the leader sheet still opens above the chrome.
