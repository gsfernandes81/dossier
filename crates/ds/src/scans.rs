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

//! Searching what the documents *say* — `ctrl+t`.
//!
//! The desktop satellite reads scans and writes what it found into the journal's
//! **`enrich`** namespace: a transcript, keywords, an issuer, a document number.
//! That text is often the only way to find a document whose name you have
//! forgotten — "the certificate with 4096 on it" — and it is also bulky, which
//! is exactly why §3.1 puts it in a second namespace.
//!
//! So it is **loaded lazily, on a worker thread, the first time `ctrl+t` is
//! pressed** (invariant 7: nothing blocks the render loop). Until then the app
//! has not paid a byte for it, which is what keeps the phone's cold start in
//! single-digit milliseconds.
//!
//! Matching here is **exact substring only**, deliberately. A transcript is
//! hundreds of words; letting a two-edit budget loose on it would match almost
//! anything, and a search that always matches is a search nobody trusts.

use std::collections::BTreeMap;

use journal::{Journal, Namespace};
use serde_json::Value;

/// The scan text, keyed by the file path it was read from.
///
/// Paths are POSIX and relative to the Syncthing root, the same as everywhere
/// else in the data model — which is what lets a document's `files` entry be
/// looked up here directly.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Scans {
    /// Folded, searchable text per file path.
    pub by_path: BTreeMap<String, String>,
}

/// The `enrich` entity kinds that carry text worth searching. Proposals are
/// included: an intake proposal is a reading of a file nobody has filed yet, and
/// "where did that scan go" is a question worth answering.
const TEXT_KINDS: [&str; 2] = ["reading", "proposal"];

/// The fields of a reading that are text a person might search for.
///
/// `evidence` and `transcript` are the bulk; the rest are the fields someone
/// actually remembers — an issuer, a name, a number on the page.
const TEXT_FIELDS: [&str; 8] = [
    "transcript",
    "evidence",
    "issuer",
    "holder_name",
    "document_number",
    "document_type",
    "issue_date_text",
    "expiry_date_text",
];

impl Scans {
    /// Read and fold the `enrich` namespace.
    ///
    /// # Errors
    /// Only when the directory exists but cannot be listed. A device with no
    /// `enrich` directory — every device before the satellite has run once — is
    /// an empty result, not a failure.
    pub fn load(journal: &Journal) -> Result<Self, journal::store::Error> {
        let load = journal.load(Namespace::Enrich)?;
        Ok(Self::from_fold(&journal::fold(&load.lines)))
    }

    /// Build from an already-folded journal.
    #[must_use]
    pub fn from_fold(fold: &journal::Fold) -> Self {
        let mut by_path = BTreeMap::new();
        for ((kind, path), value) in &fold.enrich {
            if !TEXT_KINDS.contains(&kind.as_str()) {
                continue;
            }
            let text = text_of(value);
            if text.is_empty() {
                continue;
            }
            // One path can carry both a reading and a proposal. Join rather than
            // overwrite: whichever the satellite wrote last, the words in the
            // other one are still words on that page.
            by_path
                .entry(path.clone())
                .and_modify(|existing: &mut String| {
                    existing.push(' ');
                    existing.push_str(&text);
                })
                .or_insert(text);
        }
        Self { by_path }
    }

    /// Whether any of these file paths has scan text containing `needle`.
    ///
    /// `needle` must already be [`crate::search::fold`]ed — it is folded once
    /// per keystroke by the caller, not once per document.
    #[must_use]
    pub fn any_matches(&self, paths: impl IntoIterator<Item = String>, needle: &str) -> bool {
        if needle.is_empty() {
            return false;
        }
        paths
            .into_iter()
            .any(|path| self.by_path.get(&path).is_some_and(|text| text.contains(needle)))
    }

    /// How many files have text — the number the chip shows.
    #[must_use]
    pub fn len(&self) -> usize {
        self.by_path.len()
    }

    /// Whether the satellite has read anything at all.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.by_path.is_empty()
    }
}

/// All the searchable text in one reading, folded once at load time.
fn text_of(value: &Value) -> String {
    let mut parts: Vec<String> = Vec::new();
    for field in TEXT_FIELDS {
        if let Some(text) = value.get(field).and_then(Value::as_str) {
            parts.push(text.to_string());
        }
    }
    if let Some(keywords) = value.get("keywords").and_then(Value::as_array) {
        parts.extend(keywords.iter().filter_map(Value::as_str).map(str::to_string));
    }
    crate::search::fold(&parts.join(" "))
}

#[cfg(test)]
mod tests {
    use super::*;
    use journal::{fold, parse_line, Line};

    fn enrich(kind: &str, path: &str, payload: &Value) -> Line {
        let op = serde_json::json!({
            "v": 1, "ts": 1_700_000_000_000i64, "w": "desk-lab",
            "op": kind, "ent": kind, "id": path, "val": payload,
        });
        parse_line(&serde_json::to_string(&op).unwrap())
    }

    /// Every field a person might remember is searchable, and folding happens
    /// once at load — not once per keystroke.
    #[test]
    fn a_reading_becomes_one_folded_haystack() {
        let lines = vec![enrich(
            "reading",
            "Marine/coc.pdf",
            &serde_json::json!({
                "transcript": "CERTIFICATE OF COMPETENCY — Master Mariner",
                "issuer": "Directorate General of Shipping",
                "document_number": "MUM-4096",
                "keywords": ["STCW", "Réglementation"],
                "confidence_permille": 920,
            }),
        )];
        let scans = Scans::from_fold(&fold(&lines));
        let text = &scans.by_path["Marine/coc.pdf"];
        assert!(text.contains("competency"), "case-folded: {text}");
        assert!(text.contains("mum-4096"));
        assert!(text.contains("reglementation"), "accents folded too: {text}");
        assert!(!text.contains("920"), "a confidence is not searchable text");
    }

    /// A path with both a reading and a proposal keeps the words from both.
    #[test]
    fn a_reading_and_a_proposal_for_one_path_are_joined() {
        let lines = vec![
            enrich("reading", "Inbox/scan.jpg", &serde_json::json!({"transcript": "alpha"})),
            enrich("proposal", "Inbox/scan.jpg", &serde_json::json!({"transcript": "bravo"})),
        ];
        let scans = Scans::from_fold(&fold(&lines));
        let text = &scans.by_path["Inbox/scan.jpg"];
        assert!(text.contains("alpha") && text.contains("bravo"), "{text}");
    }

    /// Matching is by file path, which is how a document reaches its scans.
    #[test]
    fn matching_is_by_the_documents_own_file_paths() {
        let lines = vec![enrich(
            "reading",
            "Marine/coc.pdf",
            &serde_json::json!({"transcript": "Master Mariner"}),
        )];
        let scans = Scans::from_fold(&fold(&lines));
        assert!(scans.any_matches(["Marine/coc.pdf".to_string()], "mariner"));
        assert!(!scans.any_matches(["Marine/other.pdf".to_string()], "mariner"));
        assert!(
            !scans.any_matches(["Marine/coc.pdf".to_string()], ""),
            "an empty query matches nothing here"
        );
    }

    /// Entries that are not readings — dedup clusters, anything a later phase
    /// adds — are ignored rather than searched as JSON.
    #[test]
    fn only_text_bearing_kinds_are_indexed() {
        let lines = vec![enrich(
            "cluster",
            "Inbox/a.jpg",
            &serde_json::json!({"transcript": "should not be searchable"}),
        )];
        assert!(Scans::from_fold(&fold(&lines)).is_empty());
    }
}
