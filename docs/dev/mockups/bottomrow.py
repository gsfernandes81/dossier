"""Which of the two bottom rows should be last.

The query row is currently second from the bottom, with the count-and-hints row
under it. Every keyboard-driven finder this design has borrowed from puts the
entry line **last**: Emacs's minibuffer is the frame's final line, Vim's `:` is
the final line, and fzf's default layout is prompt at the bottom with its info
line directly above.

Drawn at 47×24 and 47×45, the sizes the phone reports.
"""

from __future__ import annotations

from grid import GUTTER, block, plain, render, rpad, width

W = 47
SHORT = 24
CHIP = " SPC "

SPECIMENS: dict[str, str] = {}

DOCS = [
    ("COC Certificate (Master)", "«exp|! 09-26»", "cert-file 8 · marine"),
    ("COC Certificate 2019", "«dim|  ·  »", "cert-file 9 · marine"),
    ("COC Endorsement — Panama", "«soon|~ 12-26»", "cert-file 10 · marine"),
    ("COC Application Receipt", "«dim|  ·  »", "softcopy · marine"),
    ("Seaman's Book", "«ok|04-29»", "cert-file 1 · marine"),
    ("Passport", "«ok|11-31»", "wallet-doc 2 · identity"),
]


def header() -> str:
    left = " «hd|dossier»"
    right = "«dim|24 docs»  «chip| ! 3 exp »"
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


def query(text: str = "", hint: str = "For more, hit") -> str:
    """The banded entry row — every column carries the band."""
    prompt = " >"
    span = W - width(prompt) - width(CHIP) - GUTTER
    body = f" {text}█"
    if text:
        inner = f"«band|{rpad(body, span)}»"
    else:
        gap = span - width(body) - len(hint) - 1
        inner = f"«band|{body}{' ' * gap}»«banddim|{hint}»«band| »"
    return (
        f"«bandacc|{prompt}»" + inner + f"«bandchip|{CHIP}»" + f"«band|{' ' * GUTTER}»"
    )


def info(
    count: str = "24/24",
    chips: str = "",
    hints: str = "⏎ open  ^x expiry  ^t scans",
    tone: str = "dim",
) -> str:
    left = f" {count}" + (f"  «dim|{chips}»" if chips else "")
    if not hints:
        return rpad(left, W)
    gap = W - width(left) - len(hints) - GUTTER
    return left + " " * gap + f"«{tone}|{hints}»" + " " * GUTTER


def pane(chrome: list[str], docs=None, rows_total: int = SHORT) -> str:
    body = rows(docs if docs is not None else DOCS[:4])
    fill = rows_total - 1 - len(body) - len(chrome)
    return block([header()] + body + ["" for _ in range(fill)] + chrome, cols=W)


# ── the two orders, side by side ────────────────────────────────────────────
SPECIMENS["now"] = pane([query(), info()])
SPECIMENS["swapped"] = pane([info(), query()])

SPECIMENS["now-typed"] = pane([query("coc"), info("4/24")], docs=DOCS[:4])
SPECIMENS["swapped-typed"] = pane([info("4/24"), query("coc")], docs=DOCS[:4])

# ── the states the swap has to survive ──────────────────────────────────────
SPECIMENS["swapped-filter"] = pane(
    [info("3/24", "[expiring]", "^x expiry  ^t scans"), query()], docs=DOCS[:3]
)
SPECIMENS["swapped-armed"] = pane(
    [info("24/24", hints="esc again to quit", tone="warn"), query()]
)
SPECIMENS["swapped-flash"] = pane(
    [info("", hints="opened coc-master.pdf", tone="acc"), query("coc")], docs=DOCS[:4]
)

# ── with the sheet up ───────────────────────────────────────────────────────
RULE = "«dim|" + "─" * (W - 2) + "»"
COLS = (16, 16, 13)


def keyline(*items: tuple[str, str]) -> str:
    out = " "
    for i, (key, label) in enumerate(items):
        out += f"«acc|{key}» {label}" + " " * (COLS[i] - 1 - len(key) - len(label))
    return out


_crumb = " «acc|SPC»"
_note = "type to search"
SHEET = [
    " " + RULE,
    _crumb + " " * (W - width(_crumb) - len(_note) - GUTTER) + f"«dim|{_note}»" + " ",
    keyline(("f", "filter"), ("q", "quit")),
]
SPECIMENS["swapped-sheet"] = pane(
    [*SHEET, info(hints="esc closes"), query()], docs=DOCS[:3]
)

# ── whole screen, browsing height ───────────────────────────────────────────
TALL = 45
TALL_DOCS = DOCS + [
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
SPECIMENS["swapped-full"] = block(
    [header()] + rows(TALL_DOCS) + [info(), query()], cols=W
)


# ── the desktop, where the same question applies ────────────────────────────
def desk_query(text: str = "coc") -> str:
    prompt = " > "
    tail = "«dim|4/24 »"
    span = W - width(prompt) - width(tail)
    return f"«bandacc|{prompt}»«band|{rpad(f'{text}█', span)}»«banddim|4/24 »"


SPECIMENS["desk-now"] = block(
    [header()]
    + rows(DOCS[:4])
    + ["", "", desk_query(), " «dim|space menu  ⏎ open  → detail  ^x expiring»"],
    cols=W,
)
SPECIMENS["desk-swapped"] = block(
    [header()]
    + rows(DOCS[:4])
    + ["", "", " «dim|space menu  ⏎ open  → detail  ^x expiring»", desk_query()],
    cols=W,
)

render("bottomrow.src.html", SPECIMENS, "bottomrow.html")
