<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# R0.2 — the Ratatui spike: protocol, results, findings

Phase R0.2 of [`REWRITE.md`](../../REWRITE.md) is the **go/no-go gate for the
whole rewrite**: a Ratatui list of 1,000 synthetic documents, cross-compiled to
static musl, run on Termux and Windows, measuring time-to-first-paint and
verifying the terminal tricks the v2 app depends on. The code is
[`spike/`](../../spike/) (throwaway — see its README); this file is the protocol,
the results table, and the findings.

The number to embarrass is the R0.1 baseline in
[`startup-timing.md`](startup-timing.md): **phone cold 1053 ms usable, warm
median ≈ 670 ms**, against a budget of **< 100 ms (150 ms acceptable)**.

## 1. Getting a binary

**From CI (easiest, and the only route to a phone binary without a PC):** the
`spike` workflow uploads three artifacts per run — `ds-spike-aarch64-musl` (the
phone), `ds-spike-windows-latest`, `ds-spike-ubuntu-latest`. It can be started by
hand from the Actions tab (`workflow_dispatch`).

**From a PC:**

```bash
cd spike
cargo build --release                                       # this machine
rustup target add aarch64-unknown-linux-musl                # once
cargo build --release --target aarch64-unknown-linux-musl   # the phone
```

On the phone: copy the binary into `$PATH` (`~/../usr/bin` under Termux),
`chmod +x`, run `ds-spike`. **No `pkg install` of anything** — that is the
point of the static build, and it is what replaces the python/uv/libyaml chain.

## 2. Measurement protocol

Mirrors R0.1's exactly, so the two are comparable. Three runs per condition;
record every run, not just the best.

**Termux — the number that matters:**

```sh
time DS_SPIKE_TIMING=exit ds-spike        # startup: paint one frame, print, exit
ds-spike --bench                          # frame + keystroke timings, headless
ds-spike                                  # the interactive checks in §3
```

- *Cold*: swipe Termux away from recents first, then launch. Note which.
- *Warm*: repeat immediately in the same session.

The `ds-spike-timing:` line reads:

```
ds-spike-timing: usable 7.2ms (data 0.9ms · term init 0.4ms · first paint 5.9ms) [exec→main ~10ms · rss 3.1MB · 1000 docs]
```

