"""Render dossier v3 TUI mockups on an exact character grid.

Every line is padded to the pane's real column count using display widths, so a
45-column phone screen really is 45 columns. Markup is «class|text».
"""

from __future__ import annotations

import html
import json
import re
from wcwidth import wcswidth

MARK = re.compile(r"«([a-z]+)\|([^»]*)»")


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


def trunc(text: str, cols: int) -> str:
    if wcswidth(text) <= cols:
        return text
    out, used = "", 0
    for ch in text:
        w = wcswidth(ch)
        if used + w > cols - 1:
            break
        out += ch
        used += w
    return out + "…"


def rpad(text: str, cols: int) -> str:
    return trunc(text, cols) + " " * max(0, cols - wcswidth(trunc(text, cols)))


def lpad(text: str, cols: int) -> str:
    return " " * max(0, cols - wcswidth(trunc(text, cols))) + trunc(text, cols)


def screen(cols: int, rows: int, lines: list[str]) -> dict:
    if len(lines) > rows:
        raise ValueError(f"{len(lines)} lines > {rows} rows")
    filled = lines + [""] * (rows - len(lines))
    return {
        "cols": cols,
        "rows": rows,
        "html": "\n".join(to_html(pad(line, cols)) for line in filled),
    }


# ── content ───────────────────────────────────────────────────────────────────
# Real documents: marine certificates, motorcycle papers, identity.
DOCS = [
    ("COC Certificate (Master)", "cert-file 8", "marine", "exp", "! 09-26"),
    ("ENG-1 Medical", "cert-file 3", "marine", "soon", "~ 01-27"),
    ("STCW Basic Safety Training", "cert-file 4", "marine", "ok", "  05-31"),
    ("Advanced Fire Fighting", "cert-file 5", "marine", "ok", "  11-29"),
    ("Ship Security Awareness", "cert-file 6", "marine", "ok", "  02-30"),
    ("Sea Service Testimonial 2024", "softcopy", "marine", "none", "   ·   "),
    ("Passport (IN)", "passport-pouch 1", "identity travel", "ok", "  05-31"),
    ("Seaman Book (CDC)", "passport-pouch 2", "marine identity", "soon", "~ 03-27"),
    ("Yellow Fever Card", "passport-pouch 3", "travel", "none", "   ·   "),
    ("Motorcycle Insurance", "blue-folder 1", "motorcycle", "exp", "! 07-26"),
    ("RC Book — Himalayan 450", "blue-folder 2", "motorcycle", "none", "   ·   "),
    ("Driving Licence", "blue-folder 3", "identity", "ok", "  08-33"),
    ("PAN Card", "file-4096 12", "identity financial", "none", "   ·   "),
    ("Degree Certificate", "file-4096 14", "education", "none", "   ·   "),
]

PHONE = (45, 28)
DESK = (100, 26)


def phone_header(counts: str = "! 3 exp · 612 unfiled") -> str:
    return " «hd|dossier»" + lpad(counts, 45 - 8 - 1) + " "


def phone_rows(docs, selected=0, start_row=0) -> list[str]:
    """Two-line rows — the < 70-column layout."""
    out = []
    for i, (name, place, tags, kind, status) in enumerate(docs):
        cur = "▸ " if i == selected else "  "
        line1 = cur + rpad(name, 35) + f"«{kind}|{status}»"
        line2 = "    " + f"«dim|{rpad(place + ' · ' + tags, 41)}»"
        if i == selected:
            out.append(f"«sel|{plain(line1)}»")
            out.append(f"«seldim|{plain(line2)}»")
        else:
            out.append(line1)
            out.append(line2)
    return out


def phone_chrome(query: str, count: str, hints: str) -> list[str]:
    return [
        " «btn|⏎ Open»    «btn|→ Detail»   «btn|: Cmds»    «btn|⌨ Keys»",
        " «acc|>» " + rpad(query + "_", 45 - 3 - len(count) - 1) + f"«dim|{count}»",
        f" «dim|{rpad(hints, 44)}»",
    ]


