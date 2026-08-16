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

//! Property tests for the fold invariants (REWRITE.md §3.3, §10).
//!
//! The golden vectors pin behaviours someone thought of. These state the claims
//! that must hold for *every* op stream — which is the only honest way to say
//! "conflicts are structurally impossible", since that is a statement about all
//! possible sync orders, not about eight fixtures.
//!
//! One precondition runs through all of them: **`(ts, w)` is unique**. That is
//! not an assumption about luck, it is the hybrid logical clock's guarantee (a
//! writer never repeats a `ts`, §3.2) enforced by the single-writer lock. Where
//! it is violated the fold stops being a function of the op *set* — so the
//! generator below enforces it, and `FoldStats::duplicate_keys` is how a real
//! store notices the guarantee was broken.

use journal::{fold, parse_line, Line};
use proptest::prelude::*;
use serde_json::{json, Value};

/// A generated op, before it becomes a line.
#[derive(Debug, Clone)]
struct Spec {
    ts: i64,
    writer: &'static str,
    kind: &'static str,
    ent: &'static str,
    id: &'static str,
    field: &'static str,
    val: Value,
}

impl Spec {
    fn to_line(&self) -> Line {
        let mut object = serde_json::Map::new();
        object.insert("v".into(), json!(1));
        object.insert("ts".into(), json!(self.ts));
        object.insert("w".into(), json!(self.writer));
        object.insert("op".into(), json!(self.kind));
        object.insert("ent".into(), json!(self.ent));
        object.insert("id".into(), json!(self.id));
        if matches!(self.kind, "set" | "unset") {
            object.insert("f".into(), json!(self.field));
        }
        if matches!(self.kind, "set" | "state") {
            object.insert("val".into(), self.val.clone());
        }
        parse_line(&serde_json::to_string(&Value::Object(object)).expect("serializes"))
    }
}

const WRITERS: [&str; 3] = ["desk-core", "phone-core", "desk-lab"];
const KINDS: [&str; 5] = ["create", "delete", "set", "unset", "state"];
const ENTS: [&str; 3] = ["doc", "bundle", "review"];
const IDS: [&str; 4] = ["a", "b", "coc-2025", "passport"];
const FIELDS: [&str; 3] = ["name", "slot", "expiry"];

fn spec() -> impl Strategy<Value = Spec> {
    (
        1i64..500,
        0usize..WRITERS.len(),
        0usize..KINDS.len(),
        0usize..ENTS.len(),
        0usize..IDS.len(),
        0usize..FIELDS.len(),
        0i64..5,
    )
        .prop_map(|(ts, w, kind, ent, id, field, val)| Spec {
            ts,
            writer: WRITERS[w],
            kind: KINDS[kind],
            ent: ENTS[ent],
            id: IDS[id],
            field: FIELDS[field],
            val: json!(val),
        })
}

/// A stream with the store's own guarantee applied: no writer repeats a `ts`.
fn stream() -> impl Strategy<Value = Vec<Spec>> {
    prop::collection::vec(spec(), 0..60).prop_map(|specs| {
        let mut seen = std::collections::BTreeSet::new();
        specs.into_iter().filter(|s| seen.insert((s.ts, s.writer))).collect()
    })
}

fn lines(specs: &[Spec]) -> Vec<Line> {
    specs.iter().map(Spec::to_line).collect()
}

