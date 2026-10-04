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

//! `ds status` — what the store is, then what is wrong with it.
//!
//! The report is data rendered two ways: in full for a person, and as its
//! findings alone for `--quiet`, which cron runs and which says nothing while
//! the store is sound. Each fact is said once, in one half or the other.

use std::fmt::Write as _;
use std::path::Path;

use journal::store::FileReport;
use journal::{FoldStats, Load};

use crate::syncthing::State;
use crate::Store;

/// How many missing files a finding names before it only counts the rest.
const NAMED: usize = 3;

/// Everything `ds status` knows, before it is turned into text.
#[derive(Debug, Clone, Default)]
pub struct Report {
    /// The journal directory that was read.
    pub journal: String,
    /// Whether it exists; a fresh device has none, which is not damage.
    pub present: bool,
    /// Each writer's file.
    pub files: Vec<FileReport>,
    /// Documents the list shows.
    pub docs: usize,
    /// Locations.
    pub locations: usize,
    /// Ops for entities that no longer exist, which a deletion leaves behind.
    pub orphaned: usize,
    /// Keys that appeared twice with the same `(ts, w)`.
    pub duplicate_keys: usize,
    /// What the loader found wrong, in its own words.
    pub anomalies: Vec<String>,
    /// Locations two devices moved into each other, said as what to do.
    pub loops: Option<String>,
    /// Latest versions that conflict with another latest version.
    pub conflicts: usize,
    /// Names of documents whose versions replace each other in a loop.
    pub version_loops: Vec<String>,
    /// Linked files that are not under the root on this device.
    pub missing: Vec<String>,
    /// What the local Syncthing daemon says, unless `--no-sync`.
    pub sync: Option<crate::syncthing::Status>,
}

/// Something that needs a person, under the topic it is about.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Finding {
    /// The left-hand label: `journal`, `versions`, `syncthing`, …
    pub topic: &'static str,
    /// What is wrong, and what to do where that is not obvious.
    pub text: String,
}

impl Report {
    /// Assembles the report from a load, its fold's stats, the built store,
    /// and the root its file paths are relative to.
    #[must_use]
    pub fn new(
        journal: String,
        load: &Load,
        stats: &FoldStats,
        store: &Store,
        root: &Path,
    ) -> Self {
        let mut missing: Vec<String> = store
            .docs
            .iter()
            .flat_map(|doc| &doc.files)
            .filter(|file| !root.join(&file.path).exists())
            .map(|file| file.path.clone())
            .collect();
        missing.sort();
        missing.dedup();
        let mut version_loops: Vec<String> =
            store.version_loops().into_iter().map(|i| store.docs[i].name.clone()).collect();
        version_loops.sort();
        version_loops.dedup();
        Self {
            journal,
            present: load.present,
            files: load.files.clone(),
            docs: store.listed(),
            locations: store.locations.len(),
            orphaned: stats.orphaned,
            duplicate_keys: stats.duplicate_keys,
            anomalies: load.anomalies.iter().map(ToString::to_string).collect(),
            loops: store.locations.loop_message(),
            conflicts: store.conflicts(),
            version_loops,
            missing,
            sync: None,
        }
    }

    /// Whether nothing here needs a person.
    #[must_use]
    pub fn healthy(&self) -> bool {
        self.findings().is_empty()
    }

    /// Everything that needs a person. An expiring document is not one: that
    /// is the store working, and the app's header already says it.
    #[must_use]
    pub fn findings(&self) -> Vec<Finding> {
        let mut found = Vec::new();
        let mut say = |topic: &'static str, text: String| found.push(Finding { topic, text });
        for anomaly in &self.anomalies {
            say("journal", anomaly.clone());
        }
        if self.duplicate_keys > 0 {
            say(
                "journal",
                format!(
                    "{} duplicate (ts, writer) keys — two ops claim one instant",
                    self.duplicate_keys
                ),
            );
        }
        if let Some(loops) = &self.loops {
            say("locations", loops.clone());
        }
        if self.conflicts > 0 {
            say(
                "versions",
                format!(
                    "{} conflicting latest — two devices each made a new version; \
                     v on its Details view shows both",
                    self.conflicts
                ),
            );
        }
        if !self.version_loops.is_empty() {
            say(
                "versions",
                format!(
                    "versions of {} replace one another in a loop, so none is listed — include old \
                     versions in the filter, then set one's renews to none",
                    self.version_loops.join(", ")
                ),
            );
        }
        if !self.missing.is_empty() {
            let mut named = self.missing.iter().take(NAMED).cloned().collect::<Vec<_>>().join(", ");
            if self.missing.len() > NAMED {
                let _ = write!(named, " and {} more", self.missing.len() - NAMED);
            }
            say(
                "files",
                format!("not on this device: {named} — Syncthing may still be catching up"),
            );
        }
        if let Some(sync) = &self.sync {
            if sync.state.failed() {
                say(
                    "syncthing",
                    format!(
                        "{} — {}",
                        sync.state.label(),
                        sync.detail.as_deref().unwrap_or("no detail")
                    ),
                );
            } else if let Some(folder) = &sync.folder {
                if folder.paused {
                    say(
                        "syncthing",
                        format!(
                            "folder {} is paused — nothing moves until it resumes",
                            folder.label
                        ),
                    );
                }
            } else if sync.state != State::Unconfigured {
                say("syncthing", "the store is in no synced folder".into());
            }
        }
        found
    }

