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

//! Golden vectors — the fixtures both implementations must satisfy.
//!
//! REWRITE.md §10 requires shared test vectors so the Rust core and the Python
//! satellite cannot drift: each fixture is raw journal file bodies plus the
//! **canonical JSON** the fold must produce, and both languages compare that
//! string byte-for-byte. The fixtures are plain JSON in `tests/golden/` with a
//! schema documented in `tests/golden/README.md`, precisely so the Python side
//! can read the same files without a Rust toolchain in the loop.
//!
//! Two things this harness does beyond "does the fold match":
//!
//! - It folds every vector **in both file orders**, so union-commutativity is
//!   checked by every fixture rather than only by the one named after it.
//! - It fails on a fixture directory it cannot find or that is empty, because a
//!   golden-vector suite that silently tests nothing is worse than none.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use journal::{fold, parse_body, Line};
use serde::Deserialize;

/// One fixture file. Mirrors `tests/golden/README.md`.
#[derive(Debug, Deserialize)]
struct Vector {
    /// Fixture name; matches the file stem.
    name: String,
    /// Why this behaviour is contractual — read this before changing a vector.
    #[allow(dead_code)]
    why: String,
    /// Raw journal file bodies, exactly as they would sit on disk.
    files: Vec<String>,
    /// The canonical JSON the fold must produce.
    canonical: String,
    /// Expected torn tails, per file, for the vectors that have one.
    #[serde(default)]
    torn: Vec<String>,
    /// Expected health counters — only the keys a vector cares about.
    #[serde(default)]
    stats: BTreeMap<String, usize>,
    /// For compaction vectors: which file to compact, when, and how much of it
    /// should survive.
    #[serde(default)]
    compact: Option<Compact>,
}

/// The compaction half of a vector.
#[derive(Debug, Deserialize)]
struct Compact {
    /// Index into `files`.
    file: usize,
    /// Wall clock in ms — decides the 30-day retention boundary.
    at: i64,
    /// How many lines must survive.
    expect_lines: usize,
}

fn golden_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/golden")
}

fn load_vectors() -> Vec<Vector> {
    let dir = golden_dir();
    let mut vectors: Vec<Vector> = std::fs::read_dir(&dir)
        .unwrap_or_else(|e| panic!("no golden fixtures at {}: {e}", dir.display()))
        .filter_map(|entry| {
            let path = entry.expect("readable dir entry").path();
            (path.extension()? == "json").then_some(path)
        })
        .map(|path| {
            let text = std::fs::read_to_string(&path).expect("readable fixture");
            let vector: Vector = serde_json::from_str(&text)
                .unwrap_or_else(|e| panic!("{} is not a valid fixture: {e}", path.display()));
            assert_eq!(
                path.file_stem().unwrap().to_string_lossy(),
                vector.name,
                "fixture name must match its file name"
            );
            vector
        })
        .collect();
    vectors.sort_by(|a, b| a.name.cmp(&b.name));
    assert!(!vectors.is_empty(), "the golden vector suite must not be empty");
    vectors
}

/// Parse a vector's files, returning every line and every torn tail.
fn parse(vector: &Vector) -> (Vec<Line>, Vec<String>) {
    let mut lines = Vec::new();
    let mut torn = Vec::new();
    for body in &vector.files {
        let (parsed, tail) = parse_body(body);
        lines.extend(parsed);
        torn.extend(tail);
    }
    (lines, torn)
}

