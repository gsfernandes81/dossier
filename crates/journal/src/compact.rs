// Copyright © 2026-present gsfernandes81
//
// This file is part of "dossier".
//
// dossier is free software: you can redistribute it and/or modify it under the
// terms of the GNU Affero General Public License as published by the Free Software
// Foundation, either version 3 of the License, or (at your option) any later version.
//
// dossier is distributed in the hope that it will be useful, but WITHOUT ANY
// WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
// PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License along with
// dossier. If not, see <https://www.gnu.org/licenses/>.

//! Compaction: shrinking a writer's own file without changing what it means.
//!
//! An append-only log grows forever, and most of what it holds is superseded:
//! fifteen edits to one document's name leave fourteen ops that no fold will
//! ever consult again. Compaction rewrites a writer's file as the minimal set
//! that reproduces its contribution — and **only its own file**, which is why it
//! needs no coordination with the other device at all (§3.3).
//!
//! # What is kept, and why each rule exists
//!
//! | Kept | Why |
//! |---|---|
//! | every `create` and `delete` | tombstones are retained forever, or a late `set` from a device that missed the delete would resurrect the document |
//! | the newest `set`/`unset` per `(ent, id, field)` | it is the only one that can still win a last-writer-wins comparison |
//! | the newest `state`/`reading`/`proposal` per `(ent, id)` | same, per key |
//! | **everything newer than 30 days** | the journal *is* the undo history, with a durable 30-day horizon (§3.3) |
//! | every line this build did not understand | opaque and malformed lines are preserved verbatim — compaction must never be the thing that discards them |
//!
//! # The two rules that are easy to get wrong
//!
//! **An `unset` is kept even when the `set` it cancelled is dropped.** Within
//! one file the pair is a no-op, so dropping both looks safe. It is not: the
//! *other* writer may have set that field earlier, and this file's `unset` is
//! what keeps it removed. Drop it and the other device's value comes back.
//!
//! **Ops older than their entity's newest tombstone are dropped.** They can
//! never apply again: a tombstone hides everything older, and a later `create`
//! starts from empty fields (§3.3). Keeping them would only make the file
//! bigger.
//!
//! Everything here is a **pure function** of the lines and the clock —
//! [`plan`] decides, and the writer does the I/O. That is what lets the
//! "compaction preserves the fold" property be tested exhaustively rather than
//! demonstrated on an example.

use std::collections::{BTreeMap, BTreeSet};

use crate::op::{Line, OpKind};

/// How long every op is retained regardless of whether the fold still needs it.
///
/// This is the undo horizon (§3.3): undo is "append the inverse op", and the
/// previous value has to still be readable for that to work. 30 days of edits
/// costs a few hundred kilobytes.
pub const RETENTION_MS: i64 = 30 * 24 * 60 * 60 * 1000;

/// Compact when fewer than one op in this many is still live — i.e. below 25%.
///
/// A quarter is deliberately lazy: compaction rewrites a synced file, so every
/// run costs the other device a full re-transfer. Waiting until three quarters
/// of the file is dead makes that transfer worth it.
///
/// Expressed as a divisor rather than a `0.25` so the whole crate stays free of
/// floating point — the same reason the op format bans floats (§3.2): integer
/// comparisons are exact and mean the same thing in both implementations.
pub const LIVE_RATIO_TRIGGER: usize = 4;

/// Which lines survive a compaction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Plan {
    /// Indices into the input, ascending — compaction preserves file order.
    pub keep: Vec<usize>,
    /// How many lines were in the file.
    pub total: usize,
}

impl Plan {
    /// Lines that would be dropped.
    #[must_use]
    pub fn dropped(&self) -> usize {
        self.total - self.keep.len()
    }

    /// The percentage of the file that is still live, for reporting.
    #[must_use]
    pub fn live_percent(&self) -> usize {
        if self.total == 0 {
            return 100;
        }
        self.keep.len() * 100 / self.total
    }

