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

//! The fold: ops in, current state out.
//!
//! This is the heart of the store (REWRITE.md §3.3). Everything else — files,
//! writers, Syncthing — exists to deliver ops here. The fold is a **pure
//! function of the set of ops**, which is what makes conflicts structurally
//! impossible: two devices that have seen the same ops in any order compute the
//! same state, so there is nothing to reconcile.
//!
//! Three rules do all the work:
//!
//! 1. **Order is `(ts, w)`**, globally, not per file. A writer never repeats a
//!    `ts`, so the order is total and unique — the fold is a function, not a
//!    race.
//! 2. **A tombstone wins over everything older**, and ops newer than it are
//!    ignored *unless a `create` newer than the tombstone precedes them*. A
//!    stray `set` can never resurrect half a document.
//! 3. **`state` entries are per-key LWW**, independent of create/delete,
//!    because v2 ships restore verbs (`h` un-dismisses an orphan) that a
//!    monotone union could not express.
//!
//! The commutativity claim — `fold(A ∪ B) ≡ fold(B ∪ A)` — is property-tested
//! in `tests/properties.rs`, and the exact behaviours above are pinned by the
//! golden vectors in `tests/golden/`.

use std::collections::{BTreeMap, BTreeSet};

use serde_json::{Map, Value};

use crate::op::{Line, Op, OpKind};

/// `(entity kind, id)` — how the fold groups ops.
pub type EntityKey = (String, String);

/// One entity's current fields.
///
/// rust: a `BTreeMap`, not a `HashMap`. Sorted iteration is not a nicety here —
/// it *is* the canonical serialization (§10), and a `HashMap` would make the
/// golden vectors non-deterministic between runs.
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
    /// `set`/`unset` ops for an entity that does not exist (never created, or
    /// tombstoned since). Ignored by rule 2 — a non-zero count means either a
    /// lost `create` or a buggy writer, and either deserves saying out loud.
    pub orphaned: usize,
    /// Ops sharing a `(ts, w)` key. Impossible if writers obey the HLC rule, so
    /// a non-zero count means two processes wrote one writer id — exactly what
    /// the writer lock exists to prevent.
    pub duplicate_keys: usize,
    /// Highest `ts` seen per writer.
    ///
    /// Two jobs: it seeds the hybrid logical clock on startup, and its
    /// **regression** between runs is the signal that a journal was reverted
    /// behind Syncthing's back (§3.3, the Proton-revert defense).
    pub max_ts_by_writer: BTreeMap<String, i64>,
}

impl FoldStats {
    /// The highest `ts` anywhere — the value a new writer's clock starts from.
    #[must_use]
    pub fn max_ts(&self) -> i64 {
        self.max_ts_by_writer.values().copied().max().unwrap_or(0)
    }

