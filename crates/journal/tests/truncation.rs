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

//! End-to-end truncation detection, on a real directory (REWRITE.md §3.3).
//!
//! The unit tests check the rules in isolation. This one plays out the actual
//! scenario the defense exists for: a store loads normally, something outside
//! dossier reverts a journal file to an older version, and the next launch has
//! to notice — while a compaction that shrinks the same file by more must pass
//! in silence. Both halves in the same test, because it is the *pair* that is
//! the contract; either one alone can be satisfied by a broken implementation.

use journal::{fold, HighWater, Journal, Namespace};

fn op(ts: i64, id: &str) -> String {
    format!(r#"{{"v":1,"ts":{ts},"w":"desk-core","op":"create","ent":"doc","id":"{id}"}}"#)
}

fn body(ops: &[(i64, &str)]) -> String {
    ops.iter().map(|(ts, id)| format!("{}\n", op(*ts, id))).collect()
}

/// A launch: load `meta`, check the marks, then record what was seen.
fn launch(journal: &Journal, water: &mut HighWater) -> (usize, Vec<journal::Damage>) {
    let load = journal.load(Namespace::Meta).expect("loads");
    let marks = load.marks();
    let damage = water.check(&marks);
    water.observe(&marks);
    (fold(&load.lines).entities.len(), damage)
}

/// A journal reverted behind Syncthing's back is caught; a compaction that
/// shrinks the same file further is not.
#[test]
fn a_revert_is_caught_and_a_compaction_is_not() {
    let dir = tempfile::tempdir().expect("tempdir");
    let journal = Journal::under_root(dir.path());
    let file = journal.file_path(Namespace::Meta, "desk-core");
    std::fs::create_dir_all(file.parent().expect("has a parent")).expect("create");

    // Launch 1 — a healthy store of four documents.
    let full = body(&[(10, "a"), (20, "b"), (30, "c"), (40, "d")]);
    std::fs::write(&file, &full).expect("write");
    let mut water = HighWater::default();
    let (docs, damage) = launch(&journal, &mut water);
    assert_eq!(docs, 4);
    assert!(damage.is_empty(), "a first launch has nothing to compare against");

    // Launch 2 — Proton Drive reverts the file to an older version. Valid
    // JSONL, no conflict copy, two documents quietly gone.
    std::fs::write(&file, body(&[(10, "a"), (20, "b")])).expect("write");
    let (docs, damage) = launch(&journal, &mut water);
    assert_eq!(docs, 2, "the fold sees only what is on disk — it cannot know better");
    assert_eq!(damage.len(), 1, "but the high-water mark noticed: {damage:?}");
    assert!(damage[0].to_string().contains("versioning"), "and it names the recovery path");

    // Launch 3 — still reverted, still reported. Silent data loss deserves a
    // nag rather than a one-shot notice that scrolls away.
    let (_, damage) = launch(&journal, &mut water);
    assert_eq!(damage.len(), 1, "the warning persists until the data is back");

    // Launch 4 — recovered from versioning, plus a new op. Quiet again.
    std::fs::write(&file, body(&[(10, "a"), (20, "b"), (30, "c"), (40, "d"), (50, "e")]))
        .expect("write");
    let (docs, damage) = launch(&journal, &mut water);
    assert_eq!(docs, 5);
    assert!(damage.is_empty(), "recovery clears the alarm");

    // Launch 5 — compaction: the file shrinks to a fraction of its size while
    // keeping the newest op. This must be silent, or every compaction would
    // train the user to ignore the alarm that matters.
    let compacted = body(&[(50, "e")]);
    assert!(compacted.len() < full.len() / 3);
    std::fs::write(&file, &compacted).expect("write");
    let (_, damage) = launch(&journal, &mut water);
    assert!(damage.is_empty(), "a compaction that keeps the newest op is not damage");
}

/// A writer file that disappears entirely is damage too — nothing regressed,
/// because there is nothing left to regress.
#[test]
fn a_vanished_writer_file_is_caught() {
    let dir = tempfile::tempdir().expect("tempdir");
    let journal = Journal::under_root(dir.path());
    let meta = journal.path().join(Namespace::Meta.dir());
    std::fs::create_dir_all(&meta).expect("create");
    std::fs::write(meta.join("desk-core.jsonl"), body(&[(10, "a")])).expect("write");
    std::fs::write(meta.join("phone-core.jsonl"), body(&[(20, "b")])).expect("write");

    let mut water = HighWater::default();
    let (docs, damage) = launch(&journal, &mut water);
    assert_eq!(docs, 2);
    assert!(damage.is_empty());

    std::fs::remove_file(meta.join("phone-core.jsonl")).expect("remove");
    let (docs, damage) = launch(&journal, &mut water);
    assert_eq!(docs, 1);
    assert_eq!(damage.len(), 1, "the missing writer is reported: {damage:?}");
}
