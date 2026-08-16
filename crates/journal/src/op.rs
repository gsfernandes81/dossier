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

//! The op — one line of a journal file, and the unit the whole store is built from.
//!
//! REWRITE.md §3.2 is the contract this module implements:
//!
//! ```json
//! {"v":1,"ts":1755300000123,"w":"desk-core","op":"set","ent":"doc","id":"coc-card-2025","f":"expiry_date","val":"2026-09-28"}
//! ```
//!
//! The design decision that shapes this file is **forward compatibility**. A
//! journal is read by two implementations (this crate and the Python satellite)
//! and by two versions of each over time, so a line this build does not
//! understand must survive it rather than be dropped. That gives three possible
//! outcomes for a line, not two — [`Line::Op`], [`Line::Opaque`] (well-formed
//! but from the future: unknown `v` or unknown `op`) and [`Line::Malformed`]
//! (broken bytes). Only the first folds; **all three are kept**, and the last
//! two are counted so `ds status` can say so out loud.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The format version this build writes and folds.
///
/// A line carrying any other `v` is [`Line::Opaque`]: preserved, never folded,
/// never rewritten. That is the entire migration story — old builds ignore new
/// lines instead of corrupting them.
pub const FORMAT_VERSION: u32 = 1;

/// What an op does. Frozen list (REWRITE.md §3.2).
///
/// rust: a fieldless enum with serde's `rename_all`, so the wire format is the
/// lowercase word and the compiler still forces every `match` to be exhaustive.
/// Adding a variant here is a format change and breaks the build everywhere it
/// must be considered — which is the point.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OpKind {
    /// Bring an entity into existence. After a tombstone, this is a legitimate
    /// recreate and starts from empty fields.
    Create,
    /// Tombstone. Retained forever; nothing older than it survives.
    Delete,
    /// Set one field to `val`.
    Set,
    /// Remove one field.
    Unset,
    /// A review/suggestion entry's state — per-key LWW, independent of
    /// create/delete, because v2 ships restore verbs that a monotone union
    /// could never express (REWRITE.md §3.2).
    State,
    /// A scan reading (the `enrich` namespace).
    Reading,
    /// An intake proposal (the `enrich` namespace).
    Proposal,
}

impl OpKind {
    /// Whether this op belongs to the lazily-loaded `enrich` namespace.
    ///
    /// The split exists so the hot startup fold never parses transcripts
    /// (REWRITE.md §3.1); this is the predicate that keeps the two honest.
    #[must_use]
    pub fn is_enrich(self) -> bool {
        matches!(self, OpKind::Reading | OpKind::Proposal)
    }
}

/// One parsed op.
///
/// rust: `#[serde(flatten)] extra` collects any field this build does not know
/// about and re-emits it on serialize. Without it, compaction — which rewrites
/// a writer's own file — would quietly delete tomorrow's fields out of today's
/// lines. It is three words of annotation standing in for a whole class of data
/// loss.
///
/// It is not free: `flatten` makes serde buffer each line's fields instead of
/// deserializing them in place, measured at **18% of parse time** (40.0 ms vs
/// 32.7 ms for 50,000 ops). Kept anyway — that is 7 ms against silently
/// dropping data a future version wrote, at a store size three times the real
/// one. Revisit only if the phone measurement says parse is the bottleneck.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Op {
    /// Format version (always [`FORMAT_VERSION`] for a folded op).
    pub v: u32,
    /// Hybrid logical clock timestamp in milliseconds (REWRITE.md §3.2).
    ///
    /// Not raw wall time: strictly monotonic *per writer*, so a backwards clock
    /// jump can never reorder a writer against itself.
    pub ts: i64,
    /// Writer id, `<device>-<component>` — e.g. `desk-core`, `phone-core`.
    pub w: String,
    /// What this op does.
    pub op: OpKind,
    /// Entity kind: `doc`, `location`, `bundle`, `settings`, `review`, …
    pub ent: String,
    /// Entity id within its kind (the slug, for documents).
    pub id: String,
    /// Field name, for `set`/`unset`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub f: Option<String>,
    /// Value, for `set`/`state`/`reading`/`proposal`.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub val: Option<Value>,
    /// Fields from a newer format version, preserved verbatim.
    #[serde(flatten)]
    pub extra: BTreeMap<String, Value>,
}

