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
//! loaded lazily when something actually asks for them (§3.1) — keeping them off
//! the startup path is half the reason the fold is cheap enough to redo on every
//! launch instead of caching it.

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
    /// The directory that was read.
    pub path: PathBuf,
    /// Today, ISO.
    pub today: String,
    /// The far edge of the warn window, ISO.
    pub warn_until: String,
}

/// Decide which journal directory to read.
///
/// Precedence, most specific first: an explicit `--journal`, then `--root` or
/// `$DS_ROOT`, then the per-device config's `syncthing_root`, then the current
/// directory. The explicit flags win because the way R3 is daily-driven before
/// cutover is by pointing it at an exported copy.
#[must_use]
pub fn locate(
    journal: Option<PathBuf>,
    root: Option<PathBuf>,
    config_root: Option<PathBuf>,
) -> Journal {
    if let Some(journal) = journal {
        return Journal::new(journal);
    }
    let root = root
        .or_else(|| std::env::var_os("DS_ROOT").map(PathBuf::from))
        .or(config_root)
        .unwrap_or_else(|| PathBuf::from("."));
    Journal::under_root(root)
}

/// Read, fold, and build.
///
/// # Errors
/// Only when the journal directory exists but cannot be listed — the one
/// situation that must never degrade into "the store is empty".
pub fn load(journal: &Journal) -> Result<Loaded, journal::store::Error> {
    let load = journal.load(Namespace::Meta)?;
    let folded = journal::fold(&load.lines);
    let store = Store::build(&folded);
    let (today, warn_until) = window(store.warn_days());
    Ok(Loaded {
        store,
        stats: folded.stats,
        load,
        path: journal.path().to_path_buf(),
        today,
        warn_until,
    })
}

/// A century. The warn window is clamped to it because the setting comes from
/// the journal, and a hostile or fat-fingered value must not be able to stop the
/// app from starting — `jiff` panics when a span is built from a nonsense count,
/// before any checked arithmetic gets a chance to say no.
const MAX_WARN_DAYS: i64 = 36_500;

/// Today and the far edge of the warn window, both ISO.
///
/// Resolved once, at startup: every expiry comparison after this is a string
/// comparison against these two, which is why nothing else in the crate needs a
/// date library. A negative window means "warn about nothing but what has
/// already expired", which is a coherent thing to want and costs no special case.
#[must_use]
pub fn window(warn_days: i64) -> (String, String) {
    let today = Zoned::now().date();
    let days = warn_days.clamp(-MAX_WARN_DAYS, MAX_WARN_DAYS);
    let warn_until = today.checked_add(days.days()).unwrap_or(today);
    (today.to_string(), warn_until.to_string())
}

/// The Syncthing root a stored path is resolved against.
///
/// `--root` if given, else the config's, else the journal's grandparent — a
/// journal at `<root>/.dossier/journal` implies its own root, so pointing `ds`
/// at a copied journal still opens files from the right place.
#[must_use]
pub fn root_for(
    explicit: Option<PathBuf>,
    config_root: Option<PathBuf>,
    journal: &Journal,
) -> PathBuf {
    explicit
        .or_else(|| std::env::var_os("DS_ROOT").map(PathBuf::from))
        .or(config_root)
        .unwrap_or_else(|| implied_root(journal.path()))
}

fn implied_root(journal: &Path) -> PathBuf {
    journal.parent().and_then(Path::parent).map_or_else(|| PathBuf::from("."), Path::to_path_buf)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The explicit flag wins over everything, because pointing at an exported
    /// copy is how R3 is used before cutover.
    #[test]
    fn an_explicit_journal_beats_every_default() {
        let journal = locate(
            Some(PathBuf::from("/tmp/copy")),
            Some(PathBuf::from("/home/u/Sync")),
            Some(PathBuf::from("/config/root")),
        );
        assert_eq!(journal.path(), Path::new("/tmp/copy"));
    }

    /// Otherwise the root decides, and the journal is the fixed place inside it.
    #[test]
    fn a_root_implies_the_journal_directory() {
        let journal = locate(None, Some(PathBuf::from("/home/u/Sync")), None);
        assert!(journal.path().ends_with(".dossier/journal"), "{}", journal.path().display());
        assert!(journal.path().starts_with("/home/u/Sync"));
    }

    /// The config's root is the fallback — which is what makes a bare `ds` work
    /// on a configured device.
    #[test]
    fn the_config_root_is_the_last_resort_before_the_cwd() {
        std::env::remove_var("DS_ROOT");
        let journal = locate(None, None, Some(PathBuf::from("/config/root")));
        assert!(journal.path().starts_with("/config/root"));
    }

    /// **A copied journal still knows where its documents are**: two levels up
    /// from `<root>/.dossier/journal` is the root.
    #[test]
    fn a_journal_path_implies_its_root() {
        std::env::remove_var("DS_ROOT");
        let journal = Journal::new("/mnt/copy/.dossier/journal");
        assert_eq!(root_for(None, None, &journal), Path::new("/mnt/copy"));
        assert_eq!(
            root_for(Some(PathBuf::from("/elsewhere")), None, &journal),
            Path::new("/elsewhere"),
            "an explicit root still wins"
        );
    }

    /// The window is today plus the setting, and an absurd setting cannot stop
    /// the app from starting.
    #[test]
    fn the_warn_window_is_today_plus_the_setting() {
        let (today, warn_until) = window(90);
        assert_eq!(today.len(), 10, "ISO date: {today}");
        assert!(warn_until > today);
        let (_, absurd) = window(i64::MAX);
        assert!(absurd.starts_with("21"), "an absurd window clamps to a century: {absurd}");
        let (today, past) = window(-30);
        assert!(past < today, "a negative window warns about nothing not already expired");
    }
}
