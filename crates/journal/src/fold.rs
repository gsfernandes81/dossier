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

//! The fold: ops in, current state out, as a **pure function of the set of
//! ops** ordered by `(ts, w)` across every file, so devices that have seen the
//! same ops compute the same state. A tombstone wins over everything older and
//! ignores newer ops until a newer `create`; `state` entries are per-key LWW.
//! Commutativity is property-tested in `tests/properties.rs`, and the exact
//! rules are pinned by the golden vectors in `tests/golden/`.

use std::collections::BTreeMap;

use serde_json::{Map, Value};

use crate::op::{Line, Op, OpKind};

/// `(entity kind, id)` — how the fold groups ops.
pub type EntityKey = (String, String);

/// One entity's current fields.
///
/// A `BTreeMap` because sorted iteration *is* the canonical serialization; a
/// `HashMap` would make the golden vectors non-deterministic between runs.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Entity {
    /// Field name → value, last writer wins per field.
    pub fields: BTreeMap<String, Value>,
}

/// Counts a caller needs to report journal health (`ds status` anomalies).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct FoldStats {
    /// Ops this build understood and applied.
    pub folded: usize,
    /// Well-formed lines from a newer version or verb — preserved, not folded.
    pub opaque: usize,
    /// Broken lines: counted here, reported loudly, never discarded.
    pub malformed: usize,
    /// Set/unset ops for an entity that is not alive: never created, or
    /// deleted since. Ignored. A delete on one device while another sets a
    /// field leaves these normally.
    pub orphaned: usize,
    /// Ops sharing a `(ts, w)` key. Impossible if writers obey the HLC rule, so
    /// a non-zero count means two processes wrote one writer id — exactly what
    /// the writer lock exists to prevent.
    pub duplicate_keys: usize,
    /// Highest `ts` anywhere; seeds the clock.
    pub max_ts: i64,
}

impl FoldStats {
    /// Whether anything here is worth a `ds status` line.
    #[must_use]
    pub fn has_anomalies(&self) -> bool {
        self.malformed > 0 || self.duplicate_keys > 0
    }
}

/// `{kind: {id: value}}` as a JSON object — the shape every section of the
/// canonical form takes.
fn to_value_object(grouped: BTreeMap<&str, Map<String, Value>>) -> Value {
    Value::Object(
        grouped.into_iter().map(|(kind, inner)| (kind.to_string(), Value::Object(inner))).collect(),
    )
}

/// Stand-in for a `state`/`enrich` op that carried no `val`.
const NULL: &Value = &Value::Null;

/// The folded state of a journal set.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Fold {
    /// Live entities — documents, locations, bundles, settings.
    pub entities: BTreeMap<EntityKey, Entity>,
    /// Review/suggestion state entries (per-key LWW).
    pub states: BTreeMap<EntityKey, Value>,
    /// `enrich` payloads (readings, proposals), keyed by path/fingerprint, LWW
    /// on the whole value — they are opaque blobs to the core.
    pub enrich: BTreeMap<EntityKey, Value>,
    /// Deleted entities and when: retained forever, so a late `set` arriving
    /// from the other device cannot resurrect them.
    pub tombstones: BTreeMap<EntityKey, i64>,
    /// Health counters.
    pub stats: FoldStats,
}

