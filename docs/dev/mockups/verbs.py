"""The Find surface's verb inventory, drawn where it needs evidence.

Two specimens only: the places where the audit's findings are visible on screen
rather than merely arguable. Both are reproductions of what the shipped binary
actually renders — checked against it in a PTY, not imagined. The pane is drawn
at the 45×28 mockup size; the device reports 47×45 browsing and 47×24 typing.
"""

from __future__ import annotations

from grid import PHONE, bar, block, render, rpad, width, GUTTER

SPECIMENS: dict[str, str] = {}

LIST = [
    "  COC Certificate (Master)           «soon|~ 09-26» ",
    "«dim|    cert-file 8 · marine»",
]


def field(query: str = "") -> str:
    prompt = "«acc| >»"
    tail = "«chip| ⌨ »"
    span = PHONE - width(prompt) - width(tail) - GUTTER
    return prompt + f"«uline|{rpad(f' {query}█', span)}»" + tail + " " * GUTTER


def info(left: str, hint: str) -> str:
    gap = PHONE - width(left) - width(hint) - GUTTER
    return left + " " * gap + hint + " " * GUTTER


BAR = bar(["→ Detail", "^x Expiry", "^t Scans•"])

# 1 · the hint line survives one chip …
SPECIMENS["hint-one"] = block(
    LIST + [BAR, field(), info(" 14/24  «dim|[expiring]»", "«dim|⏎ open  esc back  ^q quit»")]
)

# … and disappears at two. Verified in a PTY: with both filters on the whole
# hint is dropped rather than shortened, exactly when the most state is live.
SPECIMENS["hint-none"] = block(
    LIST + [BAR, field(), info(" 14/24  «dim|[expiring]  [scans]»", "")]
)

# 2 · one fact, two conventions, two rows apart.
SPECIMENS["twice"] = block(
    LIST + [BAR, field("coc"), info(" 13/24  «dim|[expiring]  [scans]»", "")]
)

render("verbs.src.html", SPECIMENS, "verbs.html")
