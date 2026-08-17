"""Render the merged search bar — one geometry, three ways to express it.

Same discipline as ``screens.py``: every line is padded to its pane's real
column count using display widths, and an over-wide line raises rather than
wrapping. Markup is «class|text».

The geometry is the point of this page. A row of four buttons on a 45-column
screen has three separate ways to look uneven, and all three are arithmetic:

* 45 does not divide by 4, so equal quarters leave a stray column;
* the keys are not the same width (``⏎`` and ``→`` are one cell, ``^x`` and
  ``^t`` are two), so content anchored after the key starts in four different
  places;
* labels differ in length, so centring them scatters the anchors instead.

The fix is one tiling — ``1 + (10 + 1) × 4 = 45`` — with the key cap padded to a
common width and **left-anchored in its cell**, so the four caps land on a fixed
rhythm and the ragged ends fall in the gutters where nothing shows.
"""

from __future__ import annotations

import html
import pathlib
import re

from wcwidth import wcswidth

MARK = re.compile(r"«([a-z]+)\|([^»]*)»")
PHONE = 45
DESK = 100

# The tiling: a one-column gutter, then four cells with a one-column separator
# between and after each. 1 + (10 + 1) * 4 = 45, exactly.
GUTTER = 1
CELL = 10
SEP = 1
CELLS = 4


def plain(s: str) -> str:
    return MARK.sub(lambda m: m.group(2), s)


def width(s: str) -> int:
    w = wcswidth(plain(s))
    if w < 0:
        raise ValueError(f"unprintable: {s!r}")
    return w


def to_html(s: str) -> str:
    out, pos = [], 0
    for m in MARK.finditer(s):
        out.append(html.escape(s[pos : m.start()]))
        out.append(f'<span class="{m.group(1)}">{html.escape(m.group(2))}</span>')
        pos = m.end()
    out.append(html.escape(s[pos:]))
    return "".join(out)


def pad(s: str, cols: int) -> str:
    gap = cols - width(s)
    if gap < 0:
        raise ValueError(f"line is {-gap} too wide ({width(s)}/{cols}): {plain(s)!r}")
    return s + " " * gap


def rpad(text: str, cols: int) -> str:
    return text + " " * max(0, cols - wcswidth(text))


