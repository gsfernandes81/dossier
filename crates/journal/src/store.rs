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

//! Reading a journal directory: discovery, load, and what to complain about.
//!
//! ```text
//! <syncthing_root>/.dossier/journal/
//! ├─ meta/    desk-core.jsonl · phone-core.jsonl   ← the hot startup path
//! └─ enrich/  desk-lab.jsonl                       ← loaded lazily
//! ```
//!
//! The loader reads one namespace at a time, so startup never pays for an
//! `enrich` transcript. One bad file costs only that writer's ops, reported as
//! an [`Anomaly`]; the one error is a directory that cannot be listed, which
//! would otherwise pass for an empty store.

use std::collections::BTreeMap;
use std::fmt;
use std::path::{Path, PathBuf};

use crate::names;
use crate::op::{parse_body, Line};

/// Which half of the store a file belongs to.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Namespace {
    /// Documents, locations, bundles, settings, review state — the startup fold.
    Meta,
    /// Scan readings, transcripts, intake proposals — lazy.
    Enrich,
}

impl Namespace {
    /// The subdirectory this namespace lives in.
    #[must_use]
    pub fn dir(self) -> &'static str {
        match self {
            Namespace::Meta => "meta",
            Namespace::Enrich => "enrich",
        }
    }
}

impl fmt::Display for Namespace {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.dir())
    }
}

/// Something worth telling the user about, in `ds status`'s voice.
///
/// Deliberately an enum of *situations* rather than pre-rendered strings: the
/// TUI and the CLI phrase them differently, and a test asserts on a variant far
/// more usefully than on prose.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Anomaly {
    /// A Syncthing conflict copy exists, so something outside the design
    /// touched the directory (a versioning restore, a manual copy). Never read,
    /// always reported.
    SyncConflict {
        /// The offending file name.
        file: String,
    },
    /// Lines that could not be parsed. Skipped, preserved, counted.
    Malformed {
        /// Which writer file.
        file: String,
        /// How many lines.
        count: usize,
    },
    /// A final line with no trailing newline: a process died mid-append.
    TornTail {
        /// Which writer file.
        file: String,
    },
    /// A file this build could not read at all.
    Unreadable {
        /// Which file.
        file: String,
        /// Why.
        reason: String,
    },
}

impl fmt::Display for Anomaly {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Anomaly::SyncConflict { file } => write!(
                f,
                "sync conflict copy present: {file} — a journal file was written by two devices; \
                 recover from Syncthing versioning, never merge by hand"
            ),
            Anomaly::Malformed { file, count } => {
                write!(f, "{count} unreadable line(s) in {file} — kept, skipped, not folded")
            }
            Anomaly::TornTail { file } => {
                write!(f, "torn final line in {file} — an append did not complete; it was dropped")
            }
            Anomaly::Unreadable { file, reason } => write!(f, "cannot read {file}: {reason}"),
        }
    }
}

/// What one writer's file contributed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileReport {
    /// Writer id (the file stem).
    pub writer: String,
    /// Size on disk, in bytes.
    pub bytes: u64,
    /// Highest `ts` in the file. **This** is the truncation signal: compaction
    /// can never lower it, a revert always does.
    pub max_ts: i64,
    /// Lines this build folded.
    pub ops: usize,
    /// Lines it could not parse.
    pub malformed: usize,
}

/// The result of loading one namespace.
#[derive(Debug, Default)]
pub struct Load {
    /// Every parsed line, ready for [`crate::fold()`].
    pub lines: Vec<Line>,
    /// Per-file accounting, for high-water marks and `ds status`.
    pub files: Vec<FileReport>,
    /// Everything worth reporting.
    pub anomalies: Vec<Anomaly>,
    /// Whether the namespace directory existed at all. A fresh install has no
    /// journal yet, which is normal and must not read as damage.
    pub present: bool,
}

impl Load {
    /// High-water marks for this load, ready to compare against the previous
    /// run's (see [`crate::watermark`]).
    #[must_use]
    pub fn marks(&self) -> BTreeMap<String, crate::watermark::Mark> {
        self.files
            .iter()
            .map(|file| {
                (
                    file.writer.clone(),
                    crate::watermark::Mark { max_ts: file.max_ts, bytes: file.bytes },
                )
            })
            .collect()
    }
}

/// What a namespace's files look like on disk: names, sizes and modification
/// times. Equal stamps mean nothing was added, removed, appended or rewritten.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Stamp(Vec<(String, u64, Option<std::time::SystemTime>)>);

