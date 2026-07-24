<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# Testing the TUI — timing, Textual gotchas, the real-terminal driver

How dossier's Textual layer is tested, and the traps that have actually bitten. See
[`ci-gate.md`](ci-gate.md) for running the full gate and reading its conclusion.

## Never sleep-then-assert — poll for the effect

**Every timing flake in these tests has been the same shape: wait a guessed amount, then
assert.** The fix is always to poll for the *effect* instead. Three variants:

1. **`await app.workers.wait_for_complete()` does not mean "the work happened."** It
   returns *immediately* when the worker hasn't registered yet, and one
   `await pilot.pause()` doesn't guarantee it has. So `trigger; pause();
   wait_for_complete(); assert` reads as synchronous but asserts against the pre-work
   state and passes on scheduling luck. Use `await _settle(pilot, lambda: <condition>)`
   in `dossier/tests/test_tui.py` — it drains workers in a loop until the predicate holds,
   then fails with a named message. `_await_review_load(pilot)` is the review-specific
   wrapper.

2. **Repeated `await pilot.pause()` is the tell.** Two pauses in a row means someone found
   one wasn't enough — the awaited work (an async `remove_children`, a mount) needs an
   unknown number of pump turns. One pause after changing a reactive is fine; two is a
   guess. Convert to `_settle`.

3. **In the PTY harness, a fixed `settle=` is a bet on machine load.** Use
   `term.wait_for(substring)` / `term.wait_until(predicate)` / `term.wait_for_exit()`
   (all in `tools/ptyterm.py`). Two real traps: typing into the command palette *before it
   opened* let the home's type-to-search router eat the keystrokes (wait for
   `"Search for commands"` first); and waiting on `"Search name"` before sampling a cell
   colour proved nothing, because that placeholder is on screen focused or not. Polling is
   also *faster* — it returns as soon as the condition holds.

These fail only on CI's slowest runner and pass locally, so they read as noise and get
re-run instead of fixed. **"Just a flake" is a conclusion to earn** — one such flake was a
genuine product bug (Review's default tab landing on the wrong tab because the load beat
the tab bar's mount) wearing a flake's clothing.

## Textual gotcha — a Screen's own styles must use `DEFAULT_CSS`

A custom `Screen`/widget's own stylesheet must go in the **`DEFAULT_CSS`** classvar, not
`CSS`. The `CSS`/`CSS_PATH` classvars are only gathered for the **App** and for screens
registered in `SCREENS` / pushed by class — a screen returned from
`App.get_default_screen()` has its `CSS` **silently dropped** (no error; the rules never
enter `app.stylesheet`).

*Symptom:* layout looks like no CSS applied (panes evenly split, `display:none` rules
don't take, `widget.styles.get_rule("display")` is `None`). *Confirm:* count
`len(app.stylesheet.rules)` with vs. without the block. *Fix:* use `DEFAULT_CSS`. Set
`SCOPED_CSS = False` on the screen if its selectors key off the screen's *own* classes
(`MyScreen.-narrow #child`) — default scoping rewrites such self-type selectors so they
never match. This is why `HomeScreen` uses `DEFAULT_CSS` + `SCOPED_CSS = False`, and why
`ReviewPane`'s DEFAULT_CSS holds only internal rules while its breakpoint/host rules live
in `HomeScreen`'s CSS (a widget's CSS is scoped to its own type; a rule leading with
another type gets rewritten into one that can never match). Seen with Textual 8.2.8.

## Driving and *seeing* the real TUI

To drive the real `ds` TUI as text **and colours** (not SVG screenshots), use the
committed tools in `tools/`:

- **`tools/ptyterm.py`** — `PtyTerm` runs a program in a real pseudo-terminal (ConPTY via
  `pywinpty` on Windows, stdlib `pty` on POSIX/Termux) and mirrors its screen with **pyte**
  (VT100 emulator). API: `send(*keys, settle=)`, `wait_for(text)`, `wait_until(predicate)`,
  `wait_for_exit()`, `text()`, `cell(row, col)` → char **+ real fg/bg colours**.
- **`tools/run_tui_temp.py`** — launches the real `DossierApp` on a throwaway sample store
  (never the user's docs).
- Run interactively: `uv run --group driver python tools/drive_tui.py [--touch]`. The
  opt-in test: `uv run --group driver python -m pytest tools/test_terminal_integration.py`.

Deps (`pyte`, `pywinpty`) live in the non-default `driver` group, so plain `uv sync` / CI
skip them and the test `importorskip`s.

**Key gotcha:** ConPTY and Textual *query* the terminal (Device Attributes `\x1b[c`,
cursor position, sync-output) and **stall until answered** — pyte doesn't reply, so
`PtyTerm._respond` answers them. Without that, nothing renders (you get only the ~23-byte
handshake). On Windows, force `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`
before printing box-glyph screens (cp1252 chokes). Reading `cell().fg` lets you assert
colour objectively (e.g. home expiry rows: ok green, expiring orange, expired red) —
something an SVG export can't give.

**In `PtyTerm`, `Ctrl+P` is the raw byte `"\x10"`, `Ctrl+Q` is `"\x11"`** — `PtyTerm.KEYS`
has no ctrl combos.

## Where tests live

- Fast Pilot suite: `dossier/tests/test_tui.py`, run headless via `async with
  app.run_test()`. `asyncio_mode = "strict"` — async tests need `@pytest.mark.asyncio`.
- Real-PTY smoke: `tools/test_terminal_integration.py` (outside `testpaths`; run
  explicitly, see [`ci-gate.md`](ci-gate.md)).
- Cover key routing / focus / footer with the fast suite; keep the PTY suite to one or two
  cross-widget smoke flows.
