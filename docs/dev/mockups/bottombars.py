"""The bottom chrome after the Termux key row is taken into account.

Every specimen uses `grid`, so the tiling is the app's own and an over-wide line
raises rather than wrapping.

The chips reserve their state column whether or not the dot is lit, so a chip
never changes width when it is toggled — a row that reflows on a tap is a row
whose *next* tap lands somewhere else.
"""

from __future__ import annotations

from grid import (
    DESK,
    FLOOR,
    GUTTER,
    PHONE,
    block,
    cells,
    centre,
    lpad,
    plain,
    render,
    rpad,
    width,
)

QUERY = "coc"

LIST = [
    "  COC Certificate (Master)           «soon|~ 09-26» ",
    "«dim|    cert-file 8 · marine»",
    "  COC Certificate 2019                  ·    ",
    "«dim|    cert-file 9 · marine»",
]


def field(cols: int = PHONE, query: str = QUERY, tail: str = "«chip| ⌨ »") -> str:
    prompt = "«acc| >»"
    span = cols - width(prompt) - width(tail) - GUTTER
    return prompt + f"«uline|{rpad(f' {query}█', span)}»" + tail + " " * GUTTER


def chip(label: str, on: bool = False) -> str:
    """A pressable chip carrying its own state; the dot's column is reserved."""
    return f"«chip| {label}{'•' if on else ' '} »"


def truth(count: str, hint: str, expiring: bool, scans: bool, cols: int = PHONE) -> str:
    """Count and a dim hint on the left, the two toggle chips anchored right."""
    right = chip("^x Expiring", expiring) + " " + chip("^t Scans", scans)
    left = f" {count}" + (f"  «dim|{hint}»" if hint else "")
    gap = cols - width(left) - width(right) - GUTTER
    if gap < 0:  # shed the hint before anything else — never vanish wholesale
        left = f" {count}"
        gap = cols - width(left) - width(right) - GUTTER
    return left + " " * gap + right + " " * GUTTER


SPECIMENS: dict[str, str] = {}

# ── the recommendation ──────────────────────────────────────────────────────
SPECIMENS["quiet"] = block(
    LIST + ["", field(), truth("3/24", "→ detail", False, False)]
)

SPECIMENS["quiet-on"] = block(
    LIST + ["", field(), truth("14/24", "→ detail", True, True)]
)

SPECIMENS["quiet-message"] = block(
    LIST
    + [
        "«acc| tap the row again to open it»",
        field(),
        truth("3/24", "→ detail", False, False),
    ]
)

SPECIMENS["quiet-armed"] = block(
    LIST
    + [
        "«soon| esc again to quit»",
        field(cols=PHONE, query=""),
        truth("24/24", "→ detail", False, False),
    ]
)

# ── runner-up 1 · the toggle dock ───────────────────────────────────────────
FIELD_COLS, SLAB = 21, 11


def dock(state_row: bool = False) -> list[str]:
    prompt = "«acc| >»"
    span = FIELD_COLS - width(prompt)
    top = (
        prompt
        + f"«uline|{rpad(f' {QUERY}█', span)}»"
        + " "
        + f"«chip|{centre('^x Expiring', SLAB)}»"
        + " "
        + f"«chip|{centre('^t Scans', SLAB)}»"
    )
    bottom = (
        rpad(" 3/24", FIELD_COLS)
        + " "
        + f"«chip|{centre('•' if state_row else '', SLAB)}»"
        + " "
        + f"«chip|{centre('', SLAB)}»"
    )
    return [top, bottom]


SPECIMENS["dock"] = block(LIST + [""] + dock(state_row=True))


# ── runner-up 2 · the two-cell bar ──────────────────────────────────────────
def two_cell_bar() -> str:
    out, column = "", 0
    for (start, cell), label in zip(
        cells(PHONE, 2), ["^x Expiring•", "^t Scans"], strict=True
    ):
        out += " " * (start - column) + f"«chip|{centre(label, cell)}»"
        column = start + cell
    return out


SPECIMENS["twocell"] = block(
    LIST
    + [
        two_cell_bar(),
        field(),
        " 3/24" + " " * (PHONE - 5 - 22 - GUTTER) + "«dim|tap again opens  ⏎»" + " ",
    ]
)

