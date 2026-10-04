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

//! Getting from "a directory exists" to "a store, and today's date".
//!
//! Every entry point — the TUI, `ds status`, `ds open` — needs the same three
//! steps in the same order, and they must agree about what the store contains or
//! `ds status` becomes a report about a different store than the one on screen.
//! So the steps live here once.
//!
//! The `meta` namespace only. Scan text and transcripts live in `enrich` and are
//! loaded only when something asks for them, which keeps the fold cheap
//! enough to redo on every launch.

use std::path::{Path, PathBuf};

use jiff::{ToSpan, Zoned};
use journal::{FoldStats, Journal, Load, Namespace};

use crate::Store;

/// A loaded store, with the accounting `ds status` reports.
pub struct Loaded {
    /// The documents, locations and settings.
    pub store: Store,
    /// The raw load: per-file reports and anomalies.
    pub load: Load,
    /// What the fold made of it.
    pub stats: FoldStats,
    /// The journal's files as they were just before this load.
    pub stamp: journal::Stamp,
    /// The directory that was read.
    pub path: PathBuf,
    /// Today, ISO.
    pub today: String,
    /// The far edge of the warn window, ISO.
    pub warn_until: String,
}

/// Finds the journal to read and the root its file paths resolve against.
///
/// `root` is `--root` or `$DS_ROOT`, and beats the config's. An explicit
/// journal is read as given, and a journal at `<root>/.dossier/journal`
/// implies its own root when nothing else names one. Returns `None` when
/// nothing names a store: this device is not set up.
#[must_use]
pub fn locate(
    journal: Option<PathBuf>,
    root: Option<PathBuf>,
    config_root: Option<PathBuf>,
) -> Option<(Journal, PathBuf)> {
    let root = root.or(config_root);
    match (journal, root) {
        (Some(journal), root) => {
            let journal = Journal::new(journal);
            let root = root.unwrap_or_else(|| implied_root(journal.path()));
            Some((journal, root))
        }
        (None, Some(root)) => Some((Journal::under_root(root.clone()), root)),
        (None, None) => None,
    }
}

/// Read, fold, and build.
///
/// # Errors
/// Only when the journal directory exists but cannot be listed — the one
/// situation that must never degrade into "the store is empty".
pub fn load(journal: &Journal) -> Result<Loaded, journal::store::Error> {
    let stamp = journal.stamp(Namespace::Meta);
    let load = journal.load(Namespace::Meta)?;
    let folded = journal::fold(&load.lines);
    let store = Store::build(&folded);
    let (today, warn_until) = window();
    Ok(Loaded {
        store,
        stats: folded.stats,
        load,
        stamp,
        path: journal.path().to_path_buf(),
        today,
        warn_until,
    })
}

/// Today and the far edge of the warn window, both ISO.
///
/// Resolved once, at startup: every expiry comparison after this is a string
/// comparison against these two, which is why nothing else in the crate needs a
/// date library.
#[must_use]
pub fn window() -> (String, String) {
    let today = Zoned::now().date();
    let warn_until = today.checked_add(crate::doc::WARN_DAYS.days()).unwrap_or(today);
    (today.to_string(), warn_until.to_string())
}

fn implied_root(journal: &Path) -> PathBuf {
    journal.parent().and_then(Path::parent).map_or_else(|| PathBuf::from("."), Path::to_path_buf)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// An explicit journal is read as given, and an explicit root still
    /// decides where its files are.
    #[test]
    fn an_explicit_journal_beats_every_default() {
        let (journal, root) = locate(
            Some(PathBuf::from("/tmp/copy")),
            Some(PathBuf::from("/home/u/Sync")),
            Some(PathBuf::from("/config/root")),
        )
        .expect("located");
        assert_eq!(journal.path(), Path::new("/tmp/copy"));
        assert_eq!(root, Path::new("/home/u/Sync"));
    }

    /// Otherwise the root decides, and the journal is the fixed place inside it.
    #[test]
    fn a_root_implies_the_journal_directory() {
        let (journal, root) =
            locate(None, Some(PathBuf::from("/home/u/Sync")), Some(PathBuf::from("/config")))
                .expect("located");
        assert!(journal.path().ends_with(".dossier/journal"), "{}", journal.path().display());
        assert!(journal.path().starts_with("/home/u/Sync"));
        assert_eq!(root, Path::new("/home/u/Sync"));
    }

    /// The config's root is what makes a bare `ds` work on a configured device.
    #[test]
    fn the_config_root_is_the_fallback() {
        let (journal, _) =
            locate(None, None, Some(PathBuf::from("/config/root"))).expect("located");
        assert!(journal.path().starts_with("/config/root"));
    }

    /// **A copied journal still knows where its documents are**: two levels up
    /// from `<root>/.dossier/journal` is the root.
    #[test]
    fn a_journal_path_implies_its_root() {
        let (_, root) =
            locate(Some(PathBuf::from("/mnt/copy/.dossier/journal")), None, None).expect("located");
        assert_eq!(root, Path::new("/mnt/copy"));
    }

    /// Nothing naming a store is a device that is not set up.
    #[test]
    fn nothing_named_is_not_set_up() {
        assert!(locate(None, None, None).is_none());
    }

    /// The window runs from today to [`crate::doc::WARN_DAYS`] ahead.
    #[test]
    fn the_warn_window_is_today_plus_the_constant() {
        let (today, warn_until) = window();
        assert_eq!(today.len(), 10, "ISO date: {today}");
        assert!(warn_until > today);
    }
}
