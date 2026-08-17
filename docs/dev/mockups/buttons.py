"""What the three touch buttons should say: keys, glyphs, or words.

Same discipline as ``screens.py``: every line is padded to its pane's real column
count using display widths, and an over-wide line raises rather than wrapping.
Markup is «class|text».

The tiling is the one the app now uses — ``n`` equal cells, one-column
separators, the whole block centred so any remainder becomes equal margins. With
`⏎ Open` dropped the row is three cells, which at 45 columns makes each one 13
wide: enough that a bare word fits with room around it, which is what puts
"label only" in the running at all.
"""

from __future__ import annotations

import html
import pathlib
import re

from wcwidth import wcswidth

MARK = re.compile(r"«([a-z]+)\|([^»]*)»")
PHONE = 45
FLOOR = 38
GUTTER = 1
ACTIONS = 3


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
    slack = max(0, cols - wcswidth(text))
    return " " * (slack // 2) + text + " " * (slack - slack // 2)


def block(lines: list[str], cols: int = PHONE) -> str:
    return "\n".join(to_html(pad(line, cols)) for line in lines)


def cells(cols: int, n: int = ACTIONS) -> list[tuple[int, int]]:
    """The app's own tiling, in Python — equal cells, centred block."""
    inner = cols - 2 * GUTTER
    cell = max(1, (inner - (n - 1) * GUTTER) // n)
    used = n * cell + (n - 1) * GUTTER
    left = (cols - used) // 2
    return [(left + i * (cell + GUTTER), cell) for i in range(n)]


def bar(labels: list[str], cols: int = PHONE) -> str:
    out, column = "", 0
    for (start, cell), label in zip(cells(cols), labels):
        out += " " * (start - column)
        out += f"«chip|{centre(label, cell)}»"
        column = start + cell
    return out


# ── shared content ───────────────────────────────────────────────────────────
LIST = [
    "  COC Certificate (Master)           «soon|~ 09-26» ",
    "«dim|    cert-file 8 · marine»",
    "  COC Certificate 2019                  ·    ",
    "«dim|    cert-file 9 · marine»",
]

QUERY = "coc"
HINTS_KEYS = "⏎ open  esc back  ^q quit"
HINTS_FULL = "⏎ open  → detail  ^x scans  ^q quit"


def field(cols: int = PHONE, query: str = QUERY) -> str:
    prompt = "«acc| >»"
    tail = "«chip| ⌨ »" + " " * GUTTER
    span = cols - width(prompt) - width(tail)
    return prompt + f"«uline|{rpad(f' {query}█', span)}»" + tail


def info(hints: str, count: str = "3/24", cols: int = PHONE) -> str:
    left = f" {count}"
    gap = cols - width(left) - wcswidth(hints) - GUTTER
    return left + " " * gap + f"«dim|{hints}»" + " " * GUTTER


SPECIMENS: dict[str, str] = {}

SETS = {
    # As it ships: the key on the button, the label beside it.
    "keys": (["→ Detail", "^x Expiry", "^t Scans"], HINTS_KEYS),
    # A touch-shaped mark instead of the key; the keys move to the hint line.
    "glyphs": (["› Detail", "! Expiry", "⌕ Scans"], HINTS_FULL),
    # Nothing but the word. The cell is 13 wide; the words are 5 to 6.
    "words": (["Detail", "Expiry", "Scans"], HINTS_FULL),
    # The mark alone.
    "marks": (["›", "!", "⌕"], HINTS_FULL),
}

for name, (labels, hints) in SETS.items():
    SPECIMENS[name] = block(LIST + [bar(labels), field(), info(hints)])

# The recommendation at the floor, where the cells are 11 wide instead of 13.
SPECIMENS["words-floor"] = block(
    [
        "  COC Certificate (Maste…  «soon|~ 09-26» ",
        "«dim|    cert-file 8 · marine»",
        "  COC Certificate 2019       ·    ",
        "«dim|    cert-file 9 · marine»",
        bar(["Detail", "Expiry", "Scans"], FLOOR),
        field(FLOOR),
        info("⏎ open  ^q quit", cols=FLOOR),
    ],
    cols=FLOOR,
)

# The recommendation, whole screen.
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
SPECIMENS["words-full"] = block(
    FULL + [bar(["Detail", "Expiry", "Scans"]), field(), info(HINTS_FULL)]
)

page = pathlib.Path("buttons.src.html").read_text(encoding="utf-8")
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

out = pathlib.Path("buttons.html")
out.write_text(page, encoding="utf-8")
print(f"{out}  {len(page):,} bytes")
