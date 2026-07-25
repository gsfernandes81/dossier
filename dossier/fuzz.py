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

"""Typo-tolerant matching — one bounded-edit-distance primitive.

Shared by the ``/`` filter, ``ds open`` and ``ds ask`` so they agree on what "close
enough" means. Stdlib-only, no index (the store is hundreds of docs). **Exact
matching always wins**: callers only reach for this when an exact pass came up short,
so a forgiving hit can never displace a precise one.

Distance is restricted Damerau–Levenshtein (**OSA**): a transposition costs 1, since
phone-keyboard typos are dominated by swapped and dropped characters. The budget a
term may forgive scales with its length (:func:`budget`) — a short query never
fuzzes, so ``cat`` can't drift to ``car``.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable


def fold(text: str) -> str:
    """Casefold and strip diacritics (``résumé`` → ``resume``) for case- and
    accent-insensitive comparison."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def budget(term: str) -> int:
    """Edit distance a query term of this length may forgive: 0 for ≤4 (all signal —
    a 1-edit neighbourhood there is noise), 1 for 5–8, 2 for ≥9."""
    n = len(term)
    if n <= 4:
        return 0
    if n <= 8:
        return 1
    return 2


def distance(a: str, b: str, k: int) -> int:
    """OSA edit distance between ``a`` and ``b``, **capped**: the true distance when
    it is ≤ ``k``, else ``k + 1`` (callers only care whether it's within budget, and
    the cap lets the DP early-exit). ``k`` below 0 is treated as 0.

    Length-prefiltered (``abs(len a - len b) > k`` returns ``k + 1`` in O(1)) and
    row-early-exit (a whole DP row exceeding ``k`` means the distance does).
    """
    k = max(k, 0)
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > k:
        return k + 1
    prev2: list[int] | None = None  # row i-2, for the transposition step
    prev = list(range(lb + 1))  # row i-1
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        row_best = i
        ai = a[i - 1]
        for j in range(1, lb + 1):
            cost = 0 if ai == b[j - 1] else 1
            val = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if (
                prev2 is not None
                and i > 1
                and j > 1
                and ai == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                val = min(val, prev2[j - 2] + 1)  # adjacent transposition
            cur[j] = val
            if val < row_best:
                row_best = val
        if row_best > k:
            return k + 1
        prev2, prev = prev, cur
    return min(prev[lb], k + 1)


def within(a: str, b: str, k: int) -> bool:
    """Whether ``a`` and ``b`` are within ``k`` OSA edits (``k = 0`` is equality)."""
    return a == b if k <= 0 else distance(a, b, k) <= k


def term_matches(term: str, tokens: Iterable[str]) -> bool:
    """Whether any token is within ``term``'s length-budget of it.

    Both ``term`` and ``tokens`` are expected pre-:func:`fold`ed by the caller (the
    hot ``/`` path folds each side once, not per comparison). A budget-0 term must
    hit a token exactly.
    """
    k = budget(term)
    return any(within(term, token, k) for token in tokens)
