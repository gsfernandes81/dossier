"""Where verbs live once Find runs out of keys — drawn at the phone's real size.

Find binds no letter keys: every printable character is search text. That is the
constraint the whole question turns on, and it is why the answer has to come
from editors that solved the same problem — a surface where the keyboard is
already spoken for, and the verb set keeps growing.

Specimens are 47 columns, the width the device reports, and 24 or 45 rows.
"""

from __future__ import annotations

from grid import GUTTER, block, plain, render, rpad, width

W = 47
SHORT = 24
TALL = 45

SPECIMENS: dict[str, str] = {}

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
]


def header(mode: str = "") -> str:
    left = " «hd|dossier»" + (f"  «chip| {mode} »" if mode else "")
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


def field(query: str = "", prompt: str = "«acc| >»", tail: str = "«chip| ⌨ »") -> str:
    span = W - width(prompt) - width(tail) - GUTTER
    return prompt + f"«uline|{rpad(f' {query}█', span)}»" + tail + " " * GUTTER


def truth(count: str, chips: str = "", hints: str = "", tone: str = "dim") -> str:
    left = f" {count}" + (f"  «dim|{chips}»" if chips else "")
    if not hints:
        return rpad(left, W)
    gap = W - width(left) - len(hints) - GUTTER
    return left + " " * gap + f"«{tone}|{hints}»" + " " * GUTTER


RULE = "«dim|" + "─" * (W - 2) + "»"


def sheet(title: str, note: str, lines: list[str]) -> list[str]:
    """A which-key panel: a rule, a breadcrumb, then the keys it offers."""
    crumb = f" «acc|{title}»"
    gap = W - width(crumb) - len(note) - GUTTER
    return [" " + RULE, crumb + " " * gap + f"«dim|{note}»" + " " * GUTTER, *lines]


COLS = (16, 16, 13)


def keyline(*items: tuple[str, str]) -> str:
    out = " "
    for i, (key, label) in enumerate(items):
        out += f"«acc|{key}» {label}" + " " * (COLS[i] - 1 - len(key) - len(label))
    return out


def pane(body: list[str], total: int, chrome: list[str]) -> str:
    fill = total - 1 - len(body) - len(chrome)
    return block([header()] + body + ["" for _ in range(fill)] + chrome, cols=W)


# ── A · the leader sheet ────────────────────────────────────────────────────
SHEET = sheet(
    "SPC",
    "type to search",
    [
        keyline(("f", "filter"), ("g", "go to"), ("r", "review")),
        keyline(("s", "shelf"), ("b", "bundle"), ("e", "export")),
        keyline((":", "command"), ("?", "help"), ("q", "quit")),
    ],
)
SPECIMENS["sheet"] = pane(
    rows(DOCS[:5]), SHORT, [*SHEET, field(), truth("21/21", hints="esc closes")]
)


def toggle_line(on: bool, label: str, key: str) -> str:
    """A magit infix: a box that holds state, and the key that also flips it."""
    left = f" «chip| {'✓' if on else ' '} »  {label}"
    return (
        left
        + " " * (W - width(left) - len(key) - GUTTER)
        + f"«dim|{key}»"
        + " " * GUTTER
    )


def pick_line(label: str, chord: str, sel: bool = False) -> str:
    """A row of the command picker — the same shape as a document row."""
    body = f"  {label}"
    body += " " * (W - width(body) - len(chord) - GUTTER)
    return f"«sel|{body}{chord} »" if sel else body + f"«dim|{chord}»" + " " * GUTTER


# ── A2 · one level down, with the filters as toggles ────────────────────────
TOGGLE = sheet(
    "SPC f",
    "filter",
    [
        toggle_line(True, "expiring only", "^x"),
        toggle_line(False, "search scan text", "^t"),
        keyline(("s", "shelf…"), ("t", "tag…"), ("c", "clear")),
    ],
)
SPECIMENS["toggle"] = pane(
    rows(DOCS[:5]), SHORT, [*TOGGLE, field(), truth("21/21", hints="esc closes")]
)

# ── A3 · the sheet filtering, which is the picker ───────────────────────────
FILTERED = sheet(
    "SPC exp",
    "3 commands",
    [
        pick_line("expiring only", "SPC f x", sel=True),
        pick_line("export csv", "SPC e c"),
        pick_line("export markdown", "SPC e m"),
    ],
)
SPECIMENS["filtering"] = pane(
    rows(DOCS[:5]), SHORT, [*FILTERED, field(), truth("21/21", hints="⏎ runs it")]
)

# ── B · the command line, as it is already planned ──────────────────────────
CMDLINE = [
    " " + RULE,
    " «dim|:expiring»        show only what is expiring",
    " «dim|:export csv»      write a spreadsheet",
    " «dim|:expired»         show what has already lapsed",
]
SPECIMENS["cmdline"] = pane(
    rows(DOCS[:6]),
    SHORT,
    [*CMDLINE, field("exp", prompt="«acc| :»"), truth("", hints="⏎ runs  esc cancels")],
)

# ── C · a transient over a record ───────────────────────────────────────────
RECORD = [
    " «hd|COC Certificate (Master)»",
    "",
    " «dim|location»   cert-file 8",
    " «dim|expiry»     2026-09-28  «exp|! expired 22d»",
    " «dim|tags»       marine",
    " «dim|bundles»    us-visa · joining-2027",
    " «dim|files»      2   «acc|▸» coc-master.pdf «dim|(primary)»",
    " «dim|renews»     «acc|COC Certificate 2019»",
]
ACTIONS = sheet(
    "SPC",
    "COC Certificate (Master)",
    [
        keyline(("s", "supersede"), ("b", "bundle"), ("o", "open")),
        keyline(("r", "rename"), ("t", "tag"), ("u", "undo")),
        keyline(("d", "discard"), (":", "command"), ("?", "help")),
    ],
)
SPECIMENS["transient"] = block(
    RECORD
    + ["" for _ in range(SHORT - len(RECORD) - len(ACTIONS) - 2)]
    + [*ACTIONS, field(), truth("", hints="esc closes")],
    cols=W,
)

# ── D · modal, for comparison ───────────────────────────────────────────────
SPECIMENS["modal"] = block(
    [header("NORMAL")]
    + rows(DOCS[:8])
    + ["" for _ in range(SHORT - 1 - 16 - 2)]
    + [
        " «dim|" + rpad("f filter  s shelf  e export  i find  : cmd", W - 2) + "»",
        truth("21/21", hints="i types  esc normal"),
    ],
    cols=W,
)

# ── the recommendation, whole screen ────────────────────────────────────────
SPECIMENS["full"] = pane(
    rows(DOCS), TALL, [*SHEET, field(), truth("21/21", hints="esc closes")]
)

render("leader.src.html", SPECIMENS, "leader.html")
