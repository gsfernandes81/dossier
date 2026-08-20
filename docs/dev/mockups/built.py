"""What the finished Find surface looks like, at the sizes the phone reports.

No history and no argument — the other pages in this directory carry those. This
one is the picture: the screens as they would ship, drawn on the exact character
grid at **47×45** browsing and **47×24** with the keyboard up.

Every line is padded to its real column count using display widths, and a line
one column too wide raises rather than wrapping.
"""

from __future__ import annotations

from grid import DESK, GUTTER, block, lpad, plain, render, rpad, width

W = 47  # columns the phone reports
BROWSE = 45  # rows with the keyboard down
QUERY = 24  # rows with the keyboard up

SPECIMENS: dict[str, str] = {}

# ── the store ───────────────────────────────────────────────────────────────
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

EXPIRING = [DOCS[0], DOCS[2], DOCS[7]]
MATCHES = DOCS[:4]


def header(exp: int = 3, total: int = 21, cols: int = W, touch: bool = True) -> str:
    """Name on the left; the counts on the right, the expiring one pressable."""
    cell = f" ! {exp} exp "
    count = f"«chip|{cell}»" if touch else f"«acc|{cell.strip()}»"
    right = f"«dim|{total} docs»  " + count
    left = " «hd|dossier»"
    gap = cols - width(left) - width(right) - GUTTER
    return left + " " * gap + right + " " * GUTTER


def rows(docs, cols: int = W, sel: int = 0) -> list[str]:
    """Two rows a document — title with a right-anchored expiry, then where it is."""
    out: list[str] = []
    for i, (title, expiry, where) in enumerate(docs):
        head = ("▸ " if i == sel else "  ") + title
        pad_to = cols - width(head) - width(expiry) - GUTTER
        head += " " * pad_to + expiry + " " * GUTTER
        body = "    " + where
        # Markup does not nest: a reversed row subsumes the expiry's colour.
        out += (
            [f"«sel|{plain(head)}»", f"«seldim|{rpad(body, cols)}»"]
            if i == sel
            else [head, f"«dim|{body}»"]
        )
    return out


def field(query: str = "", cols: int = W, tail: str = "«chip| ⌨ »") -> str:
    prompt = "«acc| >»"
    span = cols - width(prompt) - width(tail) - GUTTER
    return prompt + f"«uline|{rpad(f' {query}█', span)}»" + tail + " " * GUTTER


def truth(
    count: str, chips: str = "", hints: str = "", cols: int = W, tone: str = "dim"
) -> str:
    """Count, live filters, then whatever the app has to say.

    The hints slot is also where a flash and `esc again to quit` land — the app
    already renders them there — so the layout never needs a row it only
    sometimes uses.
    """
    left = f" {count}" + (f"  «dim|{chips}»" if chips else "")
    if not hints:
        return rpad(left, cols)
    gap = cols - width(left) - len(hints) - GUTTER
    return left + " " * gap + f"«{tone}|{hints}»" + " " * GUTTER


HINTS = "⏎ open  ^x expiry  ^t scans"


def screen(body: list[str], rows_total: int, cols: int = W) -> list[str]:
    """Pad a screen's list area so the chrome sits on the bottom two rows."""
    return body + ["" for _ in range(rows_total - 3 - len(body))]


# ── 1 · browsing, keyboard down ─────────────────────────────────────────────
SPECIMENS["browse"] = block(
    [header()] + rows(DOCS) + [field(), truth("21/21", hints=HINTS)], cols=W
)

# ── 2 · querying, keyboard up ───────────────────────────────────────────────
SPECIMENS["query"] = block(
    [header()]
    + screen(rows(MATCHES), QUERY)
    + [field("coc"), truth("4/21", hints=HINTS)],
    cols=W,
)

# ── 3 · a filter live ───────────────────────────────────────────────────────
SPECIMENS["filtered"] = block(
    [header()]
    + screen(rows(EXPIRING), BROWSE)
    + [field(), truth("3/21", "[expiring]", HINTS)],
    cols=W,
)

