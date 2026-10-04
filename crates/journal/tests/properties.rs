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

//! Property tests for the fold invariants.
//!
//! The golden vectors pin behaviours someone thought of. These state the claims
//! that must hold for *every* op stream — which is the only honest way to say
//! "conflicts are structurally impossible", since that is a statement about all
//! possible sync orders, not about eight fixtures.
//!
//! One precondition runs through all of them: **`(ts, w)` is unique**, the
//! hybrid logical clock's guarantee enforced by the single-writer lock. Where
//! it is violated the fold stops being a function of the op *set*;
//! `FoldStats::duplicate_keys` is how a real store notices.

use journal::{compaction_plan, fold, Draft, Line, Op};
use proptest::prelude::*;
use serde_json::json;

const WRITERS: [&str; 3] = ["desk-core", "phone-core", "desk-lab"];
const KINDS: usize = 5;
const ENTS: [&str; 3] = ["doc", "bundle", "review"];
const IDS: [&str; 4] = ["a", "b", "coc-2025", "passport"];
const FIELDS: [&str; 3] = ["name", "slot", "expiry"];

fn spec() -> impl Strategy<Value = Op> {
    (
        1i64..500,
        0usize..WRITERS.len(),
        0usize..KINDS,
        0usize..ENTS.len(),
        0usize..IDS.len(),
        0usize..FIELDS.len(),
        0i64..5,
    )
        .prop_map(|(ts, w, kind, ent, id, field, val)| {
            let (ent, id, field) = (ENTS[ent], IDS[id], FIELDS[field]);
            let draft = match kind {
                0 => Draft::create(ent, id),
                1 => Draft::delete(ent, id),
                2 => Draft::set(ent, id, field, val),
                3 => Draft::unset(ent, id, field),
                _ => Draft::state(ent, id, val),
            };
            draft.stamp(ts, WRITERS[w])
        })
}

/// A stream with the store's own guarantee applied: no writer repeats a `ts`.
fn stream() -> impl Strategy<Value = Vec<Op>> {
    prop::collection::vec(spec(), 0..60).prop_map(|ops| {
        let mut seen = std::collections::BTreeSet::new();
        ops.into_iter().filter(|op| seen.insert((op.ts, op.w.clone()))).collect()
    })
}

fn lines(ops: &[Op]) -> Vec<Line> {
    ops.iter().cloned().map(Line::from).collect()
}

/// Returns `b` without any `(ts, w)` that `a` already uses, so their union stays legal.
fn disjoint(a: &[Op], b: Vec<Op>) -> Vec<Op> {
    let taken: std::collections::BTreeSet<_> = a.iter().map(Op::order_key).collect();
    b.into_iter().filter(|op| !taken.contains(&op.order_key())).collect()
}

proptest! {
    /// Two devices that have seen the same ops agree, whatever order Syncthing
    /// delivered them in.
    #[test]
    fn union_is_commutative(a in stream(), b in stream()) {
        let b = disjoint(&a, b);

        let mut ab = lines(&a);
        ab.extend(lines(&b));
        let mut ba = lines(&b);
        ba.extend(lines(&a));

        prop_assert_eq!(fold(&ab).canonical_json(), fold(&ba).canonical_json());
    }

    #[test]
    fn any_permutation_folds_the_same(specs in stream(), rotation in 0usize..60) {
        let forward = lines(&specs);
        let mut rotated = forward.clone();
        let len = rotated.len();
        if len > 0 {
            rotated.rotate_left(rotation % len);
        }
        let mut reversed = forward.clone();
        reversed.reverse();

        let expected = fold(&forward).canonical_json();
        prop_assert_eq!(fold(&rotated).canonical_json(), expected.clone());
        prop_assert_eq!(fold(&reversed).canonical_json(), expected);
    }

    #[test]
    fn a_final_tombstone_cannot_be_undone_by_sets(
        specs in stream(),
        extra in prop::collection::vec(0usize..FIELDS.len(), 0..6),
    ) {
        let mut all = lines(&specs);
        all.push(Draft::delete("doc", "passport").stamp(1000, "desk-core").into());
        for (i, field) in extra.into_iter().enumerate() {
            let set = Draft::set("doc", "passport", FIELDS[field], "zombie");
            all.push(set.stamp(1001 + i as i64, "phone-core").into());
        }

        let state = fold(&all);
        prop_assert!(state.get("doc", "passport").is_none());
        prop_assert!(state.tombstones.contains_key(&("doc".to_string(), "passport".to_string())));
    }

    #[test]
    fn a_recreate_after_a_tombstone_inherits_nothing(specs in stream()) {
        let mut all = lines(&specs);
        all.push(Draft::delete("doc", "passport").stamp(1000, "desk-core").into());
        all.push(Draft::create("doc", "passport").stamp(1001, "desk-core").into());
        all.push(Draft::set("doc", "passport", "slot", 3).stamp(1002, "desk-core").into());

        let entity = fold(&all).get("doc", "passport").cloned().expect("recreated");
        prop_assert_eq!(entity.fields.len(), 1);
        prop_assert_eq!(&entity.fields["slot"], &json!(3));
    }

    /// `now` ranges over the whole clock, so the retention window sometimes
    /// covers everything and sometimes nothing.
    #[test]
    fn compaction_preserves_the_fold(a in stream(), b in stream(), now in 0i64..2_000i64) {
        let b = disjoint(&a, b);

        let (mine, theirs) = (lines(&a), lines(&b));
        let mut before = mine.clone();
        before.extend(theirs.clone());

        let plan = compaction_plan(&mine, now);
        let mut after: Vec<Line> = plan.keep.iter().map(|&i| mine[i].clone()).collect();
        after.extend(theirs);

        prop_assert_eq!(fold(&after).canonical_json(), fold(&before).canonical_json());
        prop_assert!(plan.keep.len() <= mine.len());
    }

    /// The truncation defense treats a `max_ts` regression as damage, so a
    /// compaction that lowered it would raise a false alarm.
    #[test]
    fn compaction_never_lowers_the_high_water_mark(specs in stream(), now in 0i64..2_000i64) {
        let all = lines(&specs);
        let plan = compaction_plan(&all, now);
        let max = |lines: &[Line]| lines.iter().filter_map(Line::as_op).map(|op| op.ts).max();
        let kept: Vec<Line> = plan.keep.iter().map(|&i| all[i].clone()).collect();
        prop_assert_eq!(max(&kept), max(&all));
    }

    #[test]
    fn folding_is_deterministic(specs in stream()) {
        let all = lines(&specs);
        prop_assert_eq!(fold(&all).canonical_json(), fold(&all).canonical_json());
    }

    #[test]
    fn a_legal_stream_reports_no_damage(specs in stream()) {
        let stats = fold(&lines(&specs)).stats;
        prop_assert_eq!(stats.malformed, 0);
        prop_assert_eq!(stats.duplicate_keys, 0);
        prop_assert_eq!(stats.opaque, 0);
        prop_assert_eq!(stats.folded, specs.len());
    }
}
