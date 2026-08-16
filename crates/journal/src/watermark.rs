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

//! Truncation detection — the Proton-revert defense (REWRITE.md §3.3).
//!
//! Single-writer-per-file makes Syncthing *conflicts* structurally impossible.
//! It does not make **damage** impossible, and the difference matters: a cloud
//! layer (Proton Drive mirrors the store on the PC) can revert a file to an
//! older, shorter version, and that revert propagates through Syncthing as an
//! ordinary modification. No conflict copy, still valid JSONL, silently missing
//! the newest ops. Nothing in the format notices — so this does.
//!
//! **The signal is a `max_ts` regression, not a size change.** Compaction
//! legitimately shrinks a file, sometimes drastically, but it can never lower
//! the highest timestamp in it (it always keeps the newest ops, §3.3). A revert
//! by definition deletes them. So:
//!
//! | observation | verdict |
//! |---|---|
//! | `max_ts` fell | **damage** — loud, points at Syncthing versioning |
//! | file vanished | **damage** — a writer's whole history is gone |
//! | bytes fell, `max_ts` held | ordinary compaction — say nothing |
//! | both grew | ordinary appends |
//!
//! Getting that table backwards would either cry wolf after every compaction or
//! stay silent through real data loss, so each row has a test.
//!
//! The marks live in the device's **local** data directory, never in the synced
//! tree — a high-water mark that syncs would be reverted along with the file it
//! is supposed to be checking.

use std::collections::BTreeMap;
use std::fmt;

use serde::{Deserialize, Serialize};

/// One file's high-water mark.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Mark {
    /// Highest `ts` ever observed in this file. Monotonic by construction.
    pub max_ts: i64,
    /// Size at the last observation. Informational: a shrink alone is not a
    /// signal, it is what compaction looks like.
    pub bytes: u64,
}

/// What a comparison against the previous run found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Damage {
    /// The newest ops are gone: the file's `max_ts` is lower than it was.
    Regressed {
        /// Writer file.
        file: String,
        /// What the previous run saw.
        was: i64,
        /// What this run sees.
        now: i64,
    },
    /// A file that existed last run is no longer there at all.
    Vanished {
        /// Writer file.
        file: String,
        /// The `max_ts` its history reached before disappearing.
        was: i64,
    },
}

impl fmt::Display for Damage {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Damage::Regressed { file, was, now } => write!(
                f,
                "{file} lost its newest ops (high-water ts {was} → {now}) — the file was reverted \
                 behind Syncthing's back; recover it from Syncthing file versioning"
            ),
            Damage::Vanished { file, was } => write!(
                f,
                "{file} has disappeared (high-water ts {was}) — that writer's entire history is \
                 missing; recover it from Syncthing file versioning"
            ),
        }
    }
}

/// Per-file high-water marks, persisted in the device's local data directory.
///
/// rust: `Serialize`/`Deserialize` are derived, but this type deliberately does
/// no file I/O — the caller owns where local state lives (a platform data dir,
/// which is a `ds` concern, not the journal's). Keeping I/O out is also what
/// makes every rule below testable without a filesystem.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct HighWater {
    /// Writer id → mark.
    pub files: BTreeMap<String, Mark>,
}

impl HighWater {
    /// Compare this run's observations against the recorded marks.
    ///
    /// Returns only genuine damage. A shrunken-but-current file is compaction
    /// and produces nothing.
    #[must_use]
    pub fn check(&self, current: &BTreeMap<String, Mark>) -> Vec<Damage> {
        let mut damage = Vec::new();
        for (file, previous) in &self.files {
            match current.get(file) {
                Some(now) if now.max_ts < previous.max_ts => damage.push(Damage::Regressed {
                    file: file.clone(),
                    was: previous.max_ts,
                    now: now.max_ts,
                }),
                Some(_) => {}
                None => {
                    damage.push(Damage::Vanished { file: file.clone(), was: previous.max_ts });
                }
            }
        }
        damage
    }

    /// Fold this run's observations in.
    ///
    /// `max_ts` only ever climbs — that is what makes it a *high-water* mark,
    /// and it is why a revert keeps being reported on every launch until the
    /// data is actually recovered. A one-shot warning about silent data loss is
    /// a warning the user will miss. [`accept`](Self::accept) is the deliberate
    /// way out.
    pub fn observe(&mut self, current: &BTreeMap<String, Mark>) {
        for (file, mark) in current {
            let entry = self.files.entry(file.clone()).or_insert(*mark);
            entry.max_ts = entry.max_ts.max(mark.max_ts);
            entry.bytes = mark.bytes;
        }
    }

    /// Give up on recovering a file and stop warning about it.
    ///
    /// For the case where versioning cannot restore the ops and the user has
    /// decided to live with the loss. Lowers the mark to what is actually
    /// there — the only place a mark is ever allowed to go down.
    pub fn accept(&mut self, file: &str, current: Mark) {
        self.files.insert(file.to_string(), current);
    }

