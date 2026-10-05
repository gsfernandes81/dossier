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

use std::io::Write as _;
use std::path::PathBuf;

mod common;

use common::{clear_buffer, picking, render, type_str, writable};
use ds::app::{update, Effect, Model, Msg};
use ds::edit::Field;
use ds::theme::Theme;
use journal::{Draft, Journal, Namespace, Op, Writer};
use serde_json::json;
use tempfile::TempDir;

/// The document most tests start from.
fn coc() -> Vec<Draft> {
    vec![
        Draft::create("doc", "coc"),
        Draft::set("doc", "coc", "name", "COC Certificate"),
        Draft::set("doc", "coc", "expiry_date", "2026-09-28"),
    ]
}

/// Returns `desk-core`'s ops for `drafts`, stamped a millisecond apart.
fn desk(drafts: Vec<Draft>) -> Vec<Op> {
    (1_700_000_000_001..).zip(drafts).map(|(ts, d)| d.stamp(ts, "desk-core")).collect()
}

/// A journal directory of this test's own, holding `ops` in their writers' files.
fn journal_with(ops: &[Op]) -> (TempDir, Journal) {
    let dir = tempfile::tempdir().expect("tempdir");
    let journal = Journal::new(dir.path());
    for op in ops {
        let path = journal.file_path(Namespace::Meta, &op.w);
        std::fs::create_dir_all(path.parent().expect("a namespace dir")).expect("mkdir");
        let mut file =
            std::fs::OpenOptions::new().create(true).append(true).open(&path).expect("open");
        writeln!(file, "{}", op.to_line().expect("serializes")).expect("write");
    }
    (dir, journal)
}

/// Where the writer's advisory lock goes — a directory of this test's own, and
/// never the journal: a lock inside the synced tree would replicate to the other
/// device and lock it out of its own file.
fn lock_dir(dir: &std::path::Path) -> PathBuf {
    let locks = dir.join("state");
    std::fs::create_dir_all(&locks).expect("mkdir");
    locks
}

/// Build the model the TUI would have, from a journal on disk.
fn load_model(journal: &Journal) -> Model {
    let loaded = ds::load::load(journal).expect("load");
    writable(Model::new(loaded.store, loaded.today, loaded.warn_until, 47, 24))
}

#[test]
fn an_edit_becomes_an_op_and_survives_a_reload() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut model = load_model(&journal);

    let drafts = save_expiry(&mut model, "2031-05-31");
    write_and_reload(&mut follower(dir.path(), &journal, "phone"), drafts, &mut model);
    let screen = render(&mut model, 47, 24, Theme { color: true });
    let text: String = screen.content().iter().map(ratatui::buffer::Cell::symbol).collect();
    assert!(text.contains("2031-05-31"), "the screen shows the edit: {text}");
    let load = journal.load(Namespace::Meta).expect("load");
    let phone = load.files.iter().find(|file| file.writer == "phone-core");
    assert_eq!(phone.map(|file| file.ops), Some(1), "this device wrote it, under its own id");

    // A second device's file, never an edit to the first one's — which is what
    // makes Syncthing conflicts structurally impossible.
    assert!(dir.path().join("meta").join("phone-core.jsonl").is_file());
    let original =
        std::fs::read_to_string(dir.path().join("meta").join("desk-core.jsonl")).expect("read");
    assert_eq!(original.lines().count(), 3, "the other writer's file was not touched");

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.get("coc").expect("the document");
    assert_eq!(doc.expiry_date.as_deref(), Some("2031-05-31"), "the edit is in the store");
}

#[test]
fn clearing_the_field_removes_it_from_the_folded_store() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut model = load_model(&journal);

    let drafts = save_expiry(&mut model, "");
    write_and_reload(&mut follower(dir.path(), &journal, "phone"), drafts, &mut model);

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.get("coc").expect("the document");
    assert_eq!(doc.expiry_date, None, "the field is gone, not blank");
    assert!(!doc.is_tracked(), "so it is out of the expiry watch");
    let due = reloaded.store.due("2000-01-01", "2100-01-01");
    assert!(due.is_empty(), "{due:?}");
}

/// Never an error to exit on: browsing, opening and `ds status` all still work.
#[test]
fn a_held_lock_is_a_notice_and_not_a_failure() {
    let (dir, journal) = journal_with(&desk(coc()));
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
    let mut model = load_model(&journal);
    model.open_edit(Field::Expiry);
    update(&mut model, Msg::Char('9'));
    update(&mut model, Msg::Enter);
    update(&mut model, Msg::SaveFailed { reason: error.to_string(), permanent: true });

    assert_eq!(model.write.reason(), Some(error.to_string().as_str()));
    assert!(model.edit.is_some(), "the typing is still there to try again elsewhere");
    assert!(!model.rows.is_empty(), "and the store is still browsable");
}

