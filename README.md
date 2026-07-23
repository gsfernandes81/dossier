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

## License
[AGPL-3.0-or-later](LICENSE). © 2026–present gsfernandes81.