SCREENS: dict[str, dict] = {}

# 1 ─ Find, phone, no query ────────────────────────────────────────────────────
lines = [phone_header()]
lines += phone_rows(DOCS[:12], selected=0)
lines += phone_chrome("", "948/948", "⏎ open  → detail  : cmds  ? help")
SCREENS["find-phone"] = screen(*PHONE, lines)

# 2 ─ Find, phone, typed ──────────────────────────────────────────────────────
MATCHES = [
    ("COC Certificate (Master)", "cert-file 8", "marine", "exp", "! 09-26"),
    ("COC Certificate 2019 (superseded)", "cert-file 9", "marine", "none", "   ·   "),
    ("COC Endorsement — Panama", "cert-file 10", "marine", "soon", "~ 12-26"),
    ("COC Application Receipt", "softcopy", "marine", "none", "   ·   "),
]
lines = [phone_header()]
lines += phone_rows(MATCHES, selected=0)
lines += [""] * (24 - len(MATCHES) * 2)
lines += phone_chrome("coc", "4/948", "⏎ open  → detail  : cmds  ? help")
SCREENS["find-phone-typed"] = screen(*PHONE, lines)

# 3 ─ Detail, phone (full-screen push) ────────────────────────────────────────
lines = [
    " «hd|COC Certificate (Master)»",
    "",
    " «dim|location»   cert-file 8",
    " «dim|expiry»     2026-09-28  «exp|! expired 22d»",
    " «dim|issued»     2021-09-29",
    " «dim|tags»       marine",
    " «dim|bundles»    us-visa · joining-2027",
    " «dim|files»      2   «acc|▸» coc-master.pdf «dim|(primary)»",
    "                  coc-master-back.jpg",
    " «dim|renews»     «acc|COC Certificate 2019»",
    " «dim|notes»      Revalidation booked at MMD,",
    "            slot 14 Oct. Bring originals",
    "            + 2 photos.",
    "",
    " «dim|───────────────────────────────────────────»",
    " «warn|!» «dim|expired — renewal not yet filed»",
    "",
    " «acc|s» supersede  «acc|b» bundle  «acc|u» undo",
    "",
    "",
    "",
    "",
    "",
    "",
    "",
    " «btn|⏎ Open»    «btn|← Back»     «btn|: Cmds»    «btn|⌨ Keys»",
    " «dim|editing: tab moves field · enter saves»",
    " «dim|esc back to the list · ← close detail»",
]
SCREENS["detail-phone"] = screen(*PHONE, lines)


# 4 ─ Find, desktop (single-line rows) ────────────────────────────────────────
def desk_header(cols: int) -> str:
    left = " «hd|dossier»"
    right = "«exp|! 3 expiring» «dim|·» «acc|612 unfiled» «dim|· 948 docs» "
    return left + " " * (cols - width(left) - width(right)) + right


def desk_rows(docs, selected=0) -> list[str]:
    out = []
    for i, (name, place, tags, kind, status) in enumerate(docs):
        cur = "▸ " if i == selected else "  "
        body = (
            cur
            + rpad(name, 46)
            + f"«dim|{rpad(tags, 20)}»"
            + f"«dim|{lpad(place, 18)}»"
            + "  "
            + f"«{kind}|{status}»"
        )
        out.append(f"«sel|{plain(body)}»" if i == selected else body)
    return out


lines = [desk_header(100), ""]
lines += desk_rows(DOCS, selected=0)
lines += ["", ""]
lines += [
    " «acc|>» " + rpad("_", 100 - 3 - 8 - 1) + "«dim|948/948»",
    " «dim|" + rpad("⏎ open   → detail   : commands   ctrl+x expiring   ? help", 99) + "»",
]
SCREENS["find-desktop"] = screen(*DESK, lines)

