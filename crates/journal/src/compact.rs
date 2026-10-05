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
//! needs no coordination with the other device at all.
//!
//! # The two rules that are easy to get wrong
//!
//! **An `unset` is kept even when the `set` it cancelled is dropped.** Within
//! one file the pair is a no-op, so dropping both looks safe. It is not: the
//! *other* writer may have set that field earlier, and this file's `unset` is
//! what keeps it removed. Drop it and the other device's value comes back.
//!
//! **`set`/`unset` ops older than their entity's newest tombstone are
//! dropped.** They can never apply again: a tombstone hides every older field
//! write, and a later `create` starts from empty fields. `state` and enrich
//! ops are per key and independent of the lifecycle, so a tombstone buries
//! none of them.
//!
//! Everything here is a **pure function** of the lines and the clock —
//! [`plan`] decides, and the writer does the I/O. That is what lets the
//! "compaction preserves the fold" property be tested exhaustively rather than
//! demonstrated on an example.

use std::collections::{BTreeMap, BTreeSet};

use crate::op::{Line, OpKind};

/// How long every op is kept regardless of whether the fold still needs it, so
/// recent history stays readable in the file; 30 days of edits costs a few
/// hundred kilobytes.
pub const RETENTION_MS: i64 = 30 * 24 * 60 * 60 * 1000;

