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

//! Typo-tolerant matching — one bounded-edit-distance primitive.
//!
//! A direct port of v2's `fuzz.py`, whose contract REWRITE.md §8 keeps
//! ("**Port** — small, well-specified in v2"). The rules, and why each exists:
//!
//! * **Exact matching always wins.** The fuzzy pass runs *only* when the exact
//!   pass came up empty, so a forgiving hit can never displace a precise one.
//!   Typing `pass` on a store containing "Passport" must not surface "Pass Book"
//!   above it — it must not surface it at all.
//! * **The budget scales with term length**: 0 edits for ≤ 4 characters, 1 for
//!   5–8, 2 for ≥ 9. A short query never fuzzes, so `cat` cannot drift to `car`.
//!   At three characters a one-edit neighbourhood is noise, not tolerance.
//! * **Distance is OSA** (restricted Damerau–Levenshtein): a transposition costs
//!   1, because phone-keyboard typos are dominated by swapped and dropped
//!   characters — which is the device this is for.
//! * **Every query term must match something.** Terms are `AND`ed, so adding a
//!   word always narrows.
//!
//! Nothing here is indexed. The store is ~1,000 documents and the whole scan is
//! well inside a frame (R0.2 measured 0.33 ms for filter-plus-repaint on the
//! phone), so an index would be complexity bought with nothing.

use unicode_normalization::UnicodeNormalization;

/// Casefold and strip diacritics: `résumé` → `resume`.
///
/// rust: `nfkd()` decomposes each character into base + combining marks, and the
/// filter drops the marks — `char::is_alphabetic` would keep them, because a
/// combining acute *is* a character. `to_lowercase` alone cannot do this, which
/// is the whole reason for the `unicode-normalization` dependency.
#[must_use]
pub fn fold(text: &str) -> String {
    text.nfkd().filter(|c| !is_combining(*c)).collect::<String>().to_lowercase()
}

/// Whether a character is a combining mark (Unicode categories Mn/Mc/Me).
///
/// Hand-rolled from the ranges NFKD actually produces rather than pulling in a
/// full character-category table: after decomposition the marks that appear in
/// document names are Latin, Greek, Cyrillic and Devanagari, all covered below.
fn is_combining(c: char) -> bool {
    matches!(c as u32,
        0x0300..=0x036F      // combining diacritical marks
        | 0x0483..=0x0489    // Cyrillic
        | 0x0591..=0x05BD    // Hebrew
        | 0x0610..=0x061A    // Arabic
        | 0x064B..=0x065F
        | 0x0670
        | 0x0900..=0x0903    // Devanagari
        | 0x093A..=0x094F
        | 0x0951..=0x0957
        | 0x1AB0..=0x1AFF    // extended
        | 0x1DC0..=0x1DFF
        | 0x20D0..=0x20F0    // symbols
        | 0xFE20..=0xFE2F)
}

/// Edit distance a query term of this length may forgive.
///
/// 0 for ≤ 4 characters, 1 for 5–8, 2 for ≥ 9. Counted in **characters**, not
/// bytes — `naïve` is five characters wherever it is stored.
#[must_use]
pub fn budget(term: &str) -> usize {
    match term.chars().count() {
        0..=4 => 0,
        5..=8 => 1,
        _ => 2,
    }
}

/// OSA edit distance between `a` and `b`, **capped** at `k`.
///
/// Returns the true distance when it is ≤ `k`, else `k + 1`. Callers only care
/// whether it fits the budget, and the cap is what lets the DP quit early:
/// a length difference greater than `k` is decided in O(1), and a whole DP row
/// exceeding `k` means the distance does too.
#[must_use]
pub fn distance(a: &str, b: &str, k: usize) -> usize {
    if a == b {
        return 0;
    }
    let a: Vec<char> = a.chars().collect();
    let b: Vec<char> = b.chars().collect();
    let (la, lb) = (a.len(), b.len());
    if la.abs_diff(lb) > k {
        return k + 1;
    }

    // rust: three rows kept by value rather than a full (la+1)×(lb+1) matrix.
    // OSA needs row i-2 for the transposition step and no further back, so the
    // whole DP costs O(lb) memory whatever the store's longest name is.
    let mut two_back: Vec<usize> = Vec::new();
    let mut prev: Vec<usize> = (0..=lb).collect();
    for i in 1..=la {
        let mut cur = vec![0usize; lb + 1];
        cur[0] = i;
        let mut row_min = cur[0];
        for j in 1..=lb {
            let cost = usize::from(a[i - 1] != b[j - 1]);
            let mut value = (cur[j - 1] + 1).min(prev[j] + 1).min(prev[j - 1] + cost);
            // The transposition: `ab` → `ba` costs one, not two.
            if i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1] {
                value = value.min(two_back[j - 2] + 1);
            }
            cur[j] = value;
            row_min = row_min.min(value);
        }
        if row_min > k {
            return k + 1;
        }
        two_back = std::mem::replace(&mut prev, cur);
    }
    prev[lb].min(k + 1)
}

/// The runs of alphanumerics in already-folded text.
fn words(folded: &str) -> impl Iterator<Item = &str> {
    folded.split(|c: char| !c.is_alphanumeric()).filter(|word| !word.is_empty())
}

/// Whether `term` is within its length budget of a word of `haystack`; both
/// already folded.
#[must_use]
pub fn term_matches(term: &str, haystack: &str) -> bool {
    let k = budget(term);
    words(haystack).any(|word| distance(term, word, k) <= k)
}

