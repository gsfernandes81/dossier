"""The status line as a light bar with black text, in Termux's own palette.

Every other page here lets the terminal panes follow the *viewer's* theme, which
is the right default — it demonstrates §6's claim that the user's colours carry
the design. This page does the opposite on purpose: the question is what one
specific phone will show, so the panes are pinned to Termux's built-in default
scheme and ignore the browser entirely.

Three bars, same rows: ANSI 8 on 15 as it ships, and two lighter ones.
"""

from __future__ import annotations

from grid import GUTTER, block, plain, render, rpad, width

W = 47
ROWS = 24
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


def entry(text: str = "", hint: str = "For more, hit") -> str:
    """The entry line — plain, on the terminal's own background."""
    prompt = " >"
    span = W - width(prompt) - width(CHIP) - GUTTER
    body = f" {text}█"
    if text:
        inner = rpad(body, span)
    else:
        gap = span - width(body) - len(hint) - 1
        inner = f"{body}{' ' * gap}«dim|{hint}»" + " "
    return f"«acc|{prompt}»" + inner + f"«chip|{CHIP}»" + " " * GUTTER


def status(
    bar: str,
    count: str = "24/24",
    chips: str = "",
    hints: str = "⏎ open  ^x expiry  ^t scans",
    warn: bool = False,
) -> str:
    """The lit rule. `bar` names which palette pair the row uses."""
    left = f"«{bar}| {count}»" + (f"«{bar}dim|  {chips}»" if chips else "")
    gap = W - 1 - len(count) - (len(chips) + 2 if chips else 0) - len(hints) - GUTTER
    tone = f"{bar}warn" if warn else f"{bar}dim"
    return (
        left + f"«{bar}|{' ' * gap}»" + f"«{tone}|{hints}»" + f"«{bar}|{' ' * GUTTER}»"
    )


BARS = {
    "grey": "ANSI 8 behind, 15 in front — as it ships",
    "light": "ANSI 7 behind, 0 in front",
    "white": "ANSI 15 behind, 0 in front",
}

for bar in BARS:
    SPECIMENS[f"{bar}-rows"] = block([status(bar), entry()], cols=W)
    SPECIMENS[f"{bar}-filter"] = block(
        [status(bar, "3/24", "[expiring]", "^x expiry  ^t scans"), entry()], cols=W
    )
    SPECIMENS[f"{bar}-armed"] = block(
        [status(bar, "24/24", hints="esc again to quit", warn=True), entry()], cols=W
    )
    SPECIMENS[f"{bar}-full"] = block(
        [header()]
        + rows(DOCS)
        + ["" for _ in range(ROWS - 1 - len(DOCS) * 2 - 2)]
        + [status(bar, "24/24"), entry()],
        cols=W,
    )

render("lightbar.src.html", SPECIMENS, "lightbar.html")
