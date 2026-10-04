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

//! The Space sheet: what `Space` opens, and the verbs it lists.
//!
//! Its letters run verbs and nothing searches it; finding a command by name is
//! the command line's job. Which letters it lists follows what is on screen.
//! The rules are in REWRITE-UI.md.

use crate::app::Model;

/// What pressing an item's key does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Act {
    /// Open the filter checklist.
    Filter,
    /// Edit the record row the selector is on.
    Edit,
    /// Put the last write this session made back the way it was.
    Undo,
    /// Put back the last write that was taken back.
    Redo,
    /// Tombstone the record's document.
    Delete,
    /// Choose where the record's hard copy is filed.
    Location,
    /// Make a newer version of the record's document.
    NewVersion,
    /// List every version of the record's document.
    Versions,
    /// Open the Bundles view.
    Bundles,
    /// Rename the selected physical location.
    Rename,
    /// Move the selected physical location into another.
    Move,
    /// Delete the selected physical location and everything inside it.
    Remove,
    /// Leave.
    Quit,
}

/// One row of the sheet.
#[derive(Debug, Clone, Copy)]
pub struct Item {
    /// The key that runs it, and the letter the mnemonic hangs off.
    pub key: char,
    /// What it is called.
    pub label: &'static str,
    /// What it does.
    pub act: Act,
    /// The keyboard accelerator, shown beside the item.
    pub accel: &'static str,
}

/// The key of an item reached only by its accelerator: redo beside rename,
/// where `r` is taken.
pub const NO_KEY: char = '\0';

const fn item(key: char, label: &'static str, act: Act) -> Item {
    Item { key, label, act, accel: "" }
}

/// The sheet's verbs for what is on screen.
#[must_use]
pub fn items(model: &Model) -> Vec<Item> {
    if matches!(model.views.last(), Some(crate::app::View::Bundle { .. })) {
        vec![
            Item { accel: "e", ..item('e', "edit this row", Act::Edit) },
            Item { accel: "u ^z", ..item('u', "undo last change", Act::Undo) },
            Item { accel: "r ^y", ..item('r', "redo", Act::Redo) },
            Item { accel: "d d", ..item('d', "delete this bundle", Act::Delete) },
            item('q', "quit", Act::Quit),
        ]
    } else if matches!(
        model.views.last(),
        Some(crate::app::View::Versions { .. } | crate::app::View::Bundles { .. })
    ) {
        vec![
            Item { accel: "u ^z", ..item('u', "undo last change", Act::Undo) },
            Item { accel: "r ^y", ..item('r', "redo", Act::Redo) },
            item('q', "quit", Act::Quit),
        ]
    } else if model.locpick.is_some() {
        vec![
            item('r', "rename", Act::Rename),
            item('m', "move…", Act::Move),
            item('d', "delete", Act::Remove),
            Item { accel: "^z", ..item('u', "undo last change", Act::Undo) },
            Item { accel: "^y", ..item(NO_KEY, "redo", Act::Redo) },
            item('q', "quit", Act::Quit),
        ]
    } else if model.detail() {
        vec![
            Item { accel: "e", ..item('e', "edit this row", Act::Edit) },
            item('n', "new version", Act::NewVersion),
            item('l', "location", Act::Location),
            item('v', "versions", Act::Versions),
            item('b', "bundles", Act::Bundles),
            Item { accel: "u ^z", ..item('u', "undo last change", Act::Undo) },
            Item { accel: "r ^y", ..item('r', "redo", Act::Redo) },
            Item { accel: "d d", ..item('d', "delete this document", Act::Delete) },
            item('q', "quit", Act::Quit),
        ]
    } else {
        vec![
            item('f', "filter", Act::Filter),
            item('b', "bundles", Act::Bundles),
            Item { accel: "^z", ..item('u', "undo last change", Act::Undo) },
            Item { accel: "^y", ..item('r', "redo", Act::Redo) },
            item('q', "quit", Act::Quit),
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Each view lists its own verbs: the Find view filters, the Details view
    /// edits.
    #[test]
    fn the_verbs_follow_the_view() {
        let mut model = crate::app::tests::model();
        let keys = |model: &Model| items(model).iter().map(|item| item.key).collect::<String>();
        assert_eq!(keys(&model), "fburq");
        model.views.push(crate::app::View::Details { doc: "coc".into(), cursor: 0 });
        assert_eq!(keys(&model), "enlvburdq");
    }
}
