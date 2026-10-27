# dossier — Project Rules

> **⚠ Rewrite planned:** a v3 rewrite (Rust + Ratatui core, journal store, Python
> demoted to a desktop enrichment satellite) is specified in **[`REWRITE.md`](REWRITE.md)**.
> If your task is part of that rewrite, `REWRITE.md` is authoritative and overrides the
> Python-specific rules below for the Rust crates; the rules below still govern the
> Python code while it exists.

A cross-platform **TUI** for tracking personal documents — physical **and** digital — on
**Windows and Android (Termux)**. It replaces a Notion system with local, Syncthing-synced
Markdown files. Full design in **`DESIGN.md`** — **read it before writing feature code.**

Python 3.11+, mostly synchronous; the TUI layer (Textual) is async. Data is flat
Markdown + YAML files (one per document) plus a couple of TOML files; there is no database.

> **Working *on* dossier?** [`docs/dev/`](docs/dev/) is the "why is it like this" context —
> project constraints and performance decisions that must not be undone
> ([project-context.md](docs/dev/project-context.md)), how to verify CI honestly
> ([ci-gate.md](docs/dev/ci-gate.md)), and testing the TUI without flakes
> ([testing.md](docs/dev/testing.md)). Design each substantial phase with a **Fable
> advisor** first (Agent tool, `model:"fable"`, `subagent_type:"Plan"`, run in background),
> then build in independently shippable, CI-green slices.

> **Tooling is mirrored from the sibling project `destiny-director`** (same ruff/ty/pytest
> setup), minus everything Railway/Atlas/DB/Discord-specific, which does not apply here.
> The **remote dev container** is no longer run from this repo: `ds-dev` is a service in
> `infra`'s dev Compose project, built from this repo's `Dockerfile.dev` — see
> [Remote dev container](#remote-dev-container). There is no `Makefile`; day-to-day work
> is the `uv run` commands below.

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
- There is **no `Makefile`**: the gate is the `uv` commands above, nothing else. The dev
  container's lifecycle is infra's `make … C=ds-dev`, not part of lint/test/build.

## License headers

- Every `.py` file starts with the AGPL-3.0 header block (see any existing source file).
  Copy it verbatim into new modules.

## Git & workflow

- **Commit messages: conventional commits** — `type(scope): summary`. Types: `feat`, `fix`,
  `refactor`, `chore`, `docs`, `test`. Scopes track the module map below (`store`, `model`,
  `tui`, `migrate`, `export`, `doctor`, `cli`, `dev`).
- `origin` is `gsfernandes81/dossier` (private).
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

`ds-dev` runs on `zero` as one service in **infra's** dev Compose project
(`dev/compose.yaml`, service `ds-dev`, since 2026-10-27), behind infra's hub. How it is
started, stopped, logged into and reached — the hub, the GitHub token, the shared Claude
login, the targets — is
[infra's `dev/README.md`](https://github.com/gsfernandes81/infra/blob/main/dev/README.md)
(gsfernandes81/infra); don't copy it here. This section is what dossier's own dev files
still do: `Dockerfile.dev`, `docker-child-init.dev.sh`, `docker-login.dev.sh`,
`ssh_config.dev`, `.dockerignore`.

- **The image.** infra builds `Dockerfile.dev` from this repo's **`main` on GitHub**, `FROM
  gsrpi-dev-base` at the tag infra passes as `BASE_TAG` (the `ARG` default is only a fallback
  for a build outside infra). It adds a compiler, `uv`, and the venv at `/home/dev/venv`,
  pre-synced with `--all-extras --group driver`. So a change to these files reaches the
  container only once it is on `main` and infra rebuilds it (`make up S=ds-dev`).
- **`/workspace` is a volume holding a fresh https clone** of this repo, not a host clone —
  push to share work. No `.env` is linked in; dossier needs none. No data store is mounted:
  tests use `tmp_path`, and real documents stay off the dev box.
- **At every start** the base runs `docker-child-init.dev.sh` after its clone or pull:
  `uv sync --frozen --all-extras --group driver` adds the editable project to the venv.
  Keep those flags identical to `Dockerfile.dev`'s build sync — `uv sync` makes the
  environment match what it is asked for, so a start with fewer flags uninstalls the baked
  extras. A failed sync warns and the container still comes up.
- **GitHub is the hub's token** for this repo alone, at `/run/github/token`, used by git and
  `gh` — no `gh auth login`, no GitHub ssh key. `gh auth status` reports failure on it while
  repo commands work.
- **ssh identities**, if any, are on zero in `~/.ds-dev-secrets` (what was the clone's
  `.dev-ssh/`), mounted read-only at `/run/ds-secrets` (`DEV_SECRETS_DIR`): the base copies
  its `id_*` keys into `~/.ssh` and prepends its `ssh_config.fleet` to `ssh_config.dev`, the
  baked half of `~/.ssh/config`. Nothing in the container can write there.
- **`make login C=ds-dev`** (from `~/infra/dev` on zero) runs `docker-login.dev.sh`, baked
  at `/home/dev/login.sh`: it reports the token and the identities, logs Claude in (one
  login shared by the dev containers by default), and clones `/workspace` if start-up could
  not. Idempotent and non-fatal.
- **Getting in:** `ssh dev` lands on the hub's claude-sessions menu; pick ds-dev. It rests
  stopped — `make up S=ds-dev` starts it. No port is published and there is no per-container
  tunnel. sshd reads only the base's `sshd_config` (a child's drop-in is ignored, so there is
  none here) and sends every session through the base's `dev-door`. Zed, scp and
  port-forwards to a container have no route at present — an open gap in infra, not
  something to work around here.
- **The `dev` login shell is bash**, put back in `Dockerfile.dev`: `dev-door` runs a
  forwarded command as `$SHELL -c` and a login as `$SHELL -l`, `$SHELL` comes from
  `/etc/passwd`, and this repo's command lines assume POSIX. fish is still installed.
