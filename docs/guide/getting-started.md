# Getting started

This walks you from a fresh install to a populated store. It assumes you've
[installed dossier](install.md) and set up Syncthing on this device.

## 1. Configure this device — `ds init`

```sh
uv run ds init --root /path/to/your/synced/folder
```

`init` writes a small **per-device** config (recording just this machine's Syncthing root) and
creates the `.dossier/` layout under that root if it doesn't exist. Run it once per device;
each device can have the root in a different place — nothing device-specific is ever synced.

- `--root PATH` — the Syncthing folder that holds (or will hold) your documents. Omit it and
  `init` prompts (when run in an interactive terminal).
- `--force` — re-point an already-configured device at a different root.

`init` prints where your per-device config and data live, and reminds you about the Nerd Font /
`glyphs = "ascii"` option.

## 2. Import your documents — `ds migrate`

If you're coming from the Notion system, migrate is a **one-time cutover** (there is no ongoing
Notion sync — local files become the source of truth). Export your Notion databases to JSON,
then:

```sh
uv run ds migrate --notion-export path/to/export.json            # dry-run: a review report
uv run ds migrate --notion-export path/to/export.json --apply    # actually write the docs
```

- Without `--apply` it only prints a **review report** — read it first.
- `--verbose` lists every issue rather than per-category counts.
- Expiry dates come from the structured *Marine Documents* table, not from filenames; dates
  trapped in names become **suggestions** you accept later (see
  [workflows.md](workflows.md#accepting-suggestions)), never silent writes.

Starting fresh instead of migrating? Add documents directly in the TUI (`n` for new).

## 3. Launch the TUI

```sh
uv run ds
```

The home screen is three columns (locations → documents → detail). Press **`?`** at any time for
the full, always-current keybinding list (it's generated from the app itself). The essentials:
`Enter` opens the detail pane, `o` opens a document's file, `/` searches, `e` edits, `n` makes a
new document, and `,` opens **Settings** (icons, scan endpoint/model, expiry threshold — no
config-file editing needed). See [workflows.md](workflows.md) for what to do next.

## What `ds init` created — the `.dossier/` layout

Everything dossier owns lives in `<root>/.dossier/`, all flat text, all synced:

```
.dossier/
├─ config.toml            # synced shared settings
├─ documents/*.md         # one Markdown file per document (YAML frontmatter + notes body)
├─ locations.toml         # physical storage locations
├─ bundles.toml           # bundles (application/trip sets)
├─ reconcile.toml         # your reconcile decisions (dismiss/link/fold/…)
├─ suggestions.toml       # dismissed suggestions (suppressions only; never writes a doc)
└─ scans.toml             # ds scan readings, keyed by document id
```

Two things live **outside** `.dossier/` and are deliberately **not** synced, one per device:

- a **history** dir of pre-save document backups (last 10 per doc) — your undo safety net;
- the **dedup page-hash cache** (disposable, rebuildable).

Everything is greppable and diff-friendly. A stray `*.sync-conflict-*` file is never loaded —
if Syncthing ever makes one, [sync-conflicts.md](sync-conflicts.md) covers recovery.

## Housekeeping commands

- `ds doctor` — integrity + durability checks (conflicts, dangling references, ambiguous dates,
  missing files). Run it after a big import.
- `ds reset` — clear a folder's `.dossier/` data (never the real files) to re-import from clean;
  `ds reset --global` un-configures just this device. Both confirm first and back up before acting.
