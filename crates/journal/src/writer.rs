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

//! Appending to a journal: the clock, the lock, and the torn tail.
//!
//! A writer appends to **its own file and no other** — that single rule is what
//! makes Syncthing conflicts structurally impossible (REWRITE.md §3.1). This
//! module enforces it and the three things that have to be true around it:
//!
//! 1. **The timestamp is a hybrid logical clock, not the wall clock.**
//!    `ts = max(now_ms, own_last_ts + 1)`, seeded from the highest `ts` seen
//!    across *all* journals at startup. A writer therefore never repeats or
//!    reverses a `ts`, which is exactly the property the fold's `(ts, w)` order
//!    depends on — an NTP correction between sessions cannot reorder a writer
//!    against itself.
//! 2. **One process per writer id**, enforced with an OS advisory lock on a
//!    file in the device's **local** data directory — never on the synced tree,
//!    never on FUSE. A second process that cannot take the lock is not an
//!    error to swallow: it runs read-only with a visible notice, which is how
//!    `ds status --quiet` from cron keeps working while the TUI is open.
//! 3. **A torn tail is repaired before the first append.** A line with no
//!    trailing newline was never durable, and appending after it would glue two
//!    ops into one unparseable line — destroying the *new* op, which is the
//!    worse outcome. So the writer truncates it first, every time.
//!
//! Callers never construct an [`Op`] directly; they describe one with a
//! [`Draft`] and the writer stamps `v`, `ts` and `w`. Forging a timestamp or
//! writing under another writer's id is not a mistake this API can make.

use std::fs::{File, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::Value;

use crate::names;
use crate::op::{Op, OpKind, FORMAT_VERSION};
use crate::store::{Journal, Namespace};

/// The hybrid logical clock (REWRITE.md §3.2).
///
/// Human-meaningful (it is milliseconds since the epoch, and stays that way
/// unless the clock misbehaves) but strictly monotonic per writer.
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
        let ts = now_ms.max(self.last + 1);
        self.last = ts;
        ts
    }

    /// The next timestamp from the system clock.
    pub fn tick(&mut self) -> i64 {
        self.tick_at(now_ms())
    }

    /// The last timestamp handed out.
    #[must_use]
    pub fn last(&self) -> i64 {
        self.last
    }
}

/// Milliseconds since the Unix epoch, saturating rather than panicking on a
/// clock set before 1970 (which the HLC would correct on the next tick anyway).
fn now_ms() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0, |d| i64::try_from(d.as_millis()).unwrap_or(i64::MAX))
}

/// An op as a caller describes it — everything except the bookkeeping.
///
/// rust: a separate type from [`Op`] rather than an `Op` with optional fields.
/// The writer owns `v`, `ts` and `w`, so they are simply absent here; a caller
/// *cannot* stamp the wrong writer id or invent a timestamp, because there is
/// nowhere to put one.
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

    /// Set a review/suggestion entry's state (per-key LWW).
    pub fn state(ent: impl Into<String>, id: impl Into<String>, val: impl Into<Value>) -> Self {
        Self { op: OpKind::State, ent: ent.into(), id: id.into(), f: None, val: Some(val.into()) }
    }
}

/// Why an append could not happen.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// Another process holds this writer id. **Not fatal** — the caller should
    /// continue read-only with a visible notice (§3.1), because browsing,
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
}

/// An open, locked, append-only handle to one writer's file.
///
/// Dropping it releases the lock (the OS does, whether or not the process exits
/// cleanly — which is why an advisory lock beats a PID file here).
#[derive(Debug)]
// The `writer_id` field stutters with the type name, but `id` alone would read
// as an *entity* id — the thing this crate has most of — so the clarity wins.
#[allow(clippy::struct_field_names)]
pub struct Writer {
    writer_id: String,
    path: PathBuf,
    file: File,
    clock: Hlc,
    /// Held for its lock, never read or written.
    _lock: File,
}

