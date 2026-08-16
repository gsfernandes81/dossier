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

//! The synthetic perf gate: **fold 50k ops / 1k docs in under 20 ms**
//! (REWRITE.md §9, generous margin in CI).
//!
//! This number is load-bearing for the phone budget. Startup is
//! `read → parse → fold → paint`, the R0.2 spike measured paint at ~0.1 ms, and
//! §3.3's sizing note claims serde_json folds a store this size in single-digit
//! milliseconds. If that claim is wrong, the whole "< 100 ms to usable" budget
//! is wrong, and it is better to find out in R1 than in R3.
//!
//! The gate asserts only in **release** builds. A debug build is an order of
//! magnitude slower for reasons that have nothing to do with the design, and a
//! test that fails on `cargo test` but passes on `cargo test --release` teaches
//! people to ignore it. Debug runs still print, so the number is never hidden.

use std::time::Instant;

use journal::{fold, parse_line, Line};

/// Ops per document, roughly what a real record accumulates: a create plus
/// ~15 fields (REWRITE.md §3.3) and a few later edits.
const OPS_PER_DOC: usize = 50;
const DOCS: usize = 1_000;

/// Build a store-shaped op stream: `DOCS` documents, `OPS_PER_DOC` ops each,
/// two writers interleaved so the `(ts, w)` sort has real work to do.
fn synthetic_lines() -> Vec<String> {
    let writers = ["desk-core", "phone-core"];
    let fields =
        ["name", "location", "slot", "expiry_date", "issue_date", "tags", "notes", "files"];
    let mut lines = Vec::with_capacity(DOCS * OPS_PER_DOC);
    let mut ts: i64 = 1_755_300_000_000;
    for doc in 0..DOCS {
        let id = format!("doc-{doc:04}");
        ts += 1;
        lines.push(format!(
            r#"{{"v":1,"ts":{ts},"w":"desk-core","op":"create","ent":"doc","id":"{id}"}}"#
        ));
        for op in 1..OPS_PER_DOC {
            ts += 1;
            let writer = writers[op % writers.len()];
            let field = fields[op % fields.len()];
            lines.push(format!(
                r#"{{"v":1,"ts":{ts},"w":"{writer}","op":"set","ent":"doc","id":"{id}","f":"{field}","val":"value {op}"}}"#
            ));
        }
    }
    lines
}

/// Folding a full-sized store must stay far inside the startup budget.
#[test]
fn folding_50k_ops_stays_within_the_budget() {
    let raw = synthetic_lines();
    assert_eq!(raw.len(), DOCS * OPS_PER_DOC);

    let parse_start = Instant::now();
    let lines: Vec<Line> = raw.iter().map(|line| parse_line(line)).collect();
    let parse = parse_start.elapsed();

    let fold_start = Instant::now();
    let state = fold(&lines);
    let fold_time = fold_start.elapsed();

    assert_eq!(state.entities.len(), DOCS);
    assert_eq!(state.stats.folded, DOCS * OPS_PER_DOC);
    assert!(!state.stats.has_anomalies());

    let ms = |d: std::time::Duration| d.as_secs_f64() * 1000.0;
    println!(
        "journal perf: parse {:.1}ms · fold {:.1}ms · {} ops · {} docs ({} build)",
        ms(parse),
        ms(fold_time),
        raw.len(),
        DOCS,
        if cfg!(debug_assertions) { "debug" } else { "release" }
    );

    if cfg!(debug_assertions) {
        return;
    }
    // 50 ms rather than the 20 ms target: the gate is here to catch an
    // algorithmic regression (an accidental O(n²), a clone per op), not to
    // fail because a shared CI runner was busy. The target itself is tracked
    // by the printed number.
    assert!(
        ms(fold_time) < 50.0,
        "fold took {:.1}ms for {} ops — target is 20ms (REWRITE.md §9)",
        ms(fold_time),
        raw.len()
    );
}

/// Canonical serialization is part of the startup path for nothing, but it *is*
/// the parity harness's inner loop (R2 compares ~948 real documents), so a
/// quadratic surprise here would show up as a mysteriously slow export.
#[test]
fn canonical_serialization_of_a_full_store_is_fast() {
    let raw = synthetic_lines();
    let lines: Vec<Line> = raw.iter().map(|line| parse_line(line)).collect();
    let state = fold(&lines);

    let start = Instant::now();
    let json = state.canonical_json();
    let elapsed = start.elapsed().as_secs_f64() * 1000.0;
    println!("journal perf: canonical_json {elapsed:.1}ms · {} bytes", json.len());

    assert!(json.starts_with(r#"{"enrich":{},"entities":{"doc":{"doc-0000""#));
    if !cfg!(debug_assertions) {
        assert!(elapsed < 100.0, "canonical_json took {elapsed:.1}ms for {DOCS} docs");
    }
}
