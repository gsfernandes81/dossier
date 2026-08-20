"""Shared helpers for rendering terminal specimens on an exact character grid.

Every page in this directory pads its lines to a real column count using display
widths, so a 45-column phone screen really is 45 columns and an over-wide line
raises rather than wrapping. That machinery was copied three times before this
module existed; new pages import it.

Markup is «class|text», the same vocabulary `style.css` styles.
"""

from __future__ import annotations

import html
import pathlib
import re

from wcwidth import wcswidth

#: A class name may carry digits after the first letter — `«b8|…»` used to
#: fall through this silently, leaving the markup in the line and blowing the
#: width check up somewhere unrelated.
MARK = re.compile(r"«([a-z][a-z0-9]*)\|([^»]*)»")

PHONE = 45
FLOOR = 38
DESK = 100

#: One blank column at each end of the chrome, and between two cells — the same
#: constant the app uses (`layout::GUTTER`).
GUTTER = 1


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


def lpad(text: str, cols: int) -> str:
    return " " * max(0, cols - wcswidth(text)) + text


def centre(text: str, cols: int) -> str:
    slack = max(0, cols - wcswidth(text))
    return " " * (slack // 2) + text + " " * (slack - slack // 2)


def block(lines: list[str], cols: int = PHONE) -> str:
    return "\n".join(to_html(pad(line, cols)) for line in lines)


def cells(cols: int, n: int) -> list[tuple[int, int]]:
    """The app's own tiling (`layout::cells`): equal cells, one-column
    separators, the whole block centred so the remainder becomes equal margins."""
    if n <= 0:
        return []
    inner = cols - 2 * GUTTER
    cell = max(1, (inner - (n - 1) * GUTTER) // n)
    used = n * cell + (n - 1) * GUTTER
    left = (cols - used) // 2
    return [(left + i * (cell + GUTTER), cell) for i in range(n)]


def bar(labels: list[str], cols: int = PHONE, style: str = "chip") -> str:
    """A row of filled cells on the tiling, each label centred in its cell."""
    out, column = "", 0
    for (start, cell), label in zip(cells(cols, len(labels)), labels, strict=True):
        out += " " * (start - column)
        out += f"«{style}|{centre(label, cell)}»"
        column = start + cell
    return out


def render(page: str, specimens: dict[str, str], out_name: str) -> None:
    """Splice specimens and the stylesheet into a source page and write it."""
    text = pathlib.Path(page).read_text(encoding="utf-8")
    text = text.replace(
        "/*CSS*/", pathlib.Path("style.css").read_text(encoding="utf-8")
    )
    missing: list[str] = []

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in specimens:
            missing.append(name)
            return ""
        return specimens[name]

    text = re.sub(r"<!--S:([a-z0-9-]+)-->", sub, text)
    if missing:
        raise SystemExit(f"{page}: unknown specimens {missing}")
    out = pathlib.Path(out_name)
    out.write_text(text, encoding="utf-8")
    print(f"{out}  {len(text):,} bytes")
