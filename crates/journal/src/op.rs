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

//! The op: one line of a journal file, the unit the store is built from.
//!
//! A line is [`Line::Op`], [`Line::Opaque`] (well-formed, from the future) or
//! [`Line::Malformed`]; only the first folds.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// The format version this build writes and folds; a line carrying any other
/// `v` is [`Line::Opaque`].
pub const FORMAT_VERSION: u32 = 1;

/// What an op does. Frozen list.
///
/// Adding a variant is a format change, and the exhaustive matches make the
/// build break everywhere it must be considered.
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
    /// create/delete, because a later state op reverses a dismissal, which a
    /// monotone union could never express.
    State,
    /// A scan reading (the `enrich` namespace).
    Reading,
    /// An intake proposal (the `enrich` namespace).
    Proposal,
}

/// One parsed op.
///
/// `#[serde(flatten)] extra` collects any field this build does not know and
/// re-emits it on serialize; without it, compaction would delete a newer
/// version's fields from this writer's lines. It costs about 18% of parse time.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Op {
    /// Format version (always [`FORMAT_VERSION`] for a folded op).
    pub v: u32,
    /// Hybrid logical clock timestamp in milliseconds.
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
    /// Unique across the whole store because a writer never repeats a `ts`,
    /// which is what makes the fold a *function* rather than a race.
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

/// One line of a journal file, classified.
///
/// Compaction keeps every line this build cannot fold and copies its original
/// bytes verbatim: bytes it did not understand are bytes it must not rewrite.
#[derive(Debug, Clone, PartialEq)]
pub enum Line {
    /// A line this build folds.
    Op(Box<Op>),
    /// Well-formed JSON from a version or verb this build does not know.
    Opaque(String),
    /// Broken bytes, kept so compaction copies them verbatim.
    Malformed(Vec<u8>),
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
}

