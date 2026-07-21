# dossier

A cross-platform TUI for tracking personal documents — physical **and** digital — on Windows
and Android (Termux). It replaces a Notion-based system with local, Syncthing-synced Markdown
files: find documents by name/tags, track where the physical copy lives, open the synced soft
copy with the platform's native opener, and track issue/expiry dates.

**Status:** pre-implementation. The full specification is in [DESIGN.md](DESIGN.md); working
conventions are in [CLAUDE.md](CLAUDE.md).

## Highlights
- Flat Markdown + YAML files (one per document) — Syncthing-safe, greppable, no database.
- Permanent/temporary location tracking with an effective-location override.
- Hierarchical **tags** (what a doc is) and **bundles** (what it's gathered for — export a
  bundle to a folder for a visa/OCI application or a trip, no file duplication).
- Issue/expiry dates with an expiring-soon view.
- `dossier` / `ds` CLI + TUI; opens files via `os.startfile` (Windows) and `termux-open`
  (Termux).

## Development
Tooling mirrors the sibling project *destiny-director*: [uv](https://docs.astral.sh/uv/) for
packaging, [ruff](https://docs.astral.sh/ruff/) for lint + format, [ty](https://github.com/astral-sh/ty)
for type-checking, and pytest with tests co-located in each package's `tests/` dir.

```sh
uv sync                     # install deps (incl. dev group)
uv run pre-commit install   # enable pre-commit hooks
uv run ruff check dossier   # lint
uv run ruff format dossier  # format
uv run ty check dossier     # type-check
uv run python -m pytest     # test
```

## License
[AGPL-3.0-or-later](LICENSE). © 2026–present gsfernandes81.
