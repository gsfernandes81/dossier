"""The text entry line, read against Emacs's minibuffer and Vim's command line.

Both are the same idea: one line at the bottom of the frame that is **whatever
is being asked right now**, with a prompt that names the question. Neither draws
a box. Emacs goes further and puts the echo area on that same line, so what the
program says and what you type occupy one place rather than two.

Drawn at 47 columns, the width the phone reports.
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


def entry(
    prompt: str,
    query: str = "",
    hint: str = "",
    tail: str = "",
    chip: bool = True,
    boxed: bool = True,
    lead: str = "",
) -> str:
    """One entry line: prompt, then the field, then whatever closes the row.

    `boxed` draws the underline that says *typable*; without it the row is
    Emacs's — a prompt, a cursor and nothing else.
    """
    chip_w = width(CHIP) if chip else 0
    span = W - width(prompt) - chip_w - GUTTER - width(tail)
    line = "uline" if boxed else "plain"
    quiet = "uldim" if boxed else "dim"
    body = f" {query}█" + (f" {lead}" if lead else "")
    if hint:
        gap = span - width(body) - len(hint) - 1
        if lead:
            inner = (
                f"«{line}| {query}█ »"
                + f"«{quiet}|{lead}»"
                + f"«{line}|{' ' * gap}»"
                + f"«{quiet}|{hint}»"
                + f"«{line}| »"
            )
        else:
            inner = f"«{line}|{body}{' ' * gap}»" + f"«{quiet}|{hint}»" + f"«{line}| »"
    else:
        inner = f"«{line}|{rpad(body, span)}»"
    out = f"«acc|{prompt}»" + inner + tail
    if chip:
        out += f"«chip|{CHIP}»"
    return out + " " * GUTTER


def truth(left: str, hints: str = "⏎ open  ^x expiry  ^t scans") -> str:
    gap = W - width(left) - len(hints) - GUTTER
    return left + " " * gap + f"«dim|{hints}»" + " " * GUTTER


def pane(chrome: list[str], docs=None) -> str:
    body = rows(docs if docs is not None else DOCS)
    fill = ROWS - 1 - len(body) - len(chrome)
    return block([header()] + body + ["" for _ in range(fill)] + chrome, cols=W)


# ── 0 · as built ────────────────────────────────────────────────────────────
SPECIMENS["now"] = pane(
    [
        entry(" >", lead="Type to search", hint="For more, hit"),
        truth(" 24/24"),
    ]
)
# The placeholder only exists because `>` says nothing. Name the prompt and it
# is redundant — which is the whole Emacs argument in one line.
SPECIMENS["now-typed"] = pane([entry(" >", "coc"), truth(" 4/24")], docs=DOCS[:4])

# ── A · the prompt names the question ───────────────────────────────────────
SPECIMENS["named"] = pane([entry(" Find:", hint="For more, hit"), truth(" 24/24")])
SPECIMENS["named-typed"] = pane([entry(" Find:", "coc"), truth(" 4/24")], docs=DOCS[:4])

# ── B · no box at all, the way Emacs draws it ───────────────────────────────
SPECIMENS["bare"] = pane(
    [entry(" Find:", hint="For more, hit", boxed=False), truth(" 24/24")]
)

# ── C · the count folds into the entry row; row two becomes the echo area ───
SPECIMENS["counted"] = pane(
    [
        entry(" Find:", "coc", tail="«dim| 4/24 »"),
        truth("", hints="⏎ open  ^x expiry  ^t scans"),
    ],
    docs=DOCS[:4],
)
SPECIMENS["counted-echo"] = pane(
    [
        entry(" Find:", "coc", tail="«dim| 4/24 »"),
        truth(" «warn|esc again to quit»", hints=""),
    ],
    docs=DOCS[:4],
)

# ── D · the prompt changes with what is being asked ─────────────────────────
SPECIMENS["command"] = pane(
    [
        entry(" :", "expiring", tail="«dim| ⏎ runs »", chip=False),
        truth("", hints="esc cancels"),
    ]
)
_crumb = " «acc|SPC f»"
_note = "1 match"
SPECIMENS["picking"] = pane(
    [
        " «dim|" + "─" * (W - 2) + "»",
        _crumb
        + " " * (W - width(_crumb) - len(_note) - GUTTER)
        + f"«dim|{_note}»"
        + " ",
        "«sel|" + rpad("  expiring only                    SPC f x", W) + "»",
        entry(" SPC f:", "exp"),
        truth("", hints="⏎ runs it"),
    ],
    docs=DOCS[:3],
)

render("minibuffer.src.html", SPECIMENS, "minibuffer.html")
