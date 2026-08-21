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

/// A journal directory holding one writer file.
fn journal_dir(name: &str, lines: &[String]) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("ds-cli-{name}"));
    let _ = std::fs::remove_dir_all(&dir);
    let meta = dir.join(".dossier").join("journal").join("meta");
    std::fs::create_dir_all(&meta).expect("mkdir");
    std::fs::write(meta.join("desk-core.jsonl"), format!("{}\n", lines.join("\n"))).expect("write");
    dir
}

fn op(ts: i64, verb: &str, id: &str, field: &str, value: &str) -> String {
    let tail =
        if field.is_empty() { String::new() } else { format!(r#","f":"{field}","val":{value}"#) };
    format!(r#"{{"v":1,"ts":{ts},"w":"desk-core","op":"{verb}","ent":"doc","id":"{id}"{tail}}}"#)
}

/// Two documents: one expired with a file, one with no file at all.
fn sample(name: &str) -> PathBuf {
    let mut lines = Vec::new();
    let mut ts = 1_700_000_000_000;
    for (id, doc_name, expiry, file) in [
        ("coc", "COC Certificate", "2026-01-01", Some("Marine/coc.pdf")),
        ("eng1", "ENG-1 Medical", "2031-01-13", None),
    ] {
        lines.push(op(ts, "create", id, "", ""));
        ts += 1;
        lines.push(op(ts, "set", id, "name", &format!("\"{doc_name}\"")));
        ts += 1;
        lines.push(op(ts, "set", id, "expiry_date", &format!("\"{expiry}\"")));
        ts += 1;
        if let Some(path) = file {
            let value = format!(r#"[{{"label":"complete","path":"{path}","primary":true}}]"#);
            lines.push(op(ts, "set", id, "files", &value));
            ts += 1;
        }
    }
    journal_dir(name, &lines)
}

/// Run the built binary with a config directory of its own, so the developer's
/// real config can never change what a test sees — and, now that `ds init`
/// *writes* one, so a test can never change the developer's.
///
/// `DS_CONFIG_DIR` is what makes that true on Windows. The four variables below
/// it sandbox the config directory on Linux only: `dirs` resolves the Windows
/// path through the Known Folder API, which ignores the environment entirely.
/// While `ds` only read config that was merely useless; a writing test would
/// have written the CI runner's real `%LOCALAPPDATA%\dossier\config.toml`.
fn ds(root: &Path, args: &[&str]) -> Output {
    let sandbox = root.join("config-home");
    std::fs::create_dir_all(&sandbox).expect("mkdir");
    Command::new(env!("CARGO_BIN_EXE_ds"))
        .args(args)
        .arg("--root")
        .arg(root)
        .env("DS_CONFIG_DIR", &sandbox)
        .env("XDG_CONFIG_HOME", &sandbox)
        .env("HOME", &sandbox)
        .env("LOCALAPPDATA", &sandbox)
        .env("APPDATA", &sandbox)
        .env_remove("DS_ROOT")
        .output()
        .expect("run ds")
}

/// The config file `ds` in this sandbox would read and write.
fn config_path(root: &Path) -> PathBuf {
    root.join("config-home").join("config.toml")
}

/// An empty root, with no journal and no config — a device on its first day.
fn fresh(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("ds-cli-{name}"));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("mkdir");
    dir
}

/// The full report names the journal, counts the documents, and says the store
/// is healthy — the three things someone runs it to learn.
#[test]
fn status_reports_the_store() {
    let root = sample("status");
    let out = ds(&root, &["status"]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(out.status.success(), "{text}{}", String::from_utf8_lossy(&out.stderr));
    assert!(text.contains("documents 2"), "{text}");
    assert!(text.contains("desk-core — 7 ops"), "{text}");
    assert!(text.contains("expired"), "{text}");
    assert!(text.contains("no anomalies"), "{text}");
}

/// **A healthy store makes `--quiet` say nothing and exit 0.** That silence is
/// the entire value of running it from cron.
#[test]
fn quiet_status_is_silent_when_healthy() {
    let root = sample("quiet");
    let out = ds(&root, &["status", "--quiet"]);
    assert!(out.status.success());
    assert_eq!(String::from_utf8_lossy(&out.stdout), "", "nothing to say");
}

/// A damaged store makes it speak, and exit non-zero so cron notices.
#[test]
fn quiet_status_reports_damage_and_exits_non_zero() {
    let root = sample("damaged");
    let meta = root.join(".dossier").join("journal").join("meta");
    std::fs::write(meta.join("desk-core.jsonl"), "{ this is not json\n").expect("write");
    let out = ds(&root, &["status", "--quiet"]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert_eq!(out.status.code(), Some(3), "{text}");
    assert!(text.contains("malformed"), "{text}");
}

/// A fresh device has no journal, and that is not damage — it says so and still
/// exits 0.
#[test]
fn a_device_with_no_journal_is_not_damaged() {
    let root = std::env::temp_dir().join("ds-cli-fresh");
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).expect("mkdir");
    let out = ds(&root, &["status"]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(out.status.success(), "{text}");
    assert!(text.contains("not created yet"), "{text}");
    assert!(text.contains("documents 0"), "{text}");
}

/// `ds open` refuses to guess. Nothing matched and too much matched are both
/// exit 2, with the candidates listed so the next attempt can be exact.
#[test]
fn open_refuses_to_guess() {
    let root = sample("open");

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
    let root = sample("unsynced");
    let out = ds(&root, &["open", "coc"]);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(1), "{stderr}");
    assert!(stderr.contains("Syncthing"), "{stderr}");
}

/// `ds init` writes the config the whole write path hangs off, and says which
/// writer id this device will append as.
#[test]
fn init_names_the_device() {
    let root = fresh("init");
    let out = ds(&root, &["init", "--device", "phone"]);
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(out.status.success(), "{text}{}", String::from_utf8_lossy(&out.stderr));
    assert!(text.contains("phone-core"), "{text}");

    let written = std::fs::read_to_string(config_path(&root)).expect("config");
    assert!(written.contains("device = \"phone\""), "{written}");
    assert!(written.contains("syncthing_root"), "{written}");
}

/// **`ds init` never creates the journal directory** (REWRITE.md §7). It first
/// exists inside the synced tree at cutover, and anything created inside a
/// Syncthing folder syncs by default — so a journal appearing on the other
/// device before its store was exported is the one thing the plan cannot take.
#[test]
fn init_does_not_create_the_journal() {
    let root = fresh("init-nojournal");
    let out = ds(&root, &["init", "--device", "phone"]);
    assert!(out.status.success());
    assert!(!root.join(".dossier").exists(), "the journal must not exist yet");
    assert!(String::from_utf8_lossy(&out.stdout).contains("not there yet"));
}

/// An existing config is never silently replaced: `device` is this device's
/// identity, and changing it strands every op written under the old one.
#[test]
fn init_refuses_to_overwrite_without_force() {
    let root = fresh("init-exists");
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

/// A device name outside the frozen writer grammar is refused, and the message
/// says what the grammar is rather than only that the name was wrong.
#[test]
fn init_refuses_a_device_name_the_grammar_rejects() {
    let root = fresh("init-grammar");
    let out = ds(&root, &["init", "--device", "My_Phone"]);
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(1), "{stderr}");
    assert!(stderr.contains("My_Phone-core"), "{stderr}");
    assert!(stderr.contains("lowercase"), "{stderr}");
    assert!(!config_path(&root).exists(), "nothing was written");
}

/// **With no terminal and no flag, `ds init` fails fast instead of hanging.**
/// A CI job or a pipe has no one to answer the question, and a command that
/// waits forever for an answer that cannot come is the worse failure.
#[test]
fn init_without_a_terminal_or_a_flag_fails_fast() {
    let root = fresh("init-notty");
    let sandbox = root.join("config-home");
    std::fs::create_dir_all(&sandbox).expect("mkdir");
    let out = Command::new(env!("CARGO_BIN_EXE_ds"))
        .args(["init"])
        .arg("--root")
        .arg(&root)
        .env("DS_CONFIG_DIR", &sandbox)
        .env_remove("DS_ROOT")
        .stdin(Stdio::null())
        .output()
        .expect("run ds");
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert_eq!(out.status.code(), Some(1), "{stderr}");
    assert!(stderr.contains("--device"), "{stderr}");
}