/// The journal crate tests this too; here it goes through the app's own loader.
#[test]
fn two_devices_write_two_files_and_fold_to_one_store() {
    let (dir, journal) = journal_with(&desk(coc()));
    for (device, draft) in [
        ("phone", Draft::set("doc", "coc", "expiry_date", "2030-01-01")),
        ("desk", Draft::set("doc", "coc", "notes", "renewed in Mumbai")),
    ] {
        let saved = follower(dir.path(), &journal, device).save(vec![draft]);
        assert!(matches!(saved, Msg::Saved(_)), "{saved:?}");
    }

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.get("coc").expect("the document");
    assert_eq!(doc.expiry_date.as_deref(), Some("2030-01-01"), "the phone's field");
    assert_eq!(doc.notes, "renewed in Mumbai", "and the desk's, from the same fold");
    assert!(dir.path().join("meta").join("phone-core.jsonl").is_file());
    assert!(dir.path().join("meta").join("desk-core.jsonl").is_file());
}

/// A `set` that reached the file before its `create` would be orphaned, and the
/// document would not be there.
#[test]
fn a_created_document_survives_a_reload() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut model = load_model(&journal);
    let before = model.store.docs.len();

    type_str(&mut model, "Seaman Book");
    assert!(model.on_new, "nothing matches, so + new is selected");
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else {
        panic!("naming a new document must ask for an append");
    };
    write_and_reload(&mut follower(dir.path(), &journal, "desk"), drafts, &mut model);

    let reloaded = ds::load::load(&journal).expect("reload");
    assert_eq!(reloaded.store.docs.len(), before + 1);
    let doc =
        reloaded.store.get("seaman-book-desk").expect("the new document, keyed by name and device");
    assert_eq!(doc.name, "Seaman Book");
    assert_eq!(doc.expiry_date, None, "and nothing it was not given");
}

/// The journal is the history, so the file still holds both the edit and its
/// inverse.
#[test]
fn an_undo_restores_the_field_and_leaves_both_ops_in_the_journal() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut model = load_model(&journal);
    let mut follower = follower(dir.path(), &journal, "desk");

    let drafts = save_expiry(&mut model, "2031-05-31");
    write_and_reload(&mut follower, drafts, &mut model);
    assert_eq!(model.current().and_then(|d| d.expiry_date.clone()).as_deref(), Some("2031-05-31"));

    // Now take it back.
    update(&mut model, Msg::Char(' '));
    let Effect::Append(inverse) = update(&mut model, Msg::Char('u')) else {
        panic!("undo must ask for an append");
    };
    write_and_reload(&mut follower, inverse, &mut model);

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.get("coc").expect("the document");
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

/// Two undos walk back both edits rather than the second toggling the first
/// forward again.
#[test]
fn undo_walks_back_more_than_one_write() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut model = load_model(&journal);
    let mut follower = follower(dir.path(), &journal, "desk");

    for value in ["2031-05-31", "2032-06-30"] {
        let drafts = save_expiry(&mut model, value);
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

/// Replaces the expiry with `value` and returns the ops the model asks to append.
fn save_expiry(model: &mut Model, value: &str) -> Vec<Draft> {
    model.open_edit(Field::Expiry);
    clear_buffer(model);
    type_str(model, value);
    let Effect::Append(drafts) = update(model, Msg::Enter) else {
        panic!("saving {value:?} must ask for an append");
    };
    drafts
}

/// Saves what the model asked for through the session's own follower and
/// hands the model what comes back.
fn write_and_reload(follower: &mut ds::follow::Follower, drafts: Vec<Draft>, model: &mut Model) {
    let msg = follower.save(drafts);
    assert!(matches!(msg, Msg::Saved(_)), "{msg:?}");
    update(model, msg);
}

#[test]
fn a_redo_reapplies_the_write_and_the_journal_holds_every_step() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut model = load_model(&journal);
    let mut follower = follower(dir.path(), &journal, "desk");

    let drafts = save_expiry(&mut model, "2031-05-31");
    write_and_reload(&mut follower, drafts, &mut model);

    update(&mut model, Msg::Char(' '));
    let Effect::Append(back) = update(&mut model, Msg::Char('u')) else { panic!("no undo") };
    write_and_reload(&mut follower, back, &mut model);
    assert_eq!(model.current().and_then(|d| d.expiry_date.clone()).as_deref(), Some("2026-09-28"));

    let Effect::Append(again) = update(&mut model, Msg::Redo) else { panic!("no redo") };
    write_and_reload(&mut follower, again, &mut model);

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.get("coc").expect("the document");
    assert_eq!(doc.expiry_date.as_deref(), Some("2031-05-31"), "the write is back");

    let written =
        std::fs::read_to_string(dir.path().join("meta").join("desk-core.jsonl")).expect("read");
    assert_eq!(
        written.lines().count(),
        6,
        "three lines to begin with, then a write, an undo and a redo — each an append"
    );
}

