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

//! The commands that need no terminal, run as the user runs them.
//!
//! These spawn the **real binary** against a real journal directory, because the
//! things worth checking here are the things a library test cannot see: that the
//! arguments are wired to the code that implements them, that stdout carries
//! what a script would parse, and that the **exit code** distinguishes "the
//! store is damaged" from "your query matched nothing". A cron job's whole
//! contract with `ds status --quiet` is that exit code.

use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

use journal::Draft;
use serde_json::json;
use tempfile::TempDir;

/// Two documents: one expired with a file on disk, one with no file at all.
fn sample() -> (TempDir, PathBuf) {
    let mut drafts = Vec::new();
    for (id, doc_name, expiry, file) in [
        ("coc", "COC Certificate", "2026-01-01", Some("Marine/coc.pdf")),
        ("eng1", "ENG-1 Medical", "2031-01-13", None),
    ] {
        drafts.push(Draft::create("doc", id));
        drafts.push(Draft::set("doc", id, "name", doc_name));
        drafts.push(Draft::set("doc", id, "expiry_date", expiry));
        if let Some(path) = file {
            let files = json!([{"label": "complete", "path": path, "primary": true}]);
            drafts.push(Draft::set("doc", id, "files", files));
        }
    }
    let body: String = (1_700_000_000_000..)
        .zip(drafts)
        .map(|(ts, draft)| draft.stamp(ts, "desk-core").to_line().unwrap() + "\n")
        .collect();
    let (dir, root) = fresh();
    let meta = root.join(".dossier").join("journal").join("meta");
    std::fs::create_dir_all(&meta).expect("mkdir");
    std::fs::write(meta.join("desk-core.jsonl"), body).expect("write");
    std::fs::create_dir_all(root.join("Marine")).expect("mkdir");
    std::fs::write(root.join("Marine/coc.pdf"), "").expect("write");
    (dir, root)
}

/// Runs the binary with its own config directory; `DS_CONFIG_DIR` isolates it on
/// Windows, where `dirs` ignores the environment.
fn sandboxed(root: &Path, args: &[&str]) -> Command {
    let sandbox = root.join("config-home");
    std::fs::create_dir_all(&sandbox).expect("mkdir");
    let mut command = Command::new(env!("CARGO_BIN_EXE_ds"));
    command
        .args(args)
        .env("DS_CONFIG_DIR", &sandbox)
        .env("XDG_CONFIG_HOME", &sandbox)
        .env("HOME", &sandbox)
        .env("LOCALAPPDATA", &sandbox)
        .env("APPDATA", &sandbox)
        .env_remove("DS_ROOT");
    command
}

/// [`sandboxed`], run with `--root`.
fn ds(root: &Path, args: &[&str]) -> Output {
    sandboxed(root, args).arg("--root").arg(root).output().expect("run ds")
}

/// The config file `ds` in this sandbox would read and write.
fn config_path(root: &Path) -> PathBuf {
    root.join("config-home").join("config.toml")
}

/// An empty root, with no journal and no config — a device on its first day.
///
/// The `TempDir` must outlive every `ds` run against the path.
fn fresh() -> (TempDir, PathBuf) {
    let dir = tempfile::tempdir().expect("tempdir");
    let root = dir.path().to_path_buf();
    (dir, root)
}

