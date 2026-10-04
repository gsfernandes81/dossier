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

//! The write path, end to end, on a real journal on a real disk.
//!
//! The model tests in `app.rs` prove the *rules* — what a keystroke does, what
//! `Enter` produces, what happens when the store comes back. They cannot prove
//! the part that matters most about a store: that the op the model asked for is
//! the op that reaches the file, and that reading the directory back afterwards
//! shows the edit. Only a real [`journal::Writer`] against a real directory can,
//! so that is what these do.
//!
//! Nothing here touches `docs/dev/demo`, and nothing touches a real store: every
//! test builds its own journal in a temp directory and folds it from disk.

use std::path::PathBuf;

use ds::app::{update, Effect, Model, Msg, WriteState};
use ds::edit::Field;
use journal::{Draft, Journal, Namespace, Writer};
use serde_json::json;
use tempfile::TempDir;

/// Returns `desk-core` journal lines for `drafts`, stamped a millisecond apart from `ts`.
fn desk_lines(ts: i64, drafts: Vec<Draft>) -> String {
    (ts..).zip(drafts).map(|(ts, d)| d.stamp(ts, "desk-core").to_line().unwrap() + "\n").collect()
}

/// A journal directory of this test's own, with one document in it.
fn journal_with_a_document() -> (TempDir, Journal) {
    let dir = tempfile::tempdir().expect("tempdir");
    let meta = dir.path().join("meta");
    std::fs::create_dir_all(&meta).expect("mkdir");
    let drafts = vec![
        Draft::create("doc", "coc"),
        Draft::set("doc", "coc", "name", "COC Certificate"),
        Draft::set("doc", "coc", "expiry_date", "2026-09-28"),
    ];
    std::fs::write(meta.join("desk-core.jsonl"), desk_lines(1_700_000_000_001, drafts))
        .expect("write");
    let journal = Journal::new(dir.path());
    (dir, journal)
}

/// Where the writer's advisory lock goes — a directory of this test's own, and
/// never the journal: a lock inside the synced tree would replicate to the other
/// device and lock it out of its own file (REWRITE.md §3.1).
fn lock_dir(dir: &std::path::Path) -> PathBuf {
    let locks = dir.join("state");
    std::fs::create_dir_all(&locks).expect("mkdir");
    locks
}

/// Build the model the TUI would have, from a journal on disk.
fn load_model(journal: &Journal) -> (Model, journal::Load) {
    let loaded = ds::load::load(journal).expect("load");
    let lines = loaded.load;
    let mut model = Model::new(loaded.store, loaded.today, loaded.warn_until, 47, 24);
    model.write = WriteState::Ready { device: "desk".into() };
    (model, lines)
}

/// **An edit made in the model reaches the journal, and reading the journal back
/// shows it.** This is the whole slice in one test: keystrokes in, an op on
/// disk, and a store folded from that disk that agrees with the screen.
#[test]
fn an_edit_becomes_an_op_and_survives_a_reload() {
    let (dir, journal) = journal_with_a_document();
    let (mut model, loaded) = load_model(&journal);

    update(&mut model, Msg::EditField(Field::Expiry));
    for _ in 0..10 {
        update(&mut model, Msg::Backspace);
    }
    for c in "2031-05-31".chars() {
        update(&mut model, Msg::Char(c));
    }
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else {
        panic!("a valid date must ask for an append");
    };

    // What the journal thread's writer does with it.
    let mut writer = Writer::open(
        &journal,
        Namespace::Meta,
        "phone-core",
        &lock_dir(dir.path()),
        loaded.marks().values().map(|mark| mark.max_ts).max().unwrap_or(0),
    )
    .expect("open the writer");
    let ops = writer.append_all(drafts).expect("append");
    writer.commit().expect("fsync");
    assert_eq!(ops.len(), 1);
    assert_eq!(ops[0].w, "phone-core", "this device wrote it, under its own id");

    // A second device's file, never an edit to the first one's — which is what
    // makes Syncthing conflicts structurally impossible.
    assert!(dir.path().join("meta").join("phone-core.jsonl").is_file());
    let original =
        std::fs::read_to_string(dir.path().join("meta").join("desk-core.jsonl")).expect("read");
    assert_eq!(original.lines().count(), 3, "the other writer's file was not touched");

    drop(writer);
    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.docs.iter().find(|d| d.id == "coc").expect("the document");
    assert_eq!(doc.expiry_date.as_deref(), Some("2031-05-31"), "the edit is in the store");
}

