# dossier

A cross-platform TUI for tracking personal documents — physical **and** digital — on Windows
and Android (Termux). It replaces a Notion-based system with local, Syncthing-synced Markdown
files: find documents by name/tags, track where the physical copy lives, open the synced soft
copy with the platform's native opener, and track issue/expiry dates.

## Highlights
- Flat Markdown + YAML files (one per document) — Syncthing-safe, greppable, no database.
- A three-column (Miller) TUI: browse by location, scan documents, edit every field inline in
  the detail pane; root-wide search filters in place.
- Permanent/temporary location tracking with an effective-location override.
- Hierarchical **tags** (what a doc is) and **bundles** (what it's gathered for — export a
  bundle to a folder for a visa/OCI application or a trip, no file duplication).
- Issue/expiry dates with an **expiring-soon watch** (opt-out; superseded docs drop off).
- **Review** screen for tidying the collection — sync-conflict merges, orphan files, missing
  renditions, perceptual-hash **duplicate** clusters, successions, and integrity checks, gathered
  into one place (the `ds reconcile`/`ds resolve`/`ds doctor` CLIs cover the same ground).
- **`ds scan`** reads issue/expiry dates off scanned PDFs with a local vision model, surfacing
  them as accept/dismiss suggestions; content-based **succession** links renewal chains.
- `dossier` / `ds` CLI + TUI; opens files via `os.startfile` (Windows) and `termux-open`
  (Termux).

## Quickstart
```sh
uv sync                          # install (see Development for the [dedup]/scan extras)
uv run ds init                   # point this device at its Syncthing root
uv run ds migrate --help         # one-time Notion → local cutover (dry-run + review first)
uv run ds                        # launch the TUI (press ? in-app for the keybindings)
```
Other commands: `ds import <folder>` / `ds intake` (file unfiled documents), `ds organize`
(canonical renames), `ds expiring` (what needs renewing), `ds doctor` (integrity +
sync-conflict checks), `ds reconcile` (orphans & duplicates), `ds export <bundle> <dest>`,
`ds scan`, `ds ask`, `ds reset`, `ds profile` (time startup + data-load to find perf
bottlenecks). See [docs/guide/](docs/guide/) for install, first-run, and workflow
walkthroughs.

## Documentation map
- **README.md** (this file) — start here: what it is, quickstart, where everything lives.
- **[docs/guide/](docs/guide/)** — how to *use* dossier: install, getting started, workflows.
- **[DESIGN.md](DESIGN.md)** — the authoritative spec & rationale (as-built design record).
- **[ROADMAP.md](ROADMAP.md)** — source of truth for what has shipped and what's next.
- **[CLAUDE.md](CLAUDE.md)** — contributor rules (tooling, tests, lint, conventions).

## Background scan & phone intake (desktop service)
On a desktop, an opt-in background service reads new scans with the local vision model so the
catalogue stays current without running `ds scan` by hand. It is **plugged-in and idle only** —
never on battery or in a power-saver mode — and skips cleanly (exit 0) when gated.

```sh
ds service run              # run one pass now (power-gated, single-instance locked)
ds service install          # PRINT how it would install — writes/registers nothing
ds service install --yes    # actually register the Scheduled Task / systemd timer
ds service status           # live power decision + artifact/registration state
ds service uninstall        # remove it (prints the plan; --yes to do it)
```

`install` generates a Windows Scheduled Task (`DisallowStartIfOnBatteries` + `RunOnlyIfIdle`) or
a systemd **user** timer (`ConditionACPower=true`, hourly) pointing at `ds service run`. Without
`--yes` it only prints the plan and the exact commands it would run — it registers nothing.

**Phone intake — no VLM on the phone.** Drop a photo into the synced intake inbox (`[intake]
inbox` in `.dossier/config.toml`) from your phone. The desktop service picks it up, reads it, and
writes the reading into the synced `intake.toml` / `scans.toml` — so you file it from the review
card on *either* device, with no model ever running on Android. Set `[service] intake = "file"`
on the desktop for a hands-free loop. To route Android's share sheet into the synced inbox, put
this at `~/bin/termux-url-opener` (needs `termux-setup-storage`):

```sh
#!/data/data/com.termux/files/usr/bin/sh
cp "$1" "$HOME/storage/shared/<your-synced-root>/Inbox/"
```

### Termux smoke checklist (before tagging a release)
Desktop Termux CI isn't worth its flakiness, so verify on-device:
1. `pkg install python termux-api` **and** install the Termux:API app (same source as Termux).
2. `ds init`, then `ds doctor` — expect all clear.
3. Launch `ds`: the touch UI renders and the on-screen keyboard toggle works.
4. `o` on a document opens its PDF via `termux-open`.
5. Drop a photo in the synced inbox; after the desktop service runs and syncs, the record and its
   reading are visible on the phone.

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

The dedup and vision-scan features have optional dependency groups — see
[docs/guide/install.md](docs/guide/install.md).

## License
[AGPL-3.0-or-later](LICENSE). © 2026–present gsfernandes81.
