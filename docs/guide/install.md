# Installing dossier

dossier is a Python app (3.11+) with no *required* system libraries. It runs on **Windows** and
**Android via [Termux](https://termux.dev/)**, and uses [uv](https://docs.astral.sh/uv/) for
packaging. One optional native library — **libyaml** — makes YAML parsing ~10× faster; it's
bundled automatically in the desktop wheels and is a one-line install on Termux (see below).
Without it everything still works via a pure-Python fallback, just slower on a large store.

## Core install (both platforms)

```sh
git clone <your-remote>/dossier && cd dossier
uv sync            # creates .venv and installs dossier + its deps
uv run ds --help   # verify
```

`uv sync` installs the base app (browse, edit, migrate, reconcile without dedup, export,
doctor, reset). The two vision-adjacent features need optional extras — see below.

## Optional extras

| Extra | Enables | Install |
|-------|---------|---------|
| `dedup` | perceptual-hash duplicate detection in `ds reconcile --dedup` | `uv sync --extra dedup` |
| `scan` | `ds scan` — read dates off a scan with a local vision model | `uv sync --extra scan` |

Both pull in `pypdfium2` + `pillow` (pure wheels, no system libraries) to rasterize PDF pages.
They are **desktop-only** — the rasterization stack and (for `scan`) an 8B vision model aren't
practical on a phone; run these on your Windows machine and let the results sync to the phone.

`ds scan` additionally needs a local **OpenAI-compatible vision endpoint** (e.g. a llama.cpp
router serving `/v1/chat/completions`). Set the base URL and model in the in-app **Settings**
screen (`,`) or your device config, and discover available models with `ds scan --list-models`.
See [workflows.md](workflows.md#reading-dates-off-a-scan-ds-scan).

## Windows notes

- **Nerd Font icons.** The TUI uses Nerd Font glyphs by default. Install a
  [Nerd Font](https://www.nerdfonts.com/) and select it in your terminal, or the icons show as
  boxes. To skip them, set `glyphs = "ascii"` in your per-device config (path printed by
  `ds init`; `ds doctor` also reminds you).
- Files open with `os.startfile` (the system default handler), so PDFs/images just open.

## Termux (Android) notes

- Install Termux from **F-Droid** (the Play Store build is outdated).
- `pkg install python git libyaml`, install `uv`, then `uv sync` as above. Installing
  **libyaml first** matters: PyYAML compiles its fast C loader against it at install time, so
  the order gives you the ~10× YAML speedup from the first launch. If you already installed
  without it, run `pkg install libyaml` then rebuild PyYAML (`uv sync --reinstall-package
  pyyaml`, or reinstall dossier). Confirm the fast path with `ds profile` (the `yaml backend`
  line, or run it any time — dossier also shows a one-line nudge on startup until it's active).
- Add this to `~/.termux/termux.properties` so the soft keyboard doesn't pop up on launch:
  ```properties
  hide-soft-keyboard-on-startup=true
  ```
  Tap the on-screen ⌨ control in the TUI to raise the keyboard when you need to type.
- Files open with `termux-open` (install `termux-api` + the Termux:API app).
- The touch UI is auto-detected under Termux; force it anywhere with `ds --mobile`
  (or `ds --desktop` to force the desktop layout — handy when driving the touch UI from a
  desktop terminal for testing).

## Syncthing

dossier keeps no server and no cloud — all cross-device sync is delegated to
[Syncthing](https://syncthing.net/). Point Syncthing at the folder that holds (or will hold)
your documents, install it on each device, and share that folder between them. dossier stores
its data in a `.dossier/` subfolder of that root; see [getting-started.md](getting-started.md).

## Next

Head to [getting-started.md](getting-started.md) to configure a device and do your first import.