/// **Clearing the field writes an `unset`, and the document leaves the expiry
/// watch.** The `set` half of §3.2's contract is the obvious one; this is the
/// half that a stored empty string would have quietly broken instead.
#[test]
fn clearing_the_field_removes_it_from_the_folded_store() {
    let (dir, journal) = journal_with_a_document();
    let (mut model, _) = load_model(&journal);

    update(&mut model, Msg::EditField(Field::Expiry));
    for _ in 0..10 {
        update(&mut model, Msg::Backspace);
    }
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else {
        panic!("an empty buffer must still ask for an append");
    };

    let mut writer = Writer::open(
        &journal,
        Namespace::Meta,
        "phone-core",
        &lock_dir(dir.path()),
        1_700_000_000_003,
    )
    .expect("open the writer");
    writer.append_all(drafts).expect("append");
    writer.commit().expect("fsync");
    drop(writer);

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.docs.iter().find(|d| d.id == "coc").expect("the document");
    assert_eq!(doc.expiry_date, None, "the field is gone, not blank");
    assert!(!doc.is_tracked(), "so it is out of the expiry watch");
    let due = reloaded.store.due("2000-01-01", "2100-01-01");
    assert!(due.is_empty(), "{due:?}");
}

/// **A journal another process is writing degrades this one to read-only, and
/// says so** (REWRITE.md §3.1) — it is never an error to exit on, because
/// browsing, opening and `ds status` all still work.
#[test]
fn a_held_lock_is_a_notice_and_not_a_failure() {
    let (dir, journal) = journal_with_a_document();
    let locks = lock_dir(dir.path());
    let _held = Writer::open(&journal, Namespace::Meta, "phone-core", &locks, 0).expect("first");

    let second = Writer::open(&journal, Namespace::Meta, "phone-core", &locks, 0);
    let error = second.expect_err("a second writer under one id must not open");
    assert!(
        matches!(error, journal::writer::Error::Locked { .. }),
        "and it must be the locked case, which the shell reports as permanent: {error:?}"
    );

    // What the model does with that news: the reason is shown, editing stops
    // being offered, and nothing else about the session changes.
    let (mut model, _) = load_model(&journal);
    update(&mut model, Msg::EditField(Field::Expiry));
    update(&mut model, Msg::Char('9'));
    update(&mut model, Msg::Enter);
    update(&mut model, Msg::SaveFailed { reason: error.to_string(), permanent: true });

    assert_eq!(model.write.reason(), Some(error.to_string().as_str()));
    assert!(model.edit.is_some(), "the typing is still there to try again elsewhere");
    assert!(!model.rows.is_empty(), "and the store is still browsable");
}

/// **A different device appends to a different file, and the fold is the union.**
/// Two writers, two files, one store — the property the whole format exists for,
/// checked here through the app's own loader rather than the crate's tests.
#[test]
fn two_devices_write_two_files_and_fold_to_one_store() {
    let (dir, journal) = journal_with_a_document();
    let locks = lock_dir(dir.path());

    let mut phone =
        Writer::open(&journal, Namespace::Meta, "phone-core", &locks, 1_700_000_000_003)
            .expect("phone");
    phone.append_all(vec![Draft::set("doc", "coc", "expiry_date", "2030-01-01")]).expect("append");
    phone.commit().expect("fsync");
    drop(phone);

    let mut desk = Writer::open(&journal, Namespace::Meta, "desk-core", &locks, 1_700_000_000_003)
        .expect("desk");
    desk.append_all(vec![Draft::set("doc", "coc", "notes", "renewed in Mumbai")]).expect("append");
    desk.commit().expect("fsync");
    drop(desk);

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.docs.iter().find(|d| d.id == "coc").expect("the document");
    assert_eq!(doc.expiry_date.as_deref(), Some("2030-01-01"), "the phone's field");
    assert_eq!(doc.notes, "renewed in Mumbai", "and the desk's, from the same fold");
    assert!(dir.path().join("meta").join("phone-core.jsonl").is_file());
    assert!(dir.path().join("meta").join("desk-core.jsonl").is_file());
}