/// A query folded and split once, to match against many haystacks.
#[derive(Debug, Clone)]
pub struct Query {
    needle: String,
    terms: Vec<String>,
}

impl Query {
    /// Prepares `text` for matching.
    #[must_use]
    pub fn new(text: &str) -> Self {
        let needle = fold(text);
        let terms = words(&needle).map(str::to_string).collect();
        Self { needle, terms }
    }

    /// Whether some term is long enough to forgive an edit, so a fuzzy pass
    /// could find more than the exact one.
    #[must_use]
    pub fn can_fuzz(&self) -> bool {
        self.terms.iter().any(|term| budget(term) >= 1)
    }

    /// Whether folded `haystack` holds the query: as a substring, or with
    /// `fuzzy`, every term within its budget of one of its words.
    #[must_use]
    pub fn matches(&self, haystack: &str, fuzzy: bool) -> bool {
        if self.needle.is_empty() || haystack.contains(&self.needle) {
            return true;
        }
        fuzzy
            && !self.terms.is_empty()
            && self.terms.iter().all(|term| term_matches(term, haystack))
    }
}

/// The items whose folded text matches `query`: the exact matches, or the
/// fuzzy ones only when there are none and a term can forgive an edit.
pub fn two_pass<'a, T>(items: impl Iterator<Item = (T, &'a str)> + Clone, query: &Query) -> Vec<T> {
    let exact: Vec<T> = items
        .clone()
        .filter(|(_, text)| query.matches(text, false))
        .map(|(item, _)| item)
        .collect();
    if !exact.is_empty() || !query.can_fuzz() {
        return exact;
    }
    items.filter(|(_, text)| query.matches(text, true)).map(|(item, _)| item).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Folding is case- and accent-insensitive, so a document typed with
    /// diacritics is findable without them.
    #[test]
    fn folding_strips_case_and_accents() {
        assert_eq!(fold("Résumé"), "resume");
        assert_eq!(fold("COC Certificate"), "coc certificate");
        assert_eq!(fold("Ausweis für Führerschein"), "ausweis fur fuhrerschein");
    }

    /// **A short query never fuzzes.** At four characters or fewer a one-edit
    /// neighbourhood is noise: `cat` would reach `car`, `cab`, `bat` and `can`.
    #[test]
    fn short_terms_have_no_budget() {
        assert_eq!(budget("cat"), 0);
        assert_eq!(budget("pass"), 0);
        assert_eq!(budget("passp"), 1);
        assert_eq!(budget("passport"), 1);
        assert_eq!(budget("certificate"), 2);
        assert!(!Query::new("coc").can_fuzz());
        assert!(Query::new("passport").can_fuzz());
    }

    /// A transposition costs one edit — the typo a thumb actually makes.
    #[test]
    fn a_transposition_costs_one() {
        assert_eq!(distance("passport", "passprot", 2), 1);
        assert_eq!(distance("medical", "mediacl", 2), 1);
    }

    /// The cap is honoured, and a length difference is decided without the DP.
    #[test]
    fn distance_is_capped() {
        assert_eq!(distance("a", "abcdefgh", 2), 3, "capped at k+1, not the true 7");
        assert_eq!(distance("", "", 0), 0);
        assert_eq!(distance("same", "same", 0), 0);
    }

    /// Real typos land inside their budget; unrelated words do not.
    #[test]
    fn typos_match_and_different_words_do_not() {
        let hay = fold("Passport (IN) — identity travel");
        assert!(term_matches("passprot", &hay), "transposed");
        assert!(term_matches("pasport", &hay), "dropped letter");
        assert!(!term_matches("password", &hay), "a different word entirely");
    }

    /// Exact substring matching is the fast path and needs no fuzzy pass.
    #[test]
    fn exact_substrings_match_without_fuzzing() {
        let hay = fold("COC Certificate (Master) — marine");
        assert!(Query::new("coc").matches(&hay, false));
        assert!(Query::new("master").matches(&hay, false));
        assert!(Query::new("MARINE").matches(&hay, false), "case-insensitive");
        assert!(!Query::new("eng-1").matches(&hay, false));
    }

    /// **Every term must match.** Adding a word narrows, never widens — the
    /// property that makes typing more feel like progress.
    #[test]
    fn terms_are_anded() {
        let hay = fold("COC Certificate (Master) — marine");
        assert!(Query::new("certificate marine").matches(&hay, true));
        assert!(!Query::new("certificate motorcycle").matches(&hay, true));
    }

    /// An empty query matches everything — the unfiltered list.
    #[test]
    fn an_empty_query_matches_everything() {
        assert!(Query::new("").matches(&fold("anything at all"), false));
        assert!(Query::new("").matches("", false));
    }

    /// The fuzzy pass is genuinely more forgiving than the exact one, and only
    /// where the budget allows it.
    #[test]
    fn the_fuzzy_pass_forgives_what_the_exact_one_does_not() {
        let hay = fold("ENG-1 Medical Certificate");
        assert!(!Query::new("medicla").matches(&hay, false), "exact pass misses the typo");
        assert!(Query::new("medicla").matches(&hay, true), "fuzzy pass catches it");
        assert!(!Query::new("xyzq").matches(&hay, true), "but not nonsense");
    }
}