/// Putting a created document back is a recreate after its tombstone, so redo
/// has to re-send the name as well.
#[test]
fn a_created_document_can_be_taken_back_and_put_again() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut model = load_model(&journal);
    let mut follower = follower(dir.path(), &journal, "desk");

    type_str(&mut model, "Seaman Book");
    assert!(model.on_new, "nothing matches, so + new is selected");
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else { panic!("no append") };
    write_and_reload(&mut follower, drafts, &mut model);
    assert!(model.store.get("seaman-book-desk").is_some());

    update(&mut model, Msg::Char(' '));
    let Effect::Append(back) = update(&mut model, Msg::Char('u')) else { panic!("no undo") };
    write_and_reload(&mut follower, back, &mut model);
    assert!(model.store.get("seaman-book-desk").is_none(), "the tombstone took it out of the fold");

    let Effect::Append(again) = update(&mut model, Msg::Redo) else { panic!("no redo") };
    write_and_reload(&mut follower, again, &mut model);

    let reloaded = ds::load::load(&journal).expect("reload");
    let doc = reloaded.store.get("seaman-book-desk").expect("the document is back");
    assert_eq!(doc.name, "Seaman Book", "with its name, which a bare recreate would not have");
}

/// The fold's tombstone is kept forever and a later `create` starts from empty,
/// so an undo that only re-created the entity would give back a name and nothing
/// else.
#[test]
fn undoing_a_delete_restores_every_field() {
    let mut drafts = coc();
    drafts.extend([
        Draft::set("doc", "coc", "tags", json!(["marine", "ticket"])),
        Draft::set("doc", "coc", "location", "cert-file"),
        Draft::set("doc", "coc", "notes", "the one with the stamp"),
        Draft::set(
            "doc",
            "coc",
            "files",
            json!([{"label": "complete", "path": "Marine/coc.pdf", "primary": true}]),
        ),
    ]);
    let (dir, journal) = journal_with(&desk(drafts));

    let mut model = load_model(&journal);
    let mut follower = follower(dir.path(), &journal, "desk");
    let before = model.store.get("coc").expect("the document").clone();

    update(&mut model, Msg::Enter);
    update(&mut model, Msg::Char('d'));
    let Effect::Append(drafts) = update(&mut model, Msg::Char('d')) else {
        panic!("the second d must ask for an append");
    };
    write_and_reload(&mut follower, drafts, &mut model);
    assert!(model.store.get("coc").is_none(), "the tombstone took it out of the fold");

    update(&mut model, Msg::Char(' '));
    let Effect::Append(back) = update(&mut model, Msg::Char('u')) else { panic!("no undo") };
    write_and_reload(&mut follower, back, &mut model);

    let reloaded = ds::load::load(&journal).expect("reload");
    let after = reloaded.store.get("coc").expect("it is back");
    assert_eq!(after, &before, "and it is the same document, field for field");
}

#[test]
fn a_location_created_while_filing_is_taken_back_whole() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut model = load_model(&journal);
    let mut follower = follower(dir.path(), &journal, "desk");

    picking(&mut model);
    type_str(&mut model, "grey box");
    let Effect::Append(drafts) = update(&mut model, Msg::Enter) else {
        panic!("+ new must ask for an append");
    };
    write_and_reload(&mut follower, drafts, &mut model);
    let doc = model.store.get("coc").expect("the document");
    assert_eq!(model.store.place(doc), "grey box");
    assert_eq!(model.flash.as_deref(), Some("filed in grey box"));

    let Effect::Append(drafts) = update(&mut model, Msg::Undo) else {
        panic!("undo must ask for an append");
    };
    write_and_reload(&mut follower, drafts, &mut model);
    let doc = model.store.get("coc").expect("the document");
    assert_eq!(doc.location, None, "unfiled again");
    assert!(model.store.locations.is_empty(), "and the location is gone");
}

