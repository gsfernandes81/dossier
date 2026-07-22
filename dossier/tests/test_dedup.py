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

"""Tests for the dedup grouping engine (duplicates + subset/superset)."""

from dossier import dedup


def test_hamming():
    assert dedup.hamming(0, 0) == 0
    assert dedup.hamming(0b1010, 0b1000) == 1
    assert dedup.hamming(0xFF, 0x00) == 8


def test_contained_subset_and_fuzzy():
    assert dedup.contained([1, 2], [1, 2, 3], max_distance=0)
    assert not dedup.contained([1, 2, 3], [1, 2], max_distance=0)  # more pages
    assert not dedup.contained([1, 9], [1, 2, 3], max_distance=0)  # 9 absent
    # 2 (0b10) vs 3 (0b11) differ by one bit
    assert dedup.contained([1, 2], [1, 3], max_distance=1)
    assert not dedup.contained([1, 2], [1, 3], max_distance=0)


def test_group_superset_keeps_the_more_complete_copy():
    groups = dedup.group_files(
        {"a/short.pdf": [10, 20], "a/full.pdf": [10, 20, 30, 40]}, max_distance=0
    )
    assert len(groups) == 1
    group = groups[0]
    assert group.keep == "a/full.pdf"  # the 4-page superset
    assert group.subsets == ["a/short.pdf"]
    assert not group.ambiguous


def test_exact_duplicates_keep_the_non_bundle_copy():
    groups = dedup.group_files(
        {
            "Marine/CoC.pdf": [1, 2, 3],
            "Visas/US Application/CoC.pdf": [1, 2, 3],
        },
        max_distance=0,
    )
    assert len(groups) == 1
    group = groups[0]
    assert group.keep == "Marine/CoC.pdf"  # not the application-folder copy
    assert group.subsets == ["Visas/US Application/CoC.pdf"]
    assert not group.ambiguous


def test_fuzzy_rescan_groups_within_distance():
    groups = dedup.group_files(
        {"scan1.pdf": [0b0000, 0b0100], "scan2.pdf": [0b0001, 0b0101]},  # 1 bit/page
        max_distance=1,
    )
    assert len(groups) == 1
    assert set(groups[0].files) == {"scan1.pdf", "scan2.pdf"}


def test_unrelated_and_empty_files_are_not_grouped():
    groups = dedup.group_files(
        {"a.pdf": [1, 2], "b.pdf": [100, 200], "c.pdf": [1, 2, 3], "blank.pdf": []},
        max_distance=0,
    )
    assert len(groups) == 1  # a ⊂ c; b and the empty file stand alone
    assert set(groups[0].files) == {"a.pdf", "c.pdf"}
    assert groups[0].keep == "c.pdf"


def test_ambiguous_when_no_single_superset():
    # A and C are shared pages; B and D extend {1,2} in different ways.
    groups = dedup.group_files(
        {"A": [1], "C": [2], "B": [1, 2, 5], "D": [1, 2, 6]}, max_distance=0
    )
    assert len(groups) == 1
    group = groups[0]
    assert set(group.files) == {"A", "B", "C", "D"}
    assert group.ambiguous  # neither B nor D contains the other
    assert group.keep in {"B", "D"}  # a most-complete (3-page) copy


def test_contained_uses_augmenting_paths_not_greedy():
    # small=[0,2] ⊆ large=[0,1] at distance 1: page 0 must take large[1]=1 so
    # page 2 (which only matches large[0]=0) can be placed. Greedy first-fit would
    # grab large[0] for page 0 and strand page 2.
    assert dedup.contained([0, 2], [0, 1], max_distance=1)
    # genuine non-containment still returns False
    assert not dedup.contained([0, 15], [0, 1], max_distance=1)