# ── 4 · about to quit ───────────────────────────────────────────────────────
SPECIMENS["armed"] = block(
    [header()]
    + screen(rows(DOCS[:5]), QUERY)
    + [field(), truth("21/21", hints="esc again to quit", tone="warn")],
    cols=W,
)

# ── 5 · a record ────────────────────────────────────────────────────────────
RECORD = [
    " «hd|COC Certificate (Master)»",
    "",
    " «dim|location»   cert-file 8",
    " «dim|expiry»     2026-09-28  «exp|! expired 22d»",
    " «dim|issued»     2021-09-29",
    " «dim|tags»       marine",
    " «dim|bundles»    us-visa · joining-2027",
    " «dim|files»      2   «acc|▸» coc-master.pdf «dim|(primary)»",
    "                  coc-master-back.jpg",
    " «dim|renews»     «acc|COC Certificate 2019»",
    " «dim|notes»      Revalidation booked at MMD,",
    "            slot 14 Oct. Bring originals",
    "            + 2 photos.",
    "",
    " «dim|─────────────────────────────────────────────»",
    " «warn|!» «dim|expired — renewal not yet filed»",
    "",
    " «acc|s» supersede  «acc|b» bundle  «acc|u» undo",
]
SPECIMENS["record"] = block(
    screen(RECORD, QUERY + 1) + [field(), truth("", hints="◀ back  ⏎ open file")],
    cols=W,
)


# ── 6 · the touch map ───────────────────────────────────────────────────────
def zone_rows(docs, sel: int = 0) -> list[str]:
    out: list[str] = []
    for i, (title, expiry, where) in enumerate(docs):
        head = ("▸ " if i == sel else "  ") + title
        head += " " * (W - width(head) - width(expiry) - GUTTER) + expiry + " " * GUTTER
        body = "    " + where
        tag = "zopen" if i == sel else "zpick"
        out += [f"«{tag}|{plain(head)}»", f"«{tag}|{rpad(body, W)}»"]
    return out


ZHEAD = " «hd|dossier»"
_zright = "«dim|21 docs»  " + "«zfilter| ! 3 exp »"
_zgap = W - width(ZHEAD) - width(_zright) - GUTTER
SPECIMENS["zones"] = block(
    [ZHEAD + " " * _zgap + _zright + " " * GUTTER]
    + screen(zone_rows(DOCS[:5]), QUERY)
    + [
        "«ztype|" + rpad(plain(field()), W) + "»",
        "«ztype|" + rpad(plain(truth("21/21", hints=HINTS)), W) + "»",
    ],
    cols=W,
)

# ── 7 · the desk ────────────────────────────────────────────────────────────
DESK_ROWS = (2, 46, 16, 22, 12)  # marker, title, shelf, locator, expiry


def desk_row(title, shelf, ref, expiry, sel=False) -> str:
    _, t, s, r, e = DESK_ROWS
    body = (
        ("▸ " if sel else "  ")
        + rpad(title, t)
        + rpad(shelf, s)
        + rpad(ref, r)
        + lpad(expiry, e)
    )
    return f"«sel|{rpad(plain(body), DESK)}»" if sel else body


DESK_LIST = [
    desk_row(
        title,
        where.split(" · ")[1],
        where.split(" · ")[0],
        plain(expiry).strip() or "·",
        sel=(i == 1),
    )
    for i, (title, expiry, where) in enumerate(DOCS[:18])
]

SPECIMENS["desk"] = block(
    [header(total=21, cols=DESK, touch=False)]
    + DESK_LIST
    + [
        field(cols=DESK, tail="«dim|21/21»"),
        " «dim|⏎ open  → detail  ^x expiring  ^t scans  : commands  ^q quit»",
    ],
    cols=DESK,
)

render("built.src.html", SPECIMENS, "built.html")