impl Op {
    /// The total order for last-writer-wins: `(ts, w)`.
    ///
    /// Unique across the whole store because a writer never repeats a `ts`
    /// (§3.2), which is what makes the fold a *function* rather than a race.
    #[must_use]
    pub fn order_key(&self) -> (i64, &str) {
        (self.ts, self.w.as_str())
    }

    /// The `(ent, id)` pair the fold groups by.
    #[must_use]
    pub fn entity_key(&self) -> (&str, &str) {
        (self.ent.as_str(), self.id.as_str())
    }

    /// Serialize to exactly one journal line (no trailing newline).
    ///
    /// # Errors
    /// Only if a `val` contains something `serde_json` cannot represent, which
    /// for values that came out of [`parse_line`] cannot happen.
    pub fn to_line(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string(self)
    }
}

/// Why a well-formed line could not be folded by this build.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OpaqueReason {
    /// `v` is not [`FORMAT_VERSION`] — written by a newer dossier.
    UnknownVersion,
    /// `op` is a verb this build has never heard of.
    UnknownOp,
}

/// One line of a journal file, classified.
///
/// Every variant keeps the original bytes, because compaction copies lines it
/// retains rather than re-serializing them: bytes it did not understand are
/// bytes it must not rewrite.
#[derive(Debug, Clone, PartialEq)]
pub enum Line {
    /// A line this build folds.
    Op(Box<Op>),
    /// Well-formed JSON from a version or verb this build does not know.
    Opaque {
        /// The original bytes.
        raw: String,
        /// Which kind of unknown.
        reason: OpaqueReason,
    },
    /// Broken bytes: not JSON, not an object, or missing required fields.
    ///
    /// Counted and surfaced as a `ds status` anomaly, preserved through
    /// compaction, **never silently discarded** (REWRITE.md §3.3).
    Malformed {
        /// The original bytes.
        raw: String,
        /// A human-readable reason, for the anomaly report.
        reason: String,
    },
}

impl Line {
    /// The op, if this line is one this build folds.
    #[must_use]
    pub fn as_op(&self) -> Option<&Op> {
        match self {
            Line::Op(op) => Some(op),
            _ => None,
        }
    }

    /// The original bytes, whatever the classification.
    #[must_use]
    pub fn raw(&self) -> Option<&str> {
        match self {
            Line::Op(_) => None,
            Line::Opaque { raw, .. } | Line::Malformed { raw, .. } => Some(raw),
        }
    }
}

/// Classify and parse one line.
///
/// Never fails: an unreadable line becomes [`Line::Malformed`] rather than an
/// `Err`, because a single bad line must not abort the load of a 50,000-line
/// journal. The caller counts them and reports; nothing is thrown away.
///
/// # Performance
///
/// Journal parsing is on the phone's startup path — ~15,000 ops for the real
/// store, and `tests/perf.rs` stresses 50,000 — so this function has a **fast
/// path and a slow path**. The fast path deserializes straight into [`Op`].
/// The slow path re-reads the line as a generic `Value` only to explain *why*
/// it did not fit, which is rare by construction: a healthy journal never takes
/// it. Parsing into `Value` first and then into `Op` (the obvious version)
/// walks the line twice and allocates a whole `Map` per op — measured at 3× the
/// cost of the fold itself, for lines that are almost always fine.
pub fn parse_line(raw: &str) -> Line {
    match serde_json::from_str::<Op>(raw) {
        Ok(op) if op.v != FORMAT_VERSION => {
            Line::Opaque { raw: raw.to_string(), reason: OpaqueReason::UnknownVersion }
        }
        // Only `val` and unknown fields can smuggle a float in: every other
        // field is typed, so a float there fails the deserialize above and is
        // explained by the slow path.
        Ok(op)
            if op.val.as_ref().is_some_and(contains_float)
                || op.extra.values().any(contains_float) =>
        {
            Line::Malformed {
                raw: raw.to_string(),
                reason: "contains a floating-point number (the format is integers-only)".into(),
            }
        }
        Ok(op) => Line::Op(Box::new(op)),
        Err(_) => classify_failure(raw),
    }
}

