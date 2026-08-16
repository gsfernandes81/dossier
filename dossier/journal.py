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

"""The journal store, Python side — the satellite's half of the v3 contract.

The v3 rewrite (`REWRITE.md` §3) replaces the per-document Markdown store with
per-writer append-only JSONL journals. Two implementations read them: the Rust
core (`crates/journal`) and this module, which the Python satellite uses to fold
`meta/` + `enrich/` and to append to its own `enrich/<device>-lab.jsonl`.

**Two implementations of one format is a drift risk**, so this module is written
against the same shared fixtures the Rust crate is tested with
(`crates/journal/tests/golden/`), and `dossier/tests/test_journal.py` runs them.
The comparison is `canonical_json` — byte-for-byte — because comparing parsed
structures would let a serialization difference through, and a serialization
difference is exactly what would corrupt the other implementation's reads.

Deliberately mirrors the Rust module layout (op → fold → compaction plan) so the
two can be diffed side by side when the format changes. Where a Rust doc comment
explains *why*, the reasoning is not repeated here — see the crate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, TypeAlias

#: Format version this build writes and folds (REWRITE.md §3.2).
FORMAT_VERSION = 1

#: Ops retained regardless of whether the fold still needs them — the undo
#: horizon. Must match ``journal::compact::RETENTION_MS``.
RETENTION_MS = 30 * 24 * 60 * 60 * 1000

#: Compact below one live op in this many (i.e. under 25%). Integer, not 0.25,
#: for the same reason the format bans floats: exactness across languages.
LIVE_RATIO_TRIGGER = 4


class OpKind(StrEnum):
    """The frozen verb list (REWRITE.md §3.2)."""

    CREATE = "create"
    DELETE = "delete"
    SET = "set"
    UNSET = "unset"
    STATE = "state"
    READING = "reading"
    PROPOSAL = "proposal"

    @property
    def is_enrich(self) -> bool:
        """Whether this verb belongs to the lazily-loaded ``enrich`` namespace."""
        return self in (OpKind.READING, OpKind.PROPOSAL)


#: Required fields on every op, with the type each must have.
_REQUIRED: dict[str, type | tuple[type, ...]] = {
    "v": int,
    "ts": int,
    "w": str,
    "op": str,
    "ent": str,
    "id": str,
}

#: Fields this build knows; anything else is preserved in ``Op.extra``.
_KNOWN = frozenset({*_REQUIRED, "f", "val"})


@dataclass(frozen=True)
class Op:
    """One parsed op. ``extra`` preserves fields from a newer format version."""

    v: int
    ts: int
    w: str
    op: OpKind
    ent: str
    id: str
    f: str | None = None
    val: Any = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def order_key(self) -> tuple[int, str]:
        """The total order for last-writer-wins: ``(ts, w)``."""
        return (self.ts, self.w)

    @property
    def entity_key(self) -> tuple[str, str]:
        """The ``(ent, id)`` pair the fold groups by."""
        return (self.ent, self.id)

    def to_line(self) -> str:
        """Serialize to exactly one journal line (no trailing newline)."""
        out: dict[str, Any] = {
            "v": self.v,
            "ts": self.ts,
            "w": self.w,
            "op": self.op.value,
            "ent": self.ent,
            "id": self.id,
        }
        if self.f is not None:
            out["f"] = self.f
        if self.val is not None:
            out["val"] = self.val
        out.update(self.extra)
        return json.dumps(out, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True)
class Opaque:
    """Well-formed JSON from a version or verb this build does not know."""

    raw: str
    reason: Literal["unknown-version", "unknown-op"]


@dataclass(frozen=True)
class Malformed:
    """Broken bytes: kept, counted, reported — never silently discarded."""

    raw: str
    reason: str


Line: TypeAlias = Op | Opaque | Malformed


def _contains_float(value: Any) -> bool:
    """Whether any number anywhere in ``value`` is a float.

    The format is integers-only by construction (§3.2): the canonical comparison
    against the Rust fold could not survive float formatting differences.
    """
    if isinstance(value, float):
        return True
    if isinstance(value, list):
        return any(_contains_float(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_float(item) for item in value.values())
    return False


def parse_line(raw: str) -> Line:
    """Classify and parse one line. Never raises — a bad line is data, not an error."""
    try:
        value = json.loads(raw)
    except ValueError:
        return Malformed(raw, "not valid JSON")
    if not isinstance(value, dict):
        return Malformed(raw, "not a JSON object")

    version = value.get("v")
    # `bool` is an `int` in Python, so exclude it explicitly — `{"v": true}` is
    # not version 1.
    if not isinstance(version, int) or isinstance(version, bool):
        return Malformed(raw, "missing or non-integer `v`")
    if version != FORMAT_VERSION:
        return Opaque(raw, "unknown-version")

    verb = value.get("op")
    if verb is None:
        return Malformed(raw, "missing `op`")
    if not isinstance(verb, str):
        return Malformed(raw, "`op` is not a string")
    try:
        kind = OpKind(verb)
    except ValueError:
        return Opaque(raw, "unknown-op")

    for name, expected in _REQUIRED.items():
        got = value.get(name)
        if got is None or isinstance(got, bool) or not isinstance(got, expected):
            return Malformed(raw, f"`{name}` is missing or the wrong type")
    if _contains_float(value):
        return Malformed(raw, "contains a floating-point number (integers only)")

    field_name = value.get("f")
    if field_name is not None and not isinstance(field_name, str):
        return Malformed(raw, "`f` is not a string")

    return Op(
        v=version,
        ts=value["ts"],
        w=value["w"],
        op=kind,
        ent=value["ent"],
        id=value["id"],
        f=field_name,
        val=value.get("val"),
        extra={k: v for k, v in value.items() if k not in _KNOWN},
    )


def parse_body(body: str) -> tuple[list[Line], str | None]:
    """Parse a file body, splitting off a torn final line.

    A final line with no trailing newline was never durable (§3.3), so it is
    returned separately rather than counted as damage — and a writer must
    truncate it before appending.
    """
    if not body:
        return [], None
    torn: str | None = None
    rest = body
    if not body.endswith("\n"):
        cut = body.rfind("\n") + 1
        torn = body[cut:]
        rest = body[:cut]
    lines = [parse_line(line) for line in rest.splitlines() if line.strip()]
    return lines, torn


@dataclass
class FoldStats:
    """Counts a caller reports as `ds status` anomalies."""

    folded: int = 0
    opaque: int = 0
    malformed: int = 0
    orphaned: int = 0
    duplicate_keys: int = 0
    max_ts_by_writer: dict[str, int] = field(default_factory=dict)

    @property
    def max_ts(self) -> int:
        """Highest ``ts`` anywhere — what seeds a writer's clock."""
        return max(self.max_ts_by_writer.values(), default=0)

    @property
    def has_anomalies(self) -> bool:
        """Whether anything here is worth a `ds status` line."""
        return bool(self.malformed or self.orphaned or self.duplicate_keys)