/// **A document created in the TUI exists after a reload.** The model tests
/// prove the two ops are asked for in the right order; only a real journal
/// proves the fold accepts them — a `set` that reached the file before its
/// `create` would be orphaned, and the new document would simply not be there.
#[test]
fn a_created_document_survives_a_reload() {
    let (dir, journal) = journal_with_a_document();
    let (mut model, loaded) = load_model(&journal);
    let before = model.store.docs.len();

    for c in "Seaman Book".chars() {
        update(&mut model, Msg::Char(c));
    }
    assert!(model.on_new, "nothing matches, so + new is selected");
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else {
        panic!("naming a new document must ask for an append");
    };

    let mut writer = Writer::open(
        &journal,
        Namespace::Meta,
        "desk-core",
        &lock_dir(dir.path()),
        loaded.marks().values().map(|mark| mark.max_ts).max().unwrap_or(0),
    )
    .expect("open the writer");
    writer.append_all(drafts).expect("append");
    writer.commit().expect("fsync");
    drop(writer);

    let reloaded = ds::load::load(&journal).expect("reload");
    assert_eq!(reloaded.store.docs.len(), before + 1);
    let doc = reloaded
        .store
        .docs
        .iter()
        .find(|d| d.id == "seaman-book-desk")
        .expect("the new document, keyed by name and device");
    assert_eq!(doc.name, "Seaman Book");
    assert_eq!(doc.expiry_date, None, "and nothing it was not given");
}

/// **Undo puts the field back, and does it by appending rather than rewriting.**
/// §3.3 makes the journal the history: nothing is ever removed from it, so
/// taking an edit back is writing the op that says so — and the file has to
/// still hold both the edit and its inverse afterwards.
#[test]
fn an_undo_restores_the_field_and_leaves_both_ops_in_the_journal() {
    let (dir, journal) = journal_with_a_document();
    let (mut model, _) = load_model(&journal);
    let mut follower = follower(dir.path(), &journal);

    update(&mut model, Msg::EditField(Field::Expiry));
    for _ in 0..10 {
        update(&mut model, Msg::Backspace);
    }
    for c in "2031-05-31".chars() {
        update(&mut model, Msg::Char(c));
    }
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else { panic!("no append") };
    write_and_reload(&mut follower, drafts, &mut model);
    assert_eq!(model.current().and_then(|d| d.expiry_date.clone()).as_deref(), Some("2031-05-31"));

    // Now take it back.
    update(&mut model, Msg::Char(' '));
    let Effect::Append(inverse) = update(&mut model, Msg::Char('u')) else {
        panic!("undo must ask for an append");
    };
    write_and_reload(&mut follower, inverse, &mut model);

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.docs.iter().find(|d| d.id == "coc").expect("the document");
    assert_eq!(doc.expiry_date.as_deref(), Some("2026-09-28"), "back to what it was");

    let written =
        std::fs::read_to_string(dir.path().join("meta").join("desk-core.jsonl")).expect("read");
    assert!(written.contains("2031-05-31"), "the edit is still in the journal");
    assert!(written.contains("2026-09-28"), "and so is the op that took it back");
    assert_eq!(
        written.lines().count(),
        5,
        "the three lines it started with plus two appends — neither of them a rewrite"
    );
}