/// The slow path: work out why a line did not deserialize into an [`Op`].
///
/// Split out so the cost — a second parse into a generic `Value` — is paid only
/// by lines that are genuinely unusual, and so the fast path above stays a
/// single `match`.
fn classify_failure(raw: &str) -> Line {
    let malformed = |reason: &str| Line::Malformed { raw: raw.to_string(), reason: reason.into() };

    let Ok(value) = serde_json::from_str::<Value>(raw) else {
        return malformed("not valid JSON");
    };
    let Some(object) = value.as_object() else {
        return malformed("not a JSON object");
    };

    // Version first: a line from the future must be preserved, not judged
    // against this build's idea of required fields.
    match object.get("v").and_then(Value::as_u64) {
        None => return malformed("missing or non-integer `v`"),
        Some(v) if v != u64::from(FORMAT_VERSION) => {
            return Line::Opaque { raw: raw.to_string(), reason: OpaqueReason::UnknownVersion }
        }
        Some(_) => {}
    }

    match object.get("op") {
        None => return malformed("missing `op`"),
        Some(Value::String(name)) => {
            if serde_json::from_value::<OpKind>(Value::String(name.clone())).is_err() {
                return Line::Opaque { raw: raw.to_string(), reason: OpaqueReason::UnknownOp };
            }
        }
        Some(_) => return malformed("`op` is not a string"),
    }

    // The format is integers-only by construction (§3.2) — a float would make
    // the canonical JSON comparison against the Python fold unimplementable, so
    // it is malformed data, not a value to round-trip.
    if contains_float(&value) {
        return malformed("contains a floating-point number (the format is integers-only)");
    }

    // The line is version 1 with a known verb, so reaching here means it failed
    // the schema — a missing `ts`/`w`/`ent`/`id`, or one of them the wrong type.
    // Re-run the deserialize purely to quote serde's reason in the anomaly.
    match serde_json::from_value::<Op>(value) {
        Ok(op) => Line::Op(Box::new(op)),
        Err(err) => malformed(&format!("does not match the op schema: {err}")),
    }
}

/// Whether any number anywhere in `value` is a float.
fn contains_float(value: &Value) -> bool {
    match value {
        Value::Number(n) => n.is_f64(),
        Value::Array(items) => items.iter().any(contains_float),
        Value::Object(map) => map.values().any(contains_float),
        _ => false,
    }
}

