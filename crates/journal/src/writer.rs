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

//! Appending to a journal. A writer appends to **its own file and no other**;
//! around that it keeps a hybrid logical clock, holds one OS lock per writer
//! id, and repairs a torn tail before the first append.

use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::names;
use crate::op::{Line, Op, OpKind, FORMAT_VERSION};
use crate::store::{Journal, Namespace};

/// The hybrid logical clock: milliseconds since the epoch, strictly monotonic
/// per writer.
///
/// `ts = max(now_ms, last + 1)`, so an NTP correction between sessions cannot
/// reorder a writer against itself, which the fold's `(ts, w)` order needs.
#[derive(Debug, Clone, Copy)]
pub struct Hlc {
    last: i64,
}

impl Hlc {
    /// Seed from the highest `ts` seen across **all** journals, not just this
    /// writer's.
    ///
    /// Seeding from every writer is deliberate: if the other device is ahead —
    /// its clock is fast, or this device's is slow — starting below it would
    /// make this writer's edits lose every LWW comparison until wall time caught
    /// up. The store's own history is the floor.
    #[must_use]
    pub fn seeded(max_ts_seen: i64) -> Self {
        Self { last: max_ts_seen }
    }

    /// The next timestamp, given the current wall clock in milliseconds.
    ///
    /// Split from [`Self::tick`] so tests can drive the clock backwards on
    /// purpose — the case this type exists for cannot otherwise be reproduced.
    pub fn tick_at(&mut self, now_ms: i64) -> i64 {
        let ts = now_ms.max(self.last.saturating_add(1));
        self.last = ts;
        ts
    }

    /// The next timestamp from the system clock.
    pub fn tick(&mut self) -> i64 {
        self.tick_at(now_ms())
    }

    /// Raises the floor to `ts`, a timestamp seen in another writer's ops, so
    /// the next edit here sorts after it whatever the two clocks say.
    pub fn observe(&mut self, ts: i64) {
        self.last = self.last.max(ts);
    }
}

/// Returns milliseconds since the Unix epoch, saturating rather than panicking
/// on a clock set before 1970 (which the HLC would correct on the next tick).
#[must_use]
pub fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX))
}

/// An op as a caller describes it — everything except the bookkeeping.
///
/// The writer owns `v`, `ts` and `w`, so they are absent here: a caller cannot
/// stamp the wrong writer id or invent a timestamp.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Draft {
    /// What the op does.
    pub op: OpKind,
    /// Entity kind.
    pub ent: String,
    /// Entity id.
    pub id: String,
    /// Field name, for `set`/`unset`.
    pub f: Option<String>,
    /// Value, for `set`/`state`/`reading`/`proposal`.
    pub val: Option<Value>,
}

impl Draft {
    /// Bring an entity into existence.
    pub fn create(ent: impl Into<String>, id: impl Into<String>) -> Self {
        Self { op: OpKind::Create, ent: ent.into(), id: id.into(), f: None, val: None }
    }

    /// Tombstone an entity.
    pub fn delete(ent: impl Into<String>, id: impl Into<String>) -> Self {
        Self { op: OpKind::Delete, ent: ent.into(), id: id.into(), f: None, val: None }
    }

    /// Set one field.
    pub fn set(
        ent: impl Into<String>,
        id: impl Into<String>,
        field: impl Into<String>,
        val: impl Into<Value>,
    ) -> Self {
        Self {
            op: OpKind::Set,
            ent: ent.into(),
            id: id.into(),
            f: Some(field.into()),
            val: Some(val.into()),
        }
    }

    /// Remove one field.
    pub fn unset(ent: impl Into<String>, id: impl Into<String>, field: impl Into<String>) -> Self {
        Self { op: OpKind::Unset, ent: ent.into(), id: id.into(), f: Some(field.into()), val: None }
    }

    /// Sets one field to `val`, or removes it when there is none.
    pub fn put(
        ent: impl Into<String>,
        id: impl Into<String>,
        field: impl Into<String>,
        val: Option<Value>,
    ) -> Self {
        match val {
            Some(val) => Self::set(ent, id, field, val),
            None => Self::unset(ent, id, field),
        }
    }

