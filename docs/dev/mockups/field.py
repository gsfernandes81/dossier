"""How to mark the query field, once the underline turns out to sit too high.

A terminal draws `SGR 4` wherever the font's underline metric says, which on the
phone is close under the baseline — through the descenders rather than below
them. Nothing in the app can move it. So the question is what else can say *you
can type here*, and Emacs has two answers plus a third by omission.

Drawn at 47 columns.
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


def truth(count: str = "4/24", hints: str = "⏎ open  ^x expiry  ^t scans") -> str:
    left = f" {count}"
    gap = W - width(left) - len(hints) - GUTTER
    return left + " " * gap + f"«dim|{hints}»" + " " * GUTTER


def pane(chrome: list[str], docs=None) -> str:
    body = rows(docs if docs is not None else DOCS[:4])
    fill = ROWS - 1 - len(body) - len(chrome)
    return block([header()] + body + ["" for _ in range(fill)] + chrome, cols=W)


PROMPT = " Find:"
SPAN = W - width(PROMPT) - width(CHIP) - GUTTER  # 35


def field(mark: str, query: str = "coc", hint: str = "", prompt: str = PROMPT) -> str:
    """One entry row. `mark` is the class the field's own cells carry."""
    span = W - width(prompt) - width(CHIP) - GUTTER
    body = f" {query}█"
    if hint:
        gap = span - width(body) - len(hint) - 1
        inner = f"«{mark}|{body}{' ' * gap}»" + f"«{mark}dim|{hint}»" + f"«{mark}| »"
    else:
        inner = f"«{mark}|{rpad(body, span)}»"
    return f"«acc|{prompt}»" + inner + f"«chip|{CHIP}»" + " " * GUTTER


# ── 0 · as built ────────────────────────────────────────────────────────────
SPECIMENS["now"] = pane([field("uline"), truth()])
SPECIMENS["now-empty"] = pane(
    [field("uline", query="", hint="For more, hit"), truth("24/24")], docs=DOCS
)

# ── A · nothing at all: the prompt and the cursor ───────────────────────────
SPECIMENS["bare"] = pane([field("bright"), truth()])
SPECIMENS["bare-empty"] = pane(
    [field("bright", query="", hint="For more, hit"), truth("24/24")], docs=DOCS
)

# ── B · a background over the field's extent — Emacs's widget-field ─────────
SPECIMENS["tint"] = pane([field("tint"), truth()])
SPECIMENS["tint-empty"] = pane(
    [field("tint", query="", hint="For more, hit"), truth("24/24")], docs=DOCS
)


# ── C · the line, but only under what is there ──────────────────────────────
def short(query: str = "coc") -> str:
    body = f" {query}█"
    rest = SPAN - width(body)
    return (
        f"«acc|{PROMPT}»"
        + f"«uline|{body}»"
        + " " * rest
        + f"«chip|{CHIP}»"
        + " " * GUTTER
    )


SPECIMENS["short"] = pane([short(), truth()])


# ── D · delimiters, which no font metric can move ───────────────────────────
def bracketed(query: str = "coc") -> str:
    span = W - width(PROMPT) - 2 - width(CHIP) - GUTTER
    return (
        f"«acc|{PROMPT}»"
        + "«dim|[»"
        + f"«bright|{rpad(f'{query}█', span)}»"
        + "«dim|]»"
        + f"«chip|{CHIP}»"
        + " " * GUTTER
    )


SPECIMENS["brackets"] = pane([bracketed(), truth()])

# ── as built · the band is the whole row, edge to edge ─────────────────────
def band(query: str = "coc", hint: str = "", prompt: str = " >") -> str:
    """The shipped row: every column carries the band, including the gutters.

    Markup does not nest, so each run names the band plus its own emphasis —
    which is exactly what the renderer does with a `Paragraph` style and spans
    patched on top of it.
    """
    span = W - width(prompt) - width(CHIP) - GUTTER
    body = f" {query}█"
    out = f"«bandacc|{prompt}»"
    if hint:
        gap = span - width(body) - len(hint) - 1
        out += f"«band|{body}{' ' * gap}»«banddim|{hint}»«band| »"
    else:
        out += f"«band|{rpad(body, span)}»"
    return out + f"«bandchip|{CHIP}»" + f"«band|{' ' * GUTTER}»"


SPECIMENS["line"] = pane([band(), truth()])
SPECIMENS["line-empty"] = pane(
    [band(query="", hint="For more, hit"), truth("24/24")], docs=DOCS
)

render("field.src.html", SPECIMENS, "field.html")
