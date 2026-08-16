# A demo journal

A synthetic v3 journal — 24 documents, five locations, a superseded pair, notes,
bundles, and three scan readings in `enrich/` so `ctrl+t` has something to find.
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

`Enter` on a row will report that the file is not on this device — the demo
lists file paths that nothing backs, and that message is exactly the one a
half-synced store gives. Everything else (search, `→` detail, `ctrl+t`,
`ctrl+x`, taps) works against it for real.