    /// Set a review/suggestion entry's state (per-key LWW).
    pub fn state(ent: impl Into<String>, id: impl Into<String>, val: impl Into<Value>) -> Self {
        Self { op: OpKind::State, ent: ent.into(), id: id.into(), f: None, val: Some(val.into()) }
    }

    /// Returns the op this draft becomes when writer `w` stamps it at `ts`.
    #[must_use]
    pub fn stamp(self, ts: i64, w: &str) -> Op {
        Op {
            v: FORMAT_VERSION,
            ts,
            w: w.to_string(),
            op: self.op,
            ent: self.ent,
            id: self.id,
            f: self.f,
            val: self.val,
            extra: std::collections::BTreeMap::new(),
        }
    }
}

/// Why an append could not happen.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// Another process holds this writer id. **Not fatal** — the caller should
    /// continue read-only with a visible notice, because browsing,
    /// opening and `ds status` all still work.
    #[error("another process is already writing as `{writer}` (lock: {lock})")]
    Locked {
        /// The writer id.
        writer: String,
        /// The lock file that is held.
        lock: PathBuf,
    },
    /// The writer id does not match the frozen grammar.
    #[error("`{writer}` is not a valid writer id (lowercase letters, digits and hyphens)")]
    InvalidWriterId {
        /// The offending id.
        writer: String,
    },
    /// Any filesystem failure.
    #[error("{action} {path}: {source}")]
    Io {
        /// What was being attempted.
        action: &'static str,
        /// The path involved.
        path: PathBuf,
        /// The underlying error.
        #[source]
        source: std::io::Error,
    },
    /// An op could not be serialized (a `val` `serde_json` cannot represent).
    #[error("cannot serialize op: {0}")]
    Serialize(#[from] serde_json::Error),
    /// The file changed while it was being compacted, so it was left as it was.
    #[error("{} changed during compaction; it was left as it was", path.display())]
    Changed {
        /// The writer's file.
        path: PathBuf,
    },
}

/// The lock on one writer id, and its clock.
///
/// Dropping it releases the lock (the OS does, whether or not the process exits
/// cleanly — which is why an advisory lock beats a PID file here).
#[derive(Debug)]
pub struct Writer {
    name: String,
    path: PathBuf,
    clock: Hlc,
    /// Held for its lock, never read or written.
    _lock: File,
}

impl Writer {
    /// Opens a writer: validates the id, takes the lock, sweeps stale temps,
    /// repairs a torn tail and seeds the clock.
    ///
    /// `lock_dir` must be the device's **local** data directory — a lock on the
    /// synced tree would replicate to the other device and lock it out, and a
    /// lock on Android's FUSE mount is not reliable in the first place.
    /// `max_ts_seen` comes from folding every journal (`FoldStats::max_ts`).
    ///
    /// # Errors
    /// [`Error::Locked`] if another process holds this writer id — the caller
    /// should degrade to read-only rather than exit. Otherwise [`Error::Io`] or
    /// [`Error::InvalidWriterId`].
    pub fn open(
        journal: &Journal,
        namespace: Namespace,
        writer_id: &str,
        lock_dir: &Path,
        max_ts_seen: i64,
    ) -> Result<Self, Error> {
        if !names::is_valid_writer_id(writer_id) {
            return Err(Error::InvalidWriterId { writer: writer_id.to_string() });
        }

        let lock_path = lock_dir.join(format!("{writer_id}.{}.lock", namespace.dir()));
        std::fs::create_dir_all(lock_dir).map_err(io("create lock directory", lock_dir))?;
        let lock = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(false)
            .open(&lock_path)
            .map_err(io("open lock file", &lock_path))?;
        // A busy lock must stay distinct from an I/O failure, or a second
        // process would crash instead of running read-only.
        match lock.try_lock() {
            Ok(()) => {}
            Err(std::fs::TryLockError::WouldBlock) => {
                return Err(Error::Locked { writer: writer_id.to_string(), lock: lock_path })
            }
            Err(std::fs::TryLockError::Error(source)) => {
                return Err(Error::Io { action: "lock", path: lock_path, source })
            }
        }

        let path = journal.file_path(namespace, writer_id);
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(io("create journal directory", parent))?;
            sweep_temps(parent, writer_id);
        }
        // The repair gets its own write handle: on Windows an append handle
        // lacks `FILE_WRITE_DATA`, so `set_len` through it fails with "Access is
        // denied" while working on Linux.
        let repair = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(false)
            .open(&path)
            .map_err(io("open journal file for repair", &path))?;
        repair_torn_tail(repair, &path)?;