/// The documents filed there are never rewritten: they read as unfiled while the
/// locations are gone, and as filed again once undo recreates them.
#[test]
fn a_deleted_location_comes_back_with_everything_inside() {
    let mut drafts = coc();
    drafts.extend([
        Draft::create("location", "desk"),
        Draft::set("location", "desk", "name", "desk"),
        Draft::create("location", "folder"),
        Draft::set("location", "folder", "name", "leather folder"),
        Draft::set("location", "folder", "parent", "desk"),
        Draft::set("doc", "coc", "location", "folder"),
    ]);
    let (dir, journal) = journal_with(&desk(drafts));

    let mut model = load_model(&journal);
    let mut follower = follower(dir.path(), &journal, "desk");
    let place = |model: &Model| {
        let doc = model.store.get("coc").expect("the document");
        model.store.place(doc)
    };
    assert_eq!(place(&model), "desk › leather folder");

    picking(&mut model);
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

#[test]
fn a_new_version_replaces_the_old_one_in_the_fold() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut model = load_model(&journal);
    let mut follower = follower(dir.path(), &journal, "desk");

    update(&mut model, Msg::Enter);
    update(&mut model, Msg::Char(' '));
    let Effect::Append(drafts) = update(&mut model, Msg::Char('n')) else { panic!("no append") };
    write_and_reload(&mut follower, drafts, &mut model);

    let store = ds::load::load(&journal).expect("reload").store;
    let ids: Vec<&str> = store.versions("coc").iter().map(|&i| store.docs[i].id.as_str()).collect();
    assert_eq!(ids, ["coc", "coc-certificate-desk"]);
    let old = store.get("coc").unwrap();
    assert!(old.superseded);
    assert_eq!(old.expiry_date.as_deref(), Some("2026-09-28"));
    let new = store.get("coc-certificate-desk").unwrap();
    assert_eq!(new.name, "COC Certificate");
    assert_eq!(new.expiry_date, None, "dates start empty");
}

#[test]
fn a_created_bundle_folds_as_its_own_record() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut model = load_model(&journal);
    let mut follower = follower(dir.path(), &journal, "desk");

    update(&mut model, Msg::Char(' '));
    update(&mut model, Msg::Char('b'));
    type_str(&mut model, "Joining");
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

/// The follower a session on this journal would start with, writing as `device`.
fn follower(dir: &std::path::Path, journal: &Journal, device: &str) -> ds::follow::Follower {
    let loaded = ds::load::load(journal).expect("load");
    let owner = ds::follow::Owner::new(device, dir, &lock_dir(dir));
    ds::follow::Follower::new(journal.clone(), Some(owner), loaded.stamp, loaded.stats.max_ts)
}

fn name_in(msg: &Msg) -> String {
    let (Msg::Saved(store) | Msg::Reloaded(store)) = msg else { panic!("no store in {msg:?}") };
    store.get("coc").expect("coc").name.clone()
}

#[test]
fn a_poll_brings_in_another_writers_op() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut follower = follower(dir.path(), &journal, "desk");
    assert!(follower.poll().is_none(), "nothing changed");

    let phone = dir.path().join("meta").join("phone-core.jsonl");
    std::fs::write(&phone, line(1_700_000_000_010, "phone-core", "name", "COC PHONE") + "\n")
        .expect("write");
    let reloaded = follower.poll().expect("the new file is noticed");
    assert!(matches!(reloaded, Msg::Reloaded(_)));
    assert_eq!(name_in(&reloaded), "COC PHONE");
    assert!(follower.poll().is_none(), "and read once");
}

/// The writer's clock is raised past everything it has read, so a device whose
/// clock runs a day ahead still loses.
#[test]
fn a_save_sorts_after_what_it_read() {
    let (dir, journal) = journal_with(&desk(coc()));
    let mut follower = follower(dir.path(), &journal, "desk");
    let ahead = journal::Hlc::seeded(0).tick() + 86_400_000;
    let phone = dir.path().join("meta").join("phone-core.jsonl");
    std::fs::write(&phone, line(ahead, "phone-core", "name", "COC PHONE") + "\n").expect("write");

    let saved = follower.save(vec![Draft::set("doc", "coc", "name", "COC DESK")]);
    assert_eq!(name_in(&saved), "COC DESK", "{saved:?}");
    let reread = ds::load::load(&journal).expect("load");
    assert_eq!(reread.store.docs[0].name, "COC DESK", "and on disk");
}

/// A save that fails still read the other writer's new ops; they must reach
/// the screen at the next poll rather than wait for another change.
#[test]
fn a_failed_save_still_lets_the_next_poll_bring_in_another_writers_op() {
    let (dir, journal) = journal_with(&desk(coc()));
    let loaded = ds::load::load(&journal).expect("load");
    let mut follower =
        ds::follow::Follower::new(journal.clone(), None, loaded.stamp, loaded.stats.max_ts);
    let phone = dir.path().join("meta").join("phone-core.jsonl");
    std::fs::write(&phone, line(1_700_000_000_010, "phone-core", "name", "COC PHONE") + "\n")
        .expect("write");

    let saved = follower.save(vec![Draft::set("doc", "coc", "name", "COC DESK")]);
    assert!(matches!(saved, Msg::SaveFailed { permanent: true, .. }), "{saved:?}");
    let reloaded = follower.poll().expect("the other writer's op is still news");
    assert!(matches!(reloaded, Msg::Reloaded(_)));
    assert_eq!(name_in(&reloaded), "COC PHONE");
}
