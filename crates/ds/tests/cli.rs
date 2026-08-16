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
use std::process::{Command, Output};

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
/// real config can never change what a test sees.
fn ds(root: &Path, args: &[&str]) -> Output {
    let sandbox = root.join("config-home");
    std::fs::create_dir_all(&sandbox).expect("mkdir");
    Command::new(env!("CARGO_BIN_EXE_ds"))
        .args(args)
        .arg("--root")
        .arg(root)
        .env("XDG_CONFIG_HOME", &sandbox)
        .env("HOME", &sandbox)
        .env("LOCALAPPDATA", &sandbox)
        .env("APPDATA", &sandbox)
        .env_remove("DS_ROOT")
        .output()
        .expect("run ds")
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