        Ok(Self { name: writer_id.to_string(), path, clock: Hlc::seeded(max_ts_seen), _lock: lock })
    }

    /// Appends several ops as one consecutive run and flushes them to disk.
    ///
    /// For edits that are only correct together — a location delete unfiles
    /// the hard copies it held and tombstones the subtree. One call keeps them
    /// adjacent in one writer's file, as close to atomic as an append-only log
    /// gets. The flush is what lets a caller say "saved": a power cut must not
    /// disagree.
    ///
    /// The file is opened afresh on every call: Syncthing replaces a file by
    /// renaming a temp over it, and a handle held from before would append to
    /// the unlinked inode while the store reads the new one.
    ///
    /// # Errors
    /// [`Error::Io`] or [`Error::Serialize`]. After an I/O error the file may
    /// end in a torn line, so the writer must be dropped and reopened.
    pub fn append_all(
        &mut self,
        drafts: impl IntoIterator<Item = Draft>,
    ) -> Result<Vec<Op>, Error> {
        let mut written = Vec::new();
        let mut buffer = String::new();
        for draft in drafts {
            let op = draft.stamp(self.clock.tick(), &self.name);
            buffer.push_str(&op.to_line()?);
            buffer.push('\n');
            written.push(op);
        }
        if buffer.is_empty() {
            return Ok(written);
        }
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(io("open journal file", &self.path))?;
        // One `write_all` for the whole run: fewer partial-write windows.
        file.write_all(buffer.as_bytes()).map_err(io("append to", &self.path))?;
        file.sync_data().map_err(io("flush", &self.path))?;
        Ok(written)
    }

    /// Raises the clock's floor to `ts`; see [`Hlc::observe`].
    pub fn observe(&mut self, ts: i64) {
        self.clock.observe(ts);
    }

    /// Rewrites this writer's file as the minimal set that reproduces it, if
    /// worthwhile.
    ///
    /// [`crate::compact::Plan::worth_doing`] decides; the rewrite is a
    /// same-directory temp plus a rename. The lock cannot see a same-named `ds`
    /// across the WSL boundary, so the file is fingerprinted again before the
    /// rename and left alone if it changed.
    ///
    /// # Errors
    /// [`Error::Io`], [`Error::Serialize`] or [`Error::Changed`].
    pub fn compact(&self, now_ms: i64) -> Result<(), Error> {
        let before = fingerprint(&self.path)?;
        let body = std::fs::read(&self.path).map_err(io("read for compaction", &self.path))?;
        self.rewrite(&body, before, now_ms)
    }

    /// Compacts from `body`, the file as read when it looked like `before`.
    fn rewrite(&self, body: &[u8], before: Fingerprint, now_ms: i64) -> Result<(), Error> {
        let (lines, _torn) = crate::op::parse_body(body);
        let plan = crate::compact::plan(&lines, now_ms);
        if !plan.worth_doing() {
            return Ok(());
        }

        let directory = self.path.parent().unwrap_or_else(|| Path::new("."));
        let temp = directory.join(names::compaction_temp_file(&self.name, std::process::id()));

        // Built in memory first (a writer's file is a few megabytes), so the
        // temp exists for as short a window as possible.
        let mut rewritten = Vec::with_capacity(body.len());
        for &index in &plan.keep {
            match &lines[index] {
                // Re-serialized, which is lossless because `Op` carries unknown
                // fields (`extra`).
                Line::Op(op) => rewritten.extend_from_slice(op.to_line()?.as_bytes()),
                Line::Opaque(raw) => rewritten.extend_from_slice(raw.as_bytes()),
                Line::Malformed(raw) => rewritten.extend_from_slice(raw),
            }
            rewritten.push(b'\n');
        }

        let replaced =
            write_temp(&temp, &rewritten).map_err(io("compact", &self.path)).and_then(|()| {
                if fingerprint(&self.path)? != before {
                    return Err(Error::Changed { path: self.path.clone() });
                }
                std::fs::rename(&temp, &self.path).map_err(io("compact", &self.path))
            });
        if replaced.is_err() {
            let _ = std::fs::remove_file(&temp);
        }
        replaced
    }
}