/// **The stack is a stack**: two edits then two undos walk back both, rather
/// than the second undo toggling the first one forward again. Redo is a
/// different verb and is not built.
#[test]
fn undo_walks_back_more_than_one_write() {
    let (dir, journal) = journal_with_a_document();
    let (mut model, _) = load_model(&journal);
    let mut follower = follower(dir.path(), &journal);

    for value in ["2031-05-31", "2032-06-30"] {
        update(&mut model, Msg::EditField(Field::Expiry));
        for _ in 0..10 {
            update(&mut model, Msg::Backspace);
        }
        for c in value.chars() {
            update(&mut model, Msg::Char(c));
        }
        let Effect::Append(drafts) = update(&mut model, Msg::Enter) else { panic!("no append") };
        write_and_reload(&mut follower, drafts, &mut model);
    }
    assert_eq!(model.current().and_then(|d| d.expiry_date.clone()).as_deref(), Some("2032-06-30"));

    for expected in ["2031-05-31", "2026-09-28"] {
        update(&mut model, Msg::Char(' '));
        let Effect::Append(inverse) = update(&mut model, Msg::Char('u')) else {
            panic!("undo must ask for an append");
        };
        write_and_reload(&mut follower, inverse, &mut model);
        assert_eq!(
            model.current().and_then(|d| d.expiry_date.clone()).as_deref(),
            Some(expected),
            "each undo walks back one more write"
        );
    }

    // And then there is nothing left that this session did.
    update(&mut model, Msg::Char(' '));
    assert_eq!(update(&mut model, Msg::Char('u')), Effect::Redraw, "nothing more to undo");
    assert!(model.flash.as_deref().is_some_and(|say| say.contains("nothing to undo")));
}

/// Saves what the model asked for through the session's own follower and
/// hands the model what comes back.
fn write_and_reload(follower: &mut ds::follow::Follower, drafts: Vec<Draft>, model: &mut Model) {
    let msg = follower.save(drafts);
    assert!(matches!(msg, Msg::Saved(_)), "{msg:?}");
    update(model, msg);
}

/// **Redo puts the write back, and the journal ends up holding all three ops.**
/// Undo and redo are both ordinary appends, so a value that was set, taken back
/// and put again leaves three lines behind — not one line edited twice.
#[test]
fn a_redo_reapplies_the_write_and_the_journal_holds_every_step() {
    let (dir, journal) = journal_with_a_document();
    let (mut model, _) = load_model(&journal);
    let mut follower = follower(dir.path(), &journal);

    update(&mut model, Msg::EditField(Field::Expiry));
    for _ in 0..10 {
        update(&mut model, Msg::Backspace);
    }
    for c in "2031-05-31".chars() {
        update(&mut model, Msg::Char(c));
    }
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else { panic!("no append") };
    write_and_reload(&mut follower, drafts, &mut model);

    update(&mut model, Msg::Char(' '));
    let Effect::Append(back) = update(&mut model, Msg::Char('u')) else { panic!("no undo") };
    write_and_reload(&mut follower, back, &mut model);
    assert_eq!(model.current().and_then(|d| d.expiry_date.clone()).as_deref(), Some("2026-09-28"));

    let Effect::Append(again) = update(&mut model, Msg::Redo) else { panic!("no redo") };
    write_and_reload(&mut follower, again, &mut model);

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.docs.iter().find(|d| d.id == "coc").expect("the document");
    assert_eq!(doc.expiry_date.as_deref(), Some("2031-05-31"), "the write is back");

    let written =
        std::fs::read_to_string(dir.path().join("meta").join("desk-core.jsonl")).expect("read");
    assert_eq!(
        written.lines().count(),
        6,
        "three lines to begin with, then a write, an undo and a redo — each an append"
    );
}