    /// Whether this file is worth rewriting (§3.3's trigger).
    #[must_use]
    pub fn worth_doing(&self) -> bool {
        self.total > 0 && self.keep.len() * LIVE_RATIO_TRIGGER < self.total
    }
}

/// Decide what a compaction of these lines would keep.
///
/// `now_ms` is the wall clock; ops newer than [`RETENTION_MS`] before it are
/// kept whatever else is true. Pure: no I/O, no clock of its own, so the
/// retention boundary is a test parameter rather than a race.
///
/// The input is expected to be **one writer's file**. Handing it a mixture is
/// not unsafe — the result would still fold identically — but the ratio would
/// be meaningless.
#[must_use]
pub fn plan(lines: &[Line], now_ms: i64) -> Plan {
    let cutoff = now_ms.saturating_sub(RETENTION_MS);

    // Pass 1: find each entity's newest tombstone, and the newest op index for
    // every key that supersedes older ones.
    let mut newest_tombstone: BTreeMap<(&str, &str), i64> = BTreeMap::new();
    for line in lines {
        if let Line::Op(op) = line {
            if op.op == OpKind::Delete {
                let entry = newest_tombstone.entry(op.entity_key()).or_insert(op.ts);
                *entry = (*entry).max(op.ts);
            }
        }
    }

    // Key → the index of the newest op for that key. `f` is part of the key for
    // set/unset (per-field LWW) and absent for the per-entity verbs.
    let mut newest: BTreeMap<(&str, &str, Option<&str>, u8), usize> = BTreeMap::new();
    for (index, line) in lines.iter().enumerate() {
        let Line::Op(op) = line else { continue };
        let (ent, id) = op.entity_key();
        let key = match op.op {
            // Lifecycle ops are kept wholesale (they are O(entities), not
            // O(ops), so collapsing them would save nothing worth the risk).
            OpKind::Create | OpKind::Delete => continue,
            OpKind::Set | OpKind::Unset => (ent, id, op.f.as_deref(), 0),
            OpKind::State => (ent, id, None, 1),
            OpKind::Reading | OpKind::Proposal => (ent, id, None, 2),
        };
        match newest.get(&key) {
            Some(&previous) => {
                let previous_ts = lines[previous].as_op().map_or(i64::MIN, |op| op.ts);
                if op.ts > previous_ts {
                    newest.insert(key, index);
                }
            }
            None => {
                newest.insert(key, index);
            }
        }
    }
    let survivors: BTreeSet<usize> = newest.into_values().collect();

    // Pass 2: keep.
    let mut keep = Vec::with_capacity(lines.len());
    for (index, line) in lines.iter().enumerate() {
        let keep_this = match line {
            // Lines this build did not understand are never compaction's to
            // throw away — that is the forward-compatibility promise.
            Line::Opaque { .. } | Line::Malformed { .. } => true,
            Line::Op(op) => {
                op.ts >= cutoff
                    || match op.op {
                        OpKind::Create | OpKind::Delete => true,
                        _ => {
                            // Dead behind a tombstone, or superseded by a newer
                            // op for the same key.
                            let buried = newest_tombstone
                                .get(&op.entity_key())
                                .is_some_and(|tomb| op.ts < *tomb);
                            !buried && survivors.contains(&index)
                        }
                    }
            }
        };
        if keep_this {
            keep.push(index);
        }
    }

    Plan { keep, total: lines.len() }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fold;
    use crate::op::parse_line;

    /// `now` for the tests: a fixed "today" so retention is deterministic.
    const NOW: i64 = 1_800_000_000_000;
    /// Comfortably outside the 30-day window.
    const OLD: i64 = NOW - RETENTION_MS - 1_000_000;

    fn line(ts: i64, kind: &str, id: &str, field: Option<&str>, val: Option<&str>) -> Line {
        // rust: `write!` into a `String` needs `fmt::Write` in scope, and appends
        // without the extra allocation `push_str(&format!(…))` makes. Writing to
        // a `String` cannot fail, hence the `expect`.
        use std::fmt::Write as _;
        let mut json =
            format!(r#"{{"v":1,"ts":{ts},"w":"desk-core","op":"{kind}","ent":"doc","id":"{id}""#);
        if let Some(field) = field {
            write!(json, r#","f":"{field}""#).expect("writing to a String never fails");
        }
        if let Some(val) = val {
            write!(json, r#","val":"{val}""#).expect("writing to a String never fails");
        }
        json.push('}');
        parse_line(&json)
    }

    fn kept(lines: &[Line], plan: &Plan) -> Vec<Line> {
        plan.keep.iter().map(|&i| lines[i].clone()).collect()
    }

    /// The headline invariant: compaction changes the file, never the fold.
    #[test]
    fn compaction_preserves_the_fold() {
        let lines = vec![
            line(OLD, "create", "passport", None, None),
            line(OLD + 1, "set", "passport", Some("name"), Some("v1")),
            line(OLD + 2, "set", "passport", Some("name"), Some("v2")),
            line(OLD + 3, "set", "passport", Some("name"), Some("v3")),
            line(OLD + 4, "set", "passport", Some("slot"), Some("4")),
        ];
        let plan = plan(&lines, NOW);
        assert_eq!(plan.dropped(), 2, "two superseded name writes go");
        assert_eq!(fold(&kept(&lines, &plan)).canonical_json(), fold(&lines).canonical_json());
    }

    /// Tombstones are retained forever — dropping one would let a `set` from a
    /// device that missed the delete resurrect the document.
    #[test]
    fn tombstones_are_never_dropped() {
        let lines = vec![
            line(OLD, "create", "x", None, None),
            line(OLD + 1, "set", "x", Some("name"), Some("gone")),
            line(OLD + 2, "delete", "x", None, None),
        ];
        let plan = plan(&lines, NOW);
        let survivors = kept(&lines, &plan);
        assert!(survivors.iter().filter_map(Line::as_op).any(|op| op.op == OpKind::Delete));
        assert_eq!(fold(&survivors).canonical_json(), fold(&lines).canonical_json());
        assert_eq!(plan.dropped(), 1, "the set behind the tombstone is dead and goes");
    }

    /// **The rule that looks wrong until you think about the other device.** An
    /// `unset` survives even though the `set` it cancelled is dropped: the other
    /// writer may have set that field earlier, and this op is what keeps it
    /// removed.
    #[test]
    fn an_unset_survives_the_set_it_cancelled() {
        let lines = vec![
            line(OLD, "create", "x", None, None),
            line(OLD + 1, "set", "x", Some("expiry"), Some("2027-01-01")),
            line(OLD + 2, "unset", "x", Some("expiry"), None),
        ];
        let survivors = kept(&lines, &plan(&lines, NOW));
        assert!(
            survivors.iter().filter_map(Line::as_op).any(|op| op.op == OpKind::Unset),
            "the unset must outlive its set"
        );

        // Prove it: the other writer's earlier set must stay cancelled.
        let other = parse_line(
            r#"{"v":1,"ts":1,"w":"phone-core","op":"set","ent":"doc","id":"x","f":"expiry","val":"2099-01-01"}"#,
        );
        let mut union = survivors;
        union.push(other);
        assert!(
            !fold(&union).get("doc", "x").expect("alive").fields.contains_key("expiry"),
            "dropping the unset would have resurrected the other device's value"
        );
    }

    /// Recent ops are kept whatever the fold thinks of them — the journal is the
    /// undo history, with a 30-day horizon.
    #[test]
    fn everything_inside_the_retention_window_is_kept() {
        let lines = vec![
            line(NOW - 1000, "create", "x", None, None),
            line(NOW - 900, "set", "x", Some("name"), Some("v1")),
            line(NOW - 800, "set", "x", Some("name"), Some("v2")),
            line(NOW - 700, "set", "x", Some("name"), Some("v3")),
        ];
        let plan = plan(&lines, NOW);
        assert_eq!(plan.dropped(), 0, "nothing recent is dropped, superseded or not");
        assert!(!plan.worth_doing());
    }

    /// Lines from the future and broken lines are preserved verbatim: it is not
    /// compaction's place to discard what it could not read.
    #[test]
    fn unreadable_and_future_lines_survive() {
        let lines = vec![
            line(OLD, "create", "x", None, None),
            line(OLD + 1, "set", "x", Some("name"), Some("v1")),
            line(OLD + 2, "set", "x", Some("name"), Some("v2")),
            parse_line(r#"{"v":7,"ts":5,"w":"desk-core","op":"set","ent":"doc","id":"x"}"#),
            parse_line("{broken"),
        ];
        let survivors = kept(&lines, &plan(&lines, NOW));
        assert!(survivors.iter().any(|l| matches!(l, Line::Opaque { .. })));
        assert!(survivors.iter().any(|l| matches!(l, Line::Malformed { .. })));
    }

    /// Compaction can never lower a file's highest timestamp — which is exactly
    /// what makes a `max_ts` regression a trustworthy damage signal (§3.3).
    #[test]
    fn the_highest_timestamp_always_survives() {
        let lines = vec![
            line(OLD, "create", "x", None, None),
            line(OLD + 1, "set", "x", Some("name"), Some("v1")),
            line(OLD + 2, "set", "x", Some("name"), Some("v2")),
            line(OLD + 3, "set", "x", Some("name"), Some("v3")),
        ];
        let before = lines.iter().filter_map(Line::as_op).map(|op| op.ts).max();
        let after =
            kept(&lines, &plan(&lines, NOW)).iter().filter_map(Line::as_op).map(|op| op.ts).max();
        assert_eq!(before, after);
    }

    /// The trigger is lazy on purpose: rewriting a synced file costs the other
    /// device a full re-transfer, so it waits until most of the file is dead.
    #[test]
    fn the_trigger_waits_until_most_of_the_file_is_dead() {
        let mut lines = vec![line(OLD, "create", "x", None, None)];
        for i in 1..20 {
            lines.push(line(OLD + i, "set", "x", Some("name"), Some("v")));
        }
        let plan = plan(&lines, NOW);
        assert!(plan.live_percent() < 25, "2 of 20 ops are live");
        assert!(plan.worth_doing());

        let fresh = vec![line(NOW, "create", "y", None, None)];
        assert!(!plan_worth(&fresh), "a file with nothing dead is not worth rewriting");
        assert!(!plan_worth(&[]), "and neither is an empty one");
    }

    fn plan_worth(lines: &[Line]) -> bool {
        plan(lines, NOW).worth_doing()
    }

    /// A recreate after a tombstone keeps its own history; the pre-tombstone
    /// fields are dead and go.
    #[test]
    fn a_recreate_keeps_only_its_own_history() {
        let lines = vec![
            line(OLD, "create", "x", None, None),
            line(OLD + 1, "set", "x", Some("name"), Some("before")),
            line(OLD + 2, "delete", "x", None, None),
            line(OLD + 3, "create", "x", None, None),
            line(OLD + 4, "set", "x", Some("name"), Some("after")),
        ];
        let plan = plan(&lines, NOW);
        let survivors = kept(&lines, &plan);
        assert_eq!(fold(&survivors).canonical_json(), fold(&lines).canonical_json());
        assert!(
            !survivors.iter().filter_map(Line::as_op).any(|op| op
                .val
                .as_ref()
                .and_then(|v| v.as_str())
                == Some("before")),
            "the pre-tombstone value is unreachable and dropped"
        );
    }
}