impl From<Op> for Line {
    fn from(op: Op) -> Self {
        Line::Op(Box::new(op))
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
/// The fast path deserializes straight into [`Op`]; only a line that does not
/// fit is re-read as a generic `Value` to tell a future version or verb from
/// damage. Parsing every line into `Value` first measured at 3× the cost of the
/// fold itself.
#[must_use]
pub fn parse_line(raw: &str) -> Line {
    match serde_json::from_str::<Op>(raw) {
        Ok(op) => accept(op, raw),
        Err(_) => classify_failure(raw),
    }
}

/// Folds `op` only if it carries this build's version and no float.
fn accept(op: Op, raw: &str) -> Line {
    if op.v != FORMAT_VERSION {
        return Line::Opaque(raw.to_string());
    }
    // Only `val` and unknown fields can smuggle a float in: every other field
    // is typed, so a float there fails the deserialize.
    if op.val.as_ref().is_some_and(contains_float) || op.extra.values().any(contains_float) {
        return Line::Malformed(raw.as_bytes().to_vec());
    }
    Line::Op(Box::new(op))
}

/// Tells a line from a newer version or verb apart from damage.
fn classify_failure(raw: &str) -> Line {
    let malformed = || Line::Malformed(raw.as_bytes().to_vec());
    let Ok(value) = serde_json::from_str::<Value>(raw) else {
        return malformed();
    };
    match value.get("v").and_then(Value::as_u64) {
        None => return malformed(),
        Some(v) if v != u64::from(FORMAT_VERSION) => return Line::Opaque(raw.to_string()),
        Some(_) => {}
    }
    if let Some(Value::String(verb)) = value.get("op") {
        if serde_json::from_value::<OpKind>(Value::String(verb.clone())).is_err() {
            return Line::Opaque(raw.to_string());
        }
    }
    // `from_str` rejects a duplicate key while a `Value` keeps the last one, as
    // Python's `json.loads` does, so the two folds agree.
    serde_json::from_value::<Op>(value).map_or_else(|_| malformed(), |op| accept(op, raw))
}

/// Whether any number anywhere in `value` is a float.
///
/// The format is integers-only: no two languages agree on float formatting, so
/// a float would make the canonical JSON comparison against the Python fold
/// unimplementable.
fn contains_float(value: &Value) -> bool {
    match value {
        Value::Number(n) => n.is_f64(),
        Value::Array(items) => items.iter().any(contains_float),
        Value::Object(map) => map.values().any(contains_float),
        _ => false,
    }
}

/// Parse a whole file body into lines, returning a torn final line (one with
/// no trailing newline) separately rather than as damage.
///
/// Each line is decoded on its own, so an invalid byte costs the line it sits
/// in, which stays [`Line::Malformed`] with its bytes intact.
#[must_use]
pub fn parse_body(body: &[u8]) -> (Vec<Line>, Option<String>) {
    let (rest, tail) = body.split_at(complete_len(body));
    let torn = (!tail.is_empty()).then(|| String::from_utf8_lossy(tail).into_owned());
    let lines = rest
        .split(|&byte| byte == b'\n')
        // Blank lines are not data and not damage; a text editor or a file
        // transfer can leave one behind.
        .filter(|line| !line.trim_ascii().is_empty())
        .map(|line| match std::str::from_utf8(line) {
            Ok(text) => parse_line(text),
            Err(_) => Line::Malformed(line.to_vec()),
        })
        .collect();
    (lines, torn)
}

/// Bytes up to and including the last newline; what follows is a torn line.
pub(crate) fn complete_len(body: &[u8]) -> usize {
    body.iter().rposition(|&byte| byte == b'\n').map_or(0, |i| i + 1)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn op_line() -> &'static str {
        r#"{"v":1,"ts":1755300000123,"w":"desk-core","op":"set","ent":"doc","id":"coc-card","f":"expiry_date","val":"2026-09-28"}"#
    }

    #[test]
    fn a_well_formed_op_parses() {
        let Line::Op(op) = parse_line(op_line()) else { panic!("should parse") };
        assert_eq!(op.op, OpKind::Set);
        assert_eq!(op.entity_key(), ("doc", "coc-card"));
        assert_eq!(op.order_key(), (1_755_300_000_123, "desk-core"));
        assert_eq!(op.val.as_ref().unwrap(), "2026-09-28");
    }

    #[test]
    fn unknown_fields_survive_a_round_trip() {
        let raw = r#"{"v":1,"ts":1,"w":"a","op":"set","ent":"doc","id":"x","f":"n","val":1,"future":{"k":[1,2]}}"#;
        let Line::Op(op) = parse_line(raw) else { panic!("should parse") };
        assert!(op.extra.contains_key("future"));
        let again = parse_line(&op.to_line().unwrap());
        assert_eq!(again, Line::Op(op));
    }

    #[test]
    fn lines_from_the_future_are_opaque_not_malformed() {
        let newer = r#"{"v":2,"ts":1,"w":"a","op":"set","ent":"doc","id":"x"}"#;
        assert!(matches!(parse_line(newer), Line::Opaque(_)));
        let verb = r#"{"v":1,"ts":1,"w":"a","op":"teleport","ent":"doc","id":"x"}"#;
        assert!(matches!(parse_line(verb), Line::Opaque(_)));
    }

    #[test]
    fn broken_lines_are_malformed_and_keep_their_bytes() {
        for raw in ["{not json", "[1,2,3]", r#"{"ts":1}"#, r#"{"v":1,"ts":1,"w":"a"}"#] {
            let Line::Malformed(kept) = parse_line(raw) else {
                panic!("{raw} should be malformed")
            };
            assert_eq!(kept, raw.as_bytes());
        }
    }

    #[test]
    fn floats_are_rejected() {
        let raw = r#"{"v":1,"ts":1,"w":"a","op":"set","ent":"doc","id":"x","f":"n","val":1.5}"#;
        assert!(matches!(parse_line(raw), Line::Malformed(_)));
    }

    #[test]
    fn a_duplicate_key_takes_the_last_value() {
        let raw = r#"{"v":1,"ts":1,"w":"a","w":"b","op":"set","ent":"doc","id":"x"}"#;
        let Line::Op(op) = parse_line(raw) else { panic!("should parse") };
        assert_eq!(op.w, "b");
        let float = r#"{"v":1,"ts":1,"w":"a","w":"b","op":"set","ent":"doc","id":"x","val":1.5}"#;
        assert!(matches!(parse_line(float), Line::Malformed(_)));
    }

    #[test]
    fn a_torn_final_line_is_split_off() {
        let body = format!("{}\n{}", op_line(), r#"{"v":1,"ts":2,"w":"desk-c"#);
        let (lines, torn) = parse_body(body.as_bytes());
        assert_eq!(lines.len(), 1);
        assert!(torn.unwrap().starts_with(r#"{"v":1,"ts":2"#));

        let (lines, torn) = parse_body(format!("{}\n", op_line()).as_bytes());
        assert_eq!(lines.len(), 1);
        assert!(torn.is_none(), "a complete file has no torn tail");
    }

    #[test]
    fn blank_lines_are_ignored() {
        let (lines, _) = parse_body(format!("{}\n\n{}\n", op_line(), op_line()).as_bytes());
        assert_eq!(lines.len(), 2);
        assert!(lines.iter().all(|l| l.as_op().is_some()));
    }
}
