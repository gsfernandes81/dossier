"""Render the C+D merge candidates for the search bar.

Same discipline as ``screens.py`` and ``searchbar.py``: every line is padded to
its pane's real column count using display widths, and an over-wide line raises
rather than wrapping. Markup is «class|text».

The four candidates differ in one thing only — how strictly the two textures
(reverse video, underline) are made to mean one thing each.
"""

from __future__ import annotations

import html
import pathlib
import re

from wcwidth import wcswidth

MARK = re.compile(r"«([a-z]+)\|([^»]*)»")
PHONE = 45
DESK = 100


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
# key, label — the key is what a keyboard presses, the label what a thumb reads.
BUTTONS = [("⏎", "Open"), ("→", "Detail"), ("^x", "Expiry"), ("^t", "Scans")]
QUARTER = PHONE // 4


def buttons(style: str, active: str = "") -> str:
    """One action bar row, in one of three treatments."""
    out = []
    for key, label in BUTTONS:
        mark = "•" if label.lower().startswith(active) and active else ""
        if style == "chip":
            # The whole button reversed.
            text = f" {key} {label}{mark} "[: QUARTER - 1]
            out.append(f"«chip|{text}»" + " " * (QUARTER - wcswidth(text)))
        elif style == "cap":
            # Reverse on the key only — a cap you press, then a plain label.
            cap = f" {key} "
            out.append(f"«cap|{cap}»" + rpad(f" {label}{mark}", QUARTER - wcswidth(cap)))
        else:
            out.append(f"«dim|{rpad(f' {key} {label}{mark}', QUARTER)}»")
    return "".join(out)


def field(prompt: str, tail: str, cols: int = PHONE, query: str = QUERY) -> str:
    """The query row: prompt, then the typable span, then the tail.

    The space between the prompt and the text belongs to the *underlined* span,
    so the field reads as a box that starts right after the prompt rather than
    as a rule floating a column away from it.
    """
    typed = f" {query}█"
    span = cols - width(prompt) - width(tail)
    return prompt + f"«uline|{rpad(typed, span)}»" + tail


def info(cols: int = PHONE, hints: str = HINTS) -> str:
    left = f" {COUNT}  «dim|[scans]»"
    gap = cols - width(left) - wcswidth(hints) - 1
    return left + " " * gap + f"«dim|{hints}» "


SPECIMENS: dict[str, str] = {}

# ── M1 · two textures, strictly ─────────────────────────────────────────────
SPECIMENS["m1"] = block(
    LIST + [buttons("chip"), field("«acc| >»", "«dim| ⌨ »"), info()]
)

# ── M2 · anchored: the keyboard glyph is a button, so it is a chip ──────────
SPECIMENS["m2"] = block(
    LIST + [buttons("chip"), field("«acc| >»", "«chip| ⌨ »"), info()]
)

# ── M3 · key caps: reverse marks the key, not the whole button ─────────────
SPECIMENS["m3"] = block(
    LIST + [buttons("cap"), field("«acc| >»", "«cap| ⌨ »"), info()]
)

# ── M4 · full weight: the field is underlined *and* filled ─────────────────
SPECIMENS["m4"] = block(
    LIST
    + [
        buttons("chip"),
        "«slab| > »" + f"«slabline|{rpad(f' {QUERY}█', PHONE - 6)}»" + "«slab| ⌨ »",
        info(),
    ]
)

# ── the empty state: what launch looks like ────────────────────────────────
SPECIMENS["m3-empty"] = block(
    LIST
    + [
        buttons("cap"),
        field("«acc| >»", "«cap| ⌨ »", query=""),
        f" «dim|24/24»" + " " * (PHONE - 7 - len(HINTS) - 1) + f"«dim|{HINTS}» ",
    ]
)

# ── M3, active toggle ──────────────────────────────────────────────────────
SPECIMENS["m3-active"] = block(
    LIST + [buttons("cap", active="scans"), field("«acc| >»", "«cap| ⌨ »"), info()]
)

# ── M3 whole screen ────────────────────────────────────────────────────────
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
SPECIMENS["m3-full"] = block(
    FULL + [buttons("cap"), field("«acc| >»", "«cap| ⌨ »"), info()]
)

# ── the same rule on the keyboard layout, where there are no buttons ───────
DESK_LIST = [
    "  Ship Security Awareness            marine                     cert-file 6    02-30 ",
    "«sel|▸ COC Certificate (Master)         marine                     cert-file 8  ! 09-26 »",
    "  COC Certificate 2019                marine                     cert-file 9     ·    ",
    "  COC Endorsement — Panama            marine                    cert-file 10    12-26 ",
]
SPECIMENS["m3-desk"] = block(
    DESK_LIST
    + [
        "",
        field("«acc| >»", f"«dim|{COUNT} »", cols=DESK),
        f" «dim|{'⏎ open  → detail  ^x expiring  ^q quit'}»",
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
