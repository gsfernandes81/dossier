<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# State of the Rust port

**An index, not a spec.** [`REWRITE.md`](../../REWRITE.md) and
[`REWRITE-UI.md`](../../REWRITE-UI.md) are authoritative and this file must never
restate them — it exists to say *where the port is*, *what the phone actually
is*, *what is still open*, and *which mistakes have already been paid for*.
If something here contradicts a spec, the spec wins and this file is stale.

Last true as of **2026-08-20**, branch `rust-rewrite`.

---

## 1 · Where the port stands

**R3 is feature-complete.** `crates/journal` implements §3's format — op model,
fold, compaction, torn tails, watermark defence — and `crates/ds` is a read-only
finder on top of it: browse, fuzzy search, `ctrl+t` content search, the detail
surface, `ds status` with the Syncthing REST check, and `ds open`. The phase
list and each slice's notes are in REWRITE.md; don't duplicate them here.

Two facts that shape what R4 costs:

- **The write path is built and unused.** `journal::writer` (append, HLC,
  one-process-per-writer lock, torn-tail repair) and `journal::compact` are
  tested and green. Nothing in `crates/ds` calls either — `ds` touches only
  `Journal`, `Load`, `Fold` and `store::Error`. **R4 is wiring an existing write
  path to surfaces, not inventing one.**
- **The binary has two subcommands**, `status` and `open`, plus the TUI. Every
  other verb in REWRITE.md's module map — `init`, `reset`, `file`, `export`,
  `organize`, the review queue — is unbuilt.

The Python package in `dossier/` is still the working v2 app and stays until R6
guts it. The Rust work has not touched it.

---

## 2 · What the phone actually is

Measured on the device, not assumed. **This section is the reason this file
exists** — these facts are scattered across three documents, and a session that
does not have them will re-derive them wrongly, as this project has twice.

| | |
|---|---|
| Pane, keyboard **down** | **47 × 45** — browsing. Twenty-one two-line documents. |
| Pane, keyboard **up** | **47 × 24** — querying. Ten documents. |
| Raising the keyboard | **Resizes** the terminal (SIGWINCH), never covers it. Two layouts, not one layout half-hidden. |
| `CTRL` / `ALT` | **Sticky**: tap once and it latches until the next key. Long-press holds instead. They compose with each other and with IME letters. |
| The latch reaches | The extra-keys row's **own arrows** — `CTRL` then `▶` is `ctrl+→`. A modifier tier exists with the keyboard down and no letters at all. |
| `popup:` keys | Sent by **swipe-up**. Long press auto-repeats *ordinary* keys and *holds* latching ones. |
| The extra-keys row | An Android view above the terminal, present in both keyboard states. Mouse reporting never touches it. |
| ANSI 0 | **Indistinguishable from the terminal background.** Unusable as a band. |
| ANSI 15 on 0 | Also unusable — reverse video on a black terminal *is* that pair, so it looks like the selected row. |