/// Replaces `path` with `body` by writing a new `temp` and renaming it over.
///
/// `temp` must be in the same directory: a rename across devices fails. It is
/// removed on any failure.
///
/// # Errors
/// Any filesystem failure; `path` is then untouched.
pub fn replace_file(path: &Path, temp: &Path, body: &[u8]) -> std::io::Result<()> {
    write_temp(temp, body).and_then(|()| std::fs::rename(temp, path)).inspect_err(|_| {
        let _ = std::fs::remove_file(temp);
    })
}

/// Writes `body` to a new file at `temp`, flushed and closed, ready to rename.
fn write_temp(temp: &Path, body: &[u8]) -> std::io::Result<()> {
    let mut file = OpenOptions::new().create_new(true).write(true).open(temp)?;
    file.write_all(body)?;
    // Flushed before the rename, or a crash could leave the rename done and
    // the contents not.
    file.sync_all()?;
    // Closed before the rename: on WSL's drvfs, renaming a file still open
    // loses it.
    drop(file);
    Ok(())
}

/// A file's length and modification time, which an append changes.
type Fingerprint = (u64, Option<SystemTime>);

fn fingerprint(path: &Path) -> Result<Fingerprint, Error> {
    let meta = std::fs::metadata(path).map_err(io("stat for compaction", path))?;
    Ok((meta.len(), meta.modified().ok()))
}

/// Removes compaction temps of `writer` that a dead process left in
/// `directory`.
///
/// The writer's lock proves no compaction of it is running on this device, and
/// a leftover would sit in the synced tree, or collide with a reused pid's
/// `create_new`. A temp that will not go is harmless: no fold reads it.
fn sweep_temps(directory: &Path, writer: &str) {
    let prefix = names::compaction_temp_prefix(writer);
    let Ok(entries) = std::fs::read_dir(directory) else { return };
    for entry in entries.flatten() {
        if entry.file_name().to_str().is_some_and(|name| name.starts_with(&prefix)) {
            let _ = std::fs::remove_file(entry.path());
        }
    }
}

/// Maps an I/O error on `path` to [`Error::Io`].
fn io<'a>(action: &'static str, path: &'a Path) -> impl FnOnce(std::io::Error) -> Error + 'a {
    move |source| Error::Io { action, path: path.to_path_buf(), source }
}

