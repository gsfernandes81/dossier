<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# `ds-spike` — Phase R0.2 (throwaway)

The go/no-go spike for the v3 rewrite ([`REWRITE.md`](../REWRITE.md) §6 R0.2): a
Ratatui list of **1,000 synthetic documents**, cross-compiled to a static musl
binary, to answer one question with numbers — *is Rust + Ratatui fast enough and
workable enough on the phone to bet the rewrite on?*

**Nothing here is production code.** No journal, no real store, no persistence;
R1's `crates/journal` and R3's `crates/ds` start from scratch. What survives this
phase is the *findings* (recorded in `REWRITE.md` §6 and
[`docs/dev/spike-r02.md`](../docs/dev/spike-r02.md)) and the measurement
protocol, not the code. Delete the directory once R3 renders its own list.

## Build

```bash
cd spike
cargo build --release                                    # host
cargo build --release --target aarch64-unknown-linux-musl   # the phone
```

The phone build needs **only** `rustup target add aarch64-unknown-linux-musl`.
No NDK, no `musl-gcc`, no `cargo-zigbuild`: `.cargo/config.toml` links with
`rust-lld` against Rust's own self-contained musl objects. The result is a fully
static ~810 KB binary — copy it into `$PATH` on Termux and run it.

## Run

```bash
./target/release/ds-spike             # the TUI
./target/release/ds-spike --bench     # headless frame/keystroke timings
DS_SPIKE_TIMING=exit ./target/release/ds-spike   # paint once, print timing, exit
```

Keys: type to search (the list binds **no** letter keys), `Enter` opens,
`→` detail, `F2` input-event inspector, `F3` glyph/width check, `F4`
diagnostics + budget verdicts, `F5` drops mouse reporting so Termux raises its
keyboard, `Esc` peels one layer (twice at base quits), `ctrl+q` quits. Tap
selects; tap the selected row to open.

The full on-device measurement + verification protocol — what to run on the
phone and on Windows, and where to record it — is in
[`docs/dev/spike-r02.md`](../docs/dev/spike-r02.md).

## Layout

| File | Role |
|---|---|
| `src/main.rs` | modes, terminal setup/teardown, the event loop |
| `src/app.rs` | state + `update`: the REWRITE.md §4.5 interaction invariants |
| `src/ui.rs` | `view`: the REWRITE-UI.md Find surface, hand-virtualized |
| `src/data.rs` | the deterministic 1,000-document synthetic store |
| `src/timing.rs` | startup milestones, `exec→main`, RSS, frame stats |
| `src/bench.rs` | headless `TestBackend` timings + the CI budget gate |

`cargo test` covers the invariants as executable claims (find-fast keeps the
first character, `Enter` never dies, `Esc` peels exactly one layer, tap-then-tap
opens, the list stays virtualized, widths are counted in columns not bytes).