impl Writer {
    /// Open a writer: validate the id, take the lock, repair a torn tail, and
    /// seed the clock.
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
        create_dir_all(lock_dir, "create lock directory")?;
        let lock = OpenOptions::new()
            .create(true)
            .read(true)
            .write(true)
            .truncate(false)
            .open(&lock_path)
            .map_err(|source| Error::Io {
                action: "open lock file",
                path: lock_path.clone(),
                source,
            })?;
        // rust: `try_lock` is std's advisory file lock (flock on unix,
        // LockFileEx on Windows), stable since 1.89 — so the "one process per
        // writer" rule costs no dependency. `Err(WouldBlock)` means someone
        // else has it; a real I/O failure is a different error entirely, and
        // conflating the two would turn a busy lock into a crash.
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
            create_dir_all(parent, "create journal directory")?;
        }
        let mut file =
            OpenOptions::new().create(true).read(true).append(true).open(&path).map_err(
                |source| Error::Io { action: "open journal file", path: path.clone(), source },
            )?;
        repair_torn_tail(&mut file, &path)?;

        Ok(Self {
            writer_id: writer_id.to_string(),
            path,
            file,
            clock: Hlc::seeded(max_ts_seen),
            _lock: lock,
        })
    }

    /// The writer id this handle appends as.
    #[must_use]
    pub fn writer_id(&self) -> &str {
        &self.writer_id
    }

    /// The file being appended to.
    #[must_use]
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Append one op, returning it as it was written.
    ///
    /// Does **not** fsync — see [`Self::commit`]. One op is one `write_all` of a
    /// complete line ending in `\n`, so a crash mid-write can only ever produce
    /// a torn tail, which the next open repairs.
    ///
    /// # Errors
    /// [`Error::Io`] or [`Error::Serialize`].
    ///
    /// # Panics
    /// Never: it forwards one draft to [`Self::append_all`] and takes the one
    /// op back out. The `expect` is that arithmetic, not a fallible operation.
    pub fn append(&mut self, draft: Draft) -> Result<Op, Error> {
        self.append_all(std::iter::once(draft))
            .map(|mut ops| ops.pop().expect("one op in, one out"))
    }

    /// Append several ops as one consecutive run.
    ///
    /// The contract needs this for edits that are only correct together — an id
    /// rename is create-new + copy fields + reference fixups + delete-old, and
    /// a bundle rename rewrites every member (§3.2, §4.1). Emitting them
    /// through one call keeps them adjacent in one writer's file, which is as
    /// close to atomic as an append-only log gets.
    ///
    /// # Errors
    /// [`Error::Io`] or [`Error::Serialize`]. On an I/O failure part-way, the
    /// ops already written stay written — the journal is append-only, and a
    /// half-applied run is visible to the next fold rather than silently lost.
    pub fn append_all(
        &mut self,
        drafts: impl IntoIterator<Item = Draft>,
    ) -> Result<Vec<Op>, Error> {
        let mut written = Vec::new();
        let mut buffer = String::new();
        for draft in drafts {
            let op = Op {
                v: FORMAT_VERSION,
                ts: self.clock.tick(),
                w: self.writer_id.clone(),
                op: draft.op,
                ent: draft.ent,
                id: draft.id,
                f: draft.f,
                val: draft.val,
                extra: std::collections::BTreeMap::new(),
            };
            buffer.push_str(&op.to_line()?);
            buffer.push('\n');
            written.push(op);
        }
        if buffer.is_empty() {
            return Ok(written);
        }
        // One `write_all` for the whole run: fewer partial-write windows, and
        // for a single op it is exactly the "one op = one write" rule.
        self.file.write_all(buffer.as_bytes()).map_err(|source| Error::Io {
            action: "append to",
            path: self.path.clone(),
            source,
        })?;
        Ok(written)
    }

    /// Flush to disk.
    ///
    /// Call after a user-initiated save (§3.3). Edits are rare and an fsync
    /// costs nothing at this rate; the alternative is telling someone their
    /// document is saved when a power cut would disagree.
    ///
    /// # Errors
    /// [`Error::Io`].
    pub fn commit(&mut self) -> Result<(), Error> {
        self.file.sync_data().map_err(|source| Error::Io {
            action: "flush",
            path: self.path.clone(),
            source,
        })
    }

    /// The clock, for callers that need the next `ts` without appending.
    #[must_use]
    pub fn clock(&self) -> &Hlc {
        &self.clock
    }

    /// Rewrite this writer's file as the minimal set that reproduces it
    /// ([`crate::compact`]).
    ///
    /// Safe without any coordination: a writer compacts **only its own file**,
    /// and it holds that file's lock, so there is no reader-writer race to lose
    /// and no other device to agree with.
    ///
    /// Returns `None` when nothing was done. The rewrite is a same-directory
    /// temp plus a rename — atomic, and same-directory because a cross-device
    /// rename fails with `EXDEV` (the lesson v2 learned the hard way). The temp
    /// name deliberately does not match the journal grammar, so a compaction
    /// that dies half-way leaves a file the next fold ignores rather than a
    /// truncated history it believes.
    ///
    /// # Errors
    /// [`Error::Io`] or [`Error::Serialize`]. On failure the original file is
    /// untouched: nothing is replaced until the new one is complete and flushed.
    pub fn compact(&mut self, now_ms: i64, when: When) -> Result<Option<Report>, Error> {
        let io = |action: &'static str, path: &Path| {
            let path = path.to_path_buf();
            move |source: std::io::Error| Error::Io { action, path: path.clone(), source }
        };

        let body =
            std::fs::read_to_string(&self.path).map_err(io("read for compaction", &self.path))?;
        let (lines, _torn) = crate::op::parse_body(&body);
        let plan = crate::compact::plan(&lines, now_ms);
        if when == When::IfWorthwhile && !plan.worth_doing() {
            return Ok(None);
        }

        let directory = self.path.parent().unwrap_or_else(|| Path::new("."));
        let temp = directory.join(names::compaction_temp_file(&self.writer_id, std::process::id()));

        // Build the whole new body in memory first. A writer's file is a few
        // megabytes at the store's real scale (§3.3), and holding it means the
        // window where the temp exists is as short as possible.
        let mut rewritten = String::with_capacity(body.len());
        for &index in &plan.keep {
            match &lines[index] {
                // Re-serialized, which is lossless because `Op` carries unknown
                // fields (`extra`).
                crate::op::Line::Op(op) => rewritten.push_str(&op.to_line()?),
                // Never re-serialized: bytes this build did not understand are
                // bytes it must not rewrite.
                crate::op::Line::Opaque { raw, .. } | crate::op::Line::Malformed { raw, .. } => {
                    rewritten.push_str(raw);
                }
            }
            rewritten.push('\n');
        }

        {
            let mut file = File::create(&temp).map_err(io("create temp file", &temp))?;
            file.write_all(rewritten.as_bytes()).map_err(io("write temp file", &temp))?;
            // Flush before the rename, or a crash could leave the rename done
            // and the contents not — the one ordering that loses data.
            file.sync_all().map_err(io("flush temp file", &temp))?;
        }
        std::fs::rename(&temp, &self.path).map_err(io("rename temp file over", &self.path))?;

        // The old handle still points at the replaced file, so reopen.
        self.file = OpenOptions::new()
            .create(true)
            .read(true)
            .append(true)
            .open(&self.path)
            .map_err(io("reopen after compaction", &self.path))?;

        Ok(Some(Report {
            lines_before: plan.total,
            lines_after: plan.keep.len(),
            bytes_before: body.len() as u64,
            bytes_after: rewritten.len() as u64,
        }))
    }
}