@dataclass
class Fold:
    """The folded state of a journal set."""

    entities: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    states: dict[tuple[str, str], Any] = field(default_factory=dict)
    enrich: dict[tuple[str, str], Any] = field(default_factory=dict)
    tombstones: dict[tuple[str, str], int] = field(default_factory=dict)
    stats: FoldStats = field(default_factory=FoldStats)

    def get(self, ent: str, doc_id: str) -> dict[str, Any] | None:
        """One live entity's fields."""
        return self.entities.get((ent, doc_id))

    def canonical_json(self) -> str:
        """The byte string this fold and the Rust fold must agree on (§10).

        Sorted keys at every level, compact separators, UTF-8 with no ASCII
        escaping, integers only. Health counters are excluded on purpose: they
        describe the files an implementation happened to read, not the state.
        """

        def group(source: dict[tuple[str, str], Any]) -> dict[str, dict[str, Any]]:
            out: dict[str, dict[str, Any]] = {}
            for (ent, doc_id), value in source.items():
                out.setdefault(ent, {})[doc_id] = value
            return out

        tombstones: dict[str, list[str]] = {}
        for ent, doc_id in sorted(self.tombstones):
            tombstones.setdefault(ent, []).append(doc_id)

        document = {
            "enrich": group(self.enrich),
            "entities": group(self.entities),
            "states": group(self.states),
            "tombstones": tombstones,
        }
        return json.dumps(
            document, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )


