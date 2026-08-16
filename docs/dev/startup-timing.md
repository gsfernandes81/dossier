<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# Startup timing — the R0.1 baseline protocol

The v3 rewrite ([`REWRITE.md`](../../REWRITE.md)) budgets **time-to-usable**:
< 100 ms on the phone (150 ms acceptable). Phase R0.1 records what the *current*
Python app costs, so the rewrite has a number to beat and the win is provable.
This file is the measurement protocol; run it on the **real phone** (and the
desktop) and record the results in the table below.

## The probe

Setting the `DS_TIMING` environment variable makes the TUI print one breakdown
line to stderr, timed at the first frame that shows the loaded document rows —
the "usable" moment:

```
ds-timing: usable 2314ms (imports+init 1656ms · attention 214ms · load 380ms · paint 64ms)
```

- **imports+init** — from the first line of the `dossier` package to the home
  screen's `on_mount` (interpreter + Textual + our imports + config/store setup).
- **attention** — the pre-render conflict/inbox directory walks.
- **load** — `load_all` + first population of the documents pane.
- **paint** — layout + the first repaint that shows the rows.

`DS_TIMING=exit` additionally quits right after printing, so a shell `time`
wrapper measures the **full** span including interpreter/site boot that no
in-process probe can see. `DS_TIMING=1` prints the same line but keeps the app
running (it also mirrors it as a notification).

## Protocol

Three runs per condition; record every run, not just the best.

**Termux (the number that matters):**

```sh
time DS_TIMING=exit ds
```

- *Cold*: freshly opened Termux session, first launch (fully cold = swipe
  Termux away from recents first; note which you did).
- *Warm*: repeat immediately, same session.

**Windows (PowerShell):**

```powershell
$env:DS_TIMING = "exit"; Measure-Command { ds | Out-Default }
```

Read the results as: shell total ≈ interpreter+site boot + the `ds-timing`
total (+ a few ms of teardown). The gap between shell total and the probe's
total is the part only a native binary can remove.

## Baseline results (fill in — R0.1)

| Device | Condition | Shell total | ds-timing line |
|---|---|---|---|
| Phone (Termux) | cold ×3 | | |
| Phone (Termux) | warm ×3 | | |
| Desktop (Windows) | cold ×3 | | |
| Desktop (Windows) | warm ×3 | | |

Store size at measurement: ____ docs. Date: ____.

Once filled, copy the phone-cold median into `REWRITE.md` §Phase R0 as the
baseline the rewrite must embarrass.