/// Compacting the named file leaves the fold of the whole store unchanged.
///
/// The invariant compaction exists to not break, checked the way the Python
/// satellite will check it: plan the compaction, keep only the surviving lines,
/// and fold the store again.
#[test]
fn compaction_preserves_the_fold() {
    let mut ran = 0;
    for vector in load_vectors() {
        let Some(compact) = &vector.compact else { continue };
        ran += 1;

        // Parse each file separately: only the named one is compacted.
        let parsed: Vec<Vec<Line>> = vector.files.iter().map(|body| parse_body(body).0).collect();
        let plan = journal::compaction_plan(&parsed[compact.file], compact.at);
        assert_eq!(
            plan.keep.len(),
            compact.expect_lines,
            "vector `{}`: compaction kept {} lines, expected {}",
            vector.name,
            plan.keep.len(),
            compact.expect_lines
        );

        let mut after: Vec<Line> = Vec::new();
        for (index, lines) in parsed.iter().enumerate() {
            if index == compact.file {
                after.extend(plan.keep.iter().map(|&i| lines[i].clone()));
            } else {
                after.extend(lines.iter().cloned());
            }
        }
        assert_eq!(
            fold(&after).canonical_json(),
            vector.canonical,
            "vector `{}`: compaction changed the fold",
            vector.name
        );
    }
    assert!(ran > 0, "no compaction vector was exercised");
}

/// Every fixture folds to exactly its recorded canonical JSON.
///
/// This is the contract with the Python satellite: same bytes in, same bytes
/// out. A diff here is either a real behaviour change (update the vector *and*
/// REWRITE.md §3 in the same slice) or a bug.
#[test]
fn every_vector_folds_to_its_canonical_json() {
    for vector in load_vectors() {
        let (lines, _) = parse(&vector);
        let state = fold(&lines);
        assert_eq!(
            state.canonical_json(),
            vector.canonical,
            "vector `{}` folded differently than recorded",
            vector.name
        );
    }
}

/// Reversing the files changes nothing — `fold(A ∪ B) ≡ fold(B ∪ A)` checked on
/// every fixture, not just the one named for it.
#[test]
fn folding_is_independent_of_file_order() {
    for vector in load_vectors() {
        let reversed = Vector { files: vector.files.iter().rev().cloned().collect(), ..vector };
        let (lines, _) = parse(&reversed);
        assert_eq!(
            fold(&lines).canonical_json(),
            reversed.canonical,
            "vector `{}` depends on file order",
            reversed.name
        );
    }
}

/// Torn tails are reported exactly where a vector says they are — and nowhere
/// else, so a fixture cannot pass by accidentally losing a complete line.
#[test]
fn torn_tails_match_the_vectors() {
    for vector in load_vectors() {
        let (_, torn) = parse(&vector);
        assert_eq!(torn, vector.torn, "vector `{}` reported different torn tails", vector.name);
    }
}

/// The health counters a vector pins are the ones the fold reports. These are
/// what `ds status` turns into anomaly lines, so "the state is right" is not
/// enough — the *count* of skipped garbage has to be right too.
#[test]
fn health_counters_match_the_vectors() {
    for vector in load_vectors() {
        let (lines, _) = parse(&vector);
        let stats = fold(&lines).stats;
        for (key, expected) in &vector.stats {
            let actual = match key.as_str() {
                "folded" => stats.folded,
                "opaque" => stats.opaque,
                "malformed" => stats.malformed,
                "orphaned" => stats.orphaned,
                "duplicate_keys" => stats.duplicate_keys,
                other => panic!("vector `{}` pins unknown counter `{other}`", vector.name),
            };
            assert_eq!(actual, *expected, "vector `{}`: counter `{key}`", vector.name);
        }
    }
}

/// Every vector §10 names has a fixture. Without this, a vector could be
/// quietly deleted and the suite would still pass.
#[test]
fn the_required_vectors_are_all_present() {
    let names: Vec<String> = load_vectors().into_iter().map(|v| v.name).collect();
    for required in [
        "union-commutativity",
        "tombstone-then-newer-set",
        "tombstone-then-newer-create",
        "id-rename-with-inbound-supersedes",
        "state-per-key-lww-undismiss",
        "torn-tail",
        "mid-file-garbage",
        "compaction-preserves-fold",
    ] {
        assert!(names.iter().any(|n| n == required), "missing required vector `{required}`");
    }
}
