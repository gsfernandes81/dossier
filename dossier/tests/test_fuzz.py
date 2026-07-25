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

"""The typo-tolerant matching primitive."""

from __future__ import annotations

import random

from dossier import fuzz


def _naive_osa(a: str, b: str) -> int:
    """Reference OSA distance (full DP), to cross-check the early-exit version."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


def test_budget_boundaries():
    assert fuzz.budget("abcd") == 0  # 4
    assert fuzz.budget("abcde") == 1  # 5
    assert fuzz.budget("abcdefgh") == 1  # 8
    assert fuzz.budget("abcdefghi") == 2  # 9


def test_transposition_costs_one():
    assert fuzz.within("visa", "vsia", 1)  # a swap is one edit
    assert not fuzz.within("visa", "vsia", 0)


def test_real_world_typos():
    # A dropped char in a long word — distance 1, forgiven by its budget-2.
    assert fuzz.term_matches("cerificate", ["certificate"])
    # A short query is all-signal: never fuzzes to a neighbour.
    assert not fuzz.term_matches("cat", ["car"])
    assert not fuzz.term_matches("date", ["gate"])
    # A single typo in a medium word.
    assert fuzz.term_matches("pasport", ["passport"])
    assert fuzz.term_matches("polcy", ["policy"])


def test_folding_unifies_case_and_diacritics():
    assert fuzz.fold("Résumé") == "resume"
    assert fuzz.term_matches(fuzz.fold("resume"), [fuzz.fold("Résumé")])


def test_length_prefilter_and_equality():
    assert fuzz.within("abc", "abc", 0)
    assert not fuzz.within("abc", "abcdef", 2)  # length delta 3 > k
    assert fuzz.within("", "", 0)


def test_matches_naive_reference_over_random_strings():
    rng = random.Random(1234)
    alphabet = "abcde"
    for _ in range(4000):
        a = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 7)))
        b = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 7)))
        for k in (0, 1, 2, 3):
            assert fuzz.within(a, b, k) == (_naive_osa(a, b) <= k), (a, b, k)


def test_term_matches_empty_tokens():
    assert not fuzz.term_matches("passport", [])