/// Whether [`Writer::compact`] should respect the trigger.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum When {
    /// Only if the file is mostly dead ops (the §3.3 trigger). What a clean
    /// exit uses.
    IfWorthwhile,
    /// Regardless — for a maintenance verb the user asked for.
    Always,
}

/// What a compaction did, for the caller to report.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Report {
    /// Lines before.
    pub lines_before: usize,
    /// Lines after.
    pub lines_after: usize,
    /// Bytes before.
    pub bytes_before: u64,
    /// Bytes after.
    pub bytes_after: u64,
}

fn create_dir_all(path: &Path, action: &'static str) -> Result<(), Error> {
    std::fs::create_dir_all(path).map_err(|source| Error::Io {
        action,
        path: path.to_path_buf(),
        source,
    })
}

/// Truncate a torn final line, so the next append cannot be glued onto it.
///
/// This is the repair REWRITE.md §3.3 calls critical, and the reason is worth
/// stating plainly: without it, an append after a torn line produces
/// `{"v":1,"ts":10,"w":"desk-co{"v":1,"ts":11,…}` — one unparseable line. The
/// torn op was already lost (it was never durable); gluing destroys the **new**
/// op too, and the user would have no idea.
fn repair_torn_tail(file: &mut File, path: &Path) -> Result<(), Error> {
    let io = |action: &'static str| {
        move |source: std::io::Error| Error::Io { action, path: path.to_path_buf(), source }
    };
    let length = file.metadata().map_err(io("stat"))?.len();
    if length == 0 {
        return Ok(());
    }

    // Read only the tail: journals reach megabytes, and this runs on every
    // launch that opens a writer. 64 KiB is far more than any single op.
    let window = length.min(64 * 1024);
    let start = length - window;
    let mut tail = vec![0u8; usize::try_from(window).unwrap_or(usize::MAX)];
    file.seek(SeekFrom::Start(start)).map_err(io("seek"))?;
    std::io::Read::read_exact(file, &mut tail).map_err(io("read tail of"))?;

    if tail.last() == Some(&b'\n') {
        return Ok(());
    }
    // Everything after the last newline is the torn line. If there is no
    // newline in the whole window the file is one long unterminated line, and
    // truncating to `start` would be wrong — but a 64 KiB op cannot exist, so
    // that means the file is a single torn line: truncate it entirely.
    let keep = match tail.iter().rposition(|byte| *byte == b'\n') {
        Some(index) => start + index as u64 + 1,
        None if window == length => 0,
        None => return Ok(()),
    };
    file.set_len(keep).map_err(io("truncate torn tail of"))?;
    file.seek(SeekFrom::End(0)).map_err(io("seek to end of"))?;
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

    /// An appended op round-trips: it parses back, folds to the expected state,
    /// and carries the writer's own id and a stamped version.
    #[test]
    fn appended_ops_round_trip_through_the_fold() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append(Draft::create("doc", "passport")).expect("append");
        writer.append(Draft::set("doc", "passport", "name", "Passport")).expect("append");
        writer.commit().expect("commit");

        let load = fixture.journal.load(Namespace::Meta).expect("loads");
        assert!(load.anomalies.is_empty(), "{:?}", load.anomalies);
        let state = fold(&load.lines);
        assert_eq!(state.get("doc", "passport").unwrap().fields["name"], "Passport");
        assert_eq!(load.lines[0].as_op().unwrap().w, "desk-core");
        assert_eq!(load.lines[0].as_op().unwrap().v, FORMAT_VERSION);
    }

    /// The clock is strictly monotonic per writer even when the wall clock
    /// jumps backwards — an NTP correction between sessions must never let one
    /// writer reorder against itself.
    #[test]
    fn the_clock_never_goes_backwards() {
        let mut clock = Hlc::seeded(0);
        assert_eq!(clock.tick_at(1_000), 1_000);
        assert_eq!(clock.tick_at(500), 1_001, "a backwards clock still moves forward");
        assert_eq!(clock.tick_at(500), 1_002);
        assert_eq!(clock.tick_at(5_000), 5_000, "and it rejoins wall time when it can");
    }

    /// The clock seeds from the highest `ts` in the *whole store*, so a writer
    /// on a slow-clocked device cannot lose every LWW comparison to the other
    /// device until wall time catches up.
    #[test]
    fn the_clock_seeds_from_the_whole_store() {
        let mut clock = Hlc::seeded(9_999_999_999_999);
        assert_eq!(clock.tick_at(1_000), 10_000_000_000_000);
    }

    /// Two ops from one writer never share a timestamp, which is the property
    /// the fold's total order depends on.
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

    /// **The repair that matters.** A process died mid-append, leaving a torn
    /// line. Without truncating it first, the next append is glued onto it and
    /// the *new* op — the one the user just made — is destroyed.
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
        writer.append(Draft::create("doc", "new")).expect("append");
        writer.commit().expect("commit");

        let body = std::fs::read_to_string(&path).expect("read");
        let (lines, torn) = parse_body(&body);
        assert!(torn.is_none(), "the file ends cleanly");
        assert_eq!(lines.len(), 2, "the torn line is gone, the new op is intact");
        assert!(lines.iter().all(|line| line.as_op().is_some()), "nothing was glued together");

        let state = fold(&lines);
        assert!(state.get("doc", "a").is_some() && state.get("doc", "new").is_some());
    }

    /// A file that is nothing but one torn line is repaired to empty rather
    /// than left to poison the next append.
    #[test]
    fn a_file_of_only_a_torn_line_is_emptied() {
        let fixture = fixture();
        let path = fixture.journal.file_path(Namespace::Meta, "desk-core");
        std::fs::create_dir_all(path.parent().unwrap()).expect("create");
        std::fs::write(&path, "{\"v\":1,\"ts\":11,\"w\":\"desk-co").expect("write");

        let mut writer = open(&fixture, "desk-core");
        writer.append(Draft::create("doc", "new")).expect("append");
        let (lines, torn) = parse_body(&std::fs::read_to_string(&path).expect("read"));
        assert!(torn.is_none() && lines.len() == 1);
    }

    /// One process per writer id. The second is refused with a *recoverable*
    /// error, because the answer is "run read-only", not "exit" — `ds status`
    /// from cron has to keep working while the TUI is open.
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

    /// Dropping a writer releases the lock, so a crashed process does not lock
    /// the user out of their own store until reboot.
    #[test]
    fn dropping_a_writer_releases_the_lock() {
        let fixture = fixture();
        drop(open(&fixture, "desk-core"));
        assert!(
            Writer::open(&fixture.journal, Namespace::Meta, "desk-core", &fixture.locks, 0).is_ok()
        );
    }

    /// The lock never lives in the synced tree: it would replicate to the other
    /// device and lock it out of its own journal.
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

    /// Compaction shrinks the file and leaves the fold identical — checked
    /// through a real rewrite, not just the planner.
    #[test]
    fn compacting_shrinks_the_file_without_changing_the_fold() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append(Draft::create("doc", "passport")).expect("append");
        for i in 0..40 {
            writer
                .append(Draft::set("doc", "passport", "name", format!("Passport v{i}")))
                .expect("append");
        }
        writer.commit().expect("commit");

        let before = fold(&fixture.journal.load(Namespace::Meta).expect("loads").lines);
        // Far in the future, so nothing is inside the 30-day retention window.
        let future = writer.clock().last() + crate::compact::RETENTION_MS * 2;
        let report =
            writer.compact(future, When::IfWorthwhile).expect("compacts").expect("did work");

        assert_eq!(report.lines_after, 2, "a create and the newest name write");
        assert!(report.bytes_after < report.bytes_before / 4);

        let load = fixture.journal.load(Namespace::Meta).expect("loads");
        assert!(load.anomalies.is_empty(), "{:?}", load.anomalies);
        assert_eq!(fold(&load.lines).canonical_json(), before.canonical_json());
    }

    /// A compaction never lowers the file's high-water mark, which is what lets
    /// a `max_ts` regression be trusted as a damage signal.
    #[test]
    fn compaction_never_lowers_the_high_water_mark() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append(Draft::create("doc", "x")).expect("append");
        for i in 0..30 {
            writer.append(Draft::set("doc", "x", "name", format!("v{i}"))).expect("append");
        }
        writer.commit().expect("commit");
        let before = fixture.journal.load(Namespace::Meta).expect("loads").files[0].max_ts;

        let future = writer.clock().last() + crate::compact::RETENTION_MS * 2;
        writer.compact(future, When::Always).expect("compacts");

        let after = fixture.journal.load(Namespace::Meta).expect("loads").files[0].max_ts;
        assert_eq!(before, after);
    }

    /// The writer keeps working after a compaction — the old file handle points
    /// at a replaced inode, so it has to be reopened.
    #[test]
    fn appends_continue_after_a_compaction() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append(Draft::create("doc", "x")).expect("append");
        for i in 0..20 {
            writer.append(Draft::set("doc", "x", "name", format!("v{i}"))).expect("append");
        }
        let future = writer.clock().last() + crate::compact::RETENTION_MS * 2;
        writer.compact(future, When::Always).expect("compacts");

        writer.append(Draft::set("doc", "x", "slot", 7)).expect("append after compaction");
        writer.commit().expect("commit");

        let load = fixture.journal.load(Namespace::Meta).expect("loads");
        assert!(load.anomalies.is_empty(), "{:?}", load.anomalies);
        assert_eq!(fold(&load.lines).get("doc", "x").expect("alive").fields["slot"], 7);
    }

    /// The trigger is respected: a healthy file is left alone, and no temp file
    /// is left behind either way.
    #[test]
    fn a_healthy_file_is_left_alone_and_no_temp_survives() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append(Draft::create("doc", "x")).expect("append");
        writer.append(Draft::set("doc", "x", "name", "only")).expect("append");
        let future = writer.clock().last() + crate::compact::RETENTION_MS * 2;
        assert!(writer.compact(future, When::IfWorthwhile).expect("runs").is_none());

        let directory = writer.path().parent().expect("has a parent");
        let leftovers: Vec<_> = std::fs::read_dir(directory)
            .expect("readable")
            .filter_map(|e| e.ok().map(|e| e.file_name().to_string_lossy().into_owned()))
            .filter(|name| name.contains(".tmp-"))
            .collect();
        assert!(leftovers.is_empty(), "temp files must never be left in the synced tree");
    }

    /// An id outside the frozen grammar is refused before anything is created —
    /// it would produce a file the fold refuses to read.
    #[test]
    fn an_invalid_writer_id_is_refused() {
        let fixture = fixture();
        let bad = Writer::open(&fixture.journal, Namespace::Meta, "Desk_Core", &fixture.locks, 0);
        assert!(matches!(bad, Err(Error::InvalidWriterId { .. })), "{bad:?}");
    }

    /// A run of ops lands adjacent in one file, which is what makes an id
    /// rename (create + copy + fixups + delete) safe to fold at any point.
    #[test]
    fn a_run_of_ops_is_written_consecutively() {
        let fixture = fixture();
        let mut writer = open(&fixture, "desk-core");
        writer.append(Draft::create("doc", "coc-2019")).expect("append");
        writer
            .append_all([
                Draft::create("doc", "coc-2019-in"),
                Draft::set("doc", "coc-2025", "supersedes", "coc-2019-in"),
                Draft::delete("doc", "coc-2019"),
            ])
            .expect("append run");
        writer.commit().expect("commit");

        let load = fixture.journal.load(Namespace::Meta).expect("loads");
        let ids: Vec<&str> =
            load.lines.iter().filter_map(|l| l.as_op()).map(|op| op.id.as_str()).collect();
        assert_eq!(ids, ["coc-2019", "coc-2019-in", "coc-2025", "coc-2019"]);
    }
}
