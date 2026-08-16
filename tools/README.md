# tools/ — dev-only

Not shipped in the wheel; not touched by CI (lives outside `testpaths` and the
`ruff`/`ty` scope, which are `dossier`-only).

## Real-terminal TUI driver

`ptyterm.py` drives a program in a **real** pseudo-terminal (ConPTY on Windows
via `pywinpty`, the stdlib `pty` on POSIX/Termux) and mirrors its screen with
[`pyte`](https://pypi.org/project/pyte/), a VT100 emulator. You get the actual
terminal framebuffer as text — and per-cell colours — instead of an SVG export
you have to reconstruct. It answers the terminal *queries* (Device Attributes,
cursor position, sync-output) that ConPTY and Textual block on, which a plain
pipe would never satisfy.

These deps live in the non-default `driver` dependency group, so a plain
`uv sync` / CI never installs them and the integration test auto-skips.

### Drive the app and watch it

```bash
uv run --group driver python tools/drive_tui.py          # desktop layout
uv run --group driver python tools/drive_tui.py --touch  # Termux/touch layout
```

Runs the real `DossierApp` against a throwaway sample store (never your actual
documents) and prints each screen.

### Opt-in integration test

```bash
uv run --group driver python -m pytest tools/test_terminal_integration.py
```

### Reuse the harness

```python
from ptyterm import PtyTerm
t = PtyTerm([sys.executable, "tools/run_tui_temp.py", store_root], cols=100, rows=30)
t.wait_for("dossier")          # block until text appears
t.send("down", "right")        # named keys or literal text
print(t.text())                # whole screen as text
ch, fg, bg, bold, rev = t.cell(row, col)   # per-cell char + colours
t.close()
```

## v3 rewrite tooling

Two more dev-only scripts, both tied to [`REWRITE.md`](../REWRITE.md) and both
deleted when their phase is done.

### Drive the Rust spike (R0.2)

```bash
uv run --group driver python tools/drive_spike.py          # 100x30 desktop
uv run --group driver python tools/drive_spike.py --touch  # 45x28 phone
```

`ptyterm.py` drives a native binary as happily as the Textual app — the terminal
is the interface, not the language. Build it first (`cd spike && cargo build
--release`), or pass `--bin PATH`.

### Export a v2 store to a v3 journal (R2)

```bash
uv run python tools/export_journal.py --dest ~/journal-rehearsal
```

Read-only with respect to the store. Two guards it enforces rather than assumes:
the destination **may not be inside the Syncthing root** (anything there syncs by
default, and a half-built journal must never reach the phone before cutover), and
**parity is the exit code** — any field-level mismatch is a hard stop and prints
every difference.

To see what the Rust core makes of the result:

```bash
cargo run -p journal --example fold_dir -- ~/journal-rehearsal --stats
```

Its canonical JSON must match the Python fold's byte-for-byte; that is the
cross-language check the golden vectors cannot cover, because no fixture can be
written for the real store.