# 5 ─ Split: list + detail (≥ 100 cols) ───────────────────────────────────────
RULE = "«dim|│»"
left_rows = desk_rows(DOCS[:14], selected=0)


def split_line(left: str, right: str) -> str:
    return rpad(plain(left), 0) and left  # placeholder, replaced below


detail_pane = [
    "«hd|COC Certificate (Master)»",
    "",
    "«dim|location»  cert-file 8",
    "«dim|expiry»    2026-09-28  «exp|! expired»",
    "«dim|issued»    2021-09-29",
    "«dim|tags»      marine",
    "«dim|bundles»   us-visa · joining-2027",
    "«dim|files»     2  «acc|▸» coc-master.pdf",
    "                   coc-master-back.jpg",
    "«dim|renews»    «acc|COC Certificate 2019»",
    "«dim|notes»     Revalidation booked at MMD",
    "                Mumbai, slot 14 Oct. Bring",
    "                originals + 2 photos.",
    "",
    "«acc|s» supersede  «acc|b» bundle  «acc|u» undo",
]

LEFT_W = 54


def narrow_rows(docs, selected=0):
    """Left pane of the split: still single-line, just fewer columns."""
    out = []
    for i, (name, place, tags, kind, status) in enumerate(docs):
        cur = "▸ " if i == selected else "  "
        body = cur + rpad(name, 30) + "  " + f"«dim|{lpad(place, 12)}»" + " " + f"«{kind}|{status}»"
        out.append(f"«sel|{plain(body)}»" if i == selected else body)
    return out


left_rows = narrow_rows(DOCS[:14], selected=0)
lines = [desk_header(100), ""]
for i in range(14):
    left = left_rows[i] if i < len(left_rows) else ""
    right = detail_pane[i] if i < len(detail_pane) else ""
    left_padded = left + " " * (LEFT_W - width(left))
    lines.append(left_padded + RULE + " " + right)
lines += ["", ""]
lines += [
    " «acc|>» " + rpad("_", LEFT_W - 4) + RULE + " «dim|esc or ← closes detail»",
    " «dim|" + rpad("⏎ open   → detail   : commands   ? help", LEFT_W - 2) + "»" + RULE,
]
SCREENS["split-desktop"] = screen(*DESK, lines)

# 6 ─ Review queue, phone ─────────────────────────────────────────────────────
lines = [
    " «hd|review»" + lpad("«dim|:review»", 45 - 8 - 9) + "  ",
    " «sel|orphans 12»«dim| missing 3  dupes 5  succ 2  ✓»",
    "",
    " «dim|unfiled files with no document record»",
    "",
    "«sel|▸ Inbox/IMG_20260814_101233.jpg          »",
    "«seldim|    2.4 MB · dropped 2 days ago         »",
    "  Inbox/IMG_20260814_101301.jpg",
    "«dim|    2.1 MB · dropped 2 days ago»",
    "  Inbox/scan-mmd-receipt.pdf",
    "«dim|    488 KB · dropped 5 days ago»",
    "  Marine/coc-endorsement-pan.pdf",
    "«dim|    1.1 MB · in scope, never filed»",
    "  Marine/eng1-2026.pdf",
    "«dim|    720 KB · in scope, never filed»",
    "",
    " «dim|───────────────────────────────────────────»",
    " «acc|f» file it   «acc|d» dismiss   «acc|h» restore",
    "",
    "",
    "",
    "",
    "",
    "",
    " «btn|⏎ Open»    «btn|→ Detail»   «btn|: Cmds»    «btn|⌨ Keys»",
    " «acc|>» " + rpad("_", 45 - 3 - 6 - 1) + "«dim|12/12»",
    " «dim|" + rpad("f file  d dismiss  [ ] tabs  esc back", 44) + "»",
]
SCREENS["review-phone"] = screen(*PHONE, lines)