The user's `termux.properties` row is `CTRL·ESC  ALT·TAB  SPACE  ◀·HOME  ▲·PGUP
▼·PGDN  ▶·END  ⌨·ENTER`. REWRITE-UI §5a records what the design assumes of it.

**Never verified, and nothing depends on it:** the contents of Termux's *stock*
default extra-keys row.

---

## 3 · Settled, and open

### Settled — do not re-litigate

Each is recorded where it belongs; the link is the point of the row.

| Decision | Where |
|---|---|
| No action bar; three chrome rows on touch | REWRITE-UI §5a |
| Header expiring count is the touch filter, as a toggle | REWRITE-UI §5a |
| `Space` on an empty query opens the leader sheet — *the query is the mode* | REWRITE-UI §5a, `crates/ds/src/sheet.rs` |
| The sheet is which-key + magit infixes + picker, one object, covering the list | `sheet.rs` module docs |
| A chord is a shortcut for a verb, never a second implementation | `Model::run` |
| Entry line **last**; the row above is a lit status line | REWRITE-UI §5a |
| The band is ANSI 7 on ANSI 0, status row only, edge to edge | `Theme::band` |
| Tones on the band differ from tones off it | `Theme::on_band` |
| Three verb tiers: a key / a leader chord / a command | REWRITE-UI §5a |
| Twelve-documents-at-45×28 is superseded by the measured sizes | swept through `layout.rs`, `find.rs`, `screens.rs`, REWRITE-UI |

### Open — needs the user, or needs a phase

- **The prompt word.** `>` says nothing, which is why the empty field needs
  `Type to search` at all. `Find:` / `Search:` / `Filter:` — user's call. The
  rest of the minibuffer work rides on it: folding the match count into the
  entry line, and letting the prompt change with the question so `:` has
  somewhere to land. Drawn in `docs/dev/mockups/minibuffer.py`.
- **The verb pair revision** — `Enter` drills, `Esc` peels, arrows move the
  query cursor. **Approved and deliberately deferred**; the plan is REWRITE-UI
  §5b and the amendment markers are on REWRITE.md §4.5 invariants 2 and 6. It
  needs a selection on the detail surface, which R4 builds.
- **The arrow modifier tier** (`ctrl`/`alt` + arrows) — reserved, unbound. R4
  will want it more than Find does. Binding anything there needs modifier guards
  on the arrow arms in `input.rs`.
- **Does Termux honour `SGR 2`?** One line settles it:
  `printf '\e[2mdim\e[0m normal\n'`. If it does not, every dim element in this
  app has been at full brightness all along, which changes what the quiet parts
  of the UI are doing.
- **The succession reversal** on the filing card — deferred until the user
  confirms it is a real pain point. Do not build it speculatively.
- **Termux install notes.** The `termux.properties` minimum and recommended rows
  are written up in the mockups but not in any install doc.

---

## 4 · Traps already paid for

- **The mockups are honest about geometry and were never honest about
  attributes.** Every pane is padded to a real column count and an over-wide
  line raises — but underline, reverse and dim are drawn as a *browser* draws
  them. An underlined field was recommended for weeks with
  `text-underline-offset: 3px`, a property no terminal has; on the phone the
  rule landed through the descenders. `field.src.html` has an `.asphone` class
  that re-renders without the offset. Use it before proposing a texture.
- **Read CI's conclusion per job, never the run's overall status**, and confirm
  the run is for your HEAD. Details in [`ci-gate.md`](ci-gate.md).
- **The Windows leg is not decoration.** It has already caught a bug a green
  Linux run missed (append-mode handles lack `FILE_WRITE_DATA`, so `set_len`
  fails there and not on Linux).
- **ratatui's colour names lie about brightness.** `Color::White` is SGR 97 —
  ANSI **15**. `Color::Gray` is ANSI 7, `Color::DarkGray` is ANSI 8.
- **`«class|text»` markup does not nest**, and class names may contain digits
  only after the first letter. A nested or unmatched tag leaves raw markup in
  the line and blows up the width check somewhere unrelated.
- **Measure the phone; do not reason about it.** Three findings in a row were
  asserted from documentation and turned out wrong — the CTRL mechanic twice,
  and the pane size twice. Tag a claim as unverified rather than writing it as
  fact.

---

## 5 · Verifying your work

The full local gates are in [`../../CLAUDE.md`](../../CLAUDE.md) — mirror them
before pushing. In short: Rust is `cargo fmt --all --check`, pedantic clippy with
`-D warnings`, `cargo test --workspace --release`, and the
`aarch64-unknown-linux-musl` cross-build (which needs `clang` on PATH). Python
still has its own gate and its own CI matrix; run whichever you touched.

Three workflows: `rust.yml` (check, test on Linux + Windows, phone), `ci.yml`
(the Python matrix), `spike.yml` (the throwaway `spike/` tree).

The phone build ships as the `ds-phone` CI artifact — binary plus
[`../demo/`](../demo/), a synthetic 24-document journal so `ds` can run on a
device with no store. `gh run download <run-id> -R gsfernandes81/dossier -n
ds-phone`.
