# Copyright © 2026-present gsfernandes81
#
# This file is part of "dossier".
#
# dossier is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
#
# dossier is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License along with
# dossier. If not, see <https://www.gnu.org/licenses/>.

"""Keep the docs honest.

Docs and docstrings rot silently: a feature ships but a "not built yet" line lingers,
and the next reader (human or agent) is misled. This test fails CI when a source file,
docstring, or Markdown doc contains a phrase that asserts something is *unbuilt* — the
exact staleness class that had accumulated across README/DESIGN/cli.py.

The marker list is deliberately **narrow and curated**: only phrases that make a
false status claim, never legitimate design words like "deferred" or "planned"
(``ds import`` really is deferred; Phase 8 really is planned). If a marker ever needs
to appear legitimately, add ``# docs-honesty: ok`` (or ``<!-- docs-honesty: ok -->``)
on the same line to whitelist it.
"""

from __future__ import annotations

import re
from pathlib import Path

# Phrases that assert a feature is not built. Matched case-insensitively as substrings.
# Keep this list to unambiguous *status lies* — not general roadmap vocabulary.
STALE_MARKERS = (
    "pre-implementation",
    "not built yet",
    "not yet built",
    "coming soon",
    "later slice",
)

ALLOW = "docs-honesty: ok"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_THIS_FILE = Path(__file__).resolve()


def _scanned_files() -> list[Path]:
    """All app source + Markdown docs, minus this test and generated/vendored trees."""
    files: list[Path] = []
    for pattern in ("dossier/**/*.py", "*.md", "docs/**/*.md"):
        files.extend(_REPO_ROOT.glob(pattern))
    skip_dirs = {".git", ".claude", "__pycache__", ".venv"}
    kept = []
    for f in files:
        if f.resolve() == _THIS_FILE:
            continue
        if skip_dirs & set(f.relative_to(_REPO_ROOT).parts):
            continue
        kept.append(f)
    return sorted(kept)


def test_no_stale_status_markers() -> None:
    offenders: list[str] = []
    for path in _scanned_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if ALLOW in line:
                continue
            low = line.lower()
            for marker in STALE_MARKERS:
                if marker in low:
                    rel = path.relative_to(_REPO_ROOT).as_posix()
                    text = line.strip()
                    offenders.append(f"{rel}:{lineno}: {text!r} (marker: {marker!r})")

    assert not offenders, (
        "Stale status markers found — a doc/docstring claims something is unbuilt. "
        "Fix the wording (the feature likely shipped), or append '# docs-honesty: ok' "
        "if the phrase is genuinely correct:\n  " + "\n  ".join(offenders)
    )


def test_marker_detection_actually_works() -> None:
    """Guard against the scanner silently matching nothing (e.g. a glob regression)."""
    pattern = "|".join(re.escape(m) for m in STALE_MARKERS)
    assert re.search(pattern, "this is coming soon")
    assert len(_scanned_files()) > 10
