"""Render the search-bar alternatives (R-UI follow-up) as one page.

Same discipline as ``screens.py``: every line is padded to the pane's real
column count using display widths, so a 45-column phone screen really is 45
columns and an over-wide line is an error rather than a wrap. Markup is
«class|text».

Self-contained on purpose — it reads ``style.css`` and writes the finished
page, because this one is a decision aid rather than part of the three-page
mockup set.
"""

from __future__ import annotations

import html
import pathlib
import re

from wcwidth import wcswidth

MARK = re.compile(r"«([a-z]+)\|([^»]*)»")
COLS = 45


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


def pad(s: str, cols: int = COLS) -> str:
    gap = cols - width(s)
    if gap < 0:
        raise ValueError(f"line is {-gap} too wide ({width(s)}/{cols}): {plain(s)!r}")
    return s + " " * gap


def rpad(text: str, cols: int) -> str:
    return text + " " * max(0, cols - wcswidth(text))


def block(lines: list[str]) -> str:
    return "\n".join(to_html(pad(line)) for line in lines)


def fill(cls: str, s: str, cols: int = COLS) -> str:
    """One span covering the whole row — a background has to be unbroken."""
    return f"«{cls}|{rpad(plain(s), cols)}»"


# ── the list rows above the chrome, identical in every specimen ──────────────
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
BUTTONS = ["⏎ Open", "→ Detail", "^x Expiry", "^t Scans"]


def buttons_plain() -> str:
    quarter = COLS // 4
    return "".join(rpad(f" {label}", quarter) for label in BUTTONS)


def buttons_chips() -> str:
    """Each label as its own reverse-video chip, with the gaps left plain."""
    quarter = COLS // 4
    out = []
    for label in BUTTONS:
        text = f" {label} "[: quarter - 1]
        out.append(f"«chip|{text}»" + " " * (quarter - wcswidth(text)))
    return "".join(out)


def info_row(cursor_pad: int = 0) -> str:
    left = f" {COUNT}  «dim|[scans]»"
    gap = COLS - width(left) - len(HINTS) - 1 - cursor_pad
    return left + " " * gap + f"«dim|{HINTS}» "


SPECIMENS: dict[str, str] = {}

# ── 0 · as it ships today ────────────────────────────────────────────────────
SPECIMENS["now"] = block(
    LIST
    + [
        f"«dim|{buttons_plain()}»",
        f" «acc|>» {QUERY}_" + " " * (COLS - 6 - len(QUERY) - 3) + "«dim|⌨ »",
        info_row(),
    ]
)

# ── A · slab ────────────────────────────────────────────────────────────────
SPECIMENS["slab"] = block(
    LIST
    + [
        f"«dim|{buttons_plain()}»",
        fill("slab", f" > {QUERY}█" + " " * (COLS - 6 - len(QUERY) - 3) + "⌨ "),
        fill("slabdim", f" {COUNT}  [scans]" + " " * (COLS - 14 - len(HINTS) - 1) + HINTS + " "),
    ]
)

# ── B · rule ────────────────────────────────────────────────────────────────
SPECIMENS["rule"] = block(
    LIST[:4]
    + [
        f"«rule|{'─' * COLS}»",
        f"«dim|{buttons_plain()}»",
        f" «acc|>» {QUERY}_" + " " * (COLS - 6 - len(QUERY) - 3) + "«dim|⌨ »",
        info_row(),
    ]
)

# ── C · field ───────────────────────────────────────────────────────────────
SPECIMENS["field"] = block(
    LIST
    + [
        f"«dim|{buttons_plain()}»",
        "«chip| > »"
        + f"«uline|{rpad(' ' + QUERY + '█', COLS - 6)}»"
        + "«dim|⌨ »",
        info_row(),
    ]
)

# ── D · chips ───────────────────────────────────────────────────────────────
SPECIMENS["chips"] = block(
    LIST
    + [
        buttons_chips(),
        fill("slab", f" > {QUERY}█" + " " * (COLS - 6 - len(QUERY) - 3) + "⌨ "),
        info_row(),
    ]
)

# ── the recommendation, whole screen + monochrome ───────────────────────────
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
    FULL
    + [
        buttons_chips(),
        fill("slab", f" > {QUERY}█" + " " * (COLS - 6 - len(QUERY) - 3) + "⌨ "),
        info_row(),
    ]
)

page = pathlib.Path("searchbar.src.html").read_text(encoding="utf-8")
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

out = pathlib.Path("searchbar.html")
out.write_text(page, encoding="utf-8")
print(f"{out}  {len(page):,} bytes")
