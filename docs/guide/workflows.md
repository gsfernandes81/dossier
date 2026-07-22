# Workflows

What each feature is *for* and when you'd reach for it. This page documents intent, not
keystrokes — press **`?`** in the TUI for the authoritative, always-current keybinding list
(it's generated from the app, so it can't drift from what the keys actually do).

## Finding and opening a document

Browse by physical location in the three-column home, or press `/` to search **root-wide** by
name, notes, tags, or bundle — the columns stay put and the documents pane filters in place.
`Enter` opens the detail pane (edit any field inline); `o` opens the actual soft copy with your
platform's native app. Tags are hierarchical: searching or filtering `id` also matches
`id/passport`, `id/driving-license`, and so on.

## Tracking where the physical copy lives

Every document has a **permanent** location (its home) and an optional **temporary** override
(where it is right now — e.g. carried in a folder for a trip). When a temp location is set it
wins wholesale, and the document sorts and displays under *that* location until you clear it.
This mirrors the old Notion "effective location" behavior — set the temp location when you take
something out, clear it when it's back home.

## The expiry watch

Documents with an expiry date are tracked automatically — it's **opt-out**, not opt-in, so a
renewed passport or medical is watched the moment it has a date, with no re-flagging. A tracked
document turns **red within ~9 months** of expiry. Two things quiet the noise:

- **Supersession** — when you file a renewal, mark it as superseding the old one (the `s`
  action). The old document is kept but dropped from every expiry calculation and from the watch,
  so a replaced cert never nags you.
- **`ignore_expiry`** — a per-document opt-out for residual dead documents (old CDCs, etc.) that
  have a date but no longer matter. Toggle it in the detail pane.

Filter the home to just the expiring set from the documents pane, or open the dedicated
expiry-watch surface for a soonest-first list with an "N tracked · M red" header.

## Reconciling files against records — `ds reconcile`

Over time the file tree and the document records drift apart. Reconcile (a TUI screen, or the
`ds reconcile` CLI) surfaces three kinds of mismatch, each with **metadata-only** actions — it
**never moves or deletes a real file**:

- **Orphan files** — a file on disk that no document links. Adopt it into a new document, link it
  to an existing one, or dismiss it (persisted so it stays quiet). An ignore-glob handles whole
  folders you never want to see (wallpapers, app data).
- **Missing renditions** — a document whose linked file no longer resolves. Acknowledge it, or
  unlink the dead rendition.
- **Duplicate clusters** *(needs the `dedup` extra; run with `--dedup`)* — the same document
  scanned more than once. dossier perceptual-hashes each page and folds subsets under their
  superset; you review and **fold** a cluster (keep one, mark the rest). Folding is always
  review-only, never automatic.

## Reading dates off a scan — `ds scan`

*(Desktop-only; needs the `scan` extra and a local vision endpoint — see
[install.md](install.md#optional-extras).)*

A scan rasterizes the first page of a linked document and asks a local vision model to read a
**grounded** structured record — issue/expiry dates are extracted **verbatim** (never the model's
own reformatting), at a low temperature for determinism. Readings persist to `.dossier/scans.toml`
(synced, so a desktop scan benefits the phone), and a size+mtime fingerprint skips unchanged
files. The dates don't get written to your documents — they arrive as **suggestions** you accept
or dismiss (next section), cross-checked against the authoritative expiry.

Run it two ways:

- **From the TUI** — press `v` to scan the highlighted document; the accepted reading's dates then
  appear as suggestions in its detail pane. A bulk scan of every linked document runs from the
  command palette and is cancellable mid-run.
- **From the CLI** — `ds scan` reads all new/changed linked files:
  ```sh
  uv run ds scan                    # scan everything not yet read (or changed)
  uv run ds scan --force            # re-read even unchanged files
  uv run ds scan --limit 20         # cap this run at N files
  uv run ds scan --list-models      # list the router's models (vision-capable flagged)
  uv run ds scan --model NAME       # override the configured model for this run
  ```

Configure the endpoint in the **Settings screen** (see below) or in your device config:
`scan_base_url` / `scan_model` point at an OpenAI-compatible `/v1/chat/completions` server (e.g. a
llama.cpp router), with `scan_temperature` and `scan_dpi` tunable.

### Succession from content

`ds scan` also feeds **content-based succession**: documents that share a credential (a document
number, or type + issuer + holder) are clustered into renewal chains, and the reconcile
"Succession" tab proposes linking each renewal to the one it replaces. Accept to set the
`supersedes` link (which then quiets the expiry watch, above), or dismiss.

## Accepting suggestions

Suggestions are dossier's rule for anything inferred rather than known: a date parsed from a
filename, a folder that looks like a bundle, a reading from `ds scan`. They are **never written
automatically**. In the detail pane, an inferred value shows as an accept/dismiss affordance —
**accept** pre-fills the field (you still save it), **dismiss** suppresses it forever. Ambiguous
dates (e.g. `21-08-23`, which could be several real dates) offer each reading as a pick. Dismissals
are pure suppressions recorded in `suggestions.toml` — they never touch a document, so a lost
dismissal (via a sync conflict) only resurfaces a suggestion, never changes your data.

## Bundles and export

A **bundle** is a set of documents gathered for a purpose — a visa or OCI application, a trip —
*without duplicating files*. Bundles have hierarchical slugs (`travel/india-2024`) and a date, and
the bundles surface groups them by category, sorted chronologically. dossier can also **suggest**
bundles from folders that look like gathered sets (accept/dismiss like any suggestion).

When you actually need the files in one place — to upload or hand over — `ds export` materializes
a bundle:

```sh
uv run ds export travel/india-2024 --to ./india-application     # copy the member files
uv run ds export travel/india-2024 --to ./out --symlink          # symlink instead (Win: Dev Mode)
uv run ds export travel/india-2024 --to ./out --dry-run          # preview the plan
```

Files are named by document id, with problem flags for anything missing or already present;
`--force` overwrites. The original tree is never touched.

## Settings

Press `,` in the TUI for the **Settings screen** (also reachable from the command palette) — no
config file editing required:

- **This device** (per-device config): the icon set (Nerd Font vs ASCII — takes effect on
  restart), and the scan endpoint base URL, model, temperature, and DPI. The model field is a
  dropdown populated from the router's advertised models.
- **Synced** (shared across devices): the expiry threshold in days (how far ahead a document
  turns red).

Changes apply on the next home reload, except the icon set (restart). `ctrl+s` saves, `Esc`
cancels.

## Keeping the store healthy

- **`ds doctor`** — run after big imports or merges: it reports Syncthing conflicts, dangling
  location/supersession references, files that would change on next save, missing renditions, and
  ambiguous dates. Conflict findings include a recovery hint pointing at
  [sync-conflicts.md](sync-conflicts.md).
- **`ds reset`** — start a folder's `.dossier/` data over (never the real files), or
  `--global` to un-configure just this device. Both confirm first and back up before acting.