/// **Undo and redo of a create work on a tombstoned document.** §3.2 keeps a
/// `create` forever and makes a later one a legitimate recreate that starts from
/// empty, so putting a new document back has to re-send its name as well — which
/// it does, because redo appends the ops that were written the first time.
#[test]
fn a_created_document_can_be_taken_back_and_put_again() {
    let (dir, journal) = journal_with_a_document();
    let (mut model, _) = load_model(&journal);
    let mut follower = follower(dir.path(), &journal);

    for c in "Seaman Book".chars() {
        update(&mut model, Msg::Char(c));
    }
    assert!(model.on_new, "nothing matches, so + new is selected");
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else { panic!("no append") };
    write_and_reload(&mut follower, drafts, &mut model);
    assert!(model.store.docs.iter().any(|d| d.id == "seaman-book-desk"));

    update(&mut model, Msg::Char(' '));
    let Effect::Append(back) = update(&mut model, Msg::Char('u')) else { panic!("no undo") };
    write_and_reload(&mut follower, back, &mut model);
    assert!(
        !model.store.docs.iter().any(|d| d.id == "seaman-book-desk"),
        "the tombstone took it out of the fold"
    );

    let Effect::Append(again) = update(&mut model, Msg::Redo) else { panic!("no redo") };
    write_and_reload(&mut follower, again, &mut model);

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded
        .store
        .docs
        .iter()
        .find(|d| d.id == "seaman-book-desk")
        .expect("the document is back");
    assert_eq!(doc.name, "Seaman Book", "with its name, which a bare recreate would not have");
}

/// **A deleted document comes back whole.** This is the test the whole delete
/// slice exists for: §3.2's tombstone is retained forever and a later `create`
/// starts from *empty*, so an undo that only re-created the entity would give
/// back a document with a name and nothing else — every tag, date, file and note
/// silently gone, on the one keystroke a user presses precisely because they
/// want their data back.
#[test]
fn undoing_a_delete_restores_every_field() {
    let dir = tempfile::tempdir().expect("tempdir");
    let meta = dir.path().join("meta");
    std::fs::create_dir_all(&meta).expect("mkdir");
    let drafts = vec![
        Draft::create("doc", "coc"),
        Draft::set("doc", "coc", "name", "COC Certificate"),
        Draft::set("doc", "coc", "expiry_date", "2026-09-28"),
        Draft::set("doc", "coc", "tags", json!(["marine", "ticket"])),
        Draft::set("doc", "coc", "location", "cert-file"),
        Draft::set("doc", "coc", "notes", "the one with the stamp"),
        Draft::set(
            "doc",
            "coc",
            "files",
            json!([{"label": "complete", "path": "Marine/coc.pdf", "primary": true}]),
        ),
    ];
    std::fs::write(meta.join("desk-core.jsonl"), desk_lines(1_700_000_000_001, drafts))
        .expect("write");
    let journal = Journal::new(dir.path());

    let (mut model, _) = load_model(&journal);
    let mut follower = follower(dir.path(), &journal);
    let before = model.store.docs.iter().find(|d| d.id == "coc").expect("the document").clone();

    update(&mut model, Msg::Enter);
    update(&mut model, Msg::Char('d'));
    let Effect::Append(drafts) = update(&mut model, Msg::Char('d')) else {
        panic!("the second d must ask for an append");
    };
    write_and_reload(&mut follower, drafts, &mut model);
    assert!(
        !model.store.docs.iter().any(|d| d.id == "coc"),
        "the tombstone took it out of the fold"
    );

    update(&mut model, Msg::Char(' '));
    let Effect::Append(back) = update(&mut model, Msg::Char('u')) else { panic!("no undo") };
    write_and_reload(&mut follower, back, &mut model);

    let reloaded = ds::load::load(&journal).expect("reload");
    let after = reloaded.store.docs.iter().find(|d| d.id == "coc").expect("it is back");
    assert_eq!(after, &before, "and it is the same document, field for field");
}

