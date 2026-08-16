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

//! `ds status` — what the store is, and what is wrong with it.
//!
//! Two audiences, one report. A person runs it to see counts; **cron runs it
//! with `--quiet` and only wants to hear about problems** (REWRITE.md §3.1
//! calls that mode out by name, and it is read-only by design). So the report is
//! built as data and rendered twice, rather than printed as it is computed:
//! the quiet form is the loud form minus everything healthy, which makes it
//! impossible for the two to drift apart.
//!
//! The health counters come from the journal's own fold ([`journal::FoldStats`])
//! and load ([`journal::Load`]) rather than being recomputed here — an anomaly
//! this report invented would be an anomaly nothing else agrees with.

use std::fmt::Write as _;

use journal::{FoldStats, Load};

use crate::syncthing::State;

use crate::{Status, Store};

/// Everything `ds status` knows, before it is turned into text.
#[derive(Debug, Clone, Default)]
pub struct Report {
    /// The journal directory that was read.
    pub journal: String,
    /// Whether it existed at all. A fresh device has no journal yet, which is
    /// normal and must never read as damage.
    pub present: bool,
    /// Per-writer file summaries: `(writer, ops, bytes, malformed)`.
    pub files: Vec<FileLine>,
    /// Documents in the store.
    pub docs: usize,
    /// Documents in the expiry watch.
    pub tracked: usize,
    /// Past their expiry date.
    pub expired: usize,
    /// Inside the warn window.
    pub soon: usize,
    /// The warn window, in days.
    pub warn_days: i64,
    /// Locations.
    pub locations: usize,
    /// Ops the fold ignored because they referred to nothing (§3.3).
    pub orphaned: usize,
    /// Keys that appeared twice with the same `(ts, w)` — a real conflict, and
    /// the only one this format can produce.
    pub duplicate_keys: usize,
    /// Lines that could not be parsed at all.
    pub malformed: usize,
    /// Everything the loader thought was worth reporting.
    pub anomalies: Vec<String>,
    /// What the local Syncthing daemon says, when there is one to ask.
    pub sync: Option<crate::syncthing::Status>,
}

/// One writer's file, as the report lists it.
#[derive(Debug, Clone)]
pub struct FileLine {
    /// Writer id (the file stem).
    pub writer: String,
    /// Ops folded from it.
    pub ops: usize,
    /// Size on disk.
    pub bytes: u64,
    /// Lines it could not parse.
    pub malformed: usize,
}

impl Report {
    /// Assemble the report from a load, its fold's stats, and the built store.
    #[must_use]
    pub fn new(
        journal: String,
        load: &Load,
        stats: &FoldStats,
        store: &Store,
        today: &str,
        warn_until: &str,
    ) -> Self {
        let mut expired = 0;
        let mut soon = 0;
        let mut tracked = 0;
        for doc in &store.docs {
            match doc.status(today, warn_until) {
                Status::Expired => {
                    expired += 1;
                    tracked += 1;
                }
                Status::Soon => {
                    soon += 1;
                    tracked += 1;
                }
                Status::Ok => tracked += 1,
                Status::Untracked => {}
            }
        }
        Self {
            journal,
            present: load.present,
            files: load
                .files
                .iter()
                .map(|file| FileLine {
                    writer: file.writer.clone(),
                    ops: file.ops,
                    bytes: file.bytes,
                    malformed: file.malformed,
                })
                .collect(),
            docs: store.docs.len(),
            tracked,
            expired,
            soon,
            warn_days: store.warn_days(),
            locations: store.locations.len(),
            orphaned: stats.orphaned,
            duplicate_keys: stats.duplicate_keys,
            malformed: load.files.iter().map(|file| file.malformed).sum(),
            // The loader's own wording, verbatim: an anomaly this report
            // reworded would be an anomaly nothing else agrees with.
            anomalies: load.anomalies.iter().map(ToString::to_string).collect(),
            sync: None,
        }
    }

    /// Whether anything here needs a human.
    ///
    /// An **expiring document is not a problem with the store** — it is the
    /// store working. Only damage counts, which is what keeps a `--quiet` cron
    /// job silent for months and believable when it finally speaks.
    #[must_use]
    pub fn healthy(&self) -> bool {
        self.anomalies.is_empty()
            && self.malformed == 0
            && self.duplicate_keys == 0
            && self.sync_healthy()
    }

