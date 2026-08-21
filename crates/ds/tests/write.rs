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
use journal::{Journal, Namespace, Writer};

/// A journal directory of this test's own, with one document in it.
fn journal_with_a_document(name: &str) -> (PathBuf, Journal) {
    let dir = std::env::temp_dir().join(format!("ds-write-{name}"));
    let _ = std::fs::remove_dir_all(&dir);
    let meta = dir.join("meta");
    std::fs::create_dir_all(&meta).expect("mkdir");
    let lines = [
        r#"{"v":1,"ts":1700000000001,"w":"desk-core","op":"create","ent":"doc","id":"coc"}"#,
        r#"{"v":1,"ts":1700000000002,"w":"desk-core","op":"set","ent":"doc","id":"coc","f":"name","val":"COC Certificate"}"#,
        r#"{"v":1,"ts":1700000000003,"w":"desk-core","op":"set","ent":"doc","id":"coc","f":"expiry_date","val":"2026-09-28"}"#,
    ];
    std::fs::write(meta.join("desk-core.jsonl"), format!("{}\n", lines.join("\n"))).expect("write");
    let journal = Journal::new(&dir);
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
    let (dir, journal) = journal_with_a_document("roundtrip");
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

    // What the writer thread does with it, exactly as `main.rs` does it.
    let mut writer = Writer::open(
        &journal,
        Namespace::Meta,
        "phone-core",
        &lock_dir(&dir),
        loaded.marks().values().map(|mark| mark.max_ts).max().unwrap_or(0),
    )
    .expect("open the writer");
    let ops = writer.append_all(drafts).expect("append");
    writer.commit().expect("fsync");
    assert_eq!(ops.len(), 1);
    assert_eq!(ops[0].w, "phone-core", "this device wrote it, under its own id");

    // A second device's file, never an edit to the first one's — which is what
    // makes Syncthing conflicts structurally impossible.
    assert!(dir.join("meta").join("phone-core.jsonl").is_file());
    let original = std::fs::read_to_string(dir.join("meta").join("desk-core.jsonl")).expect("read");
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
    let (dir, journal) = journal_with_a_document("unset");
    let (mut model, _) = load_model(&journal);

    update(&mut model, Msg::EditField(Field::Expiry));
    for _ in 0..10 {
        update(&mut model, Msg::Backspace);
    }
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else {
        panic!("an empty buffer must still ask for an append");
    };

    let mut writer =
        Writer::open(&journal, Namespace::Meta, "phone-core", &lock_dir(&dir), 1_700_000_000_003)
            .expect("open the writer");
    writer.append_all(drafts).expect("append");
    writer.commit().expect("fsync");
    drop(writer);

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.docs.iter().find(|d| d.id == "coc").expect("the document");
    assert_eq!(doc.expiry_date, None, "the field is gone, not blank");
    assert!(!doc.is_tracked(), "so it is out of the expiry watch");
    assert!(reloaded.store.expiring().is_empty());
}

/// **A journal another process is writing degrades this one to read-only, and
/// says so** (REWRITE.md §3.1) — it is never an error to exit on, because
/// browsing, opening and `ds status` all still work.
#[test]
fn a_held_lock_is_a_notice_and_not_a_failure() {
    let (dir, journal) = journal_with_a_document("locked");
    let locks = lock_dir(&dir);
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
    let (dir, journal) = journal_with_a_document("union");
    let locks = lock_dir(&dir);

    let mut phone =
        Writer::open(&journal, Namespace::Meta, "phone-core", &locks, 1_700_000_000_003)
            .expect("phone");
    phone
        .append_all(vec![journal::Draft::set("doc", "coc", "expiry_date", "2030-01-01")])
        .expect("append");
    phone.commit().expect("fsync");
    drop(phone);

    let mut desk = Writer::open(&journal, Namespace::Meta, "desk-core", &locks, 1_700_000_000_003)
        .expect("desk");
    desk.append_all(vec![journal::Draft::set("doc", "coc", "notes", "renewed in Mumbai")])
        .expect("append");
    desk.commit().expect("fsync");
    drop(desk);

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.docs.iter().find(|d| d.id == "coc").expect("the document");
    assert_eq!(doc.expiry_date.as_deref(), Some("2030-01-01"), "the phone's field");
    assert_eq!(doc.notes, "renewed in Mumbai", "and the desk's, from the same fold");
    assert!(dir.join("meta").join("phone-core.jsonl").is_file());
    assert!(dir.join("meta").join("desk-core.jsonl").is_file());
}

/// **A document created in the TUI exists after a reload.** The model tests
/// prove the two ops are asked for in the right order; only a real journal
/// proves the fold accepts them — a `set` that reached the file before its
/// `create` would be orphaned, and the new document would simply not be there.
#[test]
fn a_created_document_survives_a_reload() {
    let (dir, journal) = journal_with_a_document("create");
    let (mut model, loaded) = load_model(&journal);
    let before = model.store.docs.len();

    update(&mut model, Msg::Char(' '));
    update(&mut model, Msg::Char('n'));
    for c in "Seaman Book".chars() {
        update(&mut model, Msg::Char(c));
    }
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else {
        panic!("naming a new document must ask for an append");
    };

    let mut writer = Writer::open(
        &journal,
        Namespace::Meta,
        "desk-core",
        &lock_dir(&dir),
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
