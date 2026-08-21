# dossier — Project Rules

> **⚠ Rewrite planned:** a v3 rewrite (Rust + Ratatui core, journal store, Python
> demoted to a desktop enrichment satellite) is specified in **[`REWRITE.md`](REWRITE.md)**.
> If your task is part of that rewrite, `REWRITE.md` is authoritative and overrides the
> Python-specific rules below for the Rust crates; the rules below still govern the
> Python code while it exists. The layout gate is settled in
> [`REWRITE-UI.md`](REWRITE-UI.md); **phase R0.2's go/no-go gate is GO** — the phone
> measured 6.2 ms to usable against the Python app's 1053 ms, with every touch/IME
> trick intact. The throwaway spike is [`spike/`](spike/); results and findings in
> [`docs/dev/spike-r02.md`](docs/dev/spike-r02.md). One binding finding from it:
> **Termux has no function keys**, so nothing user-facing may sit behind one.
>
> **Rust local gate** (mirror it before pushing, same discipline as the Python one).
> The workspace (`crates/*`, CI: `rust` workflow) and the throwaway spike (`spike/`,
> CI: `spike` workflow) are separate cargo trees — run both if you touched both:
> ```bash
> cargo fmt --all --check
> cargo clippy --workspace --all-targets -- -D warnings  # pedantic on; triage, never silence
> cargo test --workspace --release -- --nocapture        # perf gates assert in release only
> cargo build --workspace --release --target aarch64-unknown-linux-musl   # the phone target
> (cd spike && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test --release)
> ```
> The phone cross-build needs **clang** on PATH (`.cargo/config.toml` points `cc` at
> it). Rust itself still needs nothing but `rustup target add` — the C compiler is
> for `ring`, which arrives with the Syncthing REST check via rustls.
> **The Windows CI leg is not decoration** — it has already caught a bug a green
> Linux run missed: a file handle opened in append mode on Windows lacks
> `FILE_WRITE_DATA`, so `set_len` on it fails with "Access is denied" while working
> fine on Linux. Anything touching file handles, locks or renames is exactly what
> that leg is for; read its conclusion, never infer it from the Linux one.

A cross-platform **TUI** for tracking personal documents — physical **and** digital — on
**Windows and Android (Termux)**. It replaces a Notion system with local, Syncthing-synced
Markdown files. Full design in **`DESIGN.md`** — **read it before writing feature code.**

Python 3.11+, mostly synchronous; the TUI layer (Textual) is async. Data is flat
Markdown + YAML files (one per document) plus a couple of TOML files; there is no database.

> **Picking up the Rust port?** Start at
> [`docs/dev/state-of-the-port.md`](docs/dev/state-of-the-port.md) — where the port
> stands, what the phone measurably *is*, what is settled versus open, and the
> traps already paid for. It is an index to the specs, not a replacement for them.
>
> **Working *on* dossier?** [`docs/dev/`](docs/dev/) is the "why is it like this" context —
> project constraints and performance decisions that must not be undone
> ([project-context.md](docs/dev/project-context.md)), how to verify CI honestly
> ([ci-gate.md](docs/dev/ci-gate.md)), and testing the TUI without flakes
> ([testing.md](docs/dev/testing.md)). Design each substantial phase with a **Fable
> advisor** first (Agent tool, `model:"fable"`, `subagent_type:"Plan"`, run in background),
> then build in independently shippable, CI-green slices.

