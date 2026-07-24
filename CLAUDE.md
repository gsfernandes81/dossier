# dossier — Project Rules

A cross-platform **TUI** for tracking personal documents — physical **and** digital — on
**Windows and Android (Termux)**. It replaces a Notion system with local, Syncthing-synced
Markdown files. Full design in **`DESIGN.md`** — **read it before writing feature code.**

Python 3.11+, mostly synchronous; the TUI layer (Textual) is async. Data is flat
Markdown + YAML files (one per document) plus a couple of TOML files; there is no database.

> **Tooling is mirrored from the sibling project `destiny-director`** (same ruff/ty/pytest
> setup), minus everything Railway/Atlas/DB/Discord-specific, which does not apply here.
> The one Docker/Makefile piece we DO mirror is the **remote dev container**
> (`Dockerfile.dev`, `docker-compose.dev.yml`, `docker-*.dev.sh`, `sshd_config.dev`) —
> see [Remote dev container](#remote-dev-container). The `Makefile` exists solely to
> drive it; day-to-day work is still the `uv run` commands below, not make.

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

## CI

- `.github/workflows/ci.yml` runs on every push/PR: `ruff check` → `ruff format --check` →
  `ty check` → `pytest --cov`, all via `uv`. Run those four locally before pushing.
- The `Makefile` holds **only** the remote-dev-container targets (`make dev`, `dev-up`,
  `dev-login`, `dev-down`, `dev-down-volumes`). It is not part of lint/test/build — use the
  `uv run` commands above for those.

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

For developing dossier remotely (e.g. on a Pi/home server, driven from claude.ai/code, the
Claude mobile app, or Zed-remote), the repo ships a Docker dev environment mirrored from
`destiny-director` — **stripped of everything DB/Railway/Atlas**, since dossier has no
database and no deploy target. It bakes the toolchain (uv, git, gh, Node + Claude Code,
fish, the `driver` group for the TUI harness) into an image and **bind-mounts the clone**
at `/workspace`; the venv lives at `/home/dev/venv`, outside the mount.

- **Files:** `Dockerfile.dev`, `docker-compose.dev.yml`, `docker-entrypoint.dev.sh`,
  `docker-login.dev.sh`, `docker-rc-supervisor.dev.sh`, `sshd_config.dev`, `Makefile`,
  `.dockerignore`, `.env-example`.
- **One-time host setup:** `cp .env-example .env` and set `DEV_SSH_AUTHORIZED_KEYS` to the
  host user's `.ssh/` dir (its `authorized_keys` gates the in-container sshd on port 2222).
- **Bring up:** `make dev` (build + start + idempotent login walkthrough: git SSH → GitHub
  → Claude). Re-login later with `make dev-login`; tear down with `make dev-down` (add
  `-volumes` to also drop the persisted uv/claude/gh/ssh/history volumes).
- **Attach:** `docker exec -it ds-dev fish`, or over SSH: `ssh -t <host> 'docker exec -it
  ds-dev fish'`. Once Claude is logged in, the entrypoint's background supervisor brings up
  `claude remote-control --spawn worktree` on its own (~10s) — no manual step.
- Container/image/volumes are prefixed `ds-` (the CLI name). There is **no MySQL/Atlas/
  Railway** service, and no data store is mounted — tests use `tmp_path`; real documents
  stay off the dev box.
