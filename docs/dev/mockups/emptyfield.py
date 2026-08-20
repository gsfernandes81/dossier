"""What the search field says when it is empty.

The `⌨` chip is gone — Termux has its own keyboard key, and `SPC` takes that
corner — so nothing on a first run says the bottom of the screen is where you
type. The field can say it itself, in dim text inside the underline, which costs
no rows and disappears the moment you type a character.

Drawn at 47 columns. The underlined span is 39: 47 less the prompt, the chip and
the right gutter.
"""

from __future__ import annotations

from grid import GUTTER, block, plain, render, rpad, width

W = 47
ROWS = 24
SPAN = 39  # the underlined field: 47 − " >" − " SPC " − gutter

SPECIMENS: dict[str, str] = {}

DOCS = [
    ("COC Certificate (Master)", "«exp|! 09-26»", "cert-file 8 · marine"),
    ("COC Certificate 2019", "«dim|  ·  »", "cert-file 9 · marine"),
    ("COC Endorsement — Panama", "«soon|~ 12-26»", "cert-file 10 · marine"),
    ("COC Application Receipt", "«dim|  ·  »", "softcopy · marine"),
    ("Seaman's Book", "«ok|04-29»", "cert-file 1 · marine"),
]


def header() -> str:
    left = " «hd|dossier»"
    right = "«dim|21 docs»  «chip| ! 3 exp »"
    return left + " " * (W - width(left) - width(right) - GUTTER) + right + " " * GUTTER


def rows(docs, sel: int = 0) -> list[str]:
    out: list[str] = []
    for i, (title, expiry, where) in enumerate(docs):
        head = ("▸ " if i == sel else "  ") + title
        gap = W - width(head) - width(expiry) - GUTTER
        head += " " * gap + expiry + " " * GUTTER
        body = "    " + where
        out += (
            [f"«sel|{plain(head)}»", f"«seldim|{rpad(body, W)}»"]
            if i == sel
            else [head, f"«dim|{body}»"]
        )
    return out


def field(*segments: tuple[str, str], chip: bool = True) -> str:
    """The query row. Segments are (class, text) pairs filling the underline.

    Markup does not nest, so a dim placeholder inside an underlined field needs
    its own class — `uldim` is underline *and* dim, which in a terminal is two
    independent attributes on the same cell and needs no special handling at all.
    """
    inner = "".join(f"«{cls}|{text}»" for cls, text in segments if text)
    tail = "«chip| SPC »" if chip else ""
    return "«acc| >»" + inner + tail + " " * GUTTER


def empty(hint: str = "", right: bool = False) -> str:
    """An empty field: the cursor, then whatever the field has to say."""
    if not hint:
        return field(("uline", rpad(" █", SPAN)))
    if right:
        gap = SPAN - 2 - len(hint) - 1
        return field(("uline", " █" + " " * gap), ("uldim", hint), ("uline", " "))
    tail = SPAN - 3 - len(hint)
    return field(("uline", " █ "), ("uldim", hint), ("uline", " " * tail))


def paired(left: str, right: str = "For more, hit", cols: int = W) -> str:
    """Two dim phrases in one empty field, the right one running into the chip.

    The sentence finishes on the button: `For more, hit` then the reversed
    `SPC`. Both halves live *inside* the underline, so the field's geometry does
    not change when they go — and they go together, on the first character.
    """
    span = cols - 2 - 5 - GUTTER
    gap = span - 3 - len(left) - len(right)
    if gap < 2:  # too narrow to pair: the invitation outranks the signpost
        tail = span - 3 - len(left)
        return field(("uline", " █ "), ("uldim", left), ("uline", " " * tail))
    return field(
        ("uline", " █ "),
        ("uldim", left),
        ("uline", " " * gap),
        ("uldim", right),
    )


def typed(query: str = "coc") -> str:
    return field(("uline", rpad(f" {query}█", SPAN)))


def truth(hints: str = "⏎ open  ^x expiry", count: str = "21/21") -> str:
    left = f" {count}"
    gap = W - width(left) - len(hints) - GUTTER
    return left + " " * gap + f"«dim|{hints}»" + " " * GUTTER


def pane(chrome: list[str], docs=None) -> str:
    body = rows(docs if docs is not None else DOCS)
    fill = ROWS - 1 - len(body) - len(chrome)
    return block([header()] + body + ["" for _ in range(fill)] + chrome, cols=W)


# ── 0 · the baseline: nothing at all ────────────────────────────────────────
SPECIMENS["bare"] = pane([empty(), truth()])