# 7 ─ ds file — the filing card ───────────────────────────────────────────────
lines = [
    " «hd|file»" + lpad("«dim|612 unfiled  ·  :file»", 45 - 6 - 22) + "  ",
    "",
    " «dim|Inbox/IMG_20260814_101233.jpg»",
    " «dim|2.4 MB · JPEG · dropped 2 days ago»",
    "",
    " «dim|the desktop read this scan:»",
    "",
    "  name      «acc|ENG-1 Medical Certificate»",
    "  type      «acc|medical certificate»",
    "  issued    «acc|2026-01-14»",
    "  expires   «acc|2027-01-13»",
    "  «dim|confidence 0.92»",
    "",
    " «dim|it looks like a renewal of»",
    "  «warn|ENG-1 Medical» «dim|(cert-file 3, expires 01-27)»",
    "",
    " «dim|───────────────────────────────────────────»",
    " «acc|a» accept    «acc|e» edit first    «acc|s» skip",
    " «acc|n» not a document",
    "",
    "",
    "",
    "",
    "",
    " «btn|a Accept»  «btn|e Edit»     «btn|s Skip»    «btn|⌨ Keys»",
    " «acc|>» " + rpad("_", 45 - 3 - 9 - 1) + "«dim|1/612»",
    " «dim|" + rpad("a accept  e edit  s skip  esc back", 44) + "»",
]
SCREENS["file-phone"] = screen(*PHONE, lines)

# 8 ─ Expiring filter, phone ──────────────────────────────────────────────────
EXPIRING = [
    ("COC Certificate (Master)", "cert-file 8", "expired 22 days ago", "exp", "! 09-26"),
    ("Motorcycle Insurance", "blue-folder 1", "expired 40 days ago", "exp", "! 07-26"),
    ("ENG-1 Medical", "cert-file 3", "in 5 months", "soon", "~ 01-27"),
    ("Seaman Book (CDC)", "passport-pouch 2", "in 7 months", "soon", "~ 03-27"),
    ("COC Endorsement — Panama", "cert-file 10", "in 4 months", "soon", "~ 12-26"),
]
lines = [
    " «hd|expiring»" + lpad("«dim|18 tracked · «exp|2 red»", 45 - 10 - 13) + "  ",
    "",
]
for i, (name, place, when, kind, status) in enumerate(EXPIRING):
    cur = "▸ " if i == 0 else "  "
    l1 = cur + rpad(name, 35) + f"«{kind}|{status}»"
    l2 = "    " + f"«dim|{rpad(place + ' · ' + when, 41)}»"
    lines += [f"«sel|{plain(l1)}»", f"«seldim|{plain(l2)}»"] if i == 0 else [l1, l2]
lines += [
    "",
    " «dim|───────────────────────────────────────────»",
    " «dim|superseded and ignored docs are hidden»",
    "",
    "",
    "",
    "",
    "",
    "",
    " «btn|⏎ Open»    «btn|→ Detail»   «btn|: Cmds»    «btn|⌨ Keys»",
    " «acc|>» «dim|expiring» " + rpad("_", 45 - 3 - 9 - 6 - 1) + "«dim|5/948»",
    " «dim|" + rpad("⏎ open  → detail  esc clears the filter", 44) + "»",
]
SCREENS["expiring-phone"] = screen(*PHONE, lines)

# 9 ─ Command mode ────────────────────────────────────────────────────────────
lines = [phone_header()]
lines += phone_rows(DOCS[:8], selected=0)
lines += [
    "",
    " «dim|──────────────────────────────────────»",
    " «sel|:review»«dim|      the five review tabs      »",
    " «acc|:reset»«dim|       clear the store          »",
    " «acc|:rename»«dim|      rename a bundle          »",
    "",
    " «btn|⏎ Run»      «btn|↑↓ Pick»    «btn|esc Back»  «btn|⌨ Keys»",
    " «acc|:» " + rpad("re_", 45 - 3 - 8 - 1) + "«dim|3 matches»",
    " «dim|" + rpad("⏎ run  ↑↓ pick  esc cancels", 44) + "»",
]
SCREENS["command-phone"] = screen(*PHONE, lines)

