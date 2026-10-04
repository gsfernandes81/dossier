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
use std::time::Duration;

use journal::Journal;

use crate::app::Msg;

/// How often a session looks for ops other writers have added. A look is a
/// listing and a `stat` per file; the journal is read only when one changed.
pub const POLL: Duration = Duration::from_secs(2);

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
}

impl Follower {
    /// Follows `journal` from a read that saw `stamp` and `max_ts`, writing
    /// as `owner`.
    #[must_use]
    pub fn new(journal: Journal, owner: Option<Owner>, stamp: journal::Stamp, max_ts: i64) -> Self {
        Self { journal, owner, writer: None, stamp, max_ts }
    }

    /// Appends and commits `drafts`, then reads the journal back.
    pub fn save(&mut self, drafts: Vec<journal::Draft>) -> Msg {
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
        // The fsync a user-initiated save requires: "saved" on screen must
        // survive a power cut.
        if let Err(error) = writer.append_all(drafts).and_then(|_| writer.commit()) {
            return failed(error.to_string());
        }
        match self.read() {
            Ok(store) => Msg::Saved(Box::new(store)),
            Err(error) => failed(format!("saved, but the journal could not be read back: {error}")),
        }
    }

    /// The journal re-read, when its files changed since the last read.
    pub fn poll(&mut self) -> Option<Msg> {
        self.catch_up().map(|store| Msg::Reloaded(Box::new(store)))
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
        self.max_ts = self.max_ts.max(loaded.stats.max_ts());
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