/// **Creating a location from the picker is one change.** Filed into a new
/// location, reloaded, then undone: the location is tombstoned and the document
/// is unfiled again, with nothing left half-done in between.
#[test]
fn a_location_created_while_filing_is_taken_back_whole() {
    let (dir, journal) = journal_with_a_document();
    let (mut model, _) = load_model(&journal);
    let mut follower = follower(dir.path(), &journal);

    update(&mut model, Msg::Enter);
    update(&mut model, Msg::Leader);
    update(&mut model, Msg::Char('l'));
    for c in "grey box".chars() {
        update(&mut model, Msg::Char(c));
    }
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else {
        panic!("+ new must ask for an append");
    };
    write_and_reload(&mut follower, drafts, &mut model);
    let doc = model.store.docs.iter().find(|d| d.id == "coc").expect("the document");
    assert_eq!(model.store.place(doc), "grey box");
    assert_eq!(model.flash.as_deref(), Some("filed in grey box"));

    let Effect::Append(drafts) = update(&mut model, Msg::Undo) else {
        panic!("undo must ask for an append");
    };
    write_and_reload(&mut follower, drafts, &mut model);
    let doc = model.store.docs.iter().find(|d| d.id == "coc").expect("the document");
    assert_eq!(doc.location, None, "unfiled again");
    assert!(model.store.locations.is_empty(), "and the location is gone");
}

/// **Deleting a location takes everything inside it, and undo puts all of it
/// back.** The documents filed there are never rewritten: they read as unfiled
/// while the locations are gone, and as filed again once undo recreates them.
#[test]
fn a_deleted_location_comes_back_with_everything_inside() {
    let (dir, journal) = journal_with_a_document();
    let drafts = vec![
        Draft::create("location", "desk"),
        Draft::set("location", "desk", "name", "desk"),
        Draft::create("location", "folder"),
        Draft::set("location", "folder", "name", "leather folder"),
        Draft::set("location", "folder", "parent", "desk"),
        Draft::set("doc", "coc", "location", "folder"),
    ];
    let file = dir.path().join("meta").join("desk-core.jsonl");
    let mut text = std::fs::read_to_string(&file).expect("read");
    text.push_str(&desk_lines(1_700_000_000_004, drafts));
    std::fs::write(&file, text).expect("write");

    let (mut model, _) = load_model(&journal);
    let mut follower = follower(dir.path(), &journal);
    let place = |model: &Model| {
        let doc = model.store.docs.iter().find(|d| d.id == "coc").expect("the document");
        model.store.place(doc)
    };
    assert_eq!(place(&model), "desk › leather folder");

    update(&mut model, Msg::Enter);
    update(&mut model, Msg::Leader);
    update(&mut model, Msg::Char('l'));
    update(&mut model, Msg::Move(ds::app::Motion::Up));
    update(&mut model, Msg::Char(' '));
    update(&mut model, Msg::Char('d'));
    let Effect::Append(drafts) = update(&mut model, Msg::Char('d')) else {
        panic!("the second d must ask for an append");
    };
    write_and_reload(&mut follower, drafts, &mut model);
    assert!(model.store.locations.is_empty(), "both locations are gone");
    assert_eq!(place(&model), "", "the hard copy reads as unfiled");

    let Effect::Append(drafts) = update(&mut model, Msg::Undo) else {
        panic!("undo must ask for an append");
    };
    write_and_reload(&mut follower, drafts, &mut model);
    assert_eq!(place(&model), "desk › leather folder", "filed again, where it was");
}

/// A new version folds as the latest one and leaves the old version in place,
/// replaced and keeping its own expiry date.
#[test]
fn a_new_version_replaces_the_old_one_in_the_fold() {
    let (dir, journal) = journal_with_a_document();
    let (mut model, _) = load_model(&journal);
    let mut follower = follower(dir.path(), &journal);

    update(&mut model, Msg::Enter);
    update(&mut model, Msg::Char(' '));
    let Effect::Append(drafts) = update(&mut model, Msg::Char('n')) else { panic!("no append") };
    write_and_reload(&mut follower, drafts, &mut model);

    let store = ds::load::load(&journal).expect("reload").store;
    let ids: Vec<&str> = store.versions("coc").iter().map(|&i| store.docs[i].id.as_str()).collect();
    assert_eq!(ids, ["coc", "coc-certificate-desk"]);
    let old = &store.docs[store.index_of("coc").unwrap()];
    assert!(old.superseded);
    assert_eq!(old.expiry_date.as_deref(), Some("2026-09-28"));
    let new = &store.docs[store.index_of("coc-certificate-desk").unwrap()];
    assert_eq!(new.name, "COC Certificate");
    assert_eq!(new.expiry_date, None, "dates start empty");
}

