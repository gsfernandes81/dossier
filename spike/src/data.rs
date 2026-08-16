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

//! The synthetic store: 1,000 documents shaped like the real one (~948 docs).
//!
//! The spike deliberately does **not** read a journal — R1 hasn't been built and
//! the point of R0.2 is the *paint*, not the parse. What matters is that the row
//! data has the real shape (long names that must truncate, right-aligned
//! location/slot/expiry columns, status markers) and the real cardinality, so
//! the measured frame time is the one R3 will inherit.
//!
//! Key design decision: generation is **deterministic** — a seeded generator, no
//! `rand` dependency, no clock. Two runs on two devices produce byte-identical
//! rows, so a phone screenshot and a desktop screenshot are directly comparable.

/// The fixed "today" every expiry status is computed against.
///
/// A constant, not `now()`: the spike must render identically forever, and ISO
/// dates compare correctly as plain strings, so no date library is needed.
pub const TODAY: &str = "2026-08-16";

/// Nine months out — v2's "turns red within 9 months" rule (DESIGN §14).
const SOON: &str = "2027-05-16";

/// One row of the Find list: a document as the browse surface needs it.
///
/// rust: the fields are owned `String`s rather than borrowed `&str`. Borrowing
/// would tie every `Doc` to the lifetime of whatever produced it, which buys
/// nothing here — the store is built once and lives for the whole process — and
/// costs a lifetime parameter on every function that touches a document. Owned
/// data at API boundaries, borrowed data inside hot loops, is the rule this
/// codebase follows.
pub struct Doc {
    /// Display name — the search target and the left column.
    pub name: String,
    /// Lower-cased name, precomputed once so filtering never allocates.
    ///
    /// This is the spike's one performance trick, and it is the same one R3
    /// will need: search runs on every keystroke over 1,000 rows, so the
    /// per-keystroke work must be a scan, not 1,000 fresh allocations.
    pub haystack: String,
    /// Physical location slug (`cert-file`, `file-4096`, …) or `softcopy`.
    pub location: String,
    /// Slot within the location; `None` for digital-only documents.
    pub slot: Option<u16>,
    /// ISO expiry date, if the document expires at all.
    pub expiry: Option<String>,
    /// Flat tags (REWRITE.md §8: hierarchical tags are dropped).
    pub tags: Vec<String>,
    /// Whether a real file is linked — drives the `Enter` verb's fallthrough.
    pub has_file: bool,
}

/// How a row's expiry reads at a glance.
///
/// rust: an enum, not a string or a bool pair. The renderer `match`es on it and
/// the compiler guarantees every variant gets a marker — the kind of mistake
/// (a status with no glyph) that a stringly-typed version ships silently.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Status {
    /// Past `TODAY` and not superseded.
    Expired,
    /// Within nine months.
    Soon,
    /// Expires, but not yet worth attention.
    Ok,
    /// No expiry at all — most documents.
    None,
}

impl Status {
    /// The ASCII marker. ASCII-first is a hard requirement (REWRITE.md §4.5.5):
    /// the phone's font support is not guaranteed, and colour alone must never
    /// carry a signal.
    pub fn marker(self) -> &'static str {
        match self {
            Status::Expired => "!",
            Status::Soon => "~",
            Status::Ok => " ",
            Status::None => "·",
        }
    }
}

impl Doc {
    /// Classify the row against the fixed `TODAY`.
    pub fn status(&self) -> Status {
        // rust: `match` on an `Option<&String>` with a guard — `as_deref()`
        // turns `Option<String>` into `Option<&str>` so the comparison is
        // against string slices and nothing is cloned.
        match self.expiry.as_deref() {
            None => Status::None,
            Some(d) if d < TODAY => Status::Expired,
            Some(d) if d < SOON => Status::Soon,
            Some(_) => Status::Ok,
        }
    }

    /// `cert-file 8` / `softcopy` — the dim right-hand column.
    pub fn place(&self) -> String {
        match self.slot {
            Some(n) => format!("{} {n}", self.location),
            None => self.location.clone(),
        }
    }
}

/// A tiny deterministic PRNG (`SplitMix64`).
///
/// Six lines beats a dependency for "vary the synthetic data reproducibly", and
/// unlike hashing the index it produces well-distributed values from a counter.
struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform-enough index into a slice of length `n`.
    fn pick(&mut self, n: usize) -> usize {
        (self.next() % n as u64) as usize
    }
}

const KINDS: &[&str] = &[
    "Passport",
    "Seaman Book",
    "COC Certificate",
    "ENG-1 Medical",
    "Sea Service Testimonial",
    "STCW Basic Safety Training",
    "Advanced Fire Fighting",
    "Medical First Aid",
    "Ship Security Awareness",
    "Yellow Fever Card",
    "Motorcycle Insurance",
    "Motorcycle Registration",
    "Driving Licence",
    "PAN Card",
    "Aadhaar Card",
    "Birth Certificate",
    "Degree Certificate",
    "Bank Statement",
    "Rental Agreement",
    "Visa Approval Letter",
];

const QUALIFIERS: &[&str] = &[
    "(IN)",
    "(UK)",
    "(Panama)",
    "Renewal",
    "Original",
    "Copy",
    "Scan",
    "2019",
    "2021",
    "2023",
    "2025",
    "— Endorsement",
    "Duplicate",
    "Provisional",
    "",
];

const LOCATIONS: &[&str] =
    &["cert-file", "file-4096", "blue-folder", "passport-pouch", "desk-drawer", "softcopy"];

const TAGS: &[&str] = &["marine", "motorcycle", "identity", "financial", "education", "travel"];

