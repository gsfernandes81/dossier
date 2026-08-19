"""Six directions for the Find surface's bottom chrome, drawn at 45 columns.

Every specimen uses `grid`, so the tiling is the app's own (`layout::cells`) and
an over-wide line raises rather than wrapping. Where a direction invents a new
tiling, the arithmetic is written next to it — the last review turned on exactly
that, and a sketch that does not add up is a sketch that cannot ship.
"""

from __future__ import annotations

from grid import (
    DESK,
    FLOOR,
    GUTTER,
    PHONE,
    bar,
    block,
    centre,
    cells,
    render,
    rpad,
    width,
)

QUERY = "coc"
COUNT = "3/24"

# Two documents of context above every specimen, identical throughout.
LIST = [
    "  COC Certificate (Master)           «soon|~ 09-26» ",
    "«dim|    cert-file 8 · marine»",
    "  COC Certificate 2019                  ·    ",
    "«dim|    cert-file 9 · marine»",
]

SELECTED = [
    "«sel|▸ COC Certificate (Master)           ! 09-26 »",
    "«seldim|    cert-file 8 · marine                     »",
    "  COC Certificate 2019                  ·    ",
    "«dim|    cert-file 9 · marine»",
]


def field(cols: int = PHONE, query: str = QUERY, tail: str = "«chip| ⌨ »") -> str:
    """`" >"` + the underlined span + a tail, on the shared gutters."""
    prompt = "«acc| >»"
    span = cols - width(prompt) - width(tail) - GUTTER
    return prompt + f"«uline|{rpad(f' {query}█', span)}»" + tail + " " * GUTTER


def info(right: str, count: str = COUNT, cols: int = PHONE) -> str:
    left = f" {count}"
    gap = cols - width(left) - width(right) - GUTTER
    return left + " " * gap + right + " " * GUTTER


def chip(label: str, active: bool = False) -> str:
    """A pressable chip that carries its own state.

    The dot's column is reserved whether or not it is lit, so a chip never
    changes width when it is toggled — a row that reflows on a tap is a row
    whose next tap lands somewhere else.
    """
    return f"«chip| {label}{'•' if active else ' '} »"


SPECIMENS: dict[str, str] = {}

# ── A · repair in place ─────────────────────────────────────────────────────
SPECIMENS["a"] = block(
    LIST
    + [
        bar(["→ Detail", "^x Expiring•", "^t Scans"]),
        field(),
        info("«dim|tap again opens  ⏎»"),
    ]
)

# ── B · earn your cell ──────────────────────────────────────────────────────
HEADER_CHIP = "«chip| ! 3 expiring »"
SPECIMENS["b"] = block(
    [" «hd|dossier»" + " " * (PHONE - 8 - width(HEADER_CHIP) - GUTTER) + HEADER_CHIP + " "]
    + LIST[:2]
    + [
        bar(["→ Detail", "^t Scans•"]),
        field(),
        info("«dim|tap again opens  ⏎»"),
    ]
)

# ── C · chips are the buttons ───────────────────────────────────────────────
def chip_row(count: str = COUNT, expiring: bool = False, scans: bool = True) -> str:
    row = f" {count}  " + chip("→ Detail") + chip("^x Expiring", expiring) + chip("^t Scans", scans)
    return row


SPECIMENS["c"] = block(LIST + ["", field(), chip_row()])

SPECIMENS["c-message"] = block(
    LIST + ["«acc| no file linked — showing the record»", field(), chip_row(expiring=True)]
)

# ── D · the dock ────────────────────────────────────────────────────────────
FIELD_COLS, SLAB = 21, 11


def dock_top() -> str:
    prompt = "«acc| >»"
    span = FIELD_COLS - width(prompt)
    return (
        prompt
        + f"«uline|{rpad(f' {QUERY}█', span)}»"
        + " "
        + f"«chip|{centre('→ Detail', SLAB)}»"
        + " "
        + f"«chip|{centre('^t Scans', SLAB)}»"
    )


def dock_bottom() -> str:
    return (
        rpad(f" {COUNT}", FIELD_COLS)
        + " "
        + f"«chip|{centre('', SLAB)}»"
        + " "
        + f"«chip|{centre('•on', SLAB)}»"
    )


SPECIMENS["d"] = block(LIST + ["", dock_top(), dock_bottom()])

# ── E · the ascetic field ───────────────────────────────────────────────────
SPECIMENS["e"] = block(
    SELECTED + ["", field(tail="«chip| ⌕ »" + " " + "«chip| ⌨ »"), info("«dim|•»")]
)

# ── the recommendation, in the places that test it ──────────────────────────
FULL = [
    " «hd|dossier»                   «acc|! 3 exp · 24 docs» ",
    "«sel|▸ COC Certificate (Master)           ! 09-26 »",
    "«seldim|    cert-file 8 · marine                     »",
    "  COC Certificate 2019                  ·    ",
    "«dim|    cert-file 9 · marine»",
    "  COC Endorsement — Panama             12-26 ",
    "«dim|    cert-file 10 · marine»",
    "  COC Application Receipt               ·    ",
    "«dim|    softcopy · marine»",
]
FULL += ["" for _ in range(25 - len(FULL))]
SPECIMENS["c-full"] = block(FULL + ["", field(), chip_row()])

SPECIMENS["c-floor"] = block(
    [
        "  COC Certificate (Maste…  «soon|~ 09-26» ",
        "«dim|    cert-file 8 · marine»",
        "",
        field(FLOOR),
        " 3/24 " + "«chip| Detail »" + "«chip| Expiring  »" + "«chip| Scans• »",
    ],
    cols=FLOOR,
)

def desk_hint_row() -> str:
    """The keyboard layout keeps its hint line and gains the same chips at the
    right — one object in both layouts, clickable for a mouse."""
    hints = "«dim|⏎ open  → detail  ^x expiring  ^t scans  ^q quit»"
    chips = chip("^x Expiring") + chip("^t Scans", True)
    gap = DESK - GUTTER - width(hints) - width(chips) - GUTTER
    return " " + hints + " " * gap + chips + " " * GUTTER


DESK_LIST = [
    "  Ship Security Awareness            marine                     cert-file 6    02-30 ",
    "«sel|▸ COC Certificate (Master)         marine                     cert-file 8  ! 09-26 »",
    "  COC Certificate 2019                marine                     cert-file 9     ·    ",
]
SPECIMENS["c-desk"] = block(
    DESK_LIST
    + [
        "",
        field(DESK, tail="«dim|3/24»"),
        desk_hint_row(),
    ],
    cols=DESK,
)

render("bottombars.src.html", SPECIMENS, "bottombars.html")