impl Fold {
    /// Every live entity of one kind, in id order.
    pub fn kind<'a>(&'a self, ent: &'a str) -> impl Iterator<Item = (&'a str, &'a Entity)> {
        self.entities
            .iter()
            .filter(move |((kind, _), _)| kind == ent)
            .map(|((_, id), entity)| (id.as_str(), entity))
    }

    /// One live entity's fields.
    #[must_use]
    pub fn get(&self, ent: &str, id: &str) -> Option<&Entity> {
        self.entities.get(&(ent.to_string(), id.to_string()))
    }

    /// The canonical JSON of this state — the byte string the Rust and Python
    /// folds must agree on.
    ///
    /// Canonical means: keys sorted at every level, no insignificant
    /// whitespace, UTF-8 with no ASCII escaping, integers only. In Python the
    /// equivalent call is
    /// `json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`.
    ///
    /// Health counters are **not** included: they describe the files, not the
    /// state, and the two implementations legitimately see different files.
    #[must_use]
    pub fn canonical_json(&self) -> String {
        let group = |source: &BTreeMap<EntityKey, Value>| -> Value {
            let mut out: BTreeMap<&str, Map<String, Value>> = BTreeMap::new();
            for ((ent, id), value) in source {
                out.entry(ent).or_default().insert(id.clone(), value.clone());
            }
            to_value_object(out)
        };

        let mut entities: BTreeMap<&str, Map<String, Value>> = BTreeMap::new();
        for ((ent, id), entity) in &self.entities {
            let fields: Map<String, Value> =
                entity.fields.iter().map(|(k, v)| (k.clone(), v.clone())).collect();
            entities.entry(ent).or_default().insert(id.clone(), Value::Object(fields));
        }

        let mut tombstones: BTreeMap<&str, Vec<Value>> = BTreeMap::new();
        for (ent, id) in self.tombstones.keys() {
            tombstones.entry(ent).or_default().push(Value::String(id.clone()));
        }
        let tombstones: Map<String, Value> =
            tombstones.into_iter().map(|(ent, ids)| (ent.to_string(), Value::Array(ids))).collect();

        let doc = Value::Object(Map::from_iter([
            ("enrich".to_string(), group(&self.enrich)),
            ("entities".to_string(), to_value_object(entities)),
            ("states".to_string(), group(&self.states)),
            ("tombstones".to_string(), Value::Object(tombstones)),
        ]));
        // serde_json's `Map` is a BTreeMap, so this is already key-sorted at
        // every level, including inside values that came from `val`.
        doc.to_string()
    }
}