    /// The full report: what the store is, then its findings.
    #[must_use]
    pub fn render(&self) -> String {
        let mut out = String::new();
        let _ = writeln!(out, "journal   {}", self.journal);
        if !self.present {
            out.push_str("          not created yet — the first document creates it\n");
        }
        for file in &self.files {
            let _ =
                writeln!(out, "          {} — {} ops, {} bytes", file.writer, file.ops, file.bytes);
        }
        if self.orphaned > 0 {
            let _ = writeln!(
                out,
                "          {} ops are for deleted entries, as a deletion leaves them",
                self.orphaned
            );
        }
        let _ = writeln!(out, "documents {} in {} locations", self.docs, self.locations);
        if let Some(line) = self.sync.as_ref().and_then(sync_line) {
            let _ = writeln!(out, "syncthing {line}");
        }
        if self.healthy() {
            out.push_str("health    no problems found\n");
        }
        out.push_str(&self.problems());
        out
    }

    /// The findings alone, one per line: the `--quiet` form.
    #[must_use]
    pub fn problems(&self) -> String {
        let mut out = String::new();
        for finding in self.findings() {
            let _ = writeln!(out, "{:<9} {}", finding.topic, finding.text);
        }
        out
    }
}

/// The summary's Syncthing line, or none when the check failed, which only
/// the findings say.
fn sync_line(sync: &crate::syncthing::Status) -> Option<String> {
    match sync.state {
        State::Unconfigured => {
            return Some("not configured — `ds init` sets the API key".into());
        }
        state if state.failed() => return None,
        _ => {}
    }
    let mut line = sync.state.label().to_string();
    if let Some(version) = &sync.version {
        let _ = write!(line, " · {version}");
    }
    if let Some(folder) = &sync.folder {
        let _ = write!(line, " · folder {}", folder.label);
        if folder.versioning.is_empty() {
            line.push_str(" · no versioning");
        }
    }
    let _ = write!(line, " · {}/{} peers connected", sync.connected, sync.devices);
    Some(line)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Doc, FileRef, Store};
    use journal::Anomaly;

    fn doc(id: &str) -> Doc {
        Doc { id: id.into(), name: id.into(), ..Doc::default() }
    }

    fn present() -> Load {
        Load { present: true, ..Load::default() }
    }

    fn report(load: &Load, docs: Vec<Doc>) -> Report {
        let mut store = Store { docs, ..Store::default() };
        store.derive();
        Report::new(
            "/tmp/journal".into(),
            load,
            &FoldStats::default(),
            &store,
            &std::env::temp_dir(),
        )
    }

    #[test]
    fn a_sound_store_says_what_it_is() {
        let r = report(&present(), vec![doc("passport"), doc("visa")]);
        let text = r.render();
        assert!(text.contains("documents 2 in 0 locations"), "{text}");
        assert!(text.contains("health    no problems found"), "{text}");
        assert!(r.healthy());
        assert_eq!(r.problems(), "");
    }

    /// An expired document is the store working, so a `--quiet` cron job stays
    /// silent about it.
    #[test]
    fn an_expired_document_is_not_a_finding() {
        let mut past = doc("past");
        past.expiry_date = Some("2020-01-01".into());
        let r = report(&present(), vec![past]);
        assert!(r.healthy());
        assert!(!r.render().contains("expir"), "{}", r.render());
    }

    /// It is then found without opening the document's Versions view.
    #[test]
    fn a_conflicting_latest_version_is_a_finding() {
        let version = |id: &str| Doc { supersedes: Some("pp".into()), ..doc(id) };
        let r = report(&present(), vec![doc("pp"), version("pp-desk"), version("pp-phone")]);
        assert!(!r.healthy());
        assert!(r.problems().contains("versions  1 conflicting latest"), "{}", r.problems());
    }

    /// A loop hides every one of its versions.
    #[test]
    fn a_loop_of_versions_is_a_finding() {
        let mut a = doc("a");
        a.name = "Passport".into();
        a.supersedes = Some("b".into());
        let mut b = doc("b");
        b.name = "Passport".into();
        b.supersedes = Some("a".into());
        let r = report(&present(), vec![a, b]);
        let problems = r.problems();
        assert!(problems.contains("versions of Passport replace one another"), "{problems}");
        assert!(problems.contains("renews to none"), "{problems}");
    }

    #[test]
    fn a_missing_file_is_a_finding() {
        let mut passport = doc("passport");
        passport.files.push(FileRef {
            label: "scan".into(),
            path: "ds-status-test/no-such-scan.pdf".into(),
            primary: true,
        });
        let r = report(&present(), vec![passport]);
        assert!(
            r.problems()
                .starts_with("files     not on this device: ds-status-test/no-such-scan.pdf"),
            "{}",
            r.problems()
        );
    }

    #[test]
    fn malformed_lines_are_said_once() {
        let load = Load {
            present: true,
            anomalies: vec![Anomaly::Malformed { file: "desk.jsonl".into(), count: 2 }],
            ..Load::default()
        };
        let text = report(&load, Vec::new()).render();
        assert_eq!(text.matches("unreadable").count(), 1, "{text}");
        assert!(!text.contains("malformed"), "{text}");
    }

    #[test]
    fn a_conflict_copy_is_reported_as_never_read() {
        let load = Load {
            present: true,
            anomalies: vec![Anomaly::SyncConflict {
                file: "desk-core.sync-conflict-20260816-desk.jsonl".into(),
            }],
            ..Load::default()
        };
        let problems = report(&load, Vec::new()).problems();
        assert!(problems.contains("desk-core.sync-conflict"), "it names the file: {problems}");
        assert!(problems.contains("never merge by hand"), "and what to do: {problems}");
    }

    fn sync(state: State, folder: Option<crate::syncthing::Folder>) -> crate::syncthing::Status {
        crate::syncthing::Status {
            state,
            detail: Some("connection refused".into()),
            version: Some("v1.27.0".into()),
            folder,
            connected: 1,
            devices: 1,
        }
    }

    fn folder(paused: bool, versioning: &str) -> crate::syncthing::Folder {
        crate::syncthing::Folder {
            id: "docs".into(),
            label: "Documents".into(),
            paused,
            versioning: versioning.into(),
            folder_state: Some("idle".into()),
        }
    }

    #[test]
    fn a_paused_folder_is_said_once() {
        let mut r = report(&present(), Vec::new());
        r.sync = Some(sync(State::Idle, Some(folder(true, "staggered"))));
        let text = r.render();
        assert!(text.contains("syncthing idle · v1.27.0 · folder Documents · 1/1"), "{text}");
        assert_eq!(text.matches("paused").count(), 1, "{text}");
        assert!(!r.healthy());
    }

    /// Versioning off is worth saying but is not a fault.
    #[test]
    fn versioning_off_is_summary_not_a_finding() {
        let mut r = report(&present(), Vec::new());
        r.sync = Some(sync(State::Idle, Some(folder(false, ""))));
        assert!(r.healthy());
        assert!(r.render().contains("no versioning"));
    }

    #[test]
    fn a_failed_check_is_a_finding_and_unconfigured_is_not() {
        let mut r = report(&present(), Vec::new());
        r.sync = Some(sync(State::Unreachable, None));
        let text = r.render();
        assert_eq!(text.matches("syncthing").count(), 1, "{text}");
        assert!(text.contains("syncthing unreachable — connection refused"), "{text}");

        r.sync = Some(crate::syncthing::Status::default());
        assert!(r.healthy(), "never set up is not a fault");
        assert!(r.render().contains("syncthing not configured — `ds init` sets the API key"));
    }

    #[test]
    fn a_store_outside_every_folder_is_a_finding() {
        let mut r = report(&present(), Vec::new());
        r.sync = Some(sync(State::Idle, None));
        assert!(r.problems().contains("the store is in no synced folder"), "{}", r.problems());
    }

    #[test]
    fn a_missing_journal_is_stated_not_implied() {
        let r = report(&Load::default(), Vec::new());
        assert!(r.render().contains("not created yet"));
        assert!(r.healthy());
    }
}