/// Compact when fewer than 1 in this many ops is live; lazy because every
/// compaction makes the other device re-transfer the file.
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

    /// Whether this file is worth rewriting.
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

    // First, each entity's newest tombstone.
    let mut newest_tombstone: BTreeMap<(&str, &str), i64> = BTreeMap::new();
    for line in lines {
        if let Line::Op(op) = line {
            if op.op == OpKind::Delete {
                let entry = newest_tombstone.entry(op.entity_key()).or_insert(op.ts);
                *entry = (*entry).max(op.ts);
            }
        }
    }

    // Then the newest op per key. `f` is part of the key for set/unset
    // (per-field LWW) and absent for the per-entity verbs.
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
                if lines[previous].as_op().is_none_or(|prev| op.order_key() > prev.order_key()) {
                    newest.insert(key, index);
                }
            }
            None => {
                newest.insert(key, index);
            }
        }
    }
    let survivors: BTreeSet<usize> = newest.into_values().collect();

    // Then keep.
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
                        OpKind::Set | OpKind::Unset => {
                            let buried = newest_tombstone
                                .get(&op.entity_key())
                                .is_some_and(|tomb| op.ts < *tomb);
                            !buried && survivors.contains(&index)
                        }
                        OpKind::State | OpKind::Reading | OpKind::Proposal => {
                            survivors.contains(&index)
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
    use crate::op::parse_line;
    use crate::{fold, Draft};

    /// `now` for the tests: a fixed "today" so retention is deterministic.
    const NOW: i64 = 1_800_000_000_000;
    /// Comfortably outside the 30-day window.
    const OLD: i64 = NOW - RETENTION_MS - 1_000_000;

    fn line(ts: i64, draft: Draft) -> Line {
        draft.stamp(ts, "desk-core").into()
    }

    fn kept(lines: &[Line], plan: &Plan) -> Vec<Line> {
        plan.keep.iter().map(|&i| lines[i].clone()).collect()
    }

    #[test]
    fn compaction_preserves_the_fold() {
        let lines = vec![
            line(OLD, Draft::create("doc", "passport")),
            line(OLD + 1, Draft::set("doc", "passport", "name", "v1")),
            line(OLD + 2, Draft::set("doc", "passport", "name", "v2")),
            line(OLD + 3, Draft::set("doc", "passport", "name", "v3")),
            line(OLD + 4, Draft::set("doc", "passport", "slot", "4")),
        ];
        let plan = plan(&lines, NOW);
        assert_eq!(plan.dropped(), 2, "two superseded name writes go");
        assert_eq!(fold(&kept(&lines, &plan)).canonical_json(), fold(&lines).canonical_json());
    }

    #[test]
    fn tombstones_are_never_dropped() {
        let lines = vec![
            line(OLD, Draft::create("doc", "x")),
            line(OLD + 1, Draft::set("doc", "x", "name", "gone")),
            line(OLD + 2, Draft::delete("doc", "x")),
        ];
        let plan = plan(&lines, NOW);
        let survivors = kept(&lines, &plan);
        assert!(survivors.iter().filter_map(Line::as_op).any(|op| op.op == OpKind::Delete));
        assert_eq!(fold(&survivors).canonical_json(), fold(&lines).canonical_json());
        assert_eq!(plan.dropped(), 1, "the set behind the tombstone is dead and goes");
    }

    #[test]
    fn a_state_older_than_a_tombstone_survives() {
        let lines = vec![
            line(OLD, Draft::state("review", "x", 3)),
            line(OLD + 1, Draft::delete("review", "x")),
        ];
        let plan = plan(&lines, NOW);
        assert_eq!(plan.dropped(), 0);
        assert_eq!(fold(&kept(&lines, &plan)).canonical_json(), fold(&lines).canonical_json());
    }

    #[test]
    fn an_unset_survives_the_set_it_cancelled() {
        let lines = vec![
            line(OLD, Draft::create("doc", "x")),
            line(OLD + 1, Draft::set("doc", "x", "expiry", "2027-01-01")),
            line(OLD + 2, Draft::unset("doc", "x", "expiry")),
        ];
        let survivors = kept(&lines, &plan(&lines, NOW));
        assert!(
            survivors.iter().filter_map(Line::as_op).any(|op| op.op == OpKind::Unset),
            "the unset must outlive its set"
        );

        // Prove it: the other writer's earlier set must stay cancelled.
        let mut union = survivors;
        union.push(Draft::set("doc", "x", "expiry", "2099-01-01").stamp(1, "phone-core").into());
        assert!(
            !fold(&union).get("doc", "x").expect("alive").fields.contains_key("expiry"),
            "dropping the unset would have resurrected the other device's value"
        );
    }

    #[test]
    fn everything_inside_the_retention_window_is_kept() {
        let lines = vec![
            line(NOW - 1000, Draft::create("doc", "x")),
            line(NOW - 900, Draft::set("doc", "x", "name", "v1")),
            line(NOW - 800, Draft::set("doc", "x", "name", "v2")),
            line(NOW - 700, Draft::set("doc", "x", "name", "v3")),
        ];
        let plan = plan(&lines, NOW);
        assert_eq!(plan.dropped(), 0, "nothing recent is dropped, superseded or not");
        assert!(!plan.worth_doing());
    }

    #[test]
    fn unreadable_and_future_lines_survive() {
        let lines = vec![
            line(OLD, Draft::create("doc", "x")),
            line(OLD + 1, Draft::set("doc", "x", "name", "v1")),
            line(OLD + 2, Draft::set("doc", "x", "name", "v2")),
            parse_line(r#"{"v":7,"ts":5,"w":"desk-core","op":"set","ent":"doc","id":"x"}"#),
            parse_line("{broken"),
        ];
        let survivors = kept(&lines, &plan(&lines, NOW));
        assert!(survivors.iter().any(|l| matches!(l, Line::Opaque { .. })));
        assert!(survivors.iter().any(|l| matches!(l, Line::Malformed { .. })));
    }

    #[test]
    fn the_highest_timestamp_always_survives() {
        let lines = vec![
            line(OLD, Draft::create("doc", "x")),
            line(OLD + 1, Draft::set("doc", "x", "name", "v1")),
            line(OLD + 2, Draft::set("doc", "x", "name", "v2")),
            line(OLD + 3, Draft::set("doc", "x", "name", "v3")),
        ];
        let before = lines.iter().filter_map(Line::as_op).map(|op| op.ts).max();
        let after =
            kept(&lines, &plan(&lines, NOW)).iter().filter_map(Line::as_op).map(|op| op.ts).max();
        assert_eq!(before, after);
    }

    #[test]
    fn the_trigger_waits_until_most_of_the_file_is_dead() {
        let mut lines = vec![line(OLD, Draft::create("doc", "x"))];
        for i in 1..20 {
            lines.push(line(OLD + i, Draft::set("doc", "x", "name", "v")));
        }
        let plan = plan(&lines, NOW);
        assert!(plan.live_percent() < 25, "2 of 20 ops are live");
        assert!(plan.worth_doing());

        let fresh = vec![line(NOW, Draft::create("doc", "y"))];
        assert!(!plan_worth(&fresh), "a file with nothing dead is not worth rewriting");
        assert!(!plan_worth(&[]), "and neither is an empty one");
    }

    fn plan_worth(lines: &[Line]) -> bool {
        plan(lines, NOW).worth_doing()
    }

    #[test]
    fn a_recreate_keeps_only_its_own_history() {
        let lines = vec![
            line(OLD, Draft::create("doc", "x")),
            line(OLD + 1, Draft::set("doc", "x", "name", "before")),
            line(OLD + 2, Draft::delete("doc", "x")),
            line(OLD + 3, Draft::create("doc", "x")),
            line(OLD + 4, Draft::set("doc", "x", "name", "after")),
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
