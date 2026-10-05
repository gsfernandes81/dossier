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

//! The journal thread's work: saving for the session, and noticing when
//! another writer's ops arrive.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use journal::Journal;

use crate::app::Msg;

/// How often a session looks for ops other writers have added. A look is a
/// listing and a `stat` per file; the journal is read only when one changed.
pub const POLL: Duration = Duration::from_secs(2);

/// How long after its last save a session considers compacting its own file,
/// so a burst of edits is compacted once, after it ends.
pub const QUIET: Duration = Duration::from_secs(30);

/// Who a session writes as, and for which store.
#[derive(Debug, Clone)]
pub struct Owner {
    /// This device's name, as `ds init` recorded it.
    pub device: String,
    /// `<device>-core`.
    pub writer_id: String,
    /// The store root, which decides whether a same-named `ds.exe` is a twin.
    pub root: PathBuf,
    /// Where the writer lock is taken.
    pub lock_dir: PathBuf,
}

impl Owner {
    /// Writes as `device` for the store at `root`, locking in `lock_dir`.
    #[must_use]
    pub fn new(device: &str, root: &Path, lock_dir: &Path) -> Self {
        Self {
            device: device.to_string(),
            writer_id: crate::init::writer_id(device),
            root: root.to_path_buf(),
            lock_dir: lock_dir.to_path_buf(),
        }
    }

    /// Who this device's config writes as.
    ///
    /// # Errors
    /// Why this session cannot write, said for the band.
    pub fn for_config(config: &crate::config::Config, root: &Path) -> Result<Self, String> {
        let Some(device) = &config.device else {
            return Err("no device name — run `ds init` to enable editing".into());
        };
        let Some(lock_dir) = crate::config::state_dir() else {
            return Err(format!(
                "nowhere to keep this device's writer lock — set {}",
                crate::config::STATE_DIR_ENV
            ));
        };
        Ok(Self::new(device, root, &lock_dir))
    }
}

/// The journal thread's state: the journal as last read, and the writer once
/// a save has opened it.
///
/// Every store the UI receives is built from a fresh read of the journal, so
/// another writer's ops arrive with a save as well as with a poll, and the
/// two can never arrive out of order.
pub struct Follower {
    journal: Journal,
    /// `None` when this session cannot write; the UI then never saves.
    owner: Option<Owner>,
    writer: Option<journal::Writer>,
    /// The journal's files at the last read.
    stamp: journal::Stamp,
    /// The newest `ts` read from any writer.
    max_ts: i64,
    /// Today and the warn edge as the UI last heard them.
    window: (String, String),
    /// When the last save landed, until compaction has considered it.
    since_save: Option<Instant>,
    /// [`QUIET`], which tests shorten.
    quiet: Duration,
}

impl Follower {
    /// Follows `journal` from a read that saw `stamp` and `max_ts`, writing
    /// as `owner`.
    #[must_use]
    pub fn new(journal: Journal, owner: Option<Owner>, stamp: journal::Stamp, max_ts: i64) -> Self {
        Self {
            journal,
            owner,
            writer: None,
            stamp,
            max_ts,
            window: crate::load::window(),
            since_save: None,
            quiet: QUIET,
        }
    }

    /// Appends `drafts`, then reads the journal back.
    pub fn save(&mut self, drafts: Vec<journal::Draft>) -> Msg {
        let msg = self.write(drafts);
        // The catch-up a failed save made is not delivered with it, so the
        // next poll must read again rather than find the stamp unchanged.
        if matches!(msg, Msg::SaveFailed { .. }) {
            self.stamp = journal::Stamp::default();
        }
        msg
    }

    fn write(&mut self, drafts: Vec<journal::Draft>) -> Msg {
        let failed = |reason: String| Msg::SaveFailed { reason, permanent: false };
        // Ops that landed since the last poll still raise the clock first.
        self.catch_up();
        let Some(owner) = &self.owner else {
            return Msg::SaveFailed { reason: "this session cannot write".into(), permanent: true };
        };
        let writer = match &mut self.writer {
            Some(writer) => writer,
            empty @ None => match open(&self.journal, owner, self.max_ts) {
                Ok(writer) => empty.insert(writer),
                Err(message) => return message,
            },
        };
        if let Err(error) = writer.append_all(drafts) {
            // A partial write leaves a torn tail; reopening repairs it.
            self.writer = None;
            return failed(error.to_string());
        }
        self.since_save = Some(Instant::now());
        match self.read() {
            Ok(store) => Msg::Saved(Box::new(store)),
            Err(error) => failed(format!("saved, but the journal could not be read back: {error}")),
        }
    }

