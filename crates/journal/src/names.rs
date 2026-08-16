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

//! The frozen filename grammar (REWRITE.md §3.1).
//!
//! Discovery is a directory glob — there is no registry — so *what counts as a
//! journal file* is load-bearing safety, not cosmetics. Fold the wrong file and
//! the store gains ops that were deliberately set aside; fold a Syncthing
//! conflict copy and the "conflicts are structurally impossible" guarantee dies
//! quietly.
//!
//! Deliberately hand-written rather than a regex: the grammar is six characters
//! wide, it is checked once per file at startup, and a regex crate would be a
//! dependency carried for one line (REWRITE.md §4.3 asks for a justification
//! per dependency — this one could not have written it).

/// Extension every journal file ends with.
pub const EXTENSION: &str = ".jsonl";

/// The glob that must be in `.stignore` on **both devices before any journal
/// exists in the synced tree** (REWRITE.md §3.1, §6 R7 pre-step).
///
/// Compaction writes `<writer>.jsonl.tmp-<pid>` next to the file it is
/// rewriting, in the *synced* directory, because an atomic rename has to be
/// same-filesystem (the v2 EXDEV lesson). Without this ignore, Syncthing would
/// happily replicate half-written temp files to the other device.
pub const COMPACTION_TEMP_GLOB: &str = "*.jsonl.tmp-*";

/// Marker Syncthing puts in the name of a conflict copy.
const SYNC_CONFLICT: &str = ".sync-conflict-";

/// Whether `name` is a journal file this build may fold.
///
/// Grammar: `^[a-z0-9][a-z0-9-]*\.jsonl$` — lowercase, digits and hyphens, with
/// no dots before the extension. Every exclusion the contract lists follows
/// from that (a `.sync-conflict-…` copy and a `…jsonl.tmp-1234` temp both carry
/// extra dots), but the conflict case is checked explicitly anyway: it is the
/// one whose silent inclusion would be a correctness bug rather than noise.
#[must_use]
pub fn is_writer_file(name: &str) -> bool {
    if name.contains(SYNC_CONFLICT) {
        return false;
    }
    let Some(stem) = name.strip_suffix(EXTENSION) else { return false };
    is_valid_writer_id(stem)
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
/// Deliberately *not* matching [`is_writer_file`]: if a compaction dies
/// mid-rewrite, the leftover must be invisible to the next fold rather than
/// contributing a truncated view of the writer's history.
#[must_use]
pub fn compaction_temp_file(writer: &str, pid: u32) -> String {
    format!("{writer}{EXTENSION}.tmp-{pid}")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The names a healthy journal directory contains.
    #[test]
    fn ordinary_writer_files_are_accepted() {
        for name in ["desk-core.jsonl", "phone-core.jsonl", "desk-lab.jsonl", "a1.jsonl"] {
            assert!(is_writer_file(name), "{name} should be folded");
        }
    }

    /// Everything the contract excludes, and why each one matters:
    /// a conflict copy would break the "no conflicts" guarantee, a temp file
    /// would fold a half-written history, and the rest are simply not ours.
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
            assert!(!is_writer_file(name), "{name} must never be folded");
        }
    }

    /// A compaction temp must not look like a journal file — that is the whole
    /// reason for its shape.
    #[test]
    fn compaction_temps_are_invisible_to_the_fold() {
        let temp = compaction_temp_file("desk-core", 4231);
        assert_eq!(temp, "desk-core.jsonl.tmp-4231");
        assert!(!is_writer_file(&temp));
        // …and the glob that hides it from Syncthing matches it.
        assert!(temp.starts_with("desk-core.jsonl.tmp-"));
        assert!(COMPACTION_TEMP_GLOB.starts_with("*.jsonl.tmp-"));
    }

    /// Writer ids and file stems share one grammar, so a writer that can name
    /// its file can also sign its ops.
    #[test]
    fn writer_ids_and_file_names_agree() {
        assert!(is_valid_writer_id("desk-core"));
        assert!(!is_valid_writer_id(""));
        assert!(!is_valid_writer_id("-lead-hyphen"));
        assert!(!is_valid_writer_id("UPPER"));
        assert!(is_writer_file(&writer_file("phone-core")));
    }
}