    /// Whether the transport is in a state that will actually move documents.
    ///
    /// An unconfigured daemon is not a fault — plenty of runs have no key, and a
    /// device that was never set up should not nag. Everything else here is a
    /// silent failure: the app looks fine, and nothing reaches the other device.
    fn sync_healthy(&self) -> bool {
        let Some(sync) = &self.sync else { return true };
        match sync.state {
            State::Unconfigured => true,
            State::Refused | State::Unreachable | State::Unauthorized => false,
            State::Idle | State::Busy => sync.folder.as_ref().is_some_and(|f| !f.paused),
        }
    }

    /// The full report.
    ///
    /// rust: built with `writeln!` into a `String` rather than `println!`, so
    /// the report is a value a test can read. Writing to a `String` cannot fail,
    /// which is why the results are discarded rather than propagated.
    #[must_use]
    pub fn render(&self) -> String {
        let mut out = String::new();
        let _ = writeln!(out, "journal   {}", self.journal);
        if !self.present {
            out.push_str("          not created yet — nothing to fold\n");
        }
        for file in &self.files {
            let malformed = if file.malformed > 0 {
                format!(", {} malformed", file.malformed)
            } else {
                String::new()
            };
            let _ = writeln!(
                out,
                "          {} — {} ops, {} bytes{malformed}",
                file.writer, file.ops, file.bytes
            );
        }
        let _ = writeln!(out, "documents {} in {} locations", self.docs, self.locations);
        let _ = writeln!(
            out,
            "expiry    {} tracked · {} expired · {} within {} days",
            self.tracked, self.expired, self.soon, self.warn_days
        );
        if let Some(sync) = &self.sync {
            let _ = writeln!(out, "syncthing {}", Self::sync_line(sync));
        }
        out.push_str(&self.problems());
        if self.healthy() {
            out.push_str("health    no anomalies\n");
        }
        out
    }

    /// The Syncthing line: state, then the facts that decide whether these
    /// documents actually move.
    fn sync_line(sync: &crate::syncthing::Status) -> String {
        let mut line = sync.state.label().to_string();
        if let Some(version) = &sync.version {
            let _ = write!(line, " · {version}");
        }
        if let Some(folder) = &sync.folder {
            let _ = write!(line, " · folder {}", folder.label);
            if folder.paused {
                line.push_str(" (PAUSED)");
            }
            if folder.versioning.is_empty() {
                // The design leans on Syncthing versioning as the undo history
                // of last resort (§7). Off is worth saying out loud.
                line.push_str(" · no versioning");
            }
        } else {
            line.push_str(" · the store is in no synced folder");
        }
        let _ = write!(line, " · {}/{} peers connected", sync.connected, sync.devices);
        line
    }

    /// Only what is wrong — the `--quiet` form, and the tail of the loud one.
    #[must_use]
    pub fn problems(&self) -> String {
        let mut out = String::new();
        if self.malformed > 0 {
            let _ = writeln!(out, "health    {} malformed lines", self.malformed);
        }
        if self.duplicate_keys > 0 {
            let _ = writeln!(
                out,
                "health    {} duplicate (ts, writer) keys — two ops claim one instant",
                self.duplicate_keys
            );
        }
        if self.orphaned > 0 {
            // Not a fault: an op for a deleted entity is exactly what a
            // tombstone is supposed to leave behind (§3.3). Reported because a
            // *large* number of them means something else is wrong.
            let _ = writeln!(out, "note      {} ops referred to nothing", self.orphaned);
        }
        for anomaly in &self.anomalies {
            let _ = writeln!(out, "anomaly   {anomaly}");
        }
        // Syncthing problems belong in the quiet form too: a paused folder or a
        // daemon that has been off for a week is exactly the silent failure a
        // cron job exists to catch, and neither shows up in the journal.
        if let Some(sync) = &self.sync {
            match sync.state {
                State::Unreachable | State::Unauthorized | State::Refused => {
                    let _ = writeln!(
                        out,
                        "syncthing {} — {}",
                        sync.state.label(),
                        sync.detail.as_deref().unwrap_or("no detail")
                    );
                }
                State::Unconfigured | State::Idle | State::Busy => {}
            }
            if let Some(folder) = &sync.folder {
                if folder.paused {
                    let _ = writeln!(out, "syncthing folder {} is PAUSED", folder.label);
                }
            } else if sync.state == State::Idle || sync.state == State::Busy {
                let _ = writeln!(out, "syncthing the store is in no synced folder");
            }
        }
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Doc, Store};
    use journal::Anomaly;

    fn doc(id: &str, expiry: Option<&str>) -> Doc {
        Doc {
            id: id.into(),
            name: id.into(),
            tags: Vec::new(),
            bundles: Vec::new(),
            issue_date: None,
            expiry_date: expiry.map(str::to_string),
            ignore_expiry: false,
            supersedes: None,
            location: None,
            slot: None,
            subslot: None,
            files: Vec::new(),
            notes: String::new(),
            superseded: false,
            haystack: String::new(),
        }
    }

