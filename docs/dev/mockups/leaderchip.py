"""The leader's touch trigger — where `SPC` goes when there is no space bar.

With the keyboard down a thumb has no Space, and the header is at the top of a
45-row phone: too far, and not where anyone looks for a menu. So the touch
layout needs one pressable thing at the bottom that opens the leader sheet.

Five placements, drawn at 47 columns. Every one lives inside the existing three
chrome rows — a trigger that costs a row would undo the argument that deleted
the action bar.
"""

from __future__ import annotations

from grid import GUTTER, block, plain, render, rpad, width

W = 47
ROWS = 24

SPECIMENS: dict[str, str] = {}

DOCS = [
    ("COC Certificate (Master)", "«exp|! 09-26»", "cert-file 8 · marine"),
    ("COC Certificate 2019", "«dim|  ·  »", "cert-file 9 · marine"),
    ("COC Endorsement — Panama", "«soon|~ 12-26»", "cert-file 10 · marine"),
    ("COC Application Receipt", "«dim|  ·  »", "softcopy · marine"),
    ("Seaman's Book", "«ok|04-29»", "cert-file 1 · marine"),
]

KBD = "«chip| ⌨ »"
SPC = "«chip| SPC »"


def header() -> str:
    left = " «hd|dossier»"
    right = "«dim|21 docs»  «chip| ! 3 exp »"
    return left + " " * (W - width(left) - width(right) - GUTTER) + right + " " * GUTTER


def rows(docs, sel: int = 0) -> list[str]:
    out: list[str] = []
    for i, (title, expiry, where) in enumerate(docs):
        head = ("▸ " if i == sel else "  ") + title
        head += " " * (W - width(head) - width(expiry) - GUTTER) + expiry + " " * GUTTER
        body = "    " + where
        out += (
            [f"«sel|{plain(head)}»", f"«seldim|{rpad(body, W)}»"]
            if i == sel
            else [head, f"«dim|{body}»"]
        )
    return out


def field(
    lead: str = "", tail: str = KBD, query: str = "coc", prompt: str = "«acc| >»"
) -> str:
    """The query row, optionally with a chip before the prompt or after it."""
    used = width(lead) + width(prompt) + width(tail) + GUTTER
    span = W - used
    return lead + prompt + f"«uline|{rpad(f' {query}█', span)}»" + tail + " " * GUTTER


def truth(
    hints: str = "⏎ open  ^x expiry", tail: str = "", count: str = "21/21"
) -> str:
    left = f" {count}"
    gap = W - width(left) - len(hints) - GUTTER - width(tail) - (GUTTER if tail else 0)
    out = left + " " * gap + f"«dim|{hints}»" + " " * GUTTER
    return out + tail + " " * GUTTER if tail else out


def pane(chrome: list[str], docs=None) -> str:
    body = rows(docs or DOCS)
    fill = ROWS - 1 - len(body) - len(chrome)
    return block([header()] + body + ["" for _ in range(fill)] + chrome, cols=W)


# ── A · before the prompt ───────────────────────────────────────────────────
SPECIMENS["lead"] = pane([field(lead=" " + SPC, prompt="«acc|>»"), truth()])

# ── B · beside the keyboard chip ────────────────────────────────────────────
SPECIMENS["pair"] = pane([field(tail=SPC + KBD), truth()])

# ── C · the far corner, on the truth row ────────────────────────────────────
SPECIMENS["corner"] = pane([field(), truth(tail=SPC)])

# ── D · a wide slab, bottom right ───────────────────────────────────────────
SPECIMENS["slab"] = pane([field(), truth(hints="⏎ open", tail="«chip| SPC  more »")])

# ── E · one chip, replacing the keyboard hint ───────────────────────────────
SPECIMENS["swap"] = pane([field(tail=SPC), truth()])

# ── the sheet, opened from the chip ─────────────────────────────────────────
RULE = "«dim|" + "─" * (W - 2) + "»"
COLS = (16, 16, 13)


def keyline(*items: tuple[str, str]) -> str:
    out = " "
    for i, (key, label) in enumerate(items):
        out += f"«acc|{key}» {label}" + " " * (COLS[i] - 1 - len(key) - len(label))
    return out


crumb = " «acc|SPC»"
note = "type to search"
SHEET = [
    " " + RULE,
    crumb + " " * (W - width(crumb) - len(note) - GUTTER) + f"«dim|{note}»" + " ",
    keyline(("f", "filter"), ("g", "go to"), ("r", "review")),
    keyline(("s", "shelf"), ("b", "bundle"), ("e", "export")),
    keyline((":", "command"), ("?", "help"), ("q", "quit")),
]
SPECIMENS["open"] = pane(
    [
        *SHEET,
        field(lead=" " + SPC, prompt="«acc|>»", query=""),
        truth(hints="esc closes"),
    ],
    docs=DOCS[:3],
)

# ── the desktop, which gets no chip at all ──────────────────────────────────
SPECIMENS["desk"] = block(
    [header()]
    + rows(DOCS[:4])
    + [
        "",
        "",
        field(tail="«dim|21/21»"),
        " «dim|space  ⏎ open  → detail  ^x expiring»",
    ],
    cols=W,
)

render("leaderchip.src.html", SPECIMENS, "leaderchip.html")
