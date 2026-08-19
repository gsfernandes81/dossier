"""The bottom chrome once Termux's key row and its modifiers are accounted for.

Every specimen uses `grid`, so the tiling is the app's own and an over-wide line
raises rather than wrapping.

The recommendation here has no action bar at all, so most of these specimens are
about what the freed row does and what the truth row carries instead. Two of
them exist purely as evidence against an alternative: `chip-off`/`chip-on` show
that a state chip cannot be the control, because it does not exist until it is
already on.
"""

from __future__ import annotations

from grid import (
    DESK,
    FLOOR,
    GUTTER,
    PHONE,
    bar,
    block,
    lpad,
    plain,
    render,
    rpad,
    width,
)

QUERY = "coc"

#: The touch hints, most sheddable first — the row drops them one at a time
#: rather than dropping the line whole, which is what it does today.
HINTS = ["⏎ open", "^x expiry", "^t scans"]


def header(exp: int = 3, ndocs: int = 24, cols: int = PHONE, touch: bool = True) -> str:
    """`dossier` on the left, the counts on the right.

    On a touch layout the expiring count is **pressable** — it is the affordance
    REWRITE-UI §1 already specifies, and the only one that reaches `ctrl+x` with
    the keyboard down — so by the three-texture rule it is drawn reversed.
    """
    cell = f" ! {exp} exp "
    count = f"«chip|{cell}»" if touch else f"«acc|{cell.strip()}»"
    right = f"«dim|{ndocs} docs»  " + count
    left = " «hd|dossier»"
    gap = cols - width(left) - width(right) - GUTTER
    return left + " " * gap + right + " " * GUTTER


def field(cols: int = PHONE, query: str = QUERY, tail: str = "«chip| ⌨ »") -> str:
    prompt = "«acc| >»"
    span = cols - width(prompt) - width(tail) - GUTTER
    return prompt + f"«uline|{rpad(f' {query}█', span)}»" + tail + " " * GUTTER


def truth(count: str, chips: str = "", cols: int = PHONE) -> str:
    """Count, live filter chips, then the hints — every one of them dim.

    With the bar gone this row holds no controls, so it is the one row on the
    screen with a single texture. Hints shed one at a time from the left.
    """
    left = f" {count}" + (f"  «dim|{chips}»" if chips else "")
    for start in range(len(HINTS)):
        hint = "  ".join(HINTS[start:])
        gap = cols - width(left) - width(hint) - GUTTER
        if gap >= 2:
            return left + " " * gap + f"«dim|{hint}»" + " " * GUTTER
    return rpad(left, cols)


def docs(
    rows: list[tuple[str, str, str]], cols: int = PHONE, sel: int = 0
) -> list[str]:
    """Two rows a document: title with a right-anchored expiry, then the locator."""
    out: list[str] = []
    for i, (title, expiry, where) in enumerate(rows):
        head = ("▸ " if i == sel else "  ") + title
        gap = cols - width(head) - width(expiry) - GUTTER
        head += " " * gap + expiry + " " * GUTTER
        body = "    " + where
        # Markup does not nest — a reversed row subsumes the expiry's colour, so
        # the selected pair is written flat.
        out += (
            [f"«sel|{plain(head)}»", f"«seldim|{rpad(body, cols)}»"]
            if i == sel
            else [head, f"«dim|{body}»"]
        )
    return out


#: Twelve documents is the budget the layout is measured against: 28 rows less
#: three chrome rows leaves 25, and a document is two rows.
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

SPECIMENS: dict[str, str] = {}

# ── the recommendation ──────────────────────────────────────────────────────
SPECIMENS["barless"] = block([header()] + docs(DOCS[:4]) + ["", field(), truth("3/24")])

SPECIMENS["barless-on"] = block(
    [header()] + docs(DOCS[:4]) + ["", field(query=""), truth("3/24", "[expiring]")]
)

SPECIMENS["barless-message"] = block(
    [header()]
    + docs(DOCS[:4])
    + ["«acc| tap the row again to open it»", field(), truth("3/24")]
)

# ── evidence: a state chip cannot be the control ────────────────────────────
SPECIMENS["chip-off"] = block(docs(DOCS[:2], sel=-1) + [field(), truth("3/24")])
SPECIMENS["chip-on"] = block(
    docs(DOCS[:2], sel=-1) + [field(query=""), truth("3/24", "[expiring]")]
)

# ── the whole screen, and the state the correction actually changed ─────────
SPECIMENS["barless-full"] = block(
    [header()] + docs(DOCS) + ["", field(), truth("3/24")]
)

#: Raising the IME does not cover the terminal — Termux resizes it — so the
#: querying state is a re-layout, not an occlusion. Five documents, not twelve.
SPECIMENS["barless-ime"] = block(
    [header()] + docs(DOCS[:5]) + ["", field(query="coc c"), truth("2/24")]
)

SPECIMENS["barless-floor"] = block(
    [header(cols=FLOOR)]
    + docs(
        [
            ("COC Certificate (Maste…", "«soon|~ 09-26»", "cert-file 8 · marine"),
            ("COC Certificate 2019", "«dim|  ·  »", "cert-file 9 · marine"),
        ],
        cols=FLOOR,
        sel=-1,
    )
    + [field(FLOOR), truth("3/24", "[expiring]", cols=FLOOR)],
    cols=FLOOR,
)


# ── runner-up · one always-visible chip ─────────────────────────────────────
def one_chip(on: bool, cols: int = PHONE) -> str:
    chip = f"«chip| ^x expiry{'•' if on else ' '} »"
    hint = "«dim|⏎ open  ^t scans»"
    left = f" {'14/24' if on else '3/24'}"
    gap = cols - width(left) - width(hint) - GUTTER - width(chip) - GUTTER
    return left + " " * gap + hint + " " * GUTTER + chip + " " * GUTTER


SPECIMENS["onechip"] = block(
    [header()] + docs(DOCS[:4]) + ["", field(), one_chip(False)]
)

# ── the retired direction, for comparison ───────────────────────────────────
SPECIMENS["oldbar"] = block(
    [header(touch=False)]
    + docs(DOCS[:4])
    + [
        bar(["→ Detail", "^x Expiry", "^t Scans•"]),
        field(query=""),
        # Today's behaviour, not the new one: with two filters live the hint
        # line is dropped whole rather than shed item by item.
        rpad(" 3/24  «dim|[expiring]  [scans]»", PHONE),
    ]
)

# ── the same object at a desk ───────────────────────────────────────────────
DESK_COLS = (2, 36, 14, 16, 8)  # marker, title, shelf, locator, expiry


def desk_row(title: str, shelf: str, ref: str, expiry: str, sel: bool = False) -> str:
    _, t, s, r, e = DESK_COLS
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
    desk_row("COC Certificate 2019", "marine", "cert-file 9", "·"),
]

SPECIMENS["barless-desk"] = block(
    [header(cols=DESK, touch=False)]
    + DESK_LIST
    + [
        field(DESK, tail="«dim|3/24»"),
        " «dim|⏎ open  → detail  ^x expiring  ^t scans  ^q quit»",
    ],
    cols=DESK,
)

render("bottombars.src.html", SPECIMENS, "bottombars.html")
