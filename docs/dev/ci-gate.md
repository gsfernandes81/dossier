<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# Verifying CI — the local gate must mirror it, and the conclusion must be read

**The local gate lies unless it mirrors CI's environment exactly.** Three separate
CI failures traced to a local gate that had drifted from CI; each is avoidable.

## 1. Run the gate the way CI runs it

The exact mirror, in order (paths shown for the primary Windows host; on Linux/the
dev container `uv` is on PATH):

```bash
# check job — no extras, linux platform (this is the source of truth for ty)
uv sync
uv run --no-sync ruff check dossier
uv run --no-sync ruff format --check dossier
uv run --no-sync ty check --python-platform linux dossier

# test + driver jobs — restore extras + the driver group
uv sync --extra scan --extra dedup --group driver
uv run --no-sync python -m pytest
uv run --no-sync --group driver python -m pytest tools/test_terminal_integration.py
```

**ty must be checked as CI runs it: `--python-platform linux`, no extras.** Running ty
on Windows with the `scan`/`dedup` extras installed *misses* Linux-only errors (e.g.
`ctypes.windll` → `unresolved-attribute`; CI's check job is ubuntu). CI is stricter in
subtler ways too (it has rejected `property.fget(None)` and `App[None]` vs `App[object]`
in tests), so the linux/no-extras run is authoritative.

> `scan.py`'s `# ty: ignore[unresolved-import]` for `pypdfium2` shows as an
> *unused-ignore* locally when the `scan` extra is installed, but is correct for CI
> (which has no extra). Don't remove it to satisfy the local run.

## 2. The driver test is outside `testpaths` — plain pytest never runs it

Any TUI change (bindings, buttons, footer text) can break
`tools/test_terminal_integration.py`, and the default suite won't catch it. On any TUI
change, also run it explicitly (the command above). It drives the real app in a PTY and
presses actual keys, so a removed binding fails it. See [`testing.md`](testing.md) for
how to write PTY assertions that don't flake (poll, never sleep) — and note **never send
a multi-character burst across a focus change**: `term.send("pass")` passed locally 3/3
but failed `driver (windows-latest)` because the trailing chars raced the type-to-search
router's focus switch on a slower runner. Send one character, assert the effect; cover
multi-char typing in the fast Pilot suite.

## 3. Read the CI *conclusion* per job — never infer it

Two separate ways the exit code has lied:

- A trailing `; echo …` after a command makes the reported exit code the `echo`'s
  (always 0) — this masked four red runs.
- **`gh run watch <id> --exit-status` itself exited 0 on a run whose conclusion was
  `failure`** (a `driver (windows-latest)` job had failed).

So the only trustworthy check is to query the conclusion after the watch returns, and
read it **per job** (`check`/`test` routinely pass while `driver` fails):

```bash
gh run view <id> --json conclusion,jobs \
  --jq '{overall: .conclusion, jobs: [.jobs[] | {name: .name, conclusion: .conclusion}]}'
```

Want `overall: "success"`. Also **`git fetch` and re-check the run belongs to your HEAD**
before trusting a conclusion — overlapping runs on `main` have caused a parent commit's
failure to be misread as the current one.

**Why this matters:** claiming green when CI is red erodes trust and lets breakage pile
up — four merges once rode a red job before it was noticed. "Just a flake" is a
conclusion to *earn* (see [`testing.md`](testing.md)), never to assume: one such "flake"
was a genuine product bug wearing a flake's clothing.