/// Failure to read the journal *directory* — the one situation that must not
/// degrade into "the store is empty".
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// The namespace directory exists but could not be listed.
    #[error("cannot list {path}: {source}")]
    Listing {
        /// The directory.
        path: PathBuf,
        /// The underlying I/O error.
        #[source]
        source: std::io::Error,
    },
}

/// A journal directory on disk.
#[derive(Debug, Clone)]
pub struct Journal {
    root: PathBuf,
}

impl Journal {
    /// Point at `<syncthing_root>/.dossier/journal`.
    ///
    /// Does no I/O: a `Journal` is a path, not a handle. Nothing is opened
    /// until a load, so constructing one can never fail or block on FUSE.
    pub fn new(root: impl Into<PathBuf>) -> Self {
        Self { root: root.into() }
    }

    /// The journal directory inside a Syncthing root.
    pub fn under_root(syncthing_root: impl AsRef<Path>) -> Self {
        Self::new(syncthing_root.as_ref().join(".dossier").join("journal"))
    }

    /// The directory this journal lives in.
    #[must_use]
    pub fn path(&self) -> &Path {
        &self.root
    }

    /// The path a given writer appends to.
    #[must_use]
    pub fn file_path(&self, namespace: Namespace, writer: &str) -> PathBuf {
        self.root.join(namespace.dir()).join(names::writer_file(writer))
    }

    /// A stamp of one namespace's files, far cheaper than a load: one listing
    /// and a `stat` per file. An unreadable or missing directory stamps empty.
    #[must_use]
    pub fn stamp(&self, namespace: Namespace) -> Stamp {
        let Ok(entries) = std::fs::read_dir(self.root.join(namespace.dir())) else {
            return Stamp::default();
        };
        let mut files: Vec<_> = entries
            .filter_map(Result::ok)
            .filter_map(|entry| {
                // Not `entry.metadata()`: on Windows that is the directory
                // entry's copy, which NTFS updates lazily while another
                // process holds the file open for appending.
                let meta = std::fs::metadata(entry.path()).ok()?;
                let name = entry.file_name().to_string_lossy().into_owned();
                Some((name, meta.len(), meta.modified().ok()))
            })
            .collect();
        files.sort();
        Stamp(files)
    }