- **usable** — launch to the first frame showing rows; *the budgeted number*.
- **data** — building the 1,000-row synthetic store (R3's analog: the journal fold).
- **term init** — raw mode + alternate screen + mouse capture.
- **first paint** — render and flush.
- **exec→main** — kernel exec to the first line of `main`, ±10 ms (`USER_HZ`).
  R0.1 could only infer this as `shell total − probe total` (~300–380 ms of
  CPython boot); here it should be noise.
- **rss** — the §9 "binary RSS on phone < 30 MB" budget line.

**Windows (PowerShell):**

```powershell
$env:DS_SPIKE_TIMING = "exit"; Measure-Command { ds-spike.exe | Out-Default }
.\ds-spike.exe --bench
.\ds-spike.exe
```

## 3. Interactive verification checklist

Run `ds-spike` with no arguments and work down this list. `F4` shows the live
diagnostics; `F2` shows every input event exactly as the terminal delivered it,
which is how a "nothing happened" is turned into evidence.

| # | Check | Where | Expected |
|---|---|---|---|
| 1 | Tap a row | Termux | row highlights (tap = select) |
| 2 | Tap the highlighted row | Termux | opens (flash line names the file) |
| 3 | Drag / swipe the list | Termux | list scrolls; `F2` shows scroll or drag events |
| 4 | Terminal scrollback | Termux | list owns scrolling — the terminal's own scrollback stays out of the way (#4302) |
| 5 | `⌨ Keys` button / `F5`, then tap | Termux | soft keyboard **rises** (mouse reporting dropped for one tap); search bar says `[mouse off]` |
| 6 | Type after the keyboard is up | Termux | characters land in the search bar; mouse reporting restores itself |
| 7 | Type a bare letter on the list | both | it starts a search and the **first character is kept** |
| 8 | `Enter` | both | opens; on a row with no file it falls through to the record, never errors |
| 9 | `Esc` from a panel, then search, then detail, then base | both | peels exactly one layer per press; base state arms, second Esc quits |
| 10 | `Esc` used to dismiss the IME | Termux | does **not** quit the app |
| 11 | `ctrl+t`, `ctrl+q` | Termux | modifier combos arrive (Termux keyboard variants, #1255) |
| 12 | `F3` glyph panel | both | right-hand `|` bars line up; note which glyph rows render as boxes |
| 13 | Rotate the phone / resize the window | both | re-layouts; `F2` logs a resize; two-line rows at < 70 cols, split detail at ≥ 100 |
| 14 | Shrink below 38×12 | desktop | "terminal too small" notice instead of a broken frame |
| 15 | Quit | both | terminal is restored: no raw mode, no stuck mouse reporting, cursor back |

Anything that fails is a finding, not a bug to fix in the spike — write it into
§5 and decide whether it changes R3's plan.

## 4. Results

### 4.1 Reference numbers (dev container, x86_64 Linux — not the gate)

Measured 2026-08-16 on the remote dev container, release build. These bound the
best case and prove the code, not the phone.

| Metric | Value |
|---|---|
| usable (5 runs, real PTY) | **1.0 / 1.0 / 1.0 / 1.0 / 1.1 ms** (data 0.9 · term init 0.1 · paint 0.0–0.2) |
| wall clock, harness subtracted | ~1 ms (25 ms measured − 24 ms `script`+`true` baseline) |
| RSS | **3.0 MB** |
| frame, 45×28 / 80×24 / 120×40 (median) | 0.12 / 0.19 / 0.44 ms |
| worst keystroke→frame (incl. filtering 1,000 rows) | **0.86 ms** |
| binary size | 844 KB (host), **810 KB static aarch64 musl** |

### 4.2 aarch64 under emulation (qemu-user on x86_64 — sanity only)

Emulation costs roughly an order of magnitude, so these are a *ceiling*, not a
prediction; what they prove is that the cross-compiled binary genuinely runs.

| Metric | Value |
|---|---|
| usable (PTY, `--paint-once`) | 22.1 ms (data 16.9 · term init 1.7 · paint 3.5) |
| frame, 45×28 (median / p95) | 1.29 / 2.21 ms |
| worst keystroke→frame | 7.70 ms |

### 4.3 Phone (Termux) — **to be filled in on the device**

| Run | Condition | Shell total | usable | data | term init | first paint | exec→main | RSS |
|---|---|---|---|---|---|---|---|---|
| 1 | cold | | | | | | | |
| 2 | warm | | | | | | | |
| 3 | warm | | | | | | | |
| 4 | warm | | | | | | | |

`ds-spike --bench` on the phone:

| Shape | median | p95 | max | worst key→frame |
|---|---|---|---|---|
| 45×28 | | | | |
| 80×24 | | | | |
| 120×40 | | | | |

Checklist §3 results (1–15): _pending_

### 4.4 Windows — **to be filled in**

Already covered by CI (`windows-latest`, MSVC, first run green): `cargo test
--release` and the headless perf gate both pass, so the renderer and the
keystroke→frame budget hold on Windows. What CI cannot see is startup in a real
console and the interactive behaviour — that is what the rows below are for.

| Run | Shell total | usable | data | term init | first paint |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |

Checklist §3 results (7–9, 12–15): _pending_

### 4.5 CI status

First run of the `spike` workflow on this branch: **all four jobs green** —
`check` (fmt + pedantic clippy denied), `test (ubuntu-latest)`,
`test (windows-latest)` (both including the `--assert-budget` perf gate), and
`phone` (static aarch64 cross-build, verified statically linked, smoke-run under
qemu, artifact uploaded). The Python `CI` workflow is unaffected and green.

## 5. Findings so far

Recorded as they were established; the device-dependent ones stay open until the
phone run.

1. **The musl toolchain question is settled, and the answer is "nothing".**
   `rustup target add aarch64-unknown-linux-musl` plus `rust-lld` with
   `-C target-feature=+crt-static -C link-self-contained=yes` produces a fully
   static ARM64 binary. No NDK, no `musl-gcc`, no `cargo-zigbuild` — REWRITE.md
   §4.4 budgeted for picking one of those and none is needed. It is three lines
   in [`spike/.cargo/config.toml`](../../spike/.cargo/config.toml), it works on a
   clean CI runner, and the artifact is **810 KB**.
2. **The rendering cost is not where the budget is.** Painting 1,000 rows into a
   phone-sized viewport is ~0.1 ms because the list is virtualized by hand; the
   whole keystroke path — re-filter 1,000 documents, rebuild the window, redraw —
   is under a millisecond on the dev box and under 8 ms even under emulation.
   The 16 ms keystroke→frame budget has an enormous margin, which means R3 can
   spend on *correctness* (fuzzy matching, real fields) without watching frames.
3. **Startup is dominated by nothing at all.** `data` (0.9 ms) is the largest
   in-process bucket, and it is synthetic-store generation that R3 replaces with
   a journal fold — the sizing note in REWRITE.md §3.3 expects single-digit ms
   there too. `exec→main` is unmeasurably small next to CPython's ~300 ms. The
   structural claim behind the rewrite (only removing the interpreter+framework
   layer can reach the budget) survives contact with a real binary.
4. **Idle costs zero.** The event loop blocks in `event::read()`, so an open app
   waiting for input consumes no CPU — a difference from a framework with a
   render tick, and it matters on a phone battery.
5. **The narrow-layout rules bite earlier than the mock suggests.** With detail
   open on a 100-column terminal the list pane is 55 columns, which is below
   REWRITE-UI.md's 70-column two-line threshold, so the split shows two-line rows
   beside the detail. It is per spec but busier than the mock's single-line rows.
   R3 should decide deliberately: either lower the two-line threshold to ~50, or
   widen the split point past 100.
6. **A 45-column header has room for the counts or the title, not both.** Caught
   by driving the spike at phone size: the full header ran into the attention
   counts and the terminal clipped them mid-word. The spike now sheds the title
   below 72 columns. R3's header needs the same treatment — the attention counts
   are the part that must survive.
7. **Our own PTY harness had a wide-character bug.** `tools/ptyterm.py` died with
   an `IndexError` the moment the glyph panel put CJK and emoji on screen (pyte's
   `display` indexes `char[0]` on the empty cell it writes for the trailing half
   of a wide glyph). Fixed there. Worth knowing before R3 leans on that harness
   for TUI smoke tests, since the real app will show CJK document names.
8. **Ratatui 0.30 + crossterm 0.29 needed no workarounds** for anything the spike
   does: SGR mouse capture, alternate screen, resize, key-release filtering,
   `TestBackend` for headless rendering. Two dependencies total (`ratatui` and
   `unicode-width`), 810 KB static.

### Open until the device runs

- Whether Termux delivers taps as SGR clicks, and drags as scrolls or drags
  (checklist 1–4).
- Whether the mouse-mode drop actually raises the IME on the current Termux
  build (checklist 5–6) — DESIGN §14 says it should; nothing since has retested.
- Which glyph rows render as boxes on the phone's font (checklist 12) — decides
  how much the ASCII fallback carries.
- Real phone `usable` and RSS.

## 6. Go / no-go

The gate is REWRITE.md §9's phone budget plus the terminal tricks. A **go** needs
`usable` on the phone within 150 ms (target 100 ms), keystroke→frame within
33 ms, RSS under 50 MB, and checklist items 1–11 passing or having a workable
answer. On the evidence so far — a 1 ms desktop paint, a 22 ms *emulated* ARM
paint, a static 810 KB binary that runs — the performance half of the gate looks
decided by a wide margin; the interaction half needs the phone.

A **no-go** would look like: taps not arriving under Termux's SGR reporting, the
IME affordance no longer working (making the app unusable one-handed), or a phone
`usable` over 150 ms. None of these are visible yet, and only the phone can rule
them out.
