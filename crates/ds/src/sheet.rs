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

//! The leader sheet: what `Space` opens, and what it contains.
//!
//! Find binds no letter keys — every printable is search text — so a growing
//! verb set has nowhere to go. Spacemacs, Magit and Neovim all solved this on
//! the same constraint, and this is their answer applied here:
//!
//! 1. **One prefix.** A query never usefully begins with a space, so `Space` on
//!    an *empty* query is free while mid-query it still types a space. That is
//!    the normal-vs-insert split without modes: the query is the mode.
//! 2. **A panel that teaches** (which-key). Press the leader and read what the
//!    next key can be. New verbs then cost nothing in memorised keys.
//! 3. **Toggles as checkboxes** (magit's *infixes*). A filter is state, not a
//!    verb, and a checkbox is the only shape that shows **off** as well as on —
//!    which the status chips could never do, because a chip does not exist until
//!    its filter is already on.
//! 4. **Type and it becomes a picker** (`M-x`, Telescope). You either know the
//!    chord or you know the word.
//!
//! Nothing here is advertised before it works: the sheet lists what this build
//! can actually do, and grows as the surfaces behind it land.

use crate::app::{Filter, Model, ScanSearch};

/// What pressing an item's key does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Act {
    /// Descend into a group — `SPC f` and then the group's own keys.
    Enter(char),
    /// Flip the expiring filter (`ctrl+x`).
    Expiring,
    /// Flip searching inside scan text (`ctrl+t`).
    Scans,
    /// Drop every filter at once.
    Clear,
    /// Edit the record row the selector is on.
    Edit,
    /// Start a new document by asking for its name.
    New,
    /// Put the last write this session made back the way it was.
    Undo,
    /// Put back the last write that was taken back.
    Redo,
    /// Tombstone the record's document.
    Delete,
    /// Leave.
    Quit,
}

/// One row of the sheet.
#[derive(Debug, Clone, Copy)]
pub struct Item {
    /// The key that runs it, and the letter the mnemonic hangs off.
    pub key: char,
    /// What it is called — also what the picker matches against.
    pub label: &'static str,
    pub act: Act,
    /// `Some` when the item is a toggle: drawn as a checkbox rather than a key,
    /// because state needs somewhere to be visible in both directions.
    pub on: Option<bool>,
    /// The keyboard accelerator, taught beside the item that fires it. The sheet
    /// is where a user graduates from reading to typing.
    pub accel: &'static str,
}

const fn item(key: char, label: &'static str, act: Act) -> Item {
    Item { key, label, act, on: None, accel: "" }
}

/// The sheet's contents for a group — `None` is the top level.
///
/// Reads the model, because a toggle has to draw its own state.
#[must_use]
pub fn items(group: Option<char>, model: &Model) -> Vec<Item> {
    match group {
        // The top level is contextual: an open record adds the verbs that act
        // on it. This is the whole reason the record needs no control keys —
        // `e` is a bare letter *and* it can be read off the sheet, which a
        // `ctrl+`combination never can (Termux latches CTRL in its own UI, so
        // the app never sees a moment between the modifier and the letter).
        None if model.detail => vec![
            Item { accel: "e", ..item('e', "edit this row", Act::Edit) },
            // Offered here as well as on the list. Creating a document is not a
            // thing about the record you happen to be reading, and making the
            // user peel back to a surface that admits it would teach that it is.
            item('n', "new document", Act::New),
            Item { accel: "u", ..item('u', "undo last change", Act::Undo) },
            Item { accel: "r", ..item('r', "redo", Act::Redo) },
            // Record-only, and it is the record's document it deletes — you
            // should be able to see the thing you are removing.
            Item { accel: "d d", ..item('d', "delete this document", Act::Delete) },
            item('q', "quit", Act::Quit),
        ],
        None => vec![
            item('f', "filter", Act::Enter('f')),
            item('n', "new document", Act::New),
            item('u', "undo last change", Act::Undo),
            item('r', "redo", Act::Redo),
            item('q', "quit", Act::Quit),
        ],
        Some('f') => vec![
            Item {
                on: Some(model.filter == Filter::Expiring),
                accel: "^x",
                ..item('x', "expiring only", Act::Expiring)
            },
            Item {
                on: Some(model.scan_search != ScanSearch::Off),
                accel: "^t",
                ..item('t', "search scan text", Act::Scans)
            },
            item('c', "clear filters", Act::Clear),
        ],
        Some(_) => Vec::new(),
    }
}

/// The breadcrumb: `SPC`, then the group, then whatever is being typed.
#[must_use]
pub fn crumb(group: Option<char>, filter: &str) -> String {
    let mut out = String::from("SPC");
    if let Some(key) = group {
        out.push(' ');
        out.push(key);
    }
    if !filter.is_empty() {
        out.push(' ');
        out.push_str(filter);
    }
    out
}

/// The items a typed filter leaves, matched on the label.
///
/// Deliberately a plain substring rather than the fuzzy matcher the document
/// list uses: a command list is a dozen short labels, and fuzzy matching that
/// few strings surfaces coincidences more often than it saves keystrokes.
#[must_use]
pub fn matching(items: &[Item], filter: &str) -> Vec<Item> {
    if filter.is_empty() {
        return items.to_vec();
    }
    let needle = crate::search::fold(filter);
    items.iter().filter(|item| crate::search::fold(item.label).contains(&needle)).copied().collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// **A toggle shows both states.** This is the whole reason the filters live
    /// in the sheet rather than as pressable status chips: a chip is rendered
    /// only when its filter is on, so it can turn one off and never on.
    #[test]
    fn a_toggle_draws_its_off_state_too() {
        let mut model = crate::app::tests::model();
        let off = items(Some('f'), &model);
        assert_eq!(off[0].on, Some(false), "expiring is off and still listed");

        model.filter = Filter::Expiring;
        let on = items(Some('f'), &model);
        assert_eq!(on[0].on, Some(true));
        assert_eq!(off.len(), on.len(), "the sheet does not change shape when a filter flips");
    }

    /// Nothing is advertised that does not work: an unknown group is empty
    /// rather than a promise.
    #[test]
    fn an_unknown_group_is_empty() {
        let model = crate::app::tests::model();
        assert!(items(Some('z'), &model).is_empty());
    }

    #[test]
    fn typing_narrows_the_sheet_and_the_crumb_says_so() {
        let model = crate::app::tests::model();
        let all = items(Some('f'), &model);
        let hits = matching(&all, "scan");
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].act, Act::Scans);
        assert_eq!(crumb(Some('f'), "scan"), "SPC f scan");
        assert_eq!(crumb(None, ""), "SPC");
    }

    /// Folding is shared with the document search, so accents behave the same
    /// way in both — one matcher, one set of surprises.
    #[test]
    fn matching_folds_like_the_document_search_does() {
        let model = crate::app::tests::model();
        let all = items(Some('f'), &model);
        assert_eq!(matching(&all, "EXPIRING").len(), 1);
    }
}