/// A bundle created from the Bundles view folds back as a bundle, never as a
/// document.
#[test]
fn a_created_bundle_folds_as_its_own_record() {
    let (dir, journal) = journal_with_a_document();
    let (mut model, _) = load_model(&journal);
    let mut follower = follower(dir.path(), &journal);

    update(&mut model, Msg::Char(' '));
    update(&mut model, Msg::Char('b'));
    for c in "Joining".chars() {
        update(&mut model, Msg::Char(c));
    }
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else { panic!("no append") };
    write_and_reload(&mut follower, drafts, &mut model);

    let store = ds::load::load(&journal).expect("reload").store;
    assert_eq!(store.bundle("joining-desk").map(|b| b.name.as_str()), Some("Joining"));
    assert_eq!(store.docs.len(), 1, "no document was made");
    assert!(matches!(model.views.last(), Some(ds::View::Bundle { .. })), "it opened");
}

/// Returns the journal line in which `writer` sets `coc`'s `field` at `ts`.
fn line(ts: i64, writer: &str, field: &str, value: &str) -> String {
    Draft::set("doc", "coc", field, value).stamp(ts, writer).to_line().unwrap()
}

/// The follower a session on this journal would start with.
fn follower(dir: &std::path::Path, journal: &Journal) -> ds::follow::Follower {
    let loaded = ds::load::load(journal).expect("load");
    let owner = ds::follow::Owner::new("desk", dir, &lock_dir(dir));
    ds::follow::Follower::new(journal.clone(), Some(owner), loaded.stamp, loaded.stats.max_ts())
}

fn name_in(msg: &Msg) -> String {
    let (Msg::Saved(store) | Msg::Reloaded(store)) = msg else { panic!("no store in {msg:?}") };
    store.docs.iter().find(|doc| doc.id == "coc").expect("coc").name.clone()
}

/// **Another device's op reaches a running session**: a poll finds nothing
/// until a file changes, then hands over the store re-read with it.
#[test]
fn a_poll_brings_in_another_writers_op() {
    let (dir, journal) = journal_with_a_document();
    let mut follower = follower(dir.path(), &journal);
    assert!(follower.poll().is_none(), "nothing changed");

    let phone = dir.path().join("meta").join("phone-core.jsonl");
    std::fs::write(&phone, line(1_700_000_000_010, "phone-core", "name", "COC PHONE") + "\n")
        .expect("write");
    let reloaded = follower.poll().expect("the new file is noticed");
    assert!(matches!(reloaded, Msg::Reloaded(_)));
    assert_eq!(name_in(&reloaded), "COC PHONE");
    assert!(follower.poll().is_none(), "and read once");
}

/// **An edit made after reading another device's op wins over it**, even when
/// that device's clock runs a day ahead: the writer's clock is raised past
/// everything it has read.
#[test]
fn a_save_sorts_after_what_it_read() {
    let (dir, journal) = journal_with_a_document();
    let mut follower = follower(dir.path(), &journal);
    let ahead = journal::Hlc::seeded(0).tick() + 86_400_000;
    let phone = dir.path().join("meta").join("phone-core.jsonl");
    std::fs::write(&phone, line(ahead, "phone-core", "name", "COC PHONE") + "\n").expect("write");

    let saved = follower.save(vec![Draft::set("doc", "coc", "name", "COC DESK")]);
    assert_eq!(name_in(&saved), "COC DESK", "{saved:?}");
    let reread = ds::load::load(&journal).expect("load");
    assert_eq!(reread.store.docs[0].name, "COC DESK", "and on disk");
}