/// Expiry is not a status finding, so an expired document leaves the report
/// clean.
#[test]
fn status_reports_the_store() {
    let (_dir, root) = sample();
    let out = ds(&root, &["status"]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(out.status.success(), "{text}{}", String::from_utf8_lossy(&out.stderr));
    assert!(text.contains("documents 2"), "{text}");
    assert!(text.contains("desk-core — 7 ops"), "{text}");
    assert!(text.contains("no problems found"), "{text}");
}

#[test]
fn quiet_status_is_silent_when_healthy() {
    let (_dir, root) = sample();
    let out = ds(&root, &["status", "--quiet"]);
    assert!(out.status.success());
    assert_eq!(String::from_utf8_lossy(&out.stdout), "", "nothing to say");
}

#[test]
fn quiet_status_reports_damage_and_exits_non_zero() {
    let (_dir, root) = sample();
    let meta = root.join(".dossier").join("journal").join("meta");
    std::fs::write(meta.join("desk-core.jsonl"), "{ this is not json\n").expect("write");
    let out = ds(&root, &["status", "--quiet"]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert_eq!(out.status.code(), Some(3), "{text}");
    assert!(text.contains("unreadable line"), "{text}");
}

#[test]
fn a_device_with_no_journal_is_not_damaged() {
    let (_dir, root) = fresh();
    let out = ds(&root, &["status"]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(out.status.success(), "{text}");
    assert!(text.contains("not created yet"), "{text}");
    assert!(text.contains("documents 0"), "{text}");
}

/// Nothing matched and too much matched are both exit 2, with the candidates
/// listed so the next attempt can be exact.
#[test]
fn open_refuses_to_guess() {
    let (_dir, root) = sample();

    let out = ds(&root, &["open", "definitely-not-here"]);
    assert_eq!(out.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&out.stderr).contains("nothing matches"));

    // Both documents have an expiry date, so a term they share matches both.
    let out = ds(&root, &["open", "certificate", "medical"]);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(2), "{stderr}");

    let out = ds(&root, &["open", "eng1"]);
    assert_eq!(out.status.code(), Some(2), "matched by id, but has no file");
    assert!(String::from_utf8_lossy(&out.stderr).contains("no file linked"));
}

/// A file that the store lists but that has not synced yet is reported as
/// exactly that — the difference between "wait" and "something is broken".
#[test]
fn open_says_when_a_file_has_not_synced() {
    let (_dir, root) = sample();
    std::fs::remove_file(root.join("Marine/coc.pdf")).expect("remove");
    let out = ds(&root, &["open", "coc"]);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(1), "{stderr}");
    assert!(stderr.contains("Syncthing"), "{stderr}");
}

#[test]
fn init_names_the_device() {
    let (_dir, root) = fresh();
    let out = ds(&root, &["init", "--device", "phone"]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(out.status.success(), "{text}{}", String::from_utf8_lossy(&out.stderr));
    assert!(text.contains("phone-core"), "{text}");

    let written = std::fs::read_to_string(config_path(&root)).expect("config");
    assert!(written.contains("device = \"phone\""), "{written}");
    assert!(written.contains("syncthing_root"), "{written}");
    assert!(!root.join(".dossier").exists(), "the writer makes the journal, not init");
}

#[test]
fn init_refuses_to_overwrite_without_force() {
    let (_dir, root) = fresh();
    assert!(ds(&root, &["init", "--device", "phone"]).status.success());

    let again = ds(&root, &["init", "--device", "desk"]);
    let stderr = String::from_utf8_lossy(&again.stderr);
    assert_eq!(again.status.code(), Some(1), "{stderr}");
    assert!(stderr.contains("--force"), "{stderr}");
    assert!(
        std::fs::read_to_string(config_path(&root)).unwrap().contains("phone"),
        "the refusal changed nothing"
    );

    assert!(ds(&root, &["init", "--device", "desk", "--force"]).status.success());
    assert!(std::fs::read_to_string(config_path(&root)).unwrap().contains("desk"));
}

#[test]
fn init_refuses_a_device_name_the_grammar_rejects() {
    let (_dir, root) = fresh();
    let out = ds(&root, &["init", "--device", "My_Phone"]);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(1), "{stderr}");
    assert!(stderr.contains("My_Phone-core"), "{stderr}");
    assert!(stderr.contains("lowercase"), "{stderr}");
    assert!(!config_path(&root).exists(), "nothing was written");
}

/// A CI job or a pipe has no one to answer the question, so waiting would hang.
#[test]
fn init_without_a_terminal_or_a_flag_fails_fast() {
    let (_dir, root) = fresh();
    let out = sandboxed(&root, &["init", "--root"])
        .arg(&root)
        .stdin(Stdio::null())
        .output()
        .expect("run ds");
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(1), "{stderr}");
    assert!(stderr.contains("--device"), "{stderr}");
}

/// A device that was never set up refuses and says how, rather than reading
/// whatever folder it was started in.
#[test]
fn a_device_not_set_up_points_at_init() {
    let (_dir, root) = fresh();
    for args in [&[][..], &["status"]] {
        let out = sandboxed(&root, args).current_dir(&root).output().expect("run ds");
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert_eq!(out.status.code(), Some(1), "{stderr}");
        assert!(stderr.contains("run `ds init`, or pass --root"), "{stderr}");
    }
}