# ── the recommendation, where it is tested ──────────────────────────────────
#: Twelve documents is the budget the whole layout is measured against: 28 rows
#: less one header and three chrome rows leaves 24, and a document is two rows.
DOCS = [
    ("COC Certificate (Master)", "«exp|! 09-26»", "cert-file 8 · marine"),
    ("COC Certificate 2019", "«dim|  ·  »", "cert-file 9 · marine"),
    ("COC Endorsement — Panama", "«soon|~ 12-26»", "cert-file 10 · marine"),
    ("COC Application Receipt", "«dim|  ·  »", "softcopy · marine"),
    ("Seaman's Book", "«ok|04-29»", "cert-file 1 · marine"),
    ("Passport", "«ok|11-31»", "wallet-doc 2 · identity"),
    ("Yellow Fever Certificate", "«dim|  ·  »", "cert-file 4 · medical"),
    ("Medical Fitness (ILO)", "«soon|~ 10-26»", "cert-file 5 · medical"),
    ("Basic Safety Training", "«ok|02-30»", "cert-file 6 · marine"),
    ("Ship Security Awareness", "«ok|02-30»", "cert-file 7 · marine"),
    ("Driving Licence", "«ok|07-33»", "wallet-doc 3 · identity"),
    ("Birth Certificate", "«dim|  ·  »", "folder-home 1 · identity"),
]


def doc_rows(cols: int = PHONE) -> list[str]:
    """Twelve documents, two rows each — title + right-anchored expiry, then
    the dim locator. The first is selected, so both of its rows are reversed."""
    out: list[str] = []
    for i, (title, expiry, where) in enumerate(DOCS):
        sel = i == 0
        head = ("▸ " if sel else "  ") + title
        gap = cols - width(head) - width(expiry) - GUTTER
        head += " " * gap + expiry + " " * GUTTER
        body = "    " + where
        # Markup does not nest — a reversed row subsumes the expiry's colour, so
        # the selected pair is written flat.
        out += (
            [f"«sel|{plain(head)}»", f"«seldim|{rpad(body, cols)}»"]
            if sel
            else [head, f"«dim|{body}»"]
        )
    return out


SPECIMENS["quiet-full"] = block(
    [" «hd|dossier»                   «acc|! 3 exp · 24 docs» "]
    + doc_rows()
    + ["", field(), truth("3/24", "→ detail", False, True)]
)

SPECIMENS["quiet-floor"] = block(
    [
        "  COC Certificate (Maste…  «soon|~ 09-26» ",
        "«dim|    cert-file 8 · marine»",
        "",
        field(FLOOR),
        truth("3/24", "→ detail", True, True, cols=FLOOR),
    ],
    cols=FLOOR,
)

# ── the same object at a desk, where the chips are mouse targets ────────────
DESK_COLS = (2, 36, 14, 16, 8)  # marker, title, shelf, locator, expiry


def desk_row(title: str, shelf: str, ref: str, expiry: str, sel: bool = False) -> str:
    marker, t, s, r, e = DESK_COLS
    body = (
        ("▸ " if sel else "  ")
        + rpad(title, t)
        + rpad(shelf, s)
        + rpad(ref, r)
        + lpad(expiry, e)
    )
    return f"«sel|{rpad(plain(body), DESK)}»" if sel else body


DESK_LIST = [
    desk_row("Ship Security Awareness", "marine", "cert-file 6", "02-30"),
    desk_row("COC Certificate (Master)", "marine", "cert-file 8", "! 09-26", sel=True),
    desk_row("COC Certificate 2019", "marine", "cert-file 9", "  ·  "),
]


def desk_hints() -> str:
    hints = "«dim|⏎ open  → detail  ^x expiring  ^t scans  ^q quit»"
    chips = chip("^x Expiring") + " " + chip("^t Scans", True)
    gap = DESK - GUTTER - width(hints) - width(chips) - GUTTER
    return " " + hints + " " * gap + chips + " " * GUTTER


SPECIMENS["quiet-desk"] = block(
    DESK_LIST + ["", field(DESK, tail="«dim|3/24»"), desk_hints()], cols=DESK
)

render("bottombars.src.html", SPECIMENS, "bottombars.html")
