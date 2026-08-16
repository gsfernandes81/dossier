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

## Baseline results (R0.1 — measured 2026-08-16, Android/Termux)

| Device | Condition | Shell total | usable | imports+init | attention | load | paint |
|---|---|---|---|---|---|---|---|
| Phone | cold | 1.430s | 1053ms | 701ms | 20ms | 84ms | 249ms |
| Phone | warm | 1.005s | 693ms | 329ms | 35ms | 102ms | 227ms |
| Phone | warm | 0.941s | 660ms | 321ms | 24ms | 91ms | 224ms |
| Phone | warm | 0.945s | 657ms | 316ms | 27ms | 90ms | 224ms |
| Phone | warm | 0.999s | 669ms | 318ms | 28ms | 90ms | 233ms |
| Phone | warm | 0.976s | 689ms | 310ms | 30ms | 113ms | 237ms |
| Desktop (Windows) | — | *(not yet measured; optional)* | | | | | |

Store size at measurement: the phone store (≈948 docs per
`docs/dev/project-context.md`; unconfirmed at run time).

**Reading (v3 baseline):** phone cold = **1.43 s wall / 1053 ms usable**; warm
median ≈ **0.98 s wall / 670 ms usable** — 6.5–9.5× over the 150 ms ceiling.
The gap `shell − usable` (~300–380 ms) is CPython interpreter/site boot before
our first timestamp. Every bucket is structural to Python/Textual: interpreter
boot ~300 ms, framework imports ~320 ms warm (700 cold), first layout/paint
~230 ms. Even a hypothetically *free* store load leaves ~850 ms of
interpreter+framework overhead — the store is **not** the bottleneck at this
scale (load ≈ 95 ms warm; the libyaml + parallel-read work did its job). The
rewrite's premise is confirmed by measurement: only removing the
interpreter+framework layer can reach the budget.