    fn report(load: &Load, stats: &FoldStats, docs: Vec<Doc>) -> Report {
        let store = Store { docs, ..Store::default() };
        Report::new("/tmp/journal".into(), load, stats, &store, "2026-10-20", "2027-01-18")
    }

    /// The loud form says what the store is; the counts are the ones the header
    /// shows, so the two can never disagree.
    #[test]
    fn the_full_report_counts_documents_and_expiry() {
        let r = report(
            &Load { present: true, ..Load::default() },
            &FoldStats::default(),
            vec![
                doc("past", Some("2026-01-01")),
                doc("soon", Some("2026-12-01")),
                doc("none", None),
            ],
        );
        let text = r.render();
        assert!(text.contains("documents 3"));
        assert!(text.contains("2 tracked · 1 expired · 1 within 90 days"), "{text}");
        assert!(text.contains("no anomalies"));
        assert!(r.healthy());
    }

    /// **An expiring document is not a problem with the store.** A `--quiet` run
    /// stays silent about it, or the cron job becomes noise and gets ignored.
    #[test]
    fn quiet_says_nothing_about_a_merely_expired_document() {
        let r = report(
            &Load { present: true, ..Load::default() },
            &FoldStats::default(),
            vec![doc("past", Some("2020-01-01"))],
        );
        assert!(r.healthy());
        assert_eq!(r.problems(), "", "nothing for cron to say");
    }

    /// Damage does speak — and names the file, so the next step is obvious.
    #[test]
    fn a_conflict_copy_is_reported_as_never_read() {
        let load = Load {
            present: true,
            anomalies: vec![Anomaly::SyncConflict {
                file: "desk-core.sync-conflict-20260816-desk.jsonl".into(),
            }],
            ..Load::default()
        };
        let r = report(&load, &FoldStats::default(), Vec::new());
        assert!(!r.healthy());
        let problems = r.problems();
        assert!(problems.contains("sync conflict copy present"), "{problems}");
        assert!(problems.contains("desk-core.sync-conflict"), "it names the file: {problems}");
        assert!(problems.contains("never merge by hand"), "and what to do: {problems}");
    }

    fn sync(state: State, folder: Option<crate::syncthing::Folder>) -> crate::syncthing::Status {
        crate::syncthing::Status {
            state,
            detail: Some("detail".into()),
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

    /// **A paused folder is a silent failure**: everything looks fine and
    /// nothing reaches the other device. It fails the health check, so a cron
    /// job finds it.
    #[test]
    fn a_paused_folder_is_unhealthy_and_says_so() {
        let mut r =
            report(&Load { present: true, ..Load::default() }, &FoldStats::default(), Vec::new());
        r.sync = Some(sync(State::Idle, Some(folder(true, "staggered"))));
        assert!(!r.healthy());
        assert!(r.problems().contains("PAUSED"), "{}", r.problems());
        assert!(
            r.render().contains("syncthing idle · v1.27.0 · folder Documents (PAUSED)"),
            "{}",
            r.render()
        );
    }

    /// Versioning off is reported — the design leans on it as the undo history
    /// of last resort — but it is not damage, so cron stays quiet about it.
    #[test]
    fn versioning_off_is_mentioned_but_is_not_a_fault() {
        let mut r =
            report(&Load { present: true, ..Load::default() }, &FoldStats::default(), Vec::new());
        r.sync = Some(sync(State::Idle, Some(folder(false, ""))));
        assert!(r.healthy());
        assert!(r.render().contains("no versioning"));
        assert_eq!(r.problems(), "");
    }

    /// A device that never configured the API is not nagged; a daemon that was
    /// configured and is now unreachable is.
    #[test]
    fn unconfigured_is_quiet_and_unreachable_is_not() {
        let mut r =
            report(&Load { present: true, ..Load::default() }, &FoldStats::default(), Vec::new());
        r.sync = Some(sync(State::Unconfigured, None));
        assert!(r.healthy(), "never set up is not a fault");

        r.sync = Some(sync(State::Unreachable, None));
        assert!(!r.healthy());
        assert!(r.problems().contains("unreachable"));
    }

    /// A device with no journal yet says so plainly, rather than reporting an
    /// empty store as if it were the truth.
    #[test]
    fn a_missing_journal_is_stated_not_implied() {
        let r = report(&Load::default(), &FoldStats::default(), Vec::new());
        assert!(r.render().contains("not created yet"));
        assert!(r.healthy(), "absent is not damaged");
    }
}