proptest! {
    /// **The fold is a function of the op set, not of its order.**
    ///
    /// `fold(A ∪ B) ≡ fold(B ∪ A)` — this is the claim that makes conflicts
    /// structurally impossible: two devices that have seen the same ops agree,
    /// whatever order Syncthing delivered them in.
    #[test]
    fn union_is_commutative(a in stream(), b in stream()) {
        // Keep the union legal: drop any (ts, w) B shares with A.
        let taken: std::collections::BTreeSet<_> =
            a.iter().map(|s| (s.ts, s.writer)).collect();
        let b: Vec<Spec> = b.into_iter().filter(|s| !taken.contains(&(s.ts, s.writer))).collect();

        let mut ab = lines(&a);
        ab.extend(lines(&b));
        let mut ba = lines(&b);
        ba.extend(lines(&a));

        prop_assert_eq!(fold(&ab).canonical_json(), fold(&ba).canonical_json());
    }

    /// **Any permutation folds the same.** Commutativity of two files is the
    /// case that matters operationally; this is the general statement, and it
    /// also catches a fold that accidentally depends on insertion order.
    #[test]
    fn any_permutation_folds_the_same(specs in stream(), rotation in 0usize..60) {
        let forward = lines(&specs);
        let mut rotated = forward.clone();
        // rust: the length has to be read before `rotate_left` takes the
        // mutable borrow — the borrow checker will not let one call both read
        // and mutate the same value.
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

    /// **Tombstone supremacy.** Append a delete newer than everything, then any
    /// number of newer `set`s from another writer: the entity stays gone. No
    /// stream of stray field writes can resurrect a document, whole or partial.
    #[test]
    fn a_final_tombstone_cannot_be_undone_by_sets(
        specs in stream(),
        extra in prop::collection::vec(0usize..FIELDS.len(), 0..6),
    ) {
        let mut all = lines(&specs);
        let deleted = Spec {
            ts: 1000,
            writer: "desk-core",
            kind: "delete",
            ent: "doc",
            id: "passport",
            field: "name",
            val: json!(0),
        };
        all.push(deleted.to_line());
        for (i, field) in extra.into_iter().enumerate() {
            all.push(Spec {
                ts: 1001 + i as i64,
                writer: "phone-core",
                kind: "set",
                field: FIELDS[field],
                val: json!("zombie"),
                ..deleted.clone()
            }.to_line());
        }

        let state = fold(&all);
        prop_assert!(state.get("doc", "passport").is_none());
        prop_assert!(state.tombstones.contains_key(&("doc".to_string(), "passport".to_string())));
    }

    /// **A recreate after a tombstone starts empty.** The legitimate other half
    /// of the rule above: `create` newer than the tombstone brings the id back,
    /// but never the dead entity's fields.
    #[test]
    fn a_recreate_after_a_tombstone_inherits_nothing(specs in stream()) {
        let base = Spec {
            ts: 0, writer: "desk-core", kind: "create", ent: "doc", id: "passport",
            field: "name", val: json!(0),
        };
        let mut all = lines(&specs);
        all.push(Spec { ts: 1000, kind: "delete", ..base.clone() }.to_line());
        all.push(Spec { ts: 1001, kind: "create", ..base.clone() }.to_line());
        all.push(Spec { ts: 1002, kind: "set", field: "slot", val: json!(3), ..base }.to_line());

        let entity = fold(&all).get("doc", "passport").cloned().expect("recreated");
        prop_assert_eq!(entity.fields.len(), 1);
        prop_assert_eq!(&entity.fields["slot"], &json!(3));
    }

    /// **Folding is deterministic and free of hidden state**: the same input
    /// twice gives the same bytes. Cheap, and it is what lets a golden vector
    /// mean anything at all.
    #[test]
    fn folding_is_deterministic(specs in stream()) {
        let all = lines(&specs);
        prop_assert_eq!(fold(&all).canonical_json(), fold(&all).canonical_json());
    }

    /// **A legal stream produces no anomalies.** Every generated op is
    /// well-formed with a unique `(ts, w)`, so `malformed` and `duplicate_keys`
    /// must stay at zero — if they don't, the *counters* are lying, and they are
    /// what `ds status` reports to the user.
    #[test]
    fn a_legal_stream_reports_no_damage(specs in stream()) {
        let stats = fold(&lines(&specs)).stats;
        prop_assert_eq!(stats.malformed, 0);
        prop_assert_eq!(stats.duplicate_keys, 0);
        prop_assert_eq!(stats.opaque, 0);
        prop_assert_eq!(stats.folded, specs.len());
    }
}
