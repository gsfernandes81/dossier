<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# State of the Rust port

**An index, not a spec.** [`REWRITE.md`](../../REWRITE.md) and
[`REWRITE-UI.md`](../../REWRITE-UI.md) are authoritative and this file must never
restate them — it exists to say *where the port is*, *what the phone actually
is*, *what is still open*, and *which mistakes have already been paid for*.
If something here contradicts a spec, the spec wins and this file is stale.

Last true as of **2026-08-21**, branch `rust-rewrite`.

---

## 1 · Where the port stands

**R3 is feature-complete, and R4's first three slices have landed.** `crates/journal`
implements §3's format — op model, fold, compaction, torn tails, watermark
defence — and `crates/ds` is a finder on top of it: browse, fuzzy search,
`ctrl+t` content search, the detail surface, `ds status` with the Syncthing REST
check, `ds open`, and now `ds init` plus **every simple field of a record made
editable through one verb**. The phase list and each slice's notes are in
REWRITE.md; don't duplicate them here.

Three facts that shape what the rest of R4 costs:

- **The write path is wired, and it is the shape the rest of R4 plugs into.**
  An edit becomes `Effect::Append(Vec<Draft>)`; a thread that owns the `Writer`
  performs it — lock, append, fsync — then re-folds in memory and posts the new
  store back as `Msg::Saved`. Undo is another `Vec<Draft>` down the same
  channel, and compaction-on-clean-exit is that thread's shutdown work. Nothing
  else in the program can reach the `Writer`.
- **The writer opens on the first append, never at launch.** `Writer::open`
  creates the journal directory and the writer's file if absent, and §7 forbids
  `.dossier/journal/` existing in the synced tree before cutover — so an eager
  open would create a journal merely by running `ds`, and would litter
  `docs/dev/demo` for the same reason. **Do not "fix" this into an eager open.**
  Its one cost is that a journal another `ds` holds is discovered at the first
  save rather than at launch.
- **The binary has three subcommands**, `status`, `open` and `init`, plus the
  TUI. Every other verb in REWRITE.md's module map — `reset`, `file`, `export`,
  `organize`, the review queue — is unbuilt. `ds init` so far asks only for the
  device name and the root; the Syncthing key and the Termux checks §4.1 wants
  are one more `ask` each.

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
| **No new `ctrl` verbs.** A which-key panel for `ctrl` is *impossible*, not unbuilt: Termux latches `CTRL` in its own UI, so the app sees one finished `ctrl+e` event and never a moment between modifier and letter. That tier can only be memorised. | `detail.rs` module docs, `input.rs` |
| The record is a surface with its own verbs: search locked out, bare letters free, `e` edits the selected row | `detail.rs`, `Model::record_verb` |
| The record has a selector; `↑`/`↓` drive it and never the list underneath | `Model::move_record` |
| Twelve-documents-at-45×28 is superseded by the measured sizes | swept through `layout.rs`, `find.rs`, `screens.rs`, REWRITE-UI |
| The writer opens lazily, on the first append — never at launch | `main.rs::writer_session` |
| A save re-folds; it never patches the `Store` in place | `main.rs::write_loop` |
| The edit verb is `ctrl+e`, not a bare letter — the list has focus in the split | `input.rs` |
| Editing is off, with a reason, rather than absent — `WriteState` | `app.rs` |

### Open — needs the user, or needs a phase

- **The prompt word.** `>` says nothing, which is why the empty field needs
  `Type to search` at all. `Find:` / `Search:` / `Filter:` — user's call. The
  rest of the minibuffer work rides on it: folding the match count into the
  entry line, and letting the prompt change with the question so `:` has
  somewhere to land. Drawn in `docs/dev/mockups/minibuffer.py`.
- **The verb pair revision** — `Enter` drills, `Esc` peels, arrows move the
  query cursor. **Approved and deliberately deferred**; the plan is REWRITE-UI
  §5b and the amendment markers are on REWRITE.md §4.5 invariants 2 and 6. Its
  one prerequisite — a selection on the detail surface — **now exists** (slice
  2), so what is left is the key routing and the Home/End resolution.
- **The arrow modifier tier** (`ctrl`/`alt` + arrows) — reserved, unbound, and
  now unlikely to be used: the same argument that retired `ctrl+e` applies to it.
  Reachable by thumb, teachable by nothing.
- **`s` supersede, `b` bundle, `u` undo** — specced in REWRITE-UI §2, unbuilt.
  They are bare letters on the record surface, which is legal now that search is
  locked out there. Add them with the slices that implement them.
- **Does Termux honour `SGR 2`?** One line settles it:
  `printf '\e[2mdim\e[0m normal\n'`. If it does not, every dim element in this
  app has been at full brightness all along, which changes what the quiet parts
  of the UI are doing.
- **The succession reversal** on the filing card — deferred until the user
  confirms it is a real pain point. Do not build it speculatively.
- **Creating a document.** The largest hole in R4: nothing in the Rust build
  makes a new `doc`, only edits ones the fold already knows. It needs an id
  scheme, a create flow (`space` → `n` is the obvious spelling) and a
  name-first rule, since `name` is the one field `validate` will not leave
  empty. Until it exists the Rust build cannot own the store.
- **The rest of R4**: undo (inverse ops — the journal is the history, §3.3),
  slots with insert-and-shift, supersession, bundle membership, file
  attach/detach/primary, delete, settings ops, `ds reset`. The text fields are
  done; **what is left are the structured ones**, and each of those needs a
  picker rather than a text buffer — a slot move shifts its neighbours, and
  `bundles`/`renews` are memberships of another entity, not values.
- **Syncthing's two-folder send/receive arrangement, set up by `ds`.** The user
  asked for this explicitly: the folder pair and their send-only / receive-only
  roles get configured **from the `ds` side over the Syncthing REST API**, not by
  hand in each device's web UI. The arrangement itself is unspecified — write it
  down first. It is also the port's **first write to Syncthing's config** (the
  REST client is read-only today, `syncthing.rs`), so it needs a write-capable
  API key, idempotency, and a dry run. Recorded in REWRITE.md §7.
- **Syncthing conflict files.** The journal is conflict-free by construction, so
  `.sync-conflict-*` should never appear on it — but nothing notices if one does,
  and the real files tree can still produce them. `ds status` is where that goes.
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
- **Pedantic clippy fires on test helpers too.** A `Box<Store>` returned from a
  test fixture failed `unnecessary_box_returns` after the whole suite was green
  — `cargo test` and `cargo clippy --all-targets` are different gates and the
  second one is CI's.
- **`dirs` ignores the environment on Windows.** It resolves the config and data
  directories through the Known Folder API, so `XDG_CONFIG_HOME`/`LOCALAPPDATA`
  do not sandbox it there. Harmless while `ds` only read config; a writing test
  would have written the CI runner's real one. `DS_CONFIG_DIR` and
  `DS_STATE_DIR` are the seam — use them in any test that writes.
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