    /// Serialize for the local state file.
    ///
    /// # Errors
    /// Only if the map contains something `serde_json` cannot represent, which
    /// the types here make impossible.
    pub fn to_json(&self) -> Result<String, serde_json::Error> {
        serde_json::to_string(self)
    }

    /// Parse from the local state file.
    ///
    /// # Errors
    /// If the file is not the JSON this type writes. A caller that cannot read
    /// its marks should start from empty rather than refuse to launch: losing
    /// the marks costs one run of detection, refusing to start costs the app.
    pub fn from_json(text: &str) -> Result<Self, serde_json::Error> {
        serde_json::from_str(text)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn marks(entries: &[(&str, i64, u64)]) -> BTreeMap<String, Mark> {
        entries
            .iter()
            .map(|(file, max_ts, bytes)| {
                ((*file).to_string(), Mark { max_ts: *max_ts, bytes: *bytes })
            })
            .collect()
    }

    /// Ordinary appends: both numbers grow, nothing to report.
    #[test]
    fn growth_is_not_damage() {
        let mut water = HighWater::default();
        water.observe(&marks(&[("desk-core", 100, 5_000)]));
        assert!(water.check(&marks(&[("desk-core", 250, 9_000)])).is_empty());
    }

    /// **The row that must not cry wolf.** Compaction can shrink a file by any
    /// amount — that is its job — and as long as the newest op is still there,
    /// it is not damage.
    #[test]
    fn compaction_shrinks_the_file_without_alarming() {
        let mut water = HighWater::default();
        water.observe(&marks(&[("desk-core", 900, 4_000_000)]));
        let after_compaction = marks(&[("desk-core", 900, 120_000)]);
        assert!(
            water.check(&after_compaction).is_empty(),
            "a 97% shrink at the same max_ts is compaction"
        );
    }

    /// **The row that must catch the revert.** A shorter file whose newest ops
    /// are gone is the Proton-revert signature: valid JSONL, no conflict copy,
    /// silently missing history.
    #[test]
    fn a_max_ts_regression_is_damage() {
        let mut water = HighWater::default();
        water.observe(&marks(&[("desk-core", 900, 200_000)]));
        let reverted = water.check(&marks(&[("desk-core", 400, 150_000)]));
        assert_eq!(
            reverted,
            vec![Damage::Regressed { file: "desk-core".into(), was: 900, now: 400 }]
        );
        assert!(
            reverted[0].to_string().contains("versioning"),
            "the report names the recovery path"
        );
    }

    /// A writer's file disappearing takes its entire contribution with it, so
    /// it is reported even though nothing regressed.
    #[test]
    fn a_vanished_file_is_damage() {
        let mut water = HighWater::default();
        water.observe(&marks(&[("desk-core", 900, 200_000), ("phone-core", 800, 100_000)]));
        let damage = water.check(&marks(&[("desk-core", 900, 200_000)]));
        assert_eq!(damage, vec![Damage::Vanished { file: "phone-core".into(), was: 800 }]);
    }

    /// A new writer (a device just set up) is not damage.
    #[test]
    fn a_new_writer_is_not_damage() {
        let mut water = HighWater::default();
        water.observe(&marks(&[("desk-core", 900, 200_000)]));
        assert!(water
            .check(&marks(&[("desk-core", 900, 200_000), ("phone-core", 5, 100)]))
            .is_empty());
    }

    /// The mark only climbs, so a revert keeps being reported until the data
    /// really comes back — silent data loss deserves a nag, not a one-shot.
    #[test]
    fn the_mark_never_falls_on_its_own() {
        let mut water = HighWater::default();
        water.observe(&marks(&[("desk-core", 900, 200_000)]));
        water.observe(&marks(&[("desk-core", 400, 150_000)]));
        assert_eq!(
            water.files["desk-core"].max_ts, 900,
            "observing damage does not lower the mark"
        );
        assert_eq!(water.files["desk-core"].bytes, 150_000, "but the size is current");
        assert!(!water.check(&marks(&[("desk-core", 400, 150_000)])).is_empty(), "still reported");
    }

    /// …and `accept` is the deliberate way to stop, for a loss that cannot be
    /// recovered.
    #[test]
    fn accept_is_the_only_way_a_mark_goes_down() {
        let mut water = HighWater::default();
        water.observe(&marks(&[("desk-core", 900, 200_000)]));
        water.accept("desk-core", Mark { max_ts: 400, bytes: 150_000 });
        assert!(water.check(&marks(&[("desk-core", 400, 150_000)])).is_empty());
    }

    /// Marks round-trip through the local state file.
    #[test]
    fn marks_round_trip_as_json() {
        let mut water = HighWater::default();
        water.observe(&marks(&[("desk-core", 900, 200_000), ("phone-core", 800, 100_000)]));
        let json = water.to_json().expect("serializes");
        assert_eq!(HighWater::from_json(&json).expect("parses"), water);
    }
}
