"""Splice the rendered screens and the stylesheet into each page."""

import json
import pathlib
import re

screens = json.loads(pathlib.Path("screens.json").read_text(encoding="utf-8"))
css = pathlib.Path("style.css").read_text(encoding="utf-8")

for src in sorted(pathlib.Path().glob("*.src.html")):
    text = src.read_text(encoding="utf-8")
    text = text.replace("/*CSS*/", css)

    missing = []

    def sub(m):
        name = m.group(1)
        if name not in screens:
            missing.append(name)
            return ""
        return screens[name]["html"]

    text = re.sub(r"<!--S:([a-z0-9-]+)-->", sub, text)
    if missing:
        raise SystemExit(f"{src}: unknown screens {missing}")

    out = pathlib.Path(src.name.replace(".src", ""))
    out.write_text(text, encoding="utf-8")
    print(f"{out}  {len(text):,} bytes")