    /// A new day's dates once the date turns, else the journal re-read when
    /// its files changed since the last read — compacting this session's own
    /// file first, once its saves have gone quiet.
    pub fn poll(&mut self) -> Option<Msg> {
        let now = crate::load::window();
        if now != self.window {
            self.window = now.clone();
            return Some(Msg::Day { today: now.0, warn_until: now.1 });
        }
        self.compact_when_quiet();
        self.catch_up().map(|store| Msg::Reloaded(Box::new(store)))
    }

    /// Compacts the writer's file if worthwhile, once per burst of saves.
    ///
    /// Silent: a failed compaction leaves the file as it was and is tried again
    /// after the next quiet period. The re-read it causes folds to the same
    /// store, which the UI drops.
    fn compact_when_quiet(&mut self) {
        let (Some(writer), Some(saved)) = (&self.writer, self.since_save) else { return };
        if saved.elapsed() < self.quiet {
            return;
        }
        self.since_save = None;
        if crate::compaction::gate(self.journal.path()).allows()
            && writer.compact(journal::writer::now_ms()).is_err()
        {
            self.since_save = Some(Instant::now());
        }
    }

    /// The store, read again only when the journal's files changed.
    fn catch_up(&mut self) -> Option<crate::Store> {
        if self.journal.stamp(journal::Namespace::Meta) == self.stamp {
            return None;
        }
        self.read().ok()
    }

    /// Reads and folds the journal, and raises the writer's clock past every
    /// `ts` in it, so an edit here sorts after what it was made from.
    fn read(&mut self) -> Result<crate::Store, journal::store::Error> {
        let loaded = crate::load::load(&self.journal)?;
        self.stamp = loaded.stamp;
        self.max_ts = self.max_ts.max(loaded.stats.max_ts);
        if let Some(writer) = &mut self.writer {
            writer.observe(self.max_ts);
        }
        Ok(loaded.store)
    }
}

