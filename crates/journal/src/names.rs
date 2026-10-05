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

//! The frozen filename grammar.
//!
//! Discovery is a directory glob — there is no registry — so *what counts as a
//! journal file* is load-bearing safety, not cosmetics. Fold the wrong file and
//! the store gains ops that were deliberately set aside; fold a Syncthing
//! conflict copy and the "conflicts are structurally impossible" guarantee dies
//! quietly. Hand-written rather than a regex: the grammar is too small to be
//! worth a dependency.

/// Extension every journal file ends with.
pub const EXTENSION: &str = ".jsonl";

/// The glob that must be in `.stignore` on **both devices before any journal
/// exists in the synced tree**.
///
/// Compaction writes `<writer>.jsonl.tmp-<pid>` next to the file it is
/// rewriting, in the *synced* directory, because a cross-device rename fails
/// with `EXDEV`. Without this ignore, Syncthing would replicate half-written
/// temp files to the other device.
pub const COMPACTION_TEMP_GLOB: &str = "*.jsonl.tmp-*";

/// Whether `name` is a Syncthing conflict copy.
#[must_use]
pub fn is_sync_conflict(name: &str) -> bool {
    name.contains(".sync-conflict-")
}

/// Returns the writer id of a journal file this build may fold, or `None`.
///
/// Grammar: `^[a-z0-9][a-z0-9-]*\.jsonl$`. Conflict copies and compaction
/// temps fail it on their extra dots.
#[must_use]
pub fn writer_of(name: &str) -> Option<&str> {
    name.strip_suffix(EXTENSION).filter(|stem| is_valid_writer_id(stem))
}

/// Whether `id` is a usable writer id — the file stem, and the `w` field of
/// every op that writer emits.
///
/// Convention is `<device>-<component>` (`desk-core`, `phone-core`, `desk-lab`),
/// but the grammar only enforces the character set: the device half comes from
/// per-device config, and rejecting a user's chosen device name for having no
/// hyphen would be officious.
#[must_use]
pub fn is_valid_writer_id(id: &str) -> bool {
    let mut chars = id.chars();
    let Some(first) = chars.next() else { return false };
    if !first.is_ascii_lowercase() && !first.is_ascii_digit() {
        return false;
    }
    chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
}

/// The file name a writer appends to.
#[must_use]
pub fn writer_file(writer: &str) -> String {
    format!("{writer}{EXTENSION}")
}

/// The temp name compaction writes before its atomic rename.
///
/// Deliberately *not* matching [`writer_of`]: if a compaction dies
/// mid-rewrite, the leftover must be invisible to the next fold rather than
/// contributing a truncated view of the writer's history.
#[must_use]
pub fn compaction_temp_file(writer: &str, pid: u32) -> String {
    format!("{writer}{EXTENSION}.tmp-{pid}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ordinary_writer_files_are_accepted() {
        for name in ["desk-core.jsonl", "phone-core.jsonl", "desk-lab.jsonl", "a1.jsonl"] {
            assert!(writer_of(name).is_some(), "{name} should be folded");
        }
    }

    #[test]
    fn everything_else_is_excluded() {
        for name in [
            "desk-core.sync-conflict-20260816-120000-ABCDEFG.jsonl",
            "desk-core.jsonl.tmp-4231",
            "desk-core.jsonl.bak",
            "Desk-Core.jsonl",
            "desk_core.jsonl",
            "desk core.jsonl",
            ".jsonl",
            "-desk.jsonl",
            "desk-core.json",
            "README.md",
        ] {
            assert!(writer_of(name).is_none(), "{name} must never be folded");
        }
    }

    #[test]
    fn compaction_temps_are_invisible_to_the_fold() {
        let temp = compaction_temp_file("desk-core", 4231);
        assert_eq!(temp, "desk-core.jsonl.tmp-4231");
        assert!(writer_of(&temp).is_none());
        assert!(temp.contains(COMPACTION_TEMP_GLOB.trim_matches('*')));
    }

    /// Writer ids and file stems share one grammar, so a writer that can name
    /// its file can also sign its ops.
    #[test]
    fn writer_ids_and_file_names_agree() {
        assert!(is_valid_writer_id("desk-core"));
        assert!(!is_valid_writer_id(""));
        assert!(!is_valid_writer_id("-lead-hyphen"));
        assert!(!is_valid_writer_id("UPPER"));
        assert_eq!(writer_of(&writer_file("phone-core")), Some("phone-core"));
    }
}
