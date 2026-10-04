# A demo journal

A synthetic v3 journal — 24 documents filed in a tree of physical locations, a
superseded pair, notes, bundles, and three scan readings in `enrich/` so
"search scan text" in the filter list (`Space` `f`) has something to find.
Entirely made up; **no personal data is here and none ever goes here** (real
documents and `.dossier/` contents are gitignored and stay that way).

It exists so `ds` can be run on a device that has no store yet — in particular
the phone, before the real v2 store has been exported (Phase R7). It is also the
fixture behind the screenshots in [`spike-r02.md`](../spike-r02.md)'s successor
notes and a quick way to see the Find surface without building anything.

```sh
ds --journal docs/dev/demo            # the TUI
ds --journal docs/dev/demo status     # the report
DS_TIMING=exit ds --journal docs/dev/demo   # the startup number
```

Note the shape: `--journal` points at the directory that **contains**
`meta/` and `enrich/`, not at either of them. Pointing `--root` at a Syncthing
root instead would look for `<root>/.dossier/journal/`.

Opening a file reports that it is not on this device — the demo lists file
paths that nothing backs, and that message is exactly the one a half-synced
store gives. Everything else works against it for real: search, `Enter` for a
document's Details view, the filter list's "expiring only" and "search scan
text" (`Space` `f`), bundles, and taps.