    /// Load one namespace: every writer file, parsed and classified.
    ///
    /// A missing directory is not an error — a fresh device has no journal yet
    /// — and neither is a single unreadable file. Both are reported.
    ///
    /// # Errors
    /// Only [`Error::Listing`], when the directory exists but cannot be read.
    pub fn load(&self, namespace: Namespace) -> Result<Load, Error> {
        let dir = self.root.join(namespace.dir());
        let mut load = Load { present: dir.is_dir(), ..Load::default() };
        if !load.present {
            return Ok(load);
        }

        let entries = std::fs::read_dir(&dir)
            .map_err(|source| Error::Listing { path: dir.clone(), source })?;

        // Collect and sort by name first: a directory listing is in whatever
        // order the filesystem feels like, and while the fold does not care
        // (that is the whole point), *reports* that reorder themselves between
        // runs are miserable to read and to test.
        let mut files: Vec<(String, String)> = Vec::new();
        for entry in entries {
            let Ok(entry) = entry else { continue };
            let name = entry.file_name().to_string_lossy().into_owned();
            if names::is_sync_conflict(&name) {
                load.anomalies.push(Anomaly::SyncConflict { file: name });
            } else if let Some(writer) = names::writer_of(&name).map(str::to_owned) {
                files.push((name, writer));
            }
            // Everything else — temps, backups, the .stfolder marker — is not
            // ours and is not news.
        }
        files.sort();

        for (name, writer) in files {
            let path = dir.join(&name);
            let raw = match std::fs::read(&path) {
                Ok(raw) => raw,
                Err(source) => {
                    load.anomalies
                        .push(Anomaly::Unreadable { file: name, reason: source.to_string() });
                    continue;
                }
            };
            let (lines, torn) = parse_body(&raw);
            let bytes = raw.len() as u64;
            if torn.is_some() {
                load.anomalies.push(Anomaly::TornTail { file: name.clone() });
            }

            let mut report = FileReport { writer, bytes, max_ts: 0, ops: 0, malformed: 0 };
            for line in &lines {
                match line {
                    Line::Op(op) => {
                        report.ops += 1;
                        report.max_ts = report.max_ts.max(op.ts);
                    }
                    Line::Malformed(_) => report.malformed += 1,
                    Line::Opaque(_) => {}
                }
            }
            if report.malformed > 0 {
                load.anomalies.push(Anomaly::Malformed { file: name, count: report.malformed });
            }
            load.files.push(report);
            load.lines.extend(lines);
        }

        Ok(load)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{fold, Draft};

    /// The journal line in which writer `w` creates document `id` at `ts`.
    fn created(ts: i64, w: &str, id: &str) -> String {
        Draft::create("doc", id).stamp(ts, w).to_line().unwrap()
    }

    /// Write `files` into a namespace of a fresh temporary journal.
    fn journal_with(
        namespace: Namespace,
        files: &[(&str, String)],
    ) -> (tempfile::TempDir, Journal) {
        let dir = tempfile::tempdir().expect("tempdir");
        let journal = Journal::under_root(dir.path());
        let ns_dir = journal.path().join(namespace.dir());
        std::fs::create_dir_all(&ns_dir).expect("create namespace dir");
        for (name, body) in files {
            std::fs::write(ns_dir.join(name), body).expect("write file");
        }
        (dir, journal)
    }

    #[test]
    fn every_writer_file_contributes() {
        let (_dir, journal) = journal_with(
            Namespace::Meta,
            &[
                ("desk-core.jsonl", format!("{}\n", created(10, "desk-core", "a"))),
                ("phone-core.jsonl", format!("{}\n", created(20, "phone-core", "b"))),
            ],
        );
        let load = journal.load(Namespace::Meta).expect("loads");
        assert!(load.present);
        assert_eq!(load.files.len(), 2);
        assert!(load.anomalies.is_empty(), "{:?}", load.anomalies);

        let state = fold(&load.lines);
        assert!(state.get("doc", "a").is_some() && state.get("doc", "b").is_some());
    }

    #[test]
    fn a_missing_directory_is_not_an_error() {
        let dir = tempfile::tempdir().expect("tempdir");
        let load = Journal::under_root(dir.path()).load(Namespace::Meta).expect("loads");
        assert!(!load.present);
        assert!(load.lines.is_empty() && load.anomalies.is_empty());
    }

    #[test]
    fn conflict_copies_are_reported_and_never_read() {
        let (_dir, journal) = journal_with(
            Namespace::Meta,
            &[
                ("desk-core.jsonl", format!("{}\n", created(10, "desk-core", "a"))),
                (
                    "desk-core.sync-conflict-20260816-120000-ABCDEFG.jsonl",
                    format!("{}\n", created(11, "desk-core", "ghost")),
                ),
                ("desk-core.jsonl.tmp-4231", format!("{}\n", created(12, "desk-core", "temp"))),
                ("notes.txt", "not a journal".into()),
            ],
        );
        let load = journal.load(Namespace::Meta).expect("loads");

        assert_eq!(load.files.len(), 1, "only the real writer file is folded");
        assert!(matches!(load.anomalies.as_slice(), [Anomaly::SyncConflict { .. }]));
        let state = fold(&load.lines);
        assert!(state.get("doc", "ghost").is_none(), "the conflict copy was not read");
        assert!(state.get("doc", "temp").is_none(), "the compaction temp was not read");
    }

    #[test]
    fn damage_is_contained_to_its_file_and_reported() {
        let good =
            format!("{}\n{}\n", created(10, "desk-core", "a"), created(11, "desk-core", "b"));
        let damaged = format!(
            "{}\n{{not json\n{}",
            created(20, "phone-core", "c"),
            created(21, "phone-core", "torn")
        );
        let (_dir, journal) = journal_with(
            Namespace::Meta,
            &[("desk-core.jsonl", good), ("phone-core.jsonl", damaged)],
        );
        let load = journal.load(Namespace::Meta).expect("loads");

        assert!(load.anomalies.iter().any(|a| matches!(a, Anomaly::TornTail { .. })));
        assert!(load.anomalies.iter().any(|a| matches!(a, Anomaly::Malformed { count: 1, .. })));

        let state = fold(&load.lines);
        for id in ["a", "b", "c"] {
            assert!(state.get("doc", id).is_some(), "{id} should have survived");
        }
        assert!(state.get("doc", "torn").is_none(), "the torn op was never durable");
    }

    #[test]
    fn an_invalid_byte_costs_one_line() {
        let (_dir, journal) = journal_with(Namespace::Meta, &[]);
        let mut body = format!("{}\n", created(10, "desk-core", "a")).into_bytes();
        body.extend_from_slice(b"{\xff}\n");
        body.extend_from_slice(format!("{}\n", created(11, "desk-core", "b")).as_bytes());
        std::fs::write(journal.file_path(Namespace::Meta, "desk-core"), &body).expect("write");

        let load = journal.load(Namespace::Meta).expect("loads");
        assert_eq!(
            load.anomalies,
            [Anomaly::Malformed { file: "desk-core.jsonl".into(), count: 1 }]
        );
        assert_eq!(load.files[0].bytes, body.len() as u64);
        let state = fold(&load.lines);
        assert!(state.get("doc", "a").is_some() && state.get("doc", "b").is_some());
    }

    /// Per-file accounting is what the truncation defense compares between
    /// runs, so it has to be right per file, not just in aggregate.
    #[test]
    fn per_file_reports_carry_size_and_high_water_mark() {
        let body =
            format!("{}\n{}\n", created(10, "desk-core", "a"), created(90, "desk-core", "b"));
        let (_dir, journal) = journal_with(Namespace::Meta, &[("desk-core.jsonl", body.clone())]);
        let load = journal.load(Namespace::Meta).expect("loads");

        let report = &load.files[0];
        assert_eq!(report.writer, "desk-core");
        assert_eq!(report.max_ts, 90);
        assert_eq!(report.ops, 2);
        assert_eq!(report.bytes, body.len() as u64);
        assert_eq!(load.marks()["desk-core"].max_ts, 90);
    }

    /// The namespaces are separate stores: loading `meta` must not touch
    /// `enrich`, or the hot startup path pays for transcripts it will not show.
    #[test]
    fn namespaces_load_independently() {
        let dir = tempfile::tempdir().expect("tempdir");
        let journal = Journal::under_root(dir.path());
        for (namespace, id) in [(Namespace::Meta, "doc-a"), (Namespace::Enrich, "scan-a")] {
            let ns_dir = journal.path().join(namespace.dir());
            std::fs::create_dir_all(&ns_dir).expect("create");
            std::fs::write(
                ns_dir.join("desk-core.jsonl"),
                format!("{}\n", created(10, "desk-core", id)),
            )
            .expect("write");
        }

        let meta = journal.load(Namespace::Meta).expect("loads");
        assert_eq!(meta.files.len(), 1);
        assert!(fold(&meta.lines).get("doc", "scan-a").is_none());

        let enrich = journal.load(Namespace::Enrich).expect("loads");
        assert!(fold(&enrich.lines).get("doc", "scan-a").is_some());
    }

    /// Reports are sorted by file name, so `ds status` output and its tests do
    /// not shuffle with the filesystem's mood.
    #[test]
    fn reports_are_in_a_stable_order() {
        let (_dir, journal) = journal_with(
            Namespace::Meta,
            &[
                ("phone-core.jsonl", format!("{}\n", created(20, "phone-core", "b"))),
                ("desk-core.jsonl", format!("{}\n", created(10, "desk-core", "a"))),
                ("desk-lab.jsonl", format!("{}\n", created(30, "desk-lab", "c"))),
            ],
        );
        let load = journal.load(Namespace::Meta).expect("loads");
        let writers: Vec<&str> = load.files.iter().map(|f| f.writer.as_str()).collect();
        assert_eq!(writers, ["desk-core", "desk-lab", "phone-core"]);
    }

    #[test]
    fn a_stamp_changes_only_with_the_files() {
        let (_dir, journal) = journal_with(
            Namespace::Meta,
            &[("desk-core.jsonl", created(1, "desk-core", "a") + "\n")],
        );
        let before = journal.stamp(Namespace::Meta);
        assert_eq!(journal.stamp(Namespace::Meta), before);
        let file = journal.file_path(Namespace::Meta, "desk-core");
        let body =
            std::fs::read_to_string(&file).expect("read") + &created(2, "desk-core", "b") + "\n";
        std::fs::write(&file, body).expect("write");
        let grown = journal.stamp(Namespace::Meta);
        assert_ne!(grown, before);
        std::fs::write(journal.file_path(Namespace::Meta, "phone-core"), "").expect("write");
        assert_ne!(journal.stamp(Namespace::Meta), grown);
    }
}