    /// Whether anything here is worth a `ds status` line.
    #[must_use]
    pub fn has_anomalies(&self) -> bool {
        self.malformed > 0 || self.orphaned > 0 || self.duplicate_keys > 0
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
    /// folds must agree on (REWRITE.md §10).
    ///
    /// Canonical means: keys sorted at every level, no insignificant
    /// whitespace, UTF-8 with no ASCII escaping, integers only. In Python the
    /// equivalent call is
    /// `json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`.
    /// Comparing raw serializer defaults instead would be unimplementable —
    /// `serde_json` and `json.dumps` disagree on key order and escaping — which
    /// is why this function exists rather than a `Serialize` impl.
    ///
    /// Health counters are **not** included: they describe the files, not the
    /// state, and the two implementations legitimately see different files.
    ///
    /// # Panics
    /// Never in practice: the only fallible step is serializing a `Value` that
    /// this function just built out of other `Value`s, which `serde_json` can
    /// always represent — the format bans the one thing that could fail
    /// (floats, hence NaN) at parse time.
    #[must_use]
    pub fn canonical_json(&self) -> String {
        // rust: group into typed nested maps *first*, then convert to `Value`
        // once. Building `Value::Object`s incrementally would mean an
        // `as_object_mut().expect(…)` on every insert — an unreachable panic
        // path in a function that has no business being able to panic.
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
        serde_json::to_string(&doc).expect("a Value always serializes")
    }
}

/// Fold a set of lines into the current state.
///
/// Order of the input does not matter — that is the point (`fold(A ∪ B) ≡
/// fold(B ∪ A)`). Callers concatenate every writer's file and hand the lot over.
///
/// # Performance
///
/// This runs on every launch, so it works in **borrowed keys** and materializes
/// owned `String`s only for the entities that survive. The obvious version —
/// `(op.ent.clone(), op.id.clone())` per op — allocates three strings for every
/// op in the store (150,000 of them at the §9 stress size) to build map keys
/// that are almost always already present. Same output, a fraction of the work.
///
/// rust: the `'a` lifetime says the borrowed keys live as long as the input
/// lines, which is what lets the working maps hold `&str` into the ops. This is
/// the one place in the crate where a lifetime earns its keep; the public types
/// stay owned.
pub fn fold<'a>(lines: impl IntoIterator<Item = &'a Line>) -> Fold {
    let mut result = Fold::default();
    let mut ops: Vec<&'a Op> = Vec::new();

    for line in lines {
        match line {
            Line::Op(op) => ops.push(op),
            Line::Opaque { .. } => result.stats.opaque += 1,
            Line::Malformed { .. } => result.stats.malformed += 1,
        }
    }

    // Rule 1: one global order, `(ts, w)`. Sorting the whole set rather than
    // merging per-file streams is deliberate — it makes the input order of the
    // files structurally irrelevant instead of accidentally irrelevant.
    ops.sort_unstable_by_key(|op| op.order_key());

    // Working state, keyed by borrows into the ops. `alive` tracks existence
    // separately from `entities` because a deleted entity must stay *absent*
    // from the output while its history keeps being processed.
    let mut entities: BTreeMap<(&str, &str), Entity> = BTreeMap::new();
    let mut states: BTreeMap<(&str, &str), &'a Value> = BTreeMap::new();
    let mut enrich: BTreeMap<(&str, &str), &'a Value> = BTreeMap::new();
    let mut tombstones: BTreeMap<(&str, &str), i64> = BTreeMap::new();
    let mut alive: BTreeSet<(&str, &str)> = BTreeSet::new();
    let mut max_ts: BTreeMap<&str, i64> = BTreeMap::new();
    let mut previous_key: Option<(i64, &str)> = None;

    for op in ops {
        let key = op.order_key();
        if previous_key == Some(key) {
            result.stats.duplicate_keys += 1;
        }
        previous_key = Some(key);

        let entity_key = op.entity_key();
        let writer_max = max_ts.entry(op.w.as_str()).or_insert(i64::MIN);
        *writer_max = (*writer_max).max(op.ts);
        result.stats.folded += 1;

        match op.op {
            OpKind::Create => {
                // A create *after* a tombstone is a legitimate recreate, and it
                // starts from nothing — inheriting the dead entity's fields
                // would be a resurrection by another name.
                alive.insert(entity_key);
                tombstones.remove(&entity_key);
                entities.insert(entity_key, Entity::default());
            }
            OpKind::Delete => {
                alive.remove(&entity_key);
                entities.remove(&entity_key);
                tombstones.insert(entity_key, op.ts);
            }
            OpKind::Set | OpKind::Unset => {
                if !alive.contains(&entity_key) {
                    // Rule 2: no partial-doc resurrection, and no materializing
                    // an entity that was never created.
                    result.stats.orphaned += 1;
                    continue;
                }
                let Some(field) = op.f.as_deref() else {
                    result.stats.orphaned += 1;
                    continue;
                };
                let entity = entities.entry(entity_key).or_default();
                if op.op == OpKind::Set {
                    entity.fields.insert(field.to_string(), op.val.clone().unwrap_or(Value::Null));
                } else {
                    entity.fields.remove(field);
                }
            }
            // Rule 3: per-key LWW, independent of create/delete. Sorted
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

    // Materialize owned keys once, for what survived.
    let own = |(ent, id): (&str, &str)| (ent.to_string(), id.to_string());
    result.entities = entities.into_iter().map(|(k, v)| (own(k), v)).collect();
    result.states = states.into_iter().map(|(k, v)| (own(k), v.clone())).collect();
    result.enrich = enrich.into_iter().map(|(k, v)| (own(k), v.clone())).collect();
    result.tombstones = tombstones.into_iter().map(|(k, ts)| (own(k), ts)).collect();
    result.stats.max_ts_by_writer = max_ts.into_iter().map(|(w, ts)| (w.to_string(), ts)).collect();
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::op::parse_line;

    /// One op as a tuple: `(ts, writer, op, ent, id, field, value)`. A tuple
    /// rather than a builder because these tests are read as tables — the shape
    /// of the stream is the point, and a builder would bury it in punctuation.
    type Spec<'a> = (i64, &'a str, &'a str, &'a str, &'a str, Option<&'a str>, Option<Value>);

    /// Build lines from op tuples.
    fn lines(specs: &[Spec]) -> Vec<Line> {
        specs
            .iter()
            .map(|(ts, w, op, ent, id, f, val)| {
                let mut object = Map::new();
                object.insert("v".into(), Value::from(1));
                object.insert("ts".into(), Value::from(*ts));
                object.insert("w".into(), Value::from(*w));
                object.insert("op".into(), Value::from(*op));
                object.insert("ent".into(), Value::from(*ent));
                object.insert("id".into(), Value::from(*id));
                if let Some(f) = f {
                    object.insert("f".into(), Value::from(*f));
                }
                if let Some(val) = val {
                    object.insert("val".into(), val.clone());
                }
                parse_line(&serde_json::to_string(&Value::Object(object)).unwrap())
            })
            .collect()
    }

    /// Field-level last-writer-wins, ordered by `(ts, w)` and not by file order.
    #[test]
    fn the_newest_write_to_a_field_wins() {
        let ops = lines(&[
            (10, "desk-core", "create", "doc", "passport", None, None),
            (
                30,
                "phone-core",
                "set",
                "doc",
                "passport",
                Some("name"),
                Some("Passport (new)".into()),
            ),
            (20, "desk-core", "set", "doc", "passport", Some("name"), Some("Passport".into())),
        ]);
        let state = fold(&ops);
        assert_eq!(
            state.get("doc", "passport").unwrap().fields["name"],
            Value::from("Passport (new)")
        );
        assert!(!state.stats.has_anomalies());
    }

    /// A tombstone beats everything older, and a later `set` cannot bring back
    /// half a document. This is rule 2, and it is why a stray op from a device
    /// that missed the delete is harmless.
    #[test]
    fn a_tombstone_is_not_undone_by_a_later_set() {
        let ops = lines(&[
            (10, "a", "create", "doc", "x", None, None),
            (20, "a", "set", "doc", "x", Some("name"), Some("X".into())),
            (30, "a", "delete", "doc", "x", None, None),
            (40, "b", "set", "doc", "x", Some("name"), Some("zombie".into())),
        ]);
        let state = fold(&ops);
        assert!(state.get("doc", "x").is_none(), "the document stays deleted");
        assert!(state.tombstones.contains_key(&("doc".into(), "x".into())));
        assert_eq!(state.stats.orphaned, 1, "the stray set is counted, not applied");
    }

    /// …but a `create` newer than the tombstone *is* a legitimate recreate, and
    /// it starts empty rather than inheriting the dead entity's fields.
    #[test]
    fn a_create_after_a_tombstone_recreates_from_empty() {
        let ops = lines(&[
            (10, "a", "create", "doc", "x", None, None),
            (20, "a", "set", "doc", "x", Some("name"), Some("old".into())),
            (30, "a", "delete", "doc", "x", None, None),
            (40, "a", "create", "doc", "x", None, None),
            (50, "a", "set", "doc", "x", Some("slot"), Some(7.into())),
        ]);
        let state = fold(&ops);
        let entity = state.get("doc", "x").expect("recreated");
        assert_eq!(entity.fields["slot"], Value::from(7));
        assert!(!entity.fields.contains_key("name"), "no fields survive the tombstone");
        assert!(state.tombstones.is_empty(), "the recreate clears the tombstone");
    }

    /// `state` entries are per-key LWW and independent of create/delete — the
    /// un-dismiss verb depends on the newest op winning, in both directions.
    #[test]
    fn state_entries_are_per_key_lww_in_both_directions() {
        let ops = lines(&[
            (10, "a", "state", "review", "orphan:scan.pdf", None, Some("dismissed".into())),
            (20, "b", "state", "review", "orphan:scan.pdf", None, Some("active".into())),
            (15, "a", "state", "review", "other", None, Some("dismissed".into())),
        ]);
        let state = fold(&ops);
        assert_eq!(state.states[&("review".into(), "orphan:scan.pdf".into())], "active");
        assert_eq!(state.states[&("review".into(), "other".into())], "dismissed");
    }

    /// `unset` removes a field without touching the rest.
    #[test]
    fn unset_removes_one_field() {
        let ops = lines(&[
            (10, "a", "create", "doc", "x", None, None),
            (20, "a", "set", "doc", "x", Some("name"), Some("X".into())),
            (30, "a", "set", "doc", "x", Some("expiry"), Some("2027-01-01".into())),
            (40, "a", "unset", "doc", "x", Some("expiry"), None),
        ]);
        let entity = fold(&ops).get("doc", "x").unwrap().clone();
        assert!(entity.fields.contains_key("name") && !entity.fields.contains_key("expiry"));
    }

    /// The fold is order-independent by construction: shuffling the input
    /// changes nothing. (The general claim is property-tested; this is the
    /// cheap unit-level guard.)
    #[test]
    fn input_order_does_not_matter() {
        let ops = lines(&[
            (10, "a", "create", "doc", "x", None, None),
            (20, "a", "set", "doc", "x", Some("name"), Some("X".into())),
            (30, "b", "set", "doc", "x", Some("name"), Some("Y".into())),
        ]);
        let forward = fold(&ops).canonical_json();
        let backward = fold(ops.iter().rev()).canonical_json();
        assert_eq!(forward, backward);
    }

    /// Health counters separate "from the future" from "broken", because the
    /// first is normal and the second is a `ds status` anomaly.
    #[test]
    fn opaque_and_malformed_lines_are_counted_separately() {
        let mut ops = lines(&[(10, "a", "create", "doc", "x", None, None)]);
        ops.push(parse_line(r#"{"v":9,"ts":1,"w":"a","op":"set","ent":"doc","id":"x"}"#));
        ops.push(parse_line("{broken"));
        let state = fold(&ops);
        assert_eq!((state.stats.opaque, state.stats.malformed), (1, 1));
        assert!(state.stats.has_anomalies());
    }

    /// Per-writer high-water marks seed the hybrid logical clock and, by
    /// regressing, are how a reverted journal is detected at all.
    #[test]
    fn high_water_marks_are_tracked_per_writer() {
        let ops = lines(&[
            (10, "desk-core", "create", "doc", "x", None, None),
            (90, "phone-core", "create", "doc", "y", None, None),
            (50, "desk-core", "create", "doc", "z", None, None),
        ]);
        let stats = fold(&ops).stats;
        assert_eq!(stats.max_ts_by_writer["desk-core"], 50);
        assert_eq!(stats.max_ts_by_writer["phone-core"], 90);
        assert_eq!(stats.max_ts(), 90);
    }

    /// Two ops sharing `(ts, w)` are impossible under the HLC rule, so they are
    /// counted — that is the fold noticing two processes shared a writer id.
    #[test]
    fn duplicate_order_keys_are_counted() {
        let ops = lines(&[
            (10, "a", "create", "doc", "x", None, None),
            (10, "a", "create", "doc", "y", None, None),
        ]);
        assert_eq!(fold(&ops).stats.duplicate_keys, 1);
    }

    /// The canonical form is sorted, compact, and free of health counters (the
    /// two implementations legitimately see different files).
    #[test]
    fn canonical_json_is_sorted_and_compact() {
        let ops = lines(&[
            (10, "a", "create", "doc", "b", None, None),
            (20, "a", "set", "doc", "b", Some("z"), Some(1.into())),
            (30, "a", "set", "doc", "b", Some("a"), Some("海".into())),
            (40, "a", "create", "doc", "a", None, None),
        ]);
        let json = fold(&ops).canonical_json();
        assert_eq!(
            json,
            r#"{"enrich":{},"entities":{"doc":{"a":{},"b":{"a":"海","z":1}}},"states":{},"tombstones":{}}"#
        );
    }
}