> **Tooling is mirrored from the sibling project `destiny-director`** (same ruff/ty/pytest
> setup), minus everything Railway/Atlas/DB/Discord-specific, which does not apply here.
> The one Docker/Makefile piece we DO mirror is the **remote dev container**, which now
> lives in [`dev/`](dev/) and has since inherited most of its fixes from the further
> descendant `or3` — see [Remote dev container](#remote-dev-container) and
> [`dev/README.md`](dev/README.md). The root `Makefile` exists solely to forward into
> `dev/Makefile`; day-to-day work is still the `uv run` / `cargo` commands below, not make.

## Package management — use uv

- Use **uv** only. Never pip, poetry, or conda.
- Add runtime deps with `uv add <package>`; dev deps with `uv add --dev <package>`.
- `uv.lock` is committed — keep it in sync; never hand-edit the `pyproject.toml` dependency
  lists.
- The `dev` group (pytest, pytest-asyncio, ruff, ty, pre-commit, pytest-cov, rope) is in
  `tool.uv.default-groups`, so `uv run ruff` / `uv run ty` work out of the box.
- Don't create virtualenvs by hand or install packages globally.

## Running

- Prefix execution with `uv run` — e.g. `uv run ruff check dossier`. Don't invoke
  `python`/`pytest`/`ruff` bare.
- Launch the app: `uv run dossier` (or `uv run ds`, or `uv run python -m dossier`).

## Testing

- **pytest** with **pytest-asyncio** (`asyncio_mode = "strict"` — async tests must be
  marked; the Textual app is tested via `async with app.run_test()`).
- Tests live **inside each package** as `tests/` subdirs, e.g. `dossier/tests/test_*.py` —
  **not** a single root `tests/` dir. Follow that convention.
- Filesystem tests use pytest's `tmp_path`; never touch a real Syncthing folder or the
  user's `.dossier/` data.
- Run: `uv run python -m pytest` (add `--cov=dossier --cov-report=term-missing` for coverage).
- **TUI tests: never sleep-then-assert — poll for the effect.** `wait_for_complete()`
  returns before a worker has registered, so `trigger; pause(); assert` passes on
  scheduling luck and flakes only on CI's slow runner. Use `_settle(pilot, lambda: …)`.
  A real-terminal PTY driver lives in `tools/` for seeing the TUI as text + colours. Full
  guidance (plus the Textual `DEFAULT_CSS`/`SCOPED_CSS` screen-styling gotcha) in
  [`docs/dev/testing.md`](docs/dev/testing.md).

## Linting, formatting & type checking

- **ruff** does linting + formatting; **ty** is the type checker. Config for both is
  committed (`[tool.ruff]` in `pyproject.toml`, plus `ty.toml`), so it applies everywhere
  (`uv run`, CI, pre-commit).
- ruff: line length **88**, double quotes; isort `combine-as-imports` and
  `force-wrap-aliases` on; lint rule set `E`, `F`, `W`, `I`, `UP`, `B`, `SIM` (pycodestyle,
  pyflakes, isort, pyupgrade, bugbear, simplify).
- Commands: `uv run ruff check dossier` (lint), `uv run ruff format dossier` (format),
  `uv run ty check dossier` (types).
- ruff removes **unused imports** (F401 fails CI). When you add an import, add its usage in
  the **same edit**.
- ty: prefer fixing types over suppressing. When ty genuinely can't model a pattern,
  suppress it in a **`ty.toml` `[[overrides]]` block with an explanatory comment** — avoid
  bare inline `# type: ignore` (and if unavoidable, include the error code).

## CI — mirror it exactly, and read the conclusion

`.github/workflows/ci.yml` is a **Windows + Linux matrix** with `check` / `test` /
`driver` jobs. **Full details and the why in [`docs/dev/ci-gate.md`](docs/dev/ci-gate.md)
— read it before your first push.** The essentials, non-negotiable:

- **The local gate must mirror CI's environment or it lies.** Run, in order:
  ```bash
  uv sync                                             # no extras = CI's check job
  uv run --no-sync ruff check dossier
  uv run --no-sync ruff format --check dossier
  uv run --no-sync ty check --python-platform linux dossier   # linux/no-extras is authoritative
  uv sync --extra scan --extra dedup --group driver   # restore for the test jobs
  uv run --no-sync python -m pytest
  uv run --no-sync --group driver python -m pytest tools/test_terminal_integration.py
  ```
- **ty must run `--python-platform linux` with no extras** — a plain Windows-with-extras
  run misses Linux-only errors that fail CI.
- **The driver test is outside `testpaths`** — plain `pytest` never runs it. Run it
  explicitly on any TUI change.
- **Read the run *conclusion* per job — never infer it.** `gh run watch --exit-status`
  has exited 0 on a failed run; a trailing `; echo` masks the real code. After the watch,
  query it and `git fetch` to confirm the run is for your HEAD:
  ```bash
  gh run view <id> --json conclusion,jobs \
    --jq '{overall: .conclusion, jobs: [.jobs[] | {name, conclusion}]}'
  ```
- The root `Makefile` holds **only** forwarding aliases for the remote-dev-container
  targets in `dev/Makefile` (`make dev`, `dev-up`, `dev-status`, `dev-verify`, `dev-login`,
  `dev-shell`, `dev-down`, `dev-down-volumes`, …) — not part of lint/test/build.

## License headers

- Every `.py` file starts with the AGPL-3.0 header block (see any existing source file).
  Copy it verbatim into new modules.

## Git & workflow

- **Commit messages: conventional commits** — `type(scope): summary`. Types: `feat`, `fix`,
  `refactor`, `chore`, `docs`, `test`. Scopes track the module map below (`store`, `model`,
  `tui`, `migrate`, `export`, `doctor`, `cli`, `dev`).
- `origin` is `gsfernandes81/dossier` (**public** — the code is public; the store is not,
  and personal data stays gitignored as below).
- Name branches descriptively (`store-atomic-writes`, `fix/expiry-parse`), not by harness
  hash; rename before the first commit if needed (`git branch -m <name>`).

## Project layout & config

- All metadata in `pyproject.toml` (PEP 621); build backend **hatchling**. No `setup.py`,
  `setup.cfg`, or `requirements.txt`.
- Module map (see `DESIGN.md` §12): `model`, `config`, `store`, `query`,
  `platform_open`, `export`, `migrate`, `doctor`, `reconcile`, `suggest`, `succession`,
  `scan`, `dedup`/`dedup_hash`/`dedup_cache`, `reset`, `tui/`.

## Conventions

- Keep new code matching the surrounding style (naming, comment density, idioms).
- Don't introduce blocking I/O in the Textual async paths.
- Paths in the data model are POSIX and relative to the device's Syncthing root — see
  `DESIGN.md` §4/§6. Never store absolute or per-device paths in a document file.
- Personal data (real documents, `.dossier/` contents, per-device config) is **never**
  committed — it's gitignored; keep it that way.

## Remote dev container

For developing dossier remotely — on `zero` (Pi 5, 4 GB, arm64), driven over SSH from a
laptop or from Termux on the phone, and optionally from claude.ai/code or Zed-remote — the
repo ships a Docker dev environment in **[`dev/`](dev/)**. It descends from
`destiny-director`'s and has since inherited most of its fixes from the further descendant
`or3`, **stripped of everything DB/Railway/Atlas**: dossier has no database, no migrations
and no deploy target. **Read [`dev/README.md`](dev/README.md) before changing it** — it
carries the rationale for each mechanism, and every one of them was a bug somewhere first.

- **Files:** everything is under `dev/` — `Dockerfile`, `compose.yaml`, `Makefile`,
  `entrypoint.sh`, `login.sh`, `rc-supervisor.sh`, `status.sh`, `sshd_config`,
  `config.fish`, `screenrc`, `.env.example`, plus `README.md`. The root keeps the
  forwarding `Makefile` and the `.dockerignore` (the build context is the repo root, so
  that is the one that applies).
- **What is baked in:** the whole toolchain, for **both** languages. Rust via rustup
  (stable + `rustfmt` + `clippy` + the `aarch64-unknown-linux-musl` target) with **clang**
  and `llvm-ar` for `ring`, so all four commands of the Rust local gate run in here; uv +
  a pre-built venv at `/home/dev/venv` (`--all-extras --group driver`); Node 22 + Claude
  Code; `gh`; fish, `screen` and `abduco`. `make dev-verify` proves each piece landed.
- **What is outside the bind mount, and why:** `/home/dev/venv` (so the mount cannot
  shadow it) and `CARGO_TARGET_DIR=/home/dev/cargo-target` on a named volume (so host and
  container toolchains do not invalidate each other's artefacts, and an incremental
  `cargo test --release` survives a rebuild). Consequence: **the phone binary is at
  `/home/dev/cargo-target/aarch64-unknown-linux-musl/release/ds`**, not `target/`.
- **One-time host setup:** `cp dev/.env.example dev/.env` and set
  `DEV_SSH_AUTHORIZED_KEYS` to the host user's `.ssh/` **directory** (its
  `authorized_keys` gates the in-container sshd, so a key added there is live with no
  restart). `DEV_SSH_PORT` defaults to **2225** on the host side — 2222 is `dd-dev` and
  2224 is `or3-dev` on the same box; the container side is always 2222.
  `DEV_SSH_BIND` defaults to `127.0.0.1`, which is the whole of that port's protection —
  widen it deliberately, not by habit.