# ── A · what the search actually does ───────────────────────────────────────
SPECIMENS["part"] = pane([empty("type any part of a name"), truth()])

# ── B · the count as the invitation ─────────────────────────────────────────
SPECIMENS["count"] = pane([empty("search 21 documents"), truth()])

# ── C · plain instruction, right-aligned in the field ───────────────────────
SPECIMENS["right"] = pane([empty("type to search", right=True), truth()])

# ── D · the field teaches the leader too ────────────────────────────────────
SPECIMENS["both"] = pane([empty("type a name  ·  SPC for more"), truth()])

# ── E · no placeholder; the truth row says it instead ───────────────────────
SPECIMENS["row"] = pane([empty(), truth(hints="type to search  ⏎ open")])

# ── the moment you type, it is gone ─────────────────────────────────────────
SPECIMENS["typed"] = pane([typed(), truth()], docs=DOCS[:4])

# ── the pairing: an invitation, and a signpost that ends on the button ──────
SPECIMENS["pair"] = pane([paired("Type to search"), truth()])
SPECIMENS["pair-lower"] = pane([paired("type to search", "for more, hit"), truth()])

# The long left phrase and the signpost cannot both fit: 3 + 23 + 13 = 39 with
# no gap at all. Pairing is what makes the short invitation the right one.
SPECIMENS["pair-long"] = pane([paired("type any part of a name"), truth()])

# At the 38-column floor the pair does not fit — 3 + 14 + 13 leaves no gap — so
# the signpost sheds and the invitation stays. Sheds one at a time, as ever.
FLOOR = 38


def floor_row(text: str) -> str:
    gap = FLOOR - width(text) - GUTTER
    return text + " " * gap + " "


SPECIMENS["pair-floor"] = block(
    [
        floor_row(" «hd|dossier»          «dim|21 docs»  «chip| ! 3 exp »"),
        floor_row("▸ COC Certificate (Maste…  «exp|! 09-26»"),
        floor_row("«dim|    cert-file 8 · marine»"),
        "",
        paired("Type to search", cols=FLOOR),
        floor_row(" 21/21" + " " * 25 + "«dim|⏎ open»"),
    ],
    cols=FLOOR,
)

# ── the recommendation at browsing height ───────────────────────────────────
TALL = 45
TALL_DOCS = DOCS + [
    ("Passport", "«ok|11-31»", "wallet-doc 2 · identity"),
    ("Yellow Fever Certificate", "«dim|  ·  »", "cert-file 4 · medical"),
    ("Medical Fitness (ILO)", "«soon|~ 10-26»", "cert-file 5 · medical"),
    ("Basic Safety Training", "«ok|02-30»", "cert-file 6 · marine"),
    ("Ship Security Awareness", "«ok|02-30»", "cert-file 7 · marine"),
    ("Driving Licence", "«ok|07-33»", "wallet-doc 3 · identity"),
    ("Birth Certificate", "«dim|  ·  »", "folder-home 1 · identity"),
    ("Degree Certificate", "«dim|  ·  »", "folder-home 2 · education"),
    ("PAN Card", "«dim|  ·  »", "wallet-doc 4 · identity"),
    ("Aadhaar", "«dim|  ·  »", "wallet-doc 5 · identity"),
    ("Vaccination Record", "«dim|  ·  »", "cert-file 3 · medical"),
    ("Advanced Firefighting", "«ok|02-30»", "cert-file 11 · marine"),
    ("Medical First Aid", "«soon|~ 11-26»", "cert-file 12 · marine"),
    ("GMDSS Operator", "«ok|06-29»", "cert-file 13 · marine"),
    ("Tanker Familiarisation", "«ok|09-28»", "cert-file 14 · marine"),
    ("Panama Seafarer ID", "«ok|03-29»", "cert-file 15 · marine"),
]
SPECIMENS["full"] = block(
    [header()] + rows(TALL_DOCS) + [paired("Type to search"), truth()], cols=W
)

# ── a keyboard layout has a space bar, so it needs no signpost ──────────────
SPECIMENS["desk"] = block(
    [header()]
    + rows(DOCS[:4])
    + [
        "",
        field(
            ("uline", " █ "),
            ("uldim", "Type to search"),
            ("uline", " " * 27),
            chip=False,
        ),
        " «dim|space  ⏎ open  → detail  ^x expiring»",
    ],
    cols=W,
)

render("emptyfield.src.html", SPECIMENS, "emptyfield.html")
