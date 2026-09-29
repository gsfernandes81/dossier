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
> The one Docker/Makefile piece we DO mirror is the **remote dev container**
> (`Dockerfile.dev`, `docker-compose.dev.yml`, `docker-*.dev.sh`, `ssh_config.dev`,
> `sshd_config.dev.d/`) — both repos are now thin children of the same base image —
> see [Remote dev container](#remote-dev-container), driven by `Makefile.dev`
> (`make -f Makefile.dev dev`). The root `Makefile` holds only thin cargo wrappers for
> the Rust workspace (`make build`, `phone`, `rust-gate`, …); Python work is still the
> `uv run` commands, not make.

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
- The root `Makefile` holds only cargo wrappers for the Rust workspace (`build`,
  `phone`, `fmt`, `fmt-check`, `clippy`, `rust-test`, `rust-gate`, `run`, `clean`,
  `install` — `cargo install` into rustup's `~/.cargo/bin`, or `$PREFIX/bin` on Termux) —
  `make rust-gate` runs the Rust local gate above, minus the spike. It has no Python
  targets; the Python gate is the `uv run` commands above. The remote-dev-container
  targets (`dev`, `dev-up`, `dev-login`, `dev-down`, `dev-down-volumes`) live in
  `Makefile.dev` and run as `make -f Makefile.dev <target>`.

## Comments

Comment only where it adds something the code cannot say. Applies to Rust and Python.

- **Default to no comment.** A comment earns its place by carrying a *why*: a
  non-obvious constraint, a deliberate deviation, a gotcha, a workaround, a measured
  fact (e.g. "append-mode handles lack `FILE_WRITE_DATA` on Windows, so `set_len`
  fails there").
- **Never:** narrate what the next line does; restate a name, type or signature; mark
  block ends; narrate the change ("fixed", "now", "as requested", "the user asked");
  cite spec sections, decision IDs (`§4.1`, `D11`, `U2`) or mockups. Design rationale
  and history live in `REWRITE.md`, `REWRITE-UI.md` and `docs/dev/` — link a doc at
  most once, in a module header, never per item.
- **Rust doc comments** follow the Rust API Guidelines: `///` opens with a one-line
  summary, third person ("Returns …"), ~15 words max. Add more only for a contract the
  signature doesn't show — `# Errors`, `# Panics`, an invariant, a unit. A private
  item gets `///` only when its name and signature don't already say it. `//!` is for
  a module's purpose in 1–5 lines, not its design history.
- **`// rust:` notes are the exception that stays**, because the codebase doubles as
  the user's Rust learning material (REWRITE.md §4.6): one short note at the *first*
  use of a surprising idiom in each module, explaining the chosen pattern to a Python
  developer.
- **Editing code with over-long comments:** trim or delete the ones you touch, as part
  of the same change. Deleting a comment that restates the code is always in scope.

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

- Keep new code matching the surrounding style (naming, idioms) — but **not** its comment
  density: follow [Comments](#comments) above, even where older code doesn't yet.
- Don't introduce blocking I/O in the Textual async paths.
- Paths in the data model are POSIX and relative to the device's Syncthing root — see
  `DESIGN.md` §4/§6. Never store absolute or per-device paths in a document file.
- Personal data (real documents, `.dossier/` contents, per-device config) is **never**
  committed — it's gitignored; keep it that way.

## Remote dev container

For developing dossier remotely (e.g. on a Pi/home server, driven from claude.ai/code, the
Claude mobile app, or Zed-remote), the repo ships a Docker dev environment. Since
**2026-08-24** `Dockerfile.dev` no longer builds one — it is a **thin child of
`gsrpi-dev-base`**, the shared image the four dev containers on that Pi run
(`infra-dev`, `or3-dev`, `dd-dev`, this): python 3.13-slim, uv's siblings git and `gh`,
Node + Claude Code, fish, screen, abduco, the ssh client and server, the `dev` user, the
dotfiles and the entrypoint all come from there, pulled from
`ghcr.io/gsfernandes81/gsrpi-dev-base` at the tag pinned in `ARG BASE_TAG`. This repo
adds a compiler, `uv`, its own venv (`--all-extras --group driver`), and the Rust
toolchain (see the Rust bullet below). The clone is
**bind-mounted** at `/workspace`; the venv lives at `/home/dev/venv`, outside the mount.
The base's source is `dev/Dockerfile.base` in the `infra` repo, and nothing here needs
that repo checked out — the `FROM` pulls.

- **Files:** `Dockerfile.dev`, `docker-compose.dev.yml`, `docker-child-init.dev.sh`,
  `docker-login.dev.sh`, `ssh_config.dev`,
  `sshd_config.dev.d/`, `Makefile.dev`, `.dockerignore`, `.env-example`.
- **Rust is baked in, so the whole Rust local gate runs here:** rustup stable +
  `rustfmt` + `clippy` + the `aarch64-unknown-linux-musl` target (the set CI's `rust`
  workflow installs), with **clang** and `llvm-ar` for `ring`'s cross-build.
  `CARGO_TARGET_DIR=/home/dev/cargo-target` sits outside the bind mount on a named
  volume, so host and container toolchains never invalidate each other's artefacts and
  an incremental `cargo test --release` survives a rebuild. Consequence: **the phone
  binary is at `/home/dev/cargo-target/aarch64-unknown-linux-musl/release/ds`**, not
  `target/`. The cargo registry and git caches are volumes too; `~/.cargo` as a whole is
  not, because it also holds the toolchain a rebuild installs. A memory cap
  (`DEV_MEM_LIMIT`, default 3 GB) confines an OOM from a release build to this container.
- **What this image does at start:** the base's entrypoint pulls the clone, then runs
  `docker-child-init.dev.sh` — the `.dev-ssh` git identities, and `uv sync --frozen
  --all-extras --group driver` to add the editable project to the pre-built venv. Keep
  those flags identical to `Dockerfile.dev`'s build sync: `uv sync` makes the environment
  match what it is asked for, so a start with fewer flags uninstalls the baked extras. A
  failed sync warns and the container still comes up.
- **One-time host setup:** `cp .env-example .env` and set `DEV_SSH_AUTHORIZED_KEYS` to the
  host user's `.ssh/` dir (its `authorized_keys` gates the in-container sshd). Git
  identities go in the gitignored `.dev-ssh/`, with the ssh config named
  **`ssh_config.fleet`** — the base prepends that file to the baked defaults at every
  start, before it pulls the clone, which is why it is not called `config` and why
  nothing symlinks it. A clone still holding the old `.dev-ssh/config` keeps working:
  `docker-child-init.dev.sh` copies it into place each start and says to rename it.
- **The uid matters and is now checked.** The `dev` account is built in the BASE (uid
  1001 in the published image), not from this clone's owner as it was before. `make -f
  Makefile.dev dev-up` runs `dev-check-uid`, which compares the container's `id -u` against the clone
  owner and prints the fix — `cd ~/infra/dev && make base`, which builds the base at the
  right uid under the same name. A mismatch otherwise shows up as an unwritable
  `/workspace` and sshd refusing every login as "bad ownership or modes". Optionally set
  `DEV_SSH_PORT` to change the **host-side** port mapped to the container's sshd (defaults to
  `2222`; the container side stays `2222`) — bump it when `2222` is taken or you run more than
  one dev container, then point Zed / SSH / the Cloudflare tunnel at the port you chose.
- **Bring up:** `make -f Makefile.dev dev` (build + start + idempotent login
  walkthrough: git SSH → GitHub → Claude). Re-login later with `make -f Makefile.dev
  dev-login`; tear down with `make -f Makefile.dev dev-down` (add `-volumes` to also drop
  the persisted uv/claude/gh/ssh/history volumes). Every dev target lives in
  `Makefile.dev`, not the root `Makefile`.
- **Attach:** `docker exec -it ds-dev fish`, or over SSH: `ssh -t <host> 'docker exec -it
  ds-dev fish'`. **There is no Remote Control here as of 2026-08-25** — the supervisor is
  deleted, not defaulted off, and every dev container on this host is reached the same
  way: ssh in, then `abduco -A claude claude`, which holds the session across a dropped
  link. The base still pre-seeds Claude's workspace-trust flag for `/workspace` in
  `~/.claude.json` so a fresh volume does not meet a dialog nobody can answer.
- **An idle claude is offloaded after 90 minutes and left resumable.** The base runs
  `offload-idle-claude.sh`: a session detached, silent and running nothing for longer than
  a claude can schedule its own wake-up (the runtime clamps that to an hour) is stopped,
  and `~/.local/share/claude-offload.log` holds the `claude --resume` that brings it back.
  It never touches an attached session, one with work running under it, or one with no
  transcript. One idle session's process tree measures over a gigabyte.
- **sshd is the foreground process**, and it is the only long-lived one: the container's
  lifetime is the door's. `docker logs ds-dev` shows sshd and the start-up lines.
- **Two sshd defaults the base changed are put back** in `sshd_config.dev.d/`:
  `AuthorizedKeysFile` (the host account's, as always here) and `AllowTcpForwarding yes`
  (the OpenSSH default this container's old config left in place, which the base turns
  off). The `dev` account's login shell is likewise put back to **bash** in
  `Dockerfile.dev` — sshd runs it from `/etc/passwd`, so it is what `ssh <host> '<cmd>'`
  and Zed's remote bootstrap execute under, and this repo's tooling assumes POSIX there.
  `docker exec -it ds-dev fish` is unchanged.
- Container/image/volumes are prefixed `ds-` (the CLI name). There is **no MySQL/Atlas/
  Railway** service, and no data store is mounted — tests use `tmp_path`; real documents
  stay off the dev box.