- **Bring up:** `make dev` (build + start + idempotent login walkthrough: git SSH → GitHub
  → Claude). `make dev-status` / `make dev-verify` for the readouts, `make dev-login` to
  re-run the logins, `make dev-down` to tear down (`dev-down-volumes CONFIRM=yes` also
  drops the logins, host key and caches).
- **Attach:** the front door is SSH — `ssh ds-dev`, and hold the work in abduco with
  `ssh -t ds-dev abduco -A claude claude`. A session outside abduco dies with the link
  that carried it, which on a phone means at the lock screen. From a terminal on the host,
  `make dev-shell` / `make dev-claude` reach the same sessions; `docker exec -it ds-dev
  fish` still works.
- **sshd is the container's foreground process**, so `docker logs ds-dev` is sshd's and
  the container's lifetime is the ssh endpoint's. **Claude Remote Control is opt-in and
  off by default** (`DS_REMOTE_CONTROL=1` in `dev/.env`, then `make dev-up` — the
  entrypoint reads it at start, so `make dev-restart` will not pick it up). It runs
  backgrounded with its own filtered log (`make dev-rc-log`) and cannot take sshd or the
  container down. This is the inverse of the earlier arrangement, where the supervisor was
  PID 1 and sshd ran under it. The entrypoint still pre-seeds Claude's headless-hostile
  first-run flags (`hasTrustDialogAccepted` for `/workspace`, `remoteDialogSeen`, `theme`,
  `hasCompletedOnboarding`) on every start, because an interactive `claude` over SSH meets
  them too.
- Container/image/volumes are prefixed `ds-` (the CLI name). There is **no MySQL/Atlas/
  Railway** service, and no data store is mounted — tests use `tmp_path`; real documents
  stay off the dev box.