def centre(text: str, cols: int) -> str:
    left = max(0, (cols - wcswidth(text)) // 2)
    return rpad(" " * left + text, cols)


def block(lines: list[str], cols: int = PHONE) -> str:
    return "\n".join(to_html(pad(line, cols)) for line in lines)


# ── shared content ───────────────────────────────────────────────────────────
LIST = [
    "  Ship Security Awareness              02-30 ",
    "«dim|    cert-file 6 · marine»",
    "  COC Certificate (Master)           «soon|~ 09-26» ",
    "«dim|    cert-file 8 · marine»",
    "  COC Certificate 2019                  ·    ",
    "«dim|    cert-file 9 · marine»",
]

QUERY = "coc"
COUNT = "3/24"
HINTS = "esc back  ^q quit"
# key, label. Every key is padded to two cells so the caps are all four wide and
# land on the same rhythm — the single change that fixes the ragged row.
BUTTONS = [("⏎", "Open"), ("→", "Detail"), ("^x", "Expiry"), ("^t", "Scans")]
KEY_COLS = 2


def lpad(text: str, cols: int) -> str:
    return " " * max(0, cols - wcswidth(text)) + text


def cap(key: str) -> str:
    """A key cap of a fixed four columns, with the key **right-aligned** in its
    two-cell slot — so a one-cell `⏎` and a two-cell `^x` both end on the same
    column and the label after them starts on the same one."""
    return f" {lpad(key, KEY_COLS)} "


def old_row() -> str:
    """The row as it ships: equal quarters, content left-aligned, keys of two
    different widths — three sources of unevenness in one line."""
    quarter = PHONE // CELLS
    return "".join(
        f"«dim|{rpad(f' {key} {label}', quarter)}»" for key, label in BUTTONS
    )


def old_ruler() -> str:
    """Where the labels actually start under the old row."""
    quarter = PHONE // CELLS
    marks = ""
    for key, _ in BUTTONS:
        offset = 1 + wcswidth(key) + 1
        marks += " " * offset + "▲" + " " * (quarter - offset - 1)
    return f"«rule|{rpad(marks, PHONE)}»"


def new_ruler() -> str:
    """Where the caps start under the new tiling: a fixed rhythm."""
    marks = " " * GUTTER
    for _ in BUTTONS:
        marks += "▲" + " " * (CELL - 1) + " " * SEP
    return f"«rule|{rpad(marks, PHONE)}»"


def row(style: str, active: str = "", divider: bool = False) -> str:
    """One action bar row on the fixed tiling."""
    out = " " * GUTTER
    for i, (key, label) in enumerate(BUTTONS):
        mark = "•" if active and label.lower().startswith(active) else ""
        if style == "chip":
            # The chip *is* the cell, so its content is centred inside it.
            out += f"«chip|{centre(f'{key} {label}{mark}', CELL)}»"
        else:
            # Cap left-anchored at the cell edge, label straight after it.
            out += f"«cap|{cap(key)}»" + rpad(f"{label}{mark}", CELL - width(cap(key)))
        # Separators go *between* cells; the last one would be a rule against
        # the screen edge, which reads as a border rather than a division.
        last = i == len(BUTTONS) - 1
        out += "«rule|│»" if divider and not last else " " * SEP
    return out


def field(prompt: str, tail: str, cols: int = PHONE, query: str = QUERY) -> str:
    """The query row: prompt, the typable span, then the tail.

    The space after the prompt belongs to the *underlined* span, so the field
    reads as a box starting right after the prompt rather than a rule floating a
    column away from it. Both ends sit on the same one-column gutter as the
    buttons above.
    """
    typed = f" {query}█"
    span = cols - width(prompt) - width(tail)
    return prompt + f"«uline|{rpad(typed, span)}»" + tail


def info(cols: int = PHONE, hints: str = HINTS, count: str = COUNT, chip: bool = True) -> str:
    left = f" {count}" + ("  «dim|[scans]»" if chip else "")
    gap = cols - width(left) - wcswidth(hints) - GUTTER
    return left + " " * gap + f"«dim|{hints}»" + " " * GUTTER


SPECIMENS: dict[str, str] = {}

# ── the diagnosis ───────────────────────────────────────────────────────────
SPECIMENS["before"] = block(
    LIST[2:]
    + [
        old_row(),
        old_ruler(),
        field("«acc| >»", "«dim| ⌨ »" + " " * GUTTER),
        info(),
    ]
)

SPECIMENS["after"] = block(
    LIST[2:]
    + [
        row("cap"),
        new_ruler(),
        field("«acc| >»", "«cap| ⌨ »" + " " * GUTTER),
        info(),
    ]
)

# ── three expressions of the same tiling ────────────────────────────────────
SPECIMENS["air"] = block(
    LIST + [row("cap"), field("«acc| >»", "«cap| ⌨ »" + " " * GUTTER), info()]
)

SPECIMENS["divider"] = block(
    LIST + [row("cap", divider=True), field("«acc| >»", "«cap| ⌨ »" + " " * GUTTER), info()]
)

SPECIMENS["filled"] = block(
    LIST + [row("chip"), field("«acc| >»", "«chip| ⌨ »" + " " * GUTTER), info()]
)

# ── the chosen one, in the states that matter ───────────────────────────────
SPECIMENS["empty"] = block(
    LIST
    + [
        row("cap"),
        field("«acc| >»", "«cap| ⌨ »" + " " * GUTTER, query=""),
        info(count="24/24", chip=False),
    ]
)

SPECIMENS["active"] = block(
    LIST + [row("cap", active="scans"), field("«acc| >»", "«cap| ⌨ »" + " " * GUTTER), info()]
)

FULL = [
    " «hd|dossier»                   «acc|! 3 exp · 24 docs» ",
    "«sel|▸ COC Certificate (Master)           ! 09-26 »",
    "«seldim|    cert-file 8 · marine                     »",
    "  COC Certificate 2019                  ·    ",
    "«dim|    cert-file 9 · marine»",
    "  COC Endorsement — Panama             12-26 ",
    "«dim|    cert-file 10 · marine»",
    "  COC Application Receipt               ·    ",
    "«dim|    softcopy · marine»",
]
FULL += ["" for _ in range(25 - len(FULL))]
SPECIMENS["full"] = block(
    FULL + [row("cap"), field("«acc| >»", "«cap| ⌨ »" + " " * GUTTER), info()]
)

# ── the same rule where there are no buttons at all ─────────────────────────
DESK_LIST = [
    "  Ship Security Awareness            marine                     cert-file 6    02-30 ",
    "«sel|▸ COC Certificate (Master)         marine                     cert-file 8  ! 09-26 »",
    "  COC Certificate 2019                marine                     cert-file 9     ·    ",
    "  COC Endorsement — Panama            marine                    cert-file 10    12-26 ",
]
SPECIMENS["desk"] = block(
    DESK_LIST
    + [
        "",
        field("«acc| >»", f"«dim|{COUNT} »", cols=DESK),
        " «dim|⏎ open  → detail  ^x expiring  ^q quit»",
    ],
    cols=DESK,
)

page = pathlib.Path("searchbar-merge.src.html").read_text(encoding="utf-8")
page = page.replace("/*CSS*/", pathlib.Path("style.css").read_text(encoding="utf-8"))

missing: list[str] = []


def sub(m: re.Match[str]) -> str:
    name = m.group(1)
    if name not in SPECIMENS:
        missing.append(name)
        return ""
    return SPECIMENS[name]


page = re.sub(r"<!--S:([a-z0-9-]+)-->", sub, page)
if missing:
    raise SystemExit(f"unknown specimens: {missing}")

out = pathlib.Path("searchbar-merge.html")
out.write_text(page, encoding="utf-8")
print(f"{out}  {len(page):,} bytes")
