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


def field(*segments: tuple[str, str]) -> str:
    """The query row. Segments are (class, text) pairs filling the underline.

    Markup does not nest, so a dim placeholder inside an underlined field needs
    its own class — `uldim` is underline *and* dim, which in a terminal is two
    independent attributes on the same cell and needs no special handling at all.
    """
    inner = "".join(f"«{cls}|{text}»" for cls, text in segments if text)
    return "«acc| >»" + inner + "«chip| SPC »" + " " * GUTTER


def empty(hint: str = "", right: bool = False) -> str:
    """An empty field: the cursor, then whatever the field has to say."""
    if not hint:
        return field(("uline", rpad(" █", SPAN)))
    if right:
        gap = SPAN - 2 - len(hint) - 1
        return field(("uline", " █" + " " * gap), ("uldim", hint), ("uline", " "))
    tail = SPAN - 3 - len(hint)
    return field(("uline", " █ "), ("uldim", hint), ("uline", " " * tail))


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
    [header()] + rows(TALL_DOCS) + [empty("type any part of a name"), truth()], cols=W
)

render("emptyfield.src.html", SPECIMENS, "emptyfield.html")