/// Parse a whole file body into lines, dropping a torn final line.
///
/// A journal's last line can be torn — the process died mid-`write` — and
/// REWRITE.md §3.3 says such a line was never durable, so it is dropped with a
/// warning rather than reported as corruption. The signal is the **absence of a
/// trailing newline**: every durable append ends in one.
///
/// Returns the classified lines and the torn tail, if there was one. (A writer
/// opening its own file for append must truncate that tail *before* appending —
/// otherwise the next op is glued onto it and the new op is the one destroyed.)
pub fn parse_body(body: &str) -> (Vec<Line>, Option<String>) {
    if body.is_empty() {
        return (Vec::new(), None);
    }
    let mut torn = None;
    let mut rest = body;
    if !body.ends_with('\n') {
        let start = body.rfind('\n').map_or(0, |i| i + 1);
        torn = Some(body[start..].to_string());
        rest = &body[..start];
    }
    let lines = rest
        .lines()
        // Blank lines are not data and not damage; a text editor or a file
        // transfer can leave one behind.
        .filter(|line| !line.trim().is_empty())
        .map(parse_line)
        .collect();
    (lines, torn)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn op_line() -> &'static str {
        r#"{"v":1,"ts":1755300000123,"w":"desk-core","op":"set","ent":"doc","id":"coc-card","f":"expiry_date","val":"2026-09-28"}"#
    }

    /// A well-formed op parses into its fields, and the total order key is
    /// `(ts, w)` — the pair every LWW decision in the store rests on.
    #[test]
    fn a_well_formed_op_parses() {
        let Line::Op(op) = parse_line(op_line()) else { panic!("should parse") };
        assert_eq!(op.op, OpKind::Set);
        assert_eq!(op.entity_key(), ("doc", "coc-card"));
        assert_eq!(op.order_key(), (1_755_300_000_123, "desk-core"));
        assert_eq!(op.val.as_ref().unwrap(), "2026-09-28");
    }

    /// Round-tripping preserves the line, including fields from a future
    /// version — compaction rewrites a writer's own file, so anything dropped
    /// here is data destroyed on disk.
    #[test]
    fn unknown_fields_survive_a_round_trip() {
        let raw = r#"{"v":1,"ts":1,"w":"a","op":"set","ent":"doc","id":"x","f":"n","val":1,"future":{"k":[1,2]}}"#;
        let Line::Op(op) = parse_line(raw) else { panic!("should parse") };
        assert!(op.extra.contains_key("future"));
        let again = parse_line(&op.to_line().unwrap());
        assert_eq!(again, Line::Op(op));
    }

    /// A newer format version or an unheard-of verb is *opaque*, not broken:
    /// preserved verbatim, never folded. This is the whole migration story.
    #[test]
    fn lines_from_the_future_are_opaque_not_malformed() {
        let newer = r#"{"v":2,"ts":1,"w":"a","op":"set","ent":"doc","id":"x"}"#;
        assert!(matches!(
            parse_line(newer),
            Line::Opaque { reason: OpaqueReason::UnknownVersion, .. }
        ));
        let verb = r#"{"v":1,"ts":1,"w":"a","op":"teleport","ent":"doc","id":"x"}"#;
        assert!(matches!(parse_line(verb), Line::Opaque { reason: OpaqueReason::UnknownOp, .. }));
    }

    /// Broken bytes are classified, kept and explained — never an `Err` that
    /// would abort the load of every other line in the file.
    #[test]
    fn broken_lines_are_malformed_and_keep_their_bytes() {
        for raw in ["{not json", "[1,2,3]", r#"{"ts":1}"#, r#"{"v":1,"ts":1,"w":"a"}"#] {
            let line = parse_line(raw);
            assert!(matches!(line, Line::Malformed { .. }), "{raw} should be malformed");
            assert_eq!(line.raw(), Some(raw));
        }
    }

    /// The format is integers-only by construction, because the golden vectors
    /// compare canonical JSON byte-for-byte against Python's and no two
    /// languages agree on float formatting.
    #[test]
    fn floats_are_rejected() {
        let raw = r#"{"v":1,"ts":1,"w":"a","op":"set","ent":"doc","id":"x","f":"n","val":1.5}"#;
        assert!(matches!(parse_line(raw), Line::Malformed { .. }));
    }

    /// A torn final line — a process that died mid-write — was never durable,
    /// so it is separated out rather than counted as damage. Everything before
    /// it still loads.
    #[test]
    fn a_torn_final_line_is_split_off() {
        let body = format!("{}\n{}", op_line(), r#"{"v":1,"ts":2,"w":"desk-c"#);
        let (lines, torn) = parse_body(&body);
        assert_eq!(lines.len(), 1);
        assert!(torn.unwrap().starts_with(r#"{"v":1,"ts":2"#));

        let (lines, torn) = parse_body(&format!("{}\n", op_line()));
        assert_eq!(lines.len(), 1);
        assert!(torn.is_none(), "a complete file has no torn tail");
    }

    /// Blank lines are neither data nor damage.
    #[test]
    fn blank_lines_are_ignored() {
        let (lines, _) = parse_body(&format!("{}\n\n{}\n", op_line(), op_line()));
        assert_eq!(lines.len(), 2);
        assert!(lines.iter().all(|l| l.as_op().is_some()));
    }

    /// The namespace split is a property of the verb, so nothing can put a
    /// transcript in the file the hot startup path reads.
    #[test]
    fn enrich_verbs_are_identifiable() {
        assert!(OpKind::Reading.is_enrich() && OpKind::Proposal.is_enrich());
        for kind in [OpKind::Create, OpKind::Delete, OpKind::Set, OpKind::Unset, OpKind::State] {
            assert!(!kind.is_enrich());
        }
    }
}