/// Opens the writer, at the first save rather than at launch: opening creates
/// the journal, which must not appear in a synced folder merely because `ds`
/// was run.
fn open(journal: &Journal, owner: &Owner, max_ts: i64) -> Result<journal::Writer, Msg> {
    let failed = |reason: String, permanent| Msg::SaveFailed { reason, permanent };
    // Under WSL, `ds.exe` under the same name would share the writer file
    // behind a lock this process cannot see.
    if let Some(twin) = crate::wsl::Wsl::current()
        .and_then(|wsl| crate::wsl::windows_twin(wsl, &owner.device, Some(&owner.root)))
    {
        return Err(failed(crate::init::twin_message(&owner.device, &twin), true));
    }
    journal::Writer::open(
        journal,
        journal::Namespace::Meta,
        &owner.writer_id,
        &owner.lock_dir,
        max_ts,
    )
    .map_err(|error| {
        // A held lock will be held at the next save too.
        let permanent = matches!(error, journal::writer::Error::Locked { .. });
        failed(error.to_string(), permanent)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use journal::{Draft, Namespace};

    /// Years before any test runs, so far outside compaction's retention.
    const OLD: i64 = 1_600_000_000_000;

    struct Fixture {
        _dir: tempfile::TempDir,
        root: PathBuf,
        journal: Journal,
        locks: PathBuf,
    }

    fn fixture() -> Fixture {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path().join("Sync");
        let journal = Journal::under_root(&root);
        let locks = dir.path().join("local-state");
        Fixture { _dir: dir, root, journal, locks }
    }

    /// Plants forty old renames of one document in `desk-core`'s file and
    /// returns its path.
    fn history(fixture: &Fixture) -> PathBuf {
        let path = fixture.journal.file_path(Namespace::Meta, "desk-core");
        std::fs::create_dir_all(path.parent().expect("has a parent")).expect("mkdir");
        let mut drafts = vec![Draft::create("doc", "coc")];
        drafts.extend((1..=40).map(|i| Draft::set("doc", "coc", "name", format!("v{i}"))));
        let body: String = (OLD..)
            .zip(drafts)
            .map(|(ts, draft)| draft.stamp(ts, "desk-core").to_line().expect("line") + "\n")
            .collect();
        std::fs::write(&path, body).expect("write");
        path
    }

    /// A follower, writing as `desk` when `owner`, that compacts without
    /// waiting.
    fn follower(fixture: &Fixture, owner: bool) -> Follower {
        let loaded = crate::load::load(&fixture.journal).expect("load");
        let owner = owner.then(|| Owner::new("desk", &fixture.root, &fixture.locks));
        let mut follower =
            Follower::new(fixture.journal.clone(), owner, loaded.stamp, loaded.stats.max_ts);
        follower.quiet = Duration::ZERO;
        follower
    }

    fn save(follower: &mut Follower) {
        let saved = follower.save(vec![Draft::set("doc", "coc", "slot", 7)]);
        assert!(matches!(saved, Msg::Saved(_)), "{saved:?}");
    }

    fn lines(path: &Path) -> usize {
        std::fs::read_to_string(path).expect("read").lines().count()
    }

    #[test]
    fn compaction_waits_for_quiet() {
        let fixture = fixture();
        let path = history(&fixture);
        let mut follower = follower(&fixture, true);
        follower.quiet = Duration::from_secs(3600);
        save(&mut follower);
        follower.poll();
        assert_eq!(lines(&path), 42, "not while saves may still be coming");

        follower.quiet = Duration::ZERO;
        follower.poll();
        assert_eq!(lines(&path), 3, "the create, the newest name and the new slot");
    }

    /// A rename refused by a Syncthing scan holding the file must not wait
    /// for the next save, which may be days away.
    #[test]
    fn a_failed_compaction_is_retried_without_another_save() {
        let fixture = fixture();
        let path = history(&fixture);
        let mut follower = follower(&fixture, true);
        save(&mut follower);
        let temp = path
            .with_file_name(journal::names::compaction_temp_file("desk-core", std::process::id()));
        std::fs::write(&temp, "taken").expect("plant a temp");
        follower.poll();
        assert_eq!(lines(&path), 42, "the temp name is taken");
        assert!(!temp.exists(), "the failure cleared the way");

        follower.poll();
        assert_eq!(lines(&path), 3, "retried");
    }

    /// Opening the writer creates the journal, which must not appear merely
    /// because `ds` was run.
    #[test]
    fn compaction_runs_only_after_this_session_wrote() {
        let fresh = fixture();
        assert!(follower(&fresh, true).poll().is_none());
        assert!(!fresh.journal.path().exists(), "no journal was created");

        let fixture = fixture();
        let path = history(&fixture);
        follower(&fixture, true).poll();
        assert_eq!(lines(&path), 41, "nothing was saved, so nothing is compacted");
    }

    #[test]
    fn compaction_is_off_without_the_ignore() {
        let fixture = fixture();
        let path = history(&fixture);
        std::fs::create_dir_all(fixture.root.join(".stfolder")).expect("mkdir");
        let mut follower = follower(&fixture, true);
        save(&mut follower);
        follower.poll();
        assert_eq!(lines(&path), 42, "a temp could reach a peer half-written");

        std::fs::write(fixture.root.join(".stignore"), "*.jsonl.tmp-*\n").expect("write");
        save(&mut follower);
        follower.poll();
        assert_eq!(lines(&path), 4, "the ignore turns it on; both recent saves are kept");
    }

    #[test]
    fn a_peer_sees_the_same_store_after_compaction() {
        let fixture = fixture();
        let path = history(&fixture);
        let mut desk = follower(&fixture, true);
        let mut peer =
            Follower::new(Journal::under_root(&fixture.root), None, journal::Stamp::default(), 0);
        save(&mut desk);
        let Some(Msg::Reloaded(before)) = peer.poll() else { panic!("the peer reads the save") };

        desk.poll();
        assert_eq!(lines(&path), 3, "compacted");
        let Some(Msg::Reloaded(after)) = peer.poll() else { panic!("the peer sees the rewrite") };
        assert_eq!(after, before);
    }

    /// The writer's clock seeds from the newest `ts`; losing it would let the
    /// next edit sort before the last.
    #[test]
    fn compaction_does_not_lower_the_followers_max_ts() {
        let fixture = fixture();
        let path = history(&fixture);
        let mut follower = follower(&fixture, true);
        save(&mut follower);
        let before = follower.max_ts;
        follower.poll();
        assert_eq!(lines(&path), 3, "compacted");
        assert_eq!(follower.max_ts, before);
        assert_eq!(crate::load::load(&fixture.journal).expect("load").stats.max_ts, before);
    }
}