/// Fold a set of lines into the current state.
///
/// Order of the input does not matter — that is the point (`fold(A ∪ B) ≡
/// fold(B ∪ A)`). Callers concatenate every writer's file and hand the lot over.
///
/// # Performance
///
/// Works in borrowed keys: owned ones would allocate three strings per op on
/// every launch.
pub fn fold<'a>(lines: impl IntoIterator<Item = &'a Line>) -> Fold {
    let mut result = Fold::default();
    let mut ops: Vec<&'a Op> = Vec::new();

    for line in lines {
        match line {
            Line::Op(op) => ops.push(op),
            Line::Opaque(_) => result.stats.opaque += 1,
            Line::Malformed(_) => result.stats.malformed += 1,
        }
    }

    // One global order, `(ts, w)`. Sorting the whole set rather than
    // merging per-file streams is deliberate — it makes the input order of the
    // files structurally irrelevant instead of accidentally irrelevant.
    ops.sort_unstable_by_key(|op| op.order_key());
    result.stats.max_ts = ops.last().map_or(0, |op| op.ts);

    let mut entities: BTreeMap<(&str, &str), BTreeMap<&'a str, &'a Value>> = BTreeMap::new();
    let mut states: BTreeMap<(&str, &str), &'a Value> = BTreeMap::new();
    let mut enrich: BTreeMap<(&str, &str), &'a Value> = BTreeMap::new();
    let mut tombstones: BTreeMap<(&str, &str), i64> = BTreeMap::new();
    let mut previous_key: Option<(i64, &str)> = None;

    for op in ops {
        let key = op.order_key();
        if previous_key == Some(key) {
            result.stats.duplicate_keys += 1;
        }
        previous_key = Some(key);

        let entity_key = op.entity_key();
        result.stats.folded += 1;

        match op.op {
            OpKind::Create => {
                // A create *after* a tombstone is a legitimate recreate, and it
                // starts from nothing — inheriting the dead entity's fields
                // would be a resurrection by another name.
                tombstones.remove(&entity_key);
                entities.insert(entity_key, BTreeMap::new());
            }
            OpKind::Delete => {
                entities.remove(&entity_key);
                tombstones.insert(entity_key, op.ts);
            }
            OpKind::Set | OpKind::Unset => {
                // No partial-doc resurrection, and no materializing an entity
                // that was never created.
                let (Some(entity), Some(field)) = (entities.get_mut(&entity_key), op.f.as_deref())
                else {
                    result.stats.orphaned += 1;
                    continue;
                };
                if op.op == OpKind::Set {
                    entity.insert(field, op.val.as_ref().unwrap_or(NULL));
                } else {
                    entity.remove(field);
                }
            }
            // Per-key LWW, independent of create/delete. Sorted
            // iteration means "last write" is simply "last one applied".
            OpKind::State => {
                states.insert(entity_key, op.val.as_ref().unwrap_or(NULL));
            }
            // Enrich payloads are opaque to the core and replace wholesale.
            OpKind::Reading | OpKind::Proposal => {
                enrich.insert(entity_key, op.val.as_ref().unwrap_or(NULL));
            }
        }
    }

    let own = |(ent, id): (&str, &str)| (ent.to_string(), id.to_string());
    result.entities = entities
        .into_iter()
        .map(|(k, fields)| {
            let fields = fields.into_iter().map(|(f, v)| (f.to_string(), v.clone())).collect();
            (own(k), Entity { fields })
        })
        .collect();
    result.states = states.into_iter().map(|(k, v)| (own(k), v.clone())).collect();
    result.enrich = enrich.into_iter().map(|(k, v)| (own(k), v.clone())).collect();
    result.tombstones = tombstones.into_iter().map(|(k, ts)| (own(k), ts)).collect();
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::op::parse_line;
    use crate::Draft;

    /// Field-level last-writer-wins, ordered by `(ts, w)` and not by file order.
    #[test]
    fn the_newest_write_to_a_field_wins() {
        let ops: Vec<Line> = vec![
            Draft::create("doc", "passport").stamp(10, "desk-core").into(),
            Draft::set("doc", "passport", "name", "Passport (new)").stamp(30, "phone-core").into(),
            Draft::set("doc", "passport", "name", "Passport").stamp(20, "desk-core").into(),
        ];
        let state = fold(&ops);
        assert_eq!(
            state.get("doc", "passport").unwrap().fields["name"],
            Value::from("Passport (new)")
        );
        assert!(!state.stats.has_anomalies());
    }

    #[test]
    fn a_tombstone_is_not_undone_by_a_later_set() {
        let ops: Vec<Line> = vec![
            Draft::create("doc", "x").stamp(10, "a").into(),
            Draft::set("doc", "x", "name", "X").stamp(20, "a").into(),
            Draft::delete("doc", "x").stamp(30, "a").into(),
            Draft::set("doc", "x", "name", "zombie").stamp(40, "b").into(),
        ];
        let state = fold(&ops);
        assert!(state.get("doc", "x").is_none(), "the document stays deleted");
        assert!(state.tombstones.contains_key(&("doc".into(), "x".into())));
        assert_eq!(state.stats.orphaned, 1, "the stray set is counted, not applied");
    }

    #[test]
    fn a_create_after_a_tombstone_recreates_from_empty() {
        let ops: Vec<Line> = vec![
            Draft::create("doc", "x").stamp(10, "a").into(),
            Draft::set("doc", "x", "name", "old").stamp(20, "a").into(),
            Draft::delete("doc", "x").stamp(30, "a").into(),
            Draft::create("doc", "x").stamp(40, "a").into(),
            Draft::set("doc", "x", "slot", 7).stamp(50, "a").into(),
        ];
        let state = fold(&ops);
        let entity = state.get("doc", "x").expect("recreated");
        assert_eq!(entity.fields["slot"], Value::from(7));
        assert!(!entity.fields.contains_key("name"), "no fields survive the tombstone");
        assert!(state.tombstones.is_empty(), "the recreate clears the tombstone");
    }

    /// `state` entries are per-key LWW and independent of create/delete, so a
    /// later state op reverses a dismissal and the newest wins both ways.
    #[test]
    fn state_entries_are_per_key_lww_in_both_directions() {
        let ops: Vec<Line> = vec![
            Draft::state("review", "orphan:scan.pdf", "dismissed").stamp(10, "a").into(),
            Draft::state("review", "orphan:scan.pdf", "active").stamp(20, "b").into(),
            Draft::state("review", "other", "dismissed").stamp(15, "a").into(),
        ];
        let state = fold(&ops);
        assert_eq!(state.states[&("review".into(), "orphan:scan.pdf".into())], "active");
        assert_eq!(state.states[&("review".into(), "other".into())], "dismissed");
    }

    #[test]
    fn unset_removes_one_field() {
        let ops: Vec<Line> = vec![
            Draft::create("doc", "x").stamp(10, "a").into(),
            Draft::set("doc", "x", "name", "X").stamp(20, "a").into(),
            Draft::set("doc", "x", "expiry", "2027-01-01").stamp(30, "a").into(),
            Draft::unset("doc", "x", "expiry").stamp(40, "a").into(),
        ];
        let entity = fold(&ops).get("doc", "x").unwrap().clone();
        assert!(entity.fields.contains_key("name") && !entity.fields.contains_key("expiry"));
    }

    #[test]
    fn input_order_does_not_matter() {
        let ops: Vec<Line> = vec![
            Draft::create("doc", "x").stamp(10, "a").into(),
            Draft::set("doc", "x", "name", "X").stamp(20, "a").into(),
            Draft::set("doc", "x", "name", "Y").stamp(30, "b").into(),
        ];
        let forward = fold(&ops).canonical_json();
        let backward = fold(ops.iter().rev()).canonical_json();
        assert_eq!(forward, backward);
    }

    /// Health counters separate "from the future" from "broken", because the
    /// first is normal and the second is a `ds status` anomaly.
    #[test]
    fn opaque_and_malformed_lines_are_counted_separately() {
        let ops = [
            Draft::create("doc", "x").stamp(10, "a").into(),
            parse_line(r#"{"v":9,"ts":1,"w":"a","op":"set","ent":"doc","id":"x"}"#),
            parse_line("{broken"),
        ];
        let state = fold(&ops);
        assert_eq!((state.stats.opaque, state.stats.malformed), (1, 1));
        assert!(state.stats.has_anomalies());
    }

    #[test]
    fn max_ts_is_the_highest_ts_of_any_writer() {
        let ops: Vec<Line> = vec![
            Draft::create("doc", "x").stamp(10, "desk-core").into(),
            Draft::create("doc", "y").stamp(90, "phone-core").into(),
            Draft::create("doc", "z").stamp(50, "desk-core").into(),
        ];
        let stats = fold(&ops).stats;
        assert_eq!(stats.max_ts, 90);
    }

    #[test]
    fn duplicate_order_keys_are_counted() {
        let ops: Vec<Line> = vec![
            Draft::create("doc", "x").stamp(10, "a").into(),
            Draft::create("doc", "y").stamp(10, "a").into(),
        ];
        assert_eq!(fold(&ops).stats.duplicate_keys, 1);
    }

    /// The canonical form also leaves out the health counters.
    #[test]
    fn canonical_json_is_sorted_and_compact() {
        let ops: Vec<Line> = vec![
            Draft::create("doc", "b").stamp(10, "a").into(),
            Draft::set("doc", "b", "z", 1).stamp(20, "a").into(),
            Draft::set("doc", "b", "a", "海").stamp(30, "a").into(),
            Draft::create("doc", "a").stamp(40, "a").into(),
        ];
        let json = fold(&ops).canonical_json();
        assert_eq!(
            json,
            r#"{"enrich":{},"entities":{"doc":{"a":{},"b":{"a":"海","z":1}}},"states":{},"tombstones":{}}"#
        );
    }
}