# 10 ─ Bundles, desktop ───────────────────────────────────────────────────────
BUNDLES = [
    ("us-visa", "US Visa interview", "2027-03-01", "9 docs", "«soon|2 expiring»"),
    ("joining-2027", "Joining — MV Kestrel", "2027-01-20", "14 docs", "«exp|1 expired»"),
    ("mmd-revalidation", "MMD revalidation", "2026-10-14", "6 docs", "«dim|ready»"),
    ("bike-transfer", "Bike ownership transfer", "—", "4 docs", "«dim|ready»"),
]
lines = [
    " «hd|bundles»"
    + " " * (100 - 9 - 22)
    + "«dim|:bundles · dated order» ",
    "",
]
for i, (slug, title, date, count, state) in enumerate(BUNDLES):
    cur = "▸ " if i == 0 else "  "
    body = (
        cur
        + rpad(title, 34)
        + f"«dim|{rpad(slug, 20)}»"
        + f"«dim|{rpad(date, 14)}»"
        + f"«dim|{rpad(count, 10)}»"
        + state
    )
    lines.append(f"«sel|{plain(body)}»" if i == 0 else body)
lines += [
    "",
    " «dim|" + rpad("⏎ scopes the Find list to this bundle · e exports it with a manifest", 98) + "»",
    "",
    " «dim|─────────────────────────────────────────────────────────────────────────────────────────────»",
    "",
    " «hd|us-visa» «dim|— 9 documents, 2 expiring before 2027-03-01»",
    "",
    "   Passport (IN)                 «dim|passport-pouch 1»      «ok|  05-31»",
    "   COC Certificate (Master)      «dim|cert-file 8     »      «exp|! 09-26»",
    "   ENG-1 Medical                 «dim|cert-file 3     »      «soon|~ 01-27»",
    "   Sea Service Testimonial 2024  «dim|softcopy        »      «dim|   ·   »",
    "   «dim|… 5 more»",
    "",
    "",
    " «acc|>» " + rpad("_", 100 - 3 - 5 - 1) + "«dim|4/4»",
    " «dim|" + rpad("⏎ scope   e export   → detail   esc back", 99) + "»",
]
SCREENS["bundles-desktop"] = screen(100, 24, lines)

# 11 ─ Too-small notice ───────────────────────────────────────────────────────
lines = [
    "",
    "",
    "  «warn|terminal too small»",
    "",
    "  «dim|need at least 38×12»",
    "  «dim|have 30×10»",
    "",
    "  «dim|rotate, or shrink the font»",
    "",
    "",
]
SCREENS["too-small"] = screen(30, 10, lines)

# 12 ─ status, desktop ────────────────────────────────────────────────────────
lines = [
    " «hd|ds status»",
    "",
    " «exp|!» 2 documents expired            «dim|ds status --days 0   ·   :expiring»",
    " «soon|~» 3 expire within 9 months       «dim|:expiring»",
    " «acc|·» 612 unfiled files               «dim|ds file»",
    " «acc|·» 3 documents missing their file  «dim|:review → missing»",
    " «acc|·» 5 duplicate clusters            «dim|:review → duplicates»",
    "",
    " «ok|✓» syncthing reachable · folder shared · versioning on",
    " «ok|✓» journal healthy — 4 writers, 18,204 ops, no anomalies",
    "",
    " «dim|nothing else needs you.»",
]
SCREENS["status-desktop"] = screen(84, 13, lines)

with open("screens.json", "w", encoding="utf-8") as fh:
    json.dump(SCREENS, fh, ensure_ascii=False, indent=1)

print(f"{len(SCREENS)} screens rendered")
for name, s in SCREENS.items():
    print(f"  {name:22} {s['cols']}×{s['rows']}")
