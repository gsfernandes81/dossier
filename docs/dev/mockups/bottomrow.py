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


def query(text: str = "", hint: str = "For more, hit", band: bool = False) -> str:
    """The entry row. Plain by default — the terminal's own background."""
    prompt = " >"
    span = W - width(prompt) - width(CHIP) - GUTTER
    body = f" {text}█"
    b = "band" if band else ""
    if text:
        inner = f"«{b or 'plain'}|{rpad(body, span)}»"
    else:
        gap = span - width(body) - len(hint) - 1
        inner = (
            f"«{b or 'plain'}|{body}{' ' * gap}»"
            + f"«{b + 'dim' if b else 'dim'}|{hint}»"
            + f"«{b or 'plain'}| »"
        )
    return (
        f"«{b + 'acc' if b else 'acc'}|{prompt}»"
        + inner
        + f"«{b + 'chip' if b else 'chip'}|{CHIP}»"
        + f"«{b or 'plain'}|{' ' * GUTTER}»"
    )


def info(
    count: str = "24/24",
    chips: str = "",
    hints: str = "⏎ open  ^x expiry  ^t scans",
    tone: str = "dim",
    band: bool = False,
) -> str:
    """Count, live filters and hints.

    Banded, this is a status line in the vim sense — a lit rule between the list
    and the thing you type into, which is what separates them.
    """
    b = "band" if band else ""
    left = f" {count}" + (f"  «{b + 'dim' if b else 'dim'}|{chips}»" if chips else "")
    if not hints:
        return f"«{b}|{rpad(left, W)}»" if b else rpad(left, W)
    gap = W - width(left) - len(hints) - GUTTER
    if b:
        head = f"«band| {count} »" if not chips else ""
        del head
        return (
            f"«band| {count}»"
            + (f"«banddim|  {chips}»" if chips else "")
            + f"«band|{' ' * gap}»"
            + f"«band{'warn' if tone == 'warn' else 'dim'}|{hints}»"
            + f"«band|{' ' * GUTTER}»"
        )
    return left + " " * gap + f"«{tone}|{hints}»" + " " * GUTTER


def pane(chrome: list[str], docs=None, rows_total: int = SHORT) -> str:
    body = rows(docs if docs is not None else DOCS[:4])
    fill = rows_total - 1 - len(body) - len(chrome)
    return block([header()] + body + ["" for _ in range(fill)] + chrome, cols=W)


# ── as built, and the two things being asked for ───────────────────────────
SPECIMENS["now"] = pane([query(band=True), info()])
SPECIMENS["swap-only"] = pane([info(), query(band=True)])
SPECIMENS["sep"] = pane([info(band=True), query()])

SPECIMENS["now-typed"] = pane([query("coc", band=True), info("4/24")], docs=DOCS[:4])
SPECIMENS["sep-typed"] = pane([info("4/24", band=True), query("coc")], docs=DOCS[:4])

# ── the states it has to survive ────────────────────────────────────────────
SPECIMENS["sep-filter"] = pane(
    [info("3/24", "[expiring]", "^x expiry  ^t scans", band=True), query()],
    docs=DOCS[:3],
)
SPECIMENS["sep-armed"] = pane(
    [info("24/24", hints="esc again to quit", tone="warn", band=True), query()]
)
SPECIMENS["sep-flash"] = pane(
    [info("4/24", hints="opened coc-master.pdf", band=True), query("coc")],
    docs=DOCS[:4],
)

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
SPECIMENS["sep-sheet"] = pane(
    [*SHEET, info(hints="esc closes", band=True), query()], docs=DOCS[:3]
)

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
SPECIMENS["sep-full"] = block(
    [header()] + rows(TALL_DOCS) + [info(band=True), query()], cols=W
)


# ── the desktop, where the same arrangement applies ────────────────────────
def desk_query(text: str = "coc") -> str:
    prompt = " > "
    tail = "«dim|4/24 »"
    span = W - width(prompt) - width(tail)
    return f"«acc|{prompt}»" + rpad(f"{text}█", span) + tail


DESK_HINTS = "space menu  ⏎ open  → detail  ^x expiring"
SPECIMENS["desk-now"] = block(
    [header()] + rows(DOCS[:4]) + ["", "", desk_query(), f" «dim|{DESK_HINTS}»"],
    cols=W,
)
SPECIMENS["desk-sep"] = block(
    [header()]
    + rows(DOCS[:4])
    + [
        "",
        "",
        "«band| " + DESK_HINTS + " " * (W - len(DESK_HINTS) - 2) + "»",
        desk_query(),
    ],
    cols=W,
)

render("bottomrow.src.html", SPECIMENS, "bottomrow.html")