/// Truncates a torn final line, so the next append cannot be glued onto it.
///
/// Gluing would turn the torn op and the user's new one into a single
/// unparseable line; the torn op was never durable, the new one must survive.
fn repair_torn_tail(mut file: File, path: &Path) -> Result<(), Error> {
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes).map_err(io("read", path))?;
    let keep = crate::op::complete_len(&bytes);
    if keep != bytes.len() {
        file.set_len(keep as u64).map_err(io("truncate torn tail of", path))?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{fold, parse_body};

    struct Fixture {
        _dir: tempfile::TempDir,
        journal: Journal,
        locks: PathBuf,
    }

    fn fixture() -> Fixture {
        let dir = tempfile::tempdir().expect("tempdir");
        let journal = Journal::under_root(dir.path().join("synced"));
        let locks = dir.path().join("local-state");
        Fixture { _dir: dir, journal, locks }
    }

    fn open(fixture: &Fixture, writer: &str) -> Writer {
        Writer::open(&fixture.journal, Namespace::Meta, writer, &fixture.locks, 0).expect("opens")
    }

    fn path(fixture: &Fixture) -> PathBuf {
        fixture.journal.file_path(Namespace::Meta, "desk-core")
    }

    /// Far enough ahead that no op written now is inside the retention window.
    fn future() -> i64 {
        now_ms() + crate::compact::RETENTION_MS * 2
    }

    fn temps(directory: &Path) -> usize {
        std::fs::read_dir(directory)
            .expect("readable")
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().contains(".tmp-"))
            .count()
    }

    #[test]
    fn appended_ops_round_trip_through_the_fold() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append_all([Draft::create("doc", "passport")]).expect("append");
        writer.append_all([Draft::set("doc", "passport", "name", "Passport")]).expect("append");

        let load = fixture.journal.load(Namespace::Meta).expect("loads");
        assert!(load.anomalies.is_empty(), "{:?}", load.anomalies);
        let state = fold(&load.lines);
        assert_eq!(state.get("doc", "passport").unwrap().fields["name"], "Passport");
        assert_eq!(load.lines[0].as_op().unwrap().w, "desk-core");
        assert_eq!(load.lines[0].as_op().unwrap().v, FORMAT_VERSION);
    }

    #[test]
    fn the_clock_never_goes_backwards() {
        let mut clock = Hlc::seeded(0);
        assert_eq!(clock.tick_at(1_000), 1_000);
        assert_eq!(clock.tick_at(500), 1_001, "a backwards clock still moves forward");
        assert_eq!(clock.tick_at(500), 1_002);
        assert_eq!(clock.tick_at(5_000), 5_000, "and it rejoins wall time when it can");
    }

    #[test]
    fn the_clock_seeds_from_the_whole_store() {
        let mut clock = Hlc::seeded(9_999_999_999_999);
        assert_eq!(clock.tick_at(1_000), 10_000_000_000_000);
    }

    /// A `ts` comes from another device's file, so a hostile one must not wrap
    /// the clock to the far past.
    #[test]
    fn the_clock_saturates_rather_than_wrapping() {
        assert_eq!(Hlc::seeded(i64::MAX).tick_at(0), i64::MAX);
    }

    #[test]
    fn a_writer_never_repeats_a_timestamp() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        let ops = writer
            .append_all((0..50).map(|i| Draft::create("doc", format!("doc-{i}"))))
            .expect("append");
        let mut stamps: Vec<i64> = ops.iter().map(|op| op.ts).collect();
        let count = stamps.len();
        stamps.dedup();
        assert_eq!(stamps.len(), count, "every ts is distinct");
        assert!(stamps.windows(2).all(|w| w[0] < w[1]), "and strictly increasing");
    }

    #[test]
    fn a_torn_tail_is_repaired_before_appending() {
        let fixture = fixture();
        let path = fixture.journal.file_path(Namespace::Meta, "desk-core");
        std::fs::create_dir_all(path.parent().unwrap()).expect("create");
        std::fs::write(
            &path,
            "{\"v\":1,\"ts\":10,\"w\":\"desk-core\",\"op\":\"create\",\"ent\":\"doc\",\"id\":\"a\"}\n\
             {\"v\":1,\"ts\":11,\"w\":\"desk-co",
        )
        .expect("write");

        let mut writer = open(&fixture, "desk-core");
        writer.append_all([Draft::create("doc", "new")]).expect("append");

        let (lines, torn) = parse_body(&std::fs::read(&path).expect("read"));
        assert!(torn.is_none(), "the file ends cleanly");
        assert_eq!(lines.len(), 2, "the torn line is gone, the new op is intact");
        assert!(lines.iter().all(|line| line.as_op().is_some()), "nothing was glued together");

        let state = fold(&lines);
        assert!(state.get("doc", "a").is_some() && state.get("doc", "new").is_some());
    }

    #[test]
    fn a_file_of_only_a_torn_line_is_emptied() {
        let fixture = fixture();
        let path = fixture.journal.file_path(Namespace::Meta, "desk-core");
        std::fs::create_dir_all(path.parent().unwrap()).expect("create");
        std::fs::write(&path, "{\"v\":1,\"ts\":11,\"w\":\"desk-co").expect("write");

        let mut writer = open(&fixture, "desk-core");
        writer.append_all([Draft::create("doc", "new")]).expect("append");
        let (lines, torn) = parse_body(&std::fs::read(&path).expect("read"));
        assert!(torn.is_none() && lines.len() == 1);
    }

    /// A torn line of any length is repaired: an `enrich` op can carry a whole
    /// transcript.
    #[test]
    fn a_torn_line_longer_than_any_read_window_is_repaired() {
        let fixture = fixture();
        let path = fixture.journal.file_path(Namespace::Meta, "desk-core");
        std::fs::create_dir_all(path.parent().unwrap()).expect("create");
        let torn = format!(
            "{{\"v\":1,\"ts\":11,\"w\":\"desk-core\",\"op\":\"set\",\"ent\":\"doc\",\"id\":\"a\",\
             \"f\":\"transcript\",\"val\":\"{}",
            "x".repeat(200 * 1024)
        );
        std::fs::write(
            &path,
            format!(
                "{{\"v\":1,\"ts\":10,\"w\":\"desk-core\",\"op\":\"create\",\"ent\":\"doc\",\
                 \"id\":\"a\"}}\n{torn}"
            ),
        )
        .expect("write");

        let mut writer = open(&fixture, "desk-core");
        writer.append_all([Draft::create("doc", "new")]).expect("append");

        let (lines, torn) = parse_body(&std::fs::read(&path).expect("read"));
        assert!(torn.is_none(), "the file ends cleanly");
        assert_eq!(lines.len(), 2, "the torn line is gone, the new op is intact");
        assert!(lines.iter().all(|line| line.as_op().is_some()), "nothing was glued together");
    }

    #[test]
    fn a_second_writer_on_the_same_id_is_refused() {
        let fixture = fixture();
        let _first = open(&fixture, "desk-core");
        let second =
            Writer::open(&fixture.journal, Namespace::Meta, "desk-core", &fixture.locks, 0);
        assert!(matches!(second, Err(Error::Locked { .. })), "{second:?}");

        // A different writer id is unaffected — the lock is per writer, not
        // per store.
        assert!(Writer::open(&fixture.journal, Namespace::Meta, "phone-core", &fixture.locks, 0)
            .is_ok());
    }

    #[test]
    fn dropping_a_writer_releases_the_lock() {
        let fixture = fixture();
        drop(open(&fixture, "desk-core"));
        assert!(
            Writer::open(&fixture.journal, Namespace::Meta, "desk-core", &fixture.locks, 0).is_ok()
        );
    }

    #[test]
    fn locks_live_outside_the_synced_tree() {
        let fixture = fixture();
        let _writer = open(&fixture, "desk-core");
        let locks: Vec<_> = std::fs::read_dir(&fixture.locks)
            .expect("lock dir exists")
            .filter_map(|entry| entry.ok().map(|e| e.file_name().to_string_lossy().into_owned()))
            .collect();
        assert_eq!(locks, ["desk-core.meta.lock"]);
        assert!(!fixture.locks.starts_with(fixture.journal.path()));
    }

    /// Checked through a real rewrite, not just the planner.
    #[test]
    fn compacting_shrinks_the_file_without_changing_the_fold() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append_all([Draft::create("doc", "passport")]).expect("append");
        for i in 0..40 {
            writer
                .append_all([Draft::set("doc", "passport", "name", format!("Passport v{i}"))])
                .expect("append");
        }

        let before = fold(&fixture.journal.load(Namespace::Meta).expect("loads").lines);
        let bytes_before = std::fs::read(path(&fixture)).expect("read").len();
        writer.compact(future()).expect("compacts");

        let after = std::fs::read(path(&fixture)).expect("read");
        assert_eq!(parse_body(&after).0.len(), 2, "a create and the newest name write");
        assert!(after.len() < bytes_before / 4);

        let load = fixture.journal.load(Namespace::Meta).expect("loads");
        assert!(load.anomalies.is_empty(), "{:?}", load.anomalies);
        assert_eq!(fold(&load.lines).canonical_json(), before.canonical_json());
    }

    /// The invalid byte sits in an otherwise valid op, which must neither fold
    /// as a patched value nor be rewritten.
    #[test]
    fn a_line_with_an_invalid_byte_folds_and_compacts_untouched() {
        const BAD: &[u8] =
            b"{\"v\":1,\"ts\":13,\"w\":\"desk-core\",\"op\":\"set\",\"ent\":\"doc\",\"id\":\"a\",\"f\":\"name\",\"val\":\"caf\xe9\"}";
        let fixture = fixture();
        let path = fixture.journal.file_path(Namespace::Meta, "desk-core");
        std::fs::create_dir_all(path.parent().unwrap()).expect("create");
        let line = |op: Draft, ts| format!("{}\n", op.stamp(ts, "desk-core").to_line().unwrap());
        let mut body = line(Draft::create("doc", "a"), 1).into_bytes();
        for ts in 2..12 {
            body.extend_from_slice(line(Draft::set("doc", "a", "name", "old"), ts).as_bytes());
        }
        body.extend_from_slice(line(Draft::set("doc", "a", "name", "new"), 12).as_bytes());
        body.extend_from_slice(BAD);
        body.push(b'\n');
        std::fs::write(&path, &body).expect("write");

        let writer = open(&fixture, "desk-core");
        let state = fold(&fixture.journal.load(Namespace::Meta).expect("loads").lines);
        assert_eq!(state.get("doc", "a").expect("folds").fields["name"], "new");

        writer.compact(future()).expect("compacts");
        let after = std::fs::read(&path).expect("read");
        assert_eq!(parse_body(&after).0.len(), 3, "the create, the newest set, the bad line");
        assert!(after.split(|&byte| byte == b'\n').any(|kept| kept == BAD), "bytes kept");
        let load = fixture.journal.load(Namespace::Meta).expect("loads");
        assert_eq!(fold(&load.lines).canonical_json(), state.canonical_json());
    }

    #[test]
    fn compaction_never_lowers_the_high_water_mark() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append_all([Draft::create("doc", "x")]).expect("append");
        for i in 0..30 {
            writer.append_all([Draft::set("doc", "x", "name", format!("v{i}"))]).expect("append");
        }
        let before = fixture.journal.load(Namespace::Meta).expect("loads").files[0].max_ts;

        writer.compact(future()).expect("compacts");

        let after = fixture.journal.load(Namespace::Meta).expect("loads").files[0].max_ts;
        assert_eq!(before, after);
    }

    /// Syncthing replaces a file by renaming a temp over it; later appends must
    /// land in the new file, not the inode it replaced.
    #[test]
    fn appends_follow_a_file_replaced_under_the_writer() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append_all([Draft::create("doc", "x")]).expect("append");
        let path = path(&fixture);
        let synced = std::fs::read(&path).expect("read");
        let temp = path.with_file_name(".syncthing.desk-core.jsonl.tmp");
        std::fs::write(&temp, synced).expect("write temp");
        std::fs::rename(&temp, &path).expect("replace");

        writer.append_all([Draft::set("doc", "x", "slot", 7)]).expect("append after replace");

        let load = fixture.journal.load(Namespace::Meta).expect("loads");
        assert!(load.anomalies.is_empty(), "{:?}", load.anomalies);
        assert_eq!(fold(&load.lines).get("doc", "x").expect("alive").fields["slot"], 7);
    }

    #[test]
    fn a_healthy_file_is_left_alone_and_no_temp_survives() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append_all([Draft::create("doc", "x")]).expect("append");
        writer.append_all([Draft::set("doc", "x", "name", "only")]).expect("append");
        let path = path(&fixture);
        let before = std::fs::read(&path).expect("read");
        writer.compact(future()).expect("runs");

        assert_eq!(std::fs::read(&path).expect("read"), before);
        let directory = path.parent().expect("has a parent");
        assert_eq!(temps(directory), 0, "temp files must never be left in the synced tree");
    }

    #[test]
    fn a_failed_compaction_cleans_up_after_itself() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append_all([Draft::create("doc", "x")]).expect("append");
        for i in 0..9 {
            writer.append_all([Draft::set("doc", "x", "name", format!("v{i}"))]).expect("append");
        }
        let path = path(&fixture);
        let before = std::fs::read(&path).expect("read");
        let temp =
            path.with_file_name(names::compaction_temp_file("desk-core", std::process::id()));
        std::fs::write(&temp, "stale").expect("plant a stale temp");

        assert!(writer.compact(future()).is_err(), "the temp name is taken");
        assert!(!temp.exists(), "the temp is removed on failure");
        assert_eq!(std::fs::read(&path).expect("read"), before);

        writer.append_all([Draft::set("doc", "x", "slot", 7)]).expect("append after failure");
        let load = fixture.journal.load(Namespace::Meta).expect("loads");
        assert_eq!(fold(&load.lines).get("doc", "x").expect("alive").fields["slot"], 7);
    }

    /// Termux kills processes mid-compaction, and a reused pid would otherwise
    /// make the next compaction's `create_new` fail.
    #[test]
    fn a_stale_temp_from_a_dead_process_is_swept_at_open() {
        let fixture = fixture();
        let directory = fixture.journal.file_path(Namespace::Meta, "desk-core");
        let directory = directory.parent().expect("has a parent");
        std::fs::create_dir_all(directory).expect("create");
        let own = directory.join(names::compaction_temp_file("desk-core", 99_999));
        let other = directory.join(names::compaction_temp_file("phone-core", 1));
        std::fs::write(&own, "stale").expect("write");
        std::fs::write(&other, "someone else's").expect("write");

        let _writer = open(&fixture, "desk-core");
        assert!(!own.exists(), "this writer's leftover is gone");
        assert!(other.exists(), "another writer's temp is not this writer's to remove");
    }

    /// A same-named `ds` across the WSL boundary appends behind a lock this
    /// process cannot see; its op must not be lost to the rename.
    #[test]
    fn compaction_aborts_if_the_file_changed_underneath() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append_all([Draft::create("doc", "x")]).expect("append");
        for i in 0..30 {
            writer.append_all([Draft::set("doc", "x", "name", format!("v{i}"))]).expect("append");
        }
        let path = path(&fixture);
        let before = fingerprint(&path).expect("stat");
        let body = std::fs::read(&path).expect("read");
        writer.append_all([Draft::set("doc", "x", "slot", 7)]).expect("the twin appends");
        let appended = std::fs::read(&path).expect("read");

        let result = writer.rewrite(&body, before, future());
        assert!(matches!(result, Err(Error::Changed { .. })), "{result:?}");
        assert_eq!(std::fs::read(&path).expect("read"), appended, "left as it was");
        assert_eq!(temps(path.parent().expect("has a parent")), 0, "and no temp is left behind");
    }

    /// An id outside the frozen grammar is refused before anything is created —
    /// it would produce a file the fold refuses to read.
    #[test]
    fn an_invalid_writer_id_is_refused() {
        let fixture = fixture();
        let bad = Writer::open(&fixture.journal, Namespace::Meta, "Desk_Core", &fixture.locks, 0);
        assert!(matches!(bad, Err(Error::InvalidWriterId { .. })), "{bad:?}");
    }

    #[test]
    fn a_run_of_ops_is_written_consecutively() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append_all([Draft::create("doc", "coc-2019")]).expect("append");
        writer
            .append_all([
                Draft::create("doc", "coc-2019-in"),
                Draft::set("doc", "coc-2025", "supersedes", "coc-2019-in"),
                Draft::delete("doc", "coc-2019"),
            ])
            .expect("append run");

        let load = fixture.journal.load(Namespace::Meta).expect("loads");
        let ids: Vec<&str> =
            load.lines.iter().filter_map(|l| l.as_op()).map(|op| op.id.as_str()).collect();
        assert_eq!(ids, ["coc-2019", "coc-2019-in", "coc-2025", "coc-2019"]);
    }
}