/// A few rows with wide (CJK) and emoji characters.
///
/// Not decoration: these are the rows that expose a `len()`-based truncation or
/// a naive right-alignment. If the columns stay straight with these on screen,
/// the width handling is correct.
const WIDE_NAMES: &[&str] = &[
    "海事証明書 — Marine Certificate",
    "Ausweis für Führerschein (Übersetzung)",
    "🛳 Ship Security Awareness — 船舶保安",
    "Свидетельство о рождении",
    "PAN — पैन कार्ड",
];

/// Build `n` synthetic documents, sorted the way the Find list orders them.
///
/// Default order is location → slot → name (REWRITE-UI.md §1): physical shelf
/// order, with explicit tiebreakers so the list never jitters between runs.
pub fn synth(n: usize) -> Vec<Doc> {
    let mut rng = Rng(0x0D05_51E5);
    let mut docs = Vec::with_capacity(n);
    for i in 0..n {
        // Every 97th row is a width torture test; the rest look like the store.
        let name = if i % 97 == 3 {
            WIDE_NAMES[rng.pick(WIDE_NAMES.len())].to_string()
        } else {
            let qualifier = QUALIFIERS[rng.pick(QUALIFIERS.len())];
            let kind = KINDS[rng.pick(KINDS.len())];
            if qualifier.is_empty() {
                format!("{kind} {:04}", i + 1)
            } else {
                format!("{kind} {qualifier} {:04}", i + 1)
            }
        };
        let location = LOCATIONS[rng.pick(LOCATIONS.len())].to_string();
        let slot = (location != "softcopy").then(|| (rng.pick(48) + 1) as u16);
        // Roughly a third of the store expires — matching the real ratio
        // matters, because the header's attention counts drive the layout.
        let expiry = (rng.pick(3) == 0).then(|| {
            let year = 2024 + rng.pick(5);
            let month = rng.pick(12) + 1;
            let day = rng.pick(28) + 1;
            format!("{year:04}-{month:02}-{day:02}")
        });
        let mut tags = Vec::new();
        for _ in 0..rng.pick(3) {
            let tag = TAGS[rng.pick(TAGS.len())].to_string();
            if !tags.contains(&tag) {
                tags.push(tag);
            }
        }
        docs.push(Doc {
            haystack: name.to_lowercase(),
            name,
            location,
            slot,
            expiry,
            tags,
            has_file: rng.pick(10) != 0,
        });
    }
    docs.sort_by(|a, b| {
        a.location.cmp(&b.location).then(a.slot.cmp(&b.slot)).then(a.name.cmp(&b.name))
    });
    docs
}

/// Rows matching `query`, as indices into `docs`.
///
/// Exact-substring only — v2's bounded-OSA fuzzy pass is *not* part of this
/// spike (REWRITE.md §8 ports it in R3). What the spike needs from search is the
/// per-keystroke cost of touching 1,000 rows and re-rendering, which a substring
/// scan measures honestly; a fuzzy pass would only make the number bigger.
pub fn filter(docs: &[Doc], query: &str) -> Vec<usize> {
    if query.is_empty() {
        return (0..docs.len()).collect();
    }
    let needle = query.to_lowercase();
    // rust: `iter().enumerate().filter_map(...)` instead of an index loop —
    // the closure returns `Option<usize>` and `filter_map` keeps the `Some`s.
    docs.iter().enumerate().filter_map(|(i, d)| d.haystack.contains(&needle).then_some(i)).collect()
}

/// How many rows would show an expiry warning — the header's attention count.
pub fn expiring(docs: &[Doc]) -> usize {
    docs.iter().filter(|d| matches!(d.status(), Status::Expired | Status::Soon)).count()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The synthetic store is reproducible: same input, same rows, forever.
    /// Screenshots from two devices are only comparable if this holds.
    #[test]
    fn generation_is_deterministic() {
        let a = synth(200);
        let b = synth(200);
        assert_eq!(a.len(), b.len());
        assert!(a.iter().zip(&b).all(|(x, y)| x.name == y.name && x.place() == y.place()));
    }

    /// Rows arrive in shelf order with explicit tiebreakers, so the list never
    /// jitters between frames or runs (REWRITE.md §4.5.5).
    #[test]
    fn rows_are_sorted_by_location_then_slot_then_name() {
        let docs = synth(500);
        for pair in docs.windows(2) {
            let (a, b) = (&pair[0], &pair[1]);
            let key = |d: &Doc| (d.location.clone(), d.slot, d.name.clone());
            assert!(key(a) <= key(b), "out of order: {} then {}", a.name, b.name);
        }
    }

    /// Status is a pure function of the fixed TODAY, and every variant has a
    /// non-empty ASCII marker (colour is never the only signal).
    #[test]
    fn status_classifies_against_the_fixed_today() {
        let expired = Doc {
            name: "x".into(),
            haystack: "x".into(),
            location: "softcopy".into(),
            slot: None,
            expiry: Some("2020-01-01".into()),
            tags: vec![],
            has_file: true,
        };
        assert!(matches!(expired.status(), Status::Expired));
        for s in [Status::Expired, Status::Soon, Status::Ok, Status::None] {
            assert_eq!(s.marker().chars().count(), 1);
        }
    }

    /// Filtering is case-insensitive and empty-query means "everything".
    #[test]
    fn filter_is_case_insensitive_and_total_when_empty() {
        let docs = synth(300);
        assert_eq!(filter(&docs, "").len(), 300);
        let lower = filter(&docs, "passport").len();
        let upper = filter(&docs, "PASSPORT").len();
        assert_eq!(lower, upper);
    }
}
