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

//! Whether journal compaction may run here. Its temp sits next to the file it
//! rewrites, inside the Syncthing folder, so the folder's `.stignore` must keep
//! it from reaching a peer half-written.

use std::path::{Path, PathBuf};

use journal::names::COMPACTION_TEMP_GLOB;

/// Syncthing's marker at the top of every folder it shares.
const MARKER: &str = ".stfolder";

/// Where the store stands with respect to compaction.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Gate {
    /// No Syncthing folder holds the store, so a temp has no peer to reach.
    Unsynced,
    /// The folder's `.stignore` keeps temps from syncing.
    Ignored,
    /// The folder's `.stignore` lacks the line; compaction is off.
    Missing {
        /// The `.stignore` that needs it, which may not exist yet.
        stignore: PathBuf,
    },
}

impl Gate {
    /// Whether compaction may run.
    #[must_use]
    pub fn allows(&self) -> bool {
        !matches!(self, Self::Missing { .. })
    }
}

/// Returns the gate for a store whose journal is `dir`.
#[must_use]
pub fn gate(dir: &Path) -> Gate {
    let Some(folder) = syncthing_folder(dir) else { return Gate::Unsynced };
    let stignore = folder.join(".stignore");
    let body = std::fs::read(&stignore).unwrap_or_default();
    if String::from_utf8_lossy(&body).lines().any(|line| line.trim() == COMPACTION_TEMP_GLOB) {
        Gate::Ignored
    } else {
        Gate::Missing { stignore }
    }
}

/// Returns the nearest folder at or above `dir` that Syncthing shares.
#[must_use]
pub fn syncthing_folder(dir: &Path) -> Option<PathBuf> {
    crate::config::absolute(dir)
        .ancestors()
        .find(|folder| folder.join(MARKER).exists())
        .map(Path::to_path_buf)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A Syncthing folder at `dir/Sync`, with the store two levels below.
    fn synced(dir: &Path) -> (PathBuf, PathBuf) {
        let folder = dir.join("Sync");
        let journal = folder.join("Documents/.dossier/journal");
        std::fs::create_dir_all(folder.join(MARKER)).expect("mkdir");
        (folder, journal)
    }

    #[test]
    fn the_folder_is_found_above_the_store_root() {
        let dir = tempfile::tempdir().expect("tempdir");
        let (folder, journal) = synced(dir.path());
        assert_eq!(syncthing_folder(&journal), Some(folder));
    }

    /// Nothing syncs the store, so nothing can carry a temp to a peer.
    #[test]
    fn no_marker_allows_compaction() {
        let dir = tempfile::tempdir().expect("tempdir");
        let gate = gate(&dir.path().join("Documents/.dossier/journal"));
        assert_eq!(gate, Gate::Unsynced);
        assert!(gate.allows());
    }

    #[test]
    fn the_gate_needs_the_exact_line() {
        let dir = tempfile::tempdir().expect("tempdir");
        let (folder, journal) = synced(dir.path());
        let stignore = folder.join(".stignore");
        let missing = Gate::Missing { stignore: stignore.clone() };
        assert_eq!(gate(&journal), missing, "no .stignore at all");

        for (body, expected) in [
            ("*.jsonl.tmp-*\n", Gate::Ignored),
            ("(?d).DS_Store\r\n  *.jsonl.tmp-*  \r\n", Gate::Ignored),
            ("(?d).DS_Store\n", missing.clone()),
            ("// *.jsonl.tmp-*\n", missing.clone()),
            ("*.jsonl.tmp-*.bak\n", missing.clone()),
        ] {
            std::fs::write(&stignore, body).expect("write");
            assert_eq!(gate(&journal), expected, "{body:?}");
        }
        assert!(!missing.allows());
    }
}
