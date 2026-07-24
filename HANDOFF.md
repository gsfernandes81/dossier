<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# Handoff — persistent command line (in-flight design)

Written to move this work from local to the remote dev container mid-design. Nothing
below is committed as code yet; it's an agreed design plus the context to build it.
When it's built and merged, delete this file.

**Read first:** [`docs/dev/project-context.md`](docs/dev/project-context.md),
[`docs/dev/ci-gate.md`](docs/dev/ci-gate.md), [`docs/dev/testing.md`](docs/dev/testing.md),
and the `HomeScreen` docstring in `dossier/tui/home.py`. Everything here assumes them.

## Where we are

The command surface had a multi-part clean-up land on `main` already (all shipped, CI
green):

- **Slice 1 (done):** the command palette became discoverable — the provider now
  implements `discover()` (it opened showing only Textual's own Theme/Quit/Screenshot
  before), `ctrl+p` is a *visible* footer binding, the palette respects `check_action`
  (it used to run actions a keypress was forbidden to run — e.g. "Edit" mid-edit wiped the
  form), a phantom `ctrl+t` got bound, and the 21-entry list was trimmed to ~19 with
  duplicates merged (`dossier/tui/commands.py` is the single shared catalog; `?` shows a
  count-per-group index of it).
- Contextual **`e`/`o`/`u` verbs on the focused detail pane**, the **attention chips**
  (tap/click to Watch/Review/Intake; the `taps-land` class keeps them honest on Termux),
  **OSC 52 copy-path** (crosses SSH), and the review **footer/tab legibility** pass —
  all done.

## The decision (agreed with the user)

Build a **persistent command line** — the vim/helix/k9s idiom — instead of Textual's
modal command palette. The always-visible bottom bar does double duty:

- plain typing → **search** (the home's document filter; find-fast, unchanged);
- **`:` or `>`** as the first character → **command mode** in place — the bar becomes a
  command bar, results render in a swapped-in list, Enter runs one. **Both `:` and `>`
  trigger it**, no primary (both are non-letters, so the find-fast `on_key` router already
  routes them in).
- **Quit is a command** in the new surface (`:q`-style / a "Quit" entry) — this replaces
  the system-palette Quit lost when Textual's palette is retired. `ctrl+q` stays too.

The user's framing: the bar should ultimately be **present on every surface**, not just
the home. That splits into two things that must be kept separate:

- **Command entry (`:`/`>`) is universal** — you always might want to run a command / quit,
  so it belongs on every surface.
- **Search is per-surface** — "search" means "filter *this* list", which is meaningful on
  the home (documents) and could be on Watch/Bundles, but is meaningless on Settings (a
  form). So the *search* half appears only where there's a list to filter; the *command*
  half is always there. (Exactly vim: `:` always works, `/` searches the current buffer.)

## Why this fits (skill-lens verdict)

- It's a **canonical TUI pattern** (persistent command line / k9s `:` ex-mode), not an
  invention.
- It **finishes the modal→spatial migration** the Review refactor started — Textual's
  `ctrl+p` palette is the last hidden modal command surface.
- **Gating comes free**: the search input is already disabled while editing and under
  review-mode, so routing commands through it structurally fixes the palette's old
  `check_action` bypass rather than patching it.

## Plan — two phases

### Phase A (do first — contained, high value)

The in-place `:`/`>` command mode on the home's persistent bar, retiring Textual's modal
palette. This immediately gives the persistent-command-line feel on **home + review**
(review is already home columns, so it shares the bar). Sketch:

- New `#commands` `OptionList` swapped into the documents column under a `command-mode`
  class, mirroring how `review-mode` takes columns 1–2. **Do not reuse `#documents`** —
  that perturbs the preserved-highlight logic, the "… and N more" cap row, and the
  `DocumentList` two-click mouse verb (commands want single-tap). Fuzzy-match with the
  same `textual.fuzzy.Matcher` the palette used, over `commands.ENTRIES` (+ a Quit entry).
- Route in `on_input_changed`: `value.startswith((":", ">"))` → `command-mode`, render
  filtered commands, and **never set `_filter_text`** in that branch (it would engage the
  `.searching` class and its locations-snap side effects). `on_input_submitted` in command
  mode dispatches the highlighted/top command and **must not fall through to
  `_activate_doc`** (else `:` + Enter opens a PDF). Empty query renders the grouped catalog
  with disabled (`id=None`) group-header rows the cursor can't land on.
- Retire Textual's palette: `ENABLE_COMMAND_PALETTE = False` on `DossierApp`, and converge
  the three entrances — `ctrl+p`, the `⭘` header-icon click (`app.command_palette`), and
  the touch **Commands** button — onto `home.enter_command_mode()` (focus `#search`, insert
  `:`). **Add Quit to `ENTRIES` *before* flipping the flag**, or the system Quit vanishes.
- Migrate the tests + the PTY scene that currently assert on the palette's strings
  (`"Search for commands"`, driving "Toggle expiring" through it) to the new mode.

### Phase B (the "everywhere" completion — larger, separate)

De-modal Watch / Bundles / Intake / Settings into home modes so the persistent bar is
truly always present, with per-surface search where a list exists. Unlike Review, these
modals aren't *broken* by being modal (no lossy round-trip) — so this is a
consistency/discoverability win, not a bug fix. Worth doing, shouldn't block A.

## Traps (all previously verified against Textual 8.2.8)

- Textual dispatches `_on_click`/key handlers for **every class in the MRO** subclass-first;
  `event.stop()` does **not** stop the base handler — `prevent_default()` does.
- `check_action` returning `None` = disabled-but-**visible** (greyed); `False` =
  disabled-and-**hidden**. Use `False` to actually hide.
- A widget's `DEFAULT_CSS` is scoped to its type; host/breakpoint rules must live in
  `HomeScreen`'s CSS. `SCOPED_CSS = False` for self-type-class selectors. (See
  [`docs/dev/testing.md`](docs/dev/testing.md).)
- Don't let the type-to-search `on_key` router fire while a non-home surface owns the
  columns; guard as `review-mode` already does.

## Before starting on the remote

- Run the full gate once to confirm the container is green (see
  [`docs/dev/ci-gate.md`](docs/dev/ci-gate.md)) — including the driver test.
- Design Phase A with a **Fable advisor** first (the standing workflow), as the Review
  refactor was; it restructures the home's input routing.
- git/gh on the container is baked by the dev-container image (see CLAUDE.md) — the
  Windows-host SSH/gh notes don't apply there.
