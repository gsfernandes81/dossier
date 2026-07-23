# Recovering from a Syncthing conflict

dossier stores every document as a flat file synced by Syncthing. If **two devices edit the
same record before they sync** (e.g. you edit a doc on your laptop and your phone while one is
offline), Syncthing can't merge them — it keeps both, writing the losing side to a file named
like:

```
ENG-1-medical.sync-conflict-20260722-101500-ABCDEF7.md
```

This is normal and non-destructive: **no data is lost**, it's just parked in a second file.

## How dossier handles it for you

- **Conflict files are never loaded.** Anything with `.sync-conflict-` in its name is ignored
  by the store, so a conflict copy can't corrupt your data or show up as a phantom document.
- **`ds doctor` surfaces them; the TUI can merge them.** Run `ds doctor` and any conflict files
  appear under a `sync-conflict` group with a recovery reminder. Or open **Review › Conflicts** in
  the TUI, which previews and merges each conflict in place (the losing copy is archived first).
- **Prior versions are backed up on every save.** Before overwriting a document, dossier copies
  the previous version into a **local, non-synced** history directory
  (`<user-data-dir>/dossier/history/<doc-name>/<timestamp>.md`, last 10 kept). If you lose an
  edit, look there. dossier also refuses to overwrite a file that changed on disk since you
  loaded it (a *stale write*), prompting a reload first — so concurrent edits on one device
  can't clobber each other silently.

## Recovering a conflict, step by step

1. **Let syncing settle.** Make sure all devices are online and Syncthing shows "Up to Date"
   everywhere, so no new conflict copies appear mid-recovery.
2. **Find the conflicts.** Run:
   ```sh
   uv run ds doctor
   ```
   Each `sync-conflict` finding prints the full path to the conflict file.
3. **Compare the two files.** The conflict file sits next to the live one (same folder, same
   name minus the `.sync-conflict-…` suffix). Diff them:
   ```sh
   git diff --no-index <live-file>.md <live-file>.sync-conflict-*.md
   ```
   (any diff tool works — you're comparing two Markdown files with YAML frontmatter).
4. **Merge what you want to keep** into the **live** file (the one *without* the suffix). Usually
   only a field or two differ — a date, a location, a note. Edit the live file directly, or use
   the TUI detail pane; either way you're editing the canonical record.
5. **Delete the conflict copy** once you've salvaged anything worth keeping:
   ```sh
   rm <live-file>.sync-conflict-*.md
   ```
   dossier will save this deletion like any other change, and Syncthing propagates it. Deleting a
   `*.sync-conflict-*` file is always safe — the live record is untouched.
6. **Re-run `ds doctor`** to confirm the `sync-conflict` group is gone.

## Avoiding conflicts

- Let Syncthing finish syncing before editing the same document on a second device.
- On a device that's been offline a while (e.g. a phone off the network), open dossier only
  after Syncthing reports "Up to Date".
- Conflicts are rare in practice: most edits touch different documents, and dossier's
  deterministic serialization means re-saving an unchanged document is a no-op that produces no
  diff to conflict over.
