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

//! Fold a journal directory and print its canonical JSON.
//!
//! ```text
//! cargo run -p journal --example fold_dir -- <journal-dir> [--stats]
//! ```
//!
//! Exists for the R2 rehearsal and the R7 cutover step "confirm the phone folds
//! it" (REWRITE.md §6): the Python exporter writes a journal, this prints what
//! the **Rust** core makes of it, and the two canonical strings are compared.
//! The golden vectors prove the two folds agree on hand-written fixtures; this
//! proves it on the user's real ~948 documents, which is the case that actually
//! matters and the only one nobody can write a fixture for.
//!
//! Read-only. Prints the canonical JSON on stdout and, with `--stats`, a health
//! summary on stderr so the two never mix in a diff.

use std::process::ExitCode;

use journal::{fold, Journal, Namespace};

fn main() -> ExitCode {
    let mut args = std::env::args().skip(1);
    let Some(dir) = args.next() else {
        eprintln!("usage: fold_dir <journal-dir> [--stats]");
        return ExitCode::from(2);
    };
    let stats_wanted = args.any(|arg| arg == "--stats");

    let journal = Journal::new(&dir);
    let mut lines = Vec::new();
    let mut anomalies = Vec::new();
    let mut present = false;

    for namespace in [Namespace::Meta, Namespace::Enrich] {
        match journal.load(namespace) {
            Ok(load) => {
                present |= load.present;
                anomalies.extend(load.anomalies.iter().map(|a| format!("{namespace}: {a}")));
                lines.extend(load.lines);
            }
            Err(err) => {
                eprintln!("cannot read {namespace}: {err}");
                return ExitCode::FAILURE;
            }
        }
    }
    if !present {
        eprintln!("no journal at {dir} (expected meta/ and/or enrich/ inside it)");
        return ExitCode::from(2);
    }

    let state = fold(&lines);
    println!("{}", state.canonical_json());

    if stats_wanted {
        let stats = &state.stats;
        eprintln!(
            "folded {} ops · {} entities · opaque {} · malformed {} · orphaned {} · \
             duplicate keys {}",
            stats.folded,
            state.entities.len(),
            stats.opaque,
            stats.malformed,
            stats.orphaned,
            stats.duplicate_keys,
        );
        for anomaly in &anomalies {
            eprintln!("anomaly: {anomaly}");
        }
    }

    // A journal that folds with damage is not a journal to cut over to.
    if state.stats.has_anomalies() {
        return ExitCode::FAILURE;
    }
    ExitCode::SUCCESS
}