def fold(lines: list[Line]) -> Fold:
    """Ops in, current state out — the three rules of REWRITE.md §3.3.

    Order of the input does not matter; that is the property that makes sync
    conflicts structurally impossible.
    """
    result = Fold()
    ops: list[Op] = []
    for line in lines:
        if isinstance(line, Op):
            ops.append(line)
        elif isinstance(line, Opaque):
            result.stats.opaque += 1
        else:
            result.stats.malformed += 1

    ops.sort(key=lambda op: op.order_key)

    alive: set[tuple[str, str]] = set()
    previous_key: tuple[int, str] | None = None

    for op in ops:
        if previous_key == op.order_key:
            result.stats.duplicate_keys += 1
        previous_key = op.order_key

        key = op.entity_key
        writer_max = result.stats.max_ts_by_writer
        writer_max[op.w] = max(writer_max.get(op.w, op.ts), op.ts)
        result.stats.folded += 1

        if op.op is OpKind.CREATE:
            # A create after a tombstone is a legitimate recreate, and it starts
            # from nothing.
            alive.add(key)
            result.tombstones.pop(key, None)
            result.entities[key] = {}
        elif op.op is OpKind.DELETE:
            alive.discard(key)
            result.entities.pop(key, None)
            result.tombstones[key] = op.ts
        elif op.op in (OpKind.SET, OpKind.UNSET):
            if key not in alive or op.f is None:
                # No resurrection from a stray set, and no materializing an
                # entity that was never created.
                result.stats.orphaned += 1
                continue
            entity = result.entities.setdefault(key, {})
            if op.op is OpKind.SET:
                entity[op.f] = op.val
            else:
                entity.pop(op.f, None)
        elif op.op is OpKind.STATE:
            result.states[key] = op.val
        else:
            result.enrich[key] = op.val

    return result


@dataclass
class CompactionPlan:
    """Which line indices survive a compaction, and how much that saves."""

    keep: list[int]
    total: int

    @property
    def dropped(self) -> int:
        """Lines that would be dropped."""
        return self.total - len(self.keep)

    @property
    def live_percent(self) -> int:
        """The percentage of the file still live, for reporting."""
        if self.total == 0:
            return 100
        return len(self.keep) * 100 // self.total

    @property
    def worth_doing(self) -> bool:
        """Whether the file is worth rewriting (§3.3's trigger)."""
        return self.total > 0 and len(self.keep) * LIVE_RATIO_TRIGGER < self.total


def compaction_plan(lines: list[Line], now_ms: int) -> CompactionPlan:
    """Decide what a compaction of one writer's file would keep (§3.3).

    Keeps every ``create``/``delete``, the newest ``set``/``unset`` per field,
    the newest ``state``/``reading``/``proposal`` per key, everything inside the
    30-day undo horizon, and every line this build could not read. An ``unset``
    survives even when the ``set`` it cancelled is dropped — the *other* writer
    may have set that field earlier, and the unset is what keeps it removed.
    """
    cutoff = now_ms - RETENTION_MS

    newest_tombstone: dict[tuple[str, str], int] = {}
    for line in lines:
        if isinstance(line, Op) and line.op is OpKind.DELETE:
            key = line.entity_key
            newest_tombstone[key] = max(newest_tombstone.get(key, line.ts), line.ts)

    newest: dict[tuple[str, str, str | None, int], int] = {}
    for index, line in enumerate(lines):
        if not isinstance(line, Op):
            continue
        ent, doc_id = line.entity_key
        if line.op in (OpKind.CREATE, OpKind.DELETE):
            continue
        if line.op in (OpKind.SET, OpKind.UNSET):
            key = (ent, doc_id, line.f, 0)
        elif line.op is OpKind.STATE:
            key = (ent, doc_id, None, 1)
        else:
            key = (ent, doc_id, None, 2)
        current = newest.get(key)
        if current is None:
            newest[key] = index
        else:
            held = lines[current]
            if isinstance(held, Op) and line.ts > held.ts:
                newest[key] = index
    survivors = set(newest.values())

    keep: list[int] = []
    for index, line in enumerate(lines):
        if not isinstance(line, Op):
            # Lines this build did not understand are never compaction's to
            # throw away.
            keep.append(index)
            continue
        if line.ts >= cutoff or line.op in (OpKind.CREATE, OpKind.DELETE):
            keep.append(index)
            continue
        buried = line.ts < newest_tombstone.get(line.entity_key, line.ts)
        if not buried and index in survivors:
            keep.append(index)

    return CompactionPlan(keep=keep, total=len(lines))
