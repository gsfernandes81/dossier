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

"""The Python fold against the **shared** golden vectors (REWRITE.md §10).

These are the same fixture files `crates/journal/tests/golden.rs` runs — not a
copy, not a translation. That is the whole point: two implementations of one
format drift, and the only thing that reliably stops it is both of them
answering the same questions with the same bytes.

If a fixture fails here but passes in Rust (or the reverse), the two folds have
diverged and one of them is now silently mis-reading the other's journal. Fix
the implementation; never re-record a vector to make a test pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from dossier.journal import (
    Malformed,
    Op,
    Opaque,
    OpKind,
    compaction_plan,
    fold,
    parse_body,
    parse_line,
)

GOLDEN = Path(__file__).resolve().parents[2] / "crates" / "journal" / "tests" / "golden"


def _vectors() -> list[dict[str, Any]]:
    """Load every shared fixture, failing loudly if there are none."""
    files = sorted(GOLDEN.glob("*.json"))
    assert files, f"no golden fixtures at {GOLDEN} — the suite must not be empty"
    loaded = []
    for path in files:
        vector = json.loads(path.read_text(encoding="utf-8"))
        assert vector["name"] == path.stem, "fixture name must match its file name"
        loaded.append(vector)
    return loaded


VECTORS = _vectors()
IDS = [v["name"] for v in VECTORS]


def _parse(vector: dict[str, Any]) -> tuple[list[Any], list[str]]:
    lines: list[Any] = []
    torn: list[str] = []
    for body in vector["files"]:
        parsed, tail = parse_body(body)
        lines.extend(parsed)
        if tail is not None:
            torn.append(tail)
    return lines, torn


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_folds_to_the_recorded_canonical_json(vector: dict[str, Any]) -> None:
    """The contract with the Rust core: same bytes in, same bytes out."""
    lines, _ = _parse(vector)
    assert fold(lines).canonical_json() == vector["canonical"]


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_folding_is_independent_of_file_order(vector: dict[str, Any]) -> None:
    """`fold(A ∪ B) ≡ fold(B ∪ A)`, checked on every fixture rather than one."""
    reversed_vector = dict(vector, files=list(reversed(vector["files"])))
    lines, _ = _parse(reversed_vector)
    assert fold(lines).canonical_json() == vector["canonical"]


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_torn_tails_match(vector: dict[str, Any]) -> None:
    """A torn tail is split off exactly where the vector says, and nowhere else."""
    _, torn = _parse(vector)
    assert torn == vector.get("torn", [])


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_health_counters_match(vector: dict[str, Any]) -> None:
    """The counters `ds status` reports have to agree too, not just the state."""
    lines, _ = _parse(vector)
    stats = fold(lines).stats
    for key, expected in vector.get("stats", {}).items():
        assert getattr(stats, key) == expected, key


@pytest.mark.parametrize("vector", VECTORS, ids=IDS)
def test_compaction_preserves_the_fold(vector: dict[str, Any]) -> None:
    """Compacting the named file must not change what the store folds to."""
    compact = vector.get("compact")
    if compact is None:
        pytest.skip("not a compaction vector")

    parsed = [parse_body(body)[0] for body in vector["files"]]
    plan = compaction_plan(parsed[compact["file"]], compact["at"])
    assert len(plan.keep) == compact["expect_lines"]

    after: list[Any] = []
    for index, lines in enumerate(parsed):
        if index == compact["file"]:
            after.extend(lines[i] for i in plan.keep)
        else:
            after.extend(lines)
    assert fold(after).canonical_json() == vector["canonical"]


def test_the_required_vectors_are_present() -> None:
    """A suite that silently tests nothing is worse than no suite."""
    names = {v["name"] for v in VECTORS}
    required = {
        "union-commutativity",
        "tombstone-then-newer-set",
        "tombstone-then-newer-create",
        "id-rename-with-inbound-supersedes",
        "state-per-key-lww-undismiss",
        "torn-tail",
        "mid-file-garbage",
        "compaction-preserves-fold",
    }
    assert required <= names, f"missing vectors: {sorted(required - names)}"


def test_lines_from_the_future_are_opaque_not_malformed() -> None:
    """Forward compatibility: a newer version or verb is preserved, not broken."""
    newer = parse_line('{"v":2,"ts":1,"w":"a","op":"set","ent":"doc","id":"x"}')
    assert isinstance(newer, Opaque) and newer.reason == "unknown-version"
    verb = parse_line('{"v":1,"ts":1,"w":"a","op":"teleport","ent":"doc","id":"x"}')
    assert isinstance(verb, Opaque) and verb.reason == "unknown-op"


@pytest.mark.parametrize(
    "raw",
    [
        "{not json",
        "[1,2,3]",
        '{"ts":1}',
        '{"v":1,"ts":1,"w":"a"}',
        '{"v":1,"ts":1,"w":"a","op":"set","ent":"doc","id":"x","f":"n","val":1.5}',
        '{"v":true,"ts":1,"w":"a","op":"set","ent":"doc","id":"x"}',
    ],
)
def test_broken_lines_are_malformed_and_keep_their_bytes(raw: str) -> None:
    """Damage is classified and kept — never an exception that aborts the load."""
    line = parse_line(raw)
    assert isinstance(line, Malformed)
    assert line.raw == raw


def test_unknown_fields_survive_a_round_trip() -> None:
    """Compaction re-serializes ops, so a future version's fields must survive."""
    raw = (
        '{"v":1,"ts":1,"w":"a","op":"set","ent":"doc","id":"x","f":"n","val":1,'
        '"future":{"k":[1,2]}}'
    )
    line = parse_line(raw)
    assert isinstance(line, Op)
    assert line.extra == {"future": {"k": [1, 2]}}
    assert parse_line(line.to_line()) == line


def test_canonical_json_is_sorted_compact_and_unescaped() -> None:
    """The exact serializer settings the cross-language comparison depends on."""
    lines = [
        parse_line('{"v":1,"ts":10,"w":"a","op":"create","ent":"doc","id":"b"}'),
        parse_line(
            '{"v":1,"ts":20,"w":"a","op":"set","ent":"doc","id":"b","f":"z","val":1}'
        ),
        parse_line(
            '{"v":1,"ts":30,"w":"a","op":"set","ent":"doc","id":"b","f":"a","val":"海"}'
        ),
        parse_line('{"v":1,"ts":40,"w":"a","op":"create","ent":"doc","id":"a"}'),
    ]
    assert fold(lines).canonical_json() == (
        '{"enrich":{},"entities":{"doc":{"a":{},"b":{"a":"海","z":1}}},'
        '"states":{},"tombstones":{}}'
    )


def test_enrich_verbs_are_identifiable() -> None:
    """The namespace split is a property of the verb.

    Nothing can put a transcript in the file the hot startup path reads.
    """
    assert OpKind.READING.is_enrich and OpKind.PROPOSAL.is_enrich
    meta_verbs = (OpKind.CREATE, OpKind.DELETE, OpKind.SET, OpKind.UNSET, OpKind.STATE)
    assert not any(kind.is_enrich for kind in meta_verbs)
