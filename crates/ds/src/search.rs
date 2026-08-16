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

/// Whether `term` is within its length-budget of any token.
///
/// Both sides are expected to be [`fold`]ed already — the hot path folds each
/// side once, not once per comparison.
#[must_use]
pub fn term_matches(term: &str, tokens: &[String]) -> bool {
    let k = budget(term);
    tokens.iter().any(|token| distance(term, token, k) <= k)
}

/// Split text into search tokens: runs of alphanumerics, folded.
#[must_use]
pub fn tokens(text: &str) -> Vec<String> {
    fold(text)
        .split(|c: char| !c.is_alphanumeric())
        .filter(|t| !t.is_empty())
        .map(str::to_string)
        .collect()
}

/// Whether `haystack` matches `query`, exactly or (when allowed) fuzzily.
///
/// `haystack` must be pre-[`fold`]ed; `query` is folded here because it changes
/// on every keystroke while the haystacks do not.
#[must_use]
pub fn matches(haystack: &str, query: &str, fuzzy: bool) -> bool {
    let needle = fold(query);
    if needle.is_empty() || haystack.contains(&needle) {
        return true;
    }
    if !fuzzy {
        return false;
    }
    let terms = tokens(query);
    if terms.is_empty() {
        return false;
    }
    let haystack_tokens = tokens(haystack);
    terms.iter().all(|term| term_matches(term, &haystack_tokens))
}

/// Whether a query is even eligible for the fuzzy pass.
///
/// If no term is long enough to forgive an edit, the fuzzy pass would return
/// exactly what the exact pass did, so it is skipped — that is the check that
/// keeps a two-character query from ever fuzzing.
#[must_use]
pub fn can_fuzz(query: &str) -> bool {
    tokens(query).iter().any(|term| budget(term) >= 1)
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
        assert!(!can_fuzz("coc"));
        assert!(can_fuzz("passport"));
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
        let tokens = tokens("Passport (IN) — identity travel");
        assert!(term_matches("passprot", &tokens), "transposed");
        assert!(term_matches("pasport", &tokens), "dropped letter");
        assert!(!term_matches("password", &tokens), "a different word entirely");
    }

    /// Exact substring matching is the fast path and needs no fuzzy pass.
    #[test]
    fn exact_substrings_match_without_fuzzing() {
        let hay = fold("COC Certificate (Master) — marine");
        assert!(matches(&hay, "coc", false));
        assert!(matches(&hay, "master", false));
        assert!(matches(&hay, "MARINE", false), "case-insensitive");
        assert!(!matches(&hay, "eng-1", false));
    }

    /// **Every term must match.** Adding a word narrows, never widens — the
    /// property that makes typing more feel like progress.
    #[test]
    fn terms_are_anded() {
        let hay = fold("COC Certificate (Master) — marine");
        assert!(matches(&hay, "certificate marine", true));
        assert!(!matches(&hay, "certificate motorcycle", true));
    }

    /// An empty query matches everything — the unfiltered list.
    #[test]
    fn an_empty_query_matches_everything() {
        assert!(matches(&fold("anything at all"), "", false));
        assert!(matches("", "", false));
    }

    /// The fuzzy pass is genuinely more forgiving than the exact one, and only
    /// where the budget allows it.
    #[test]
    fn the_fuzzy_pass_forgives_what_the_exact_one_does_not() {
        let hay = fold("ENG-1 Medical Certificate");
        assert!(!matches(&hay, "medicla", false), "exact pass misses the typo");
        assert!(matches(&hay, "medicla", true), "fuzzy pass catches it");
        assert!(!matches(&hay, "xyzq", true), "but not nonsense");
    }
}
