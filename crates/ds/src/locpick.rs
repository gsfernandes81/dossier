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

//! The location picker: the tree of physical locations, drawn like `tree`.
//!
//! The behaviour is in REWRITE-UI.md, "The location picker". [`LocationPicker::rows`]
//! is what the renderer draws and the keys walk, derived afresh every frame.

use std::collections::BTreeSet;

use crate::place::HardCopy;
use crate::Store;

/// Documents listed under a location before a "more" row stands in.
pub const SHOWN: usize = 2;

/// What the picker is choosing a location for.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Mode {
    /// Filing this document's hard copy.
    File(String),
    /// Moving this location.
    Move(String),
}

/// What the cursor is on, by identity rather than by row index, so a refold
/// or a re-root can never leave it on the wrong row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Target {
    /// The root row.
    Root,
    /// A location.
    Location(String),
    /// The "more" row under a location.
    More(String),
    /// The `+ new` row while searching.
    New,
    /// A search match.
    Match(String),
}

/// One drawn row of the tree.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Row {
    /// The root: a location's path, or the whole tree.
    Root,
    /// A location, with its connectors and how many levels below the root.
    Location {
        /// The location.
        id: String,
        /// The `├ │ └` connectors before it.
        lead: String,
        /// One or two.
        depth: usize,
        /// `Some(open)` when it holds anything, `None` when it is empty.
        open: Option<bool>,
    },
    /// A document filed in the location above it.
    Doc {
        /// Index into [`Store::docs`].
        index: usize,
        /// The connectors before it.
        lead: String,
    },
    /// Creates the typed name inside the anchor.
    New,
    /// A location matching the search, drawn with its full path.
    Match(String),
    /// Stands in for the documents not listed under a location.
    More {
        /// The location they are in.
        id: String,
        /// The connectors before it.
        lead: String,
        /// How many are not listed.
        hidden: usize,
    },
}

impl Row {
    /// What the cursor would be on here; `None` for a row it skips.
    #[must_use]
    pub fn target(&self) -> Option<Target> {
        match self {
            Row::Root => Some(Target::Root),
            Row::Location { id, .. } => Some(Target::Location(id.clone())),
            Row::More { id, .. } => Some(Target::More(id.clone())),
            Row::New => Some(Target::New),
            Row::Match(id) => Some(Target::Match(id.clone())),
            Row::Doc { .. } => None,
        }
    }
}

/// The picker's state.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocationPicker {
    /// What it is for.
    pub mode: Mode,
    /// The location at the root row; `None` for the whole tree.
    pub root: Option<String>,
    /// The first-level locations shown open.
    pub open: BTreeSet<String>,
    /// The locations listing every document rather than the first few.
    pub expanded: BTreeSet<String>,
    /// The selection.
    pub cursor: Target,
    /// Typed text searching every location.
    pub filter: String,
    /// Where `+ new` creates: what was selected when typing began.
    pub anchor: Option<String>,
    /// The picker Move… was opened from, put back when the move is done.
    pub back: Option<Box<LocationPicker>>,
}

impl LocationPicker {
    /// Opens on a document, rooted one level above where its hard copy is, with
    /// that location open and selected.
    #[must_use]
    pub fn file(store: &Store, doc: &str) -> Self {
        let at = store.filed_at(doc).map(str::to_string);
        let mut picker = Self {
            mode: Mode::File(doc.to_string()),
            root: at.as_deref().and_then(|id| store.locations.parent(id)).map(str::to_string),
            open: at.iter().cloned().collect(),
            expanded: BTreeSet::new(),
            cursor: at.map_or(Target::Root, Target::Location),
            filter: String::new(),
            anchor: None,
            back: None,
        };
        if picker.cursor == Target::Root {
            picker.cursor = picker.selectable(store).get(1).cloned().unwrap_or(Target::Root);
        }
        picker
    }

    /// Opens on the whole tree to choose where `moving` goes.
    #[must_use]
    pub fn moving(moving: &str) -> Self {
        Self {
            mode: Mode::Move(moving.to_string()),
            root: None,
            open: BTreeSet::new(),
            expanded: BTreeSet::new(),
            cursor: Target::Root,
            filter: String::new(),
            anchor: None,
            back: None,
        }
    }

    /// The document being filed, in file mode.
    #[must_use]
    pub fn doc(&self) -> Option<&str> {
        match &self.mode {
            Mode::File(doc) => Some(doc),
            Mode::Move(_) => None,
        }
    }

    /// Every row, top to bottom.
    #[must_use]
    pub fn rows(&self, store: &Store) -> Vec<Row> {
        if self.searching() {
            let mut rows = Vec::new();
            if matches!(self.mode, Mode::File(_)) {
                rows.push(Row::New);
            }
            rows.extend(
                store
                    .locations
                    .search(&self.filter)
                    .into_iter()
                    .filter(|id| !self.left_out(store, id))
                    .map(|id| Row::Match(id.to_string())),
            );
            return rows;
        }
        let mut rows = vec![Row::Root];
        self.level(store, self.root.as_deref(), 1, "", &mut rows);
        rows
    }

    /// The targets the cursor can stop on, in order.
    #[must_use]
    pub fn selectable(&self, store: &Store) -> Vec<Target> {
        self.rows(store).iter().filter_map(Row::target).collect()
    }

    /// The location under the cursor, with the root row standing for the root.
    #[must_use]
    pub fn chosen(&self) -> Option<&str> {
        match &self.cursor {
            Target::Root => self.root.as_deref(),
            Target::Location(id) | Target::Match(id) => Some(id),
            Target::More(_) | Target::New => None,
        }
    }

    /// Whether typed text is narrowing the tree to a search.
    #[must_use]
    pub fn searching(&self) -> bool {
        !self.filter.trim().is_empty()
    }

    /// The name `+ new` would create.
    #[must_use]
    pub fn new_name(&self) -> &str {
        self.filter.trim()
    }

    /// Types a character into the search, anchoring `+ new` at the first one.
    pub fn type_char(&mut self, store: &Store, c: char) {
        if self.filter.is_empty() {
            self.anchor = match &self.cursor {
                Target::Root => self.root.clone(),
                Target::Location(id) | Target::More(id) | Target::Match(id) => Some(id.clone()),
                Target::New => self.anchor.clone(),
            };
        }
        self.filter.push(c);
        self.first_match(store);
    }

    /// Rubs out the last typed character; with none left, the tree comes back
    /// on what was selected before.
    pub fn rub_out(&mut self, store: &Store) {
        self.filter.pop();
        if self.searching() {
            self.first_match(store);
        } else {
            self.clear_search(store);
        }
    }

    /// Drops the search and puts the cursor back where typing began.
    pub fn clear_search(&mut self, store: &Store) {
        self.filter.clear();
        self.cursor = self.anchor.clone().map_or(Target::Root, Target::Location);
        if !self.selectable(store).contains(&self.cursor) {
            self.cursor = Target::Root;
        }
    }

    fn first_match(&mut self, store: &Store) {
        let targets = self.selectable(store);
        self.cursor = targets
            .iter()
            .find(|t| matches!(t, Target::Match(_)))
            .or_else(|| targets.first())
            .cloned()
            .unwrap_or(Target::Root);
    }

    /// Whether this mode hides `id`: the moving location and everything in it.
    pub(crate) fn left_out(&self, store: &Store, id: &str) -> bool {
        matches!(&self.mode, Mode::Move(moving) if store.locations.is_within(id, moving))
    }

    /// How many levels below the root `id` is drawn; 0 for the root.
    #[must_use]
    pub fn depth(&self, store: &Store, id: &str) -> usize {
        let chain = store.locations.ancestry(id);
        match &self.root {
            None => chain.len(),
            Some(root) => {
                chain.iter().position(|at| at == root).map_or(0, |at| chain.len() - at - 1)
            }
        }
    }

    /// Whether this location holds anything the tree can show inside it.
    #[must_use]
    pub fn holds_anything(&self, store: &Store, id: &str) -> bool {
        !self.children(store, Some(id)).is_empty() || !self.filed(store, id).is_empty()
    }

    /// `→`: opens a first-level location in place, or moves the root down so a
    /// second-level one can open as a first level.
    pub fn right(&mut self, store: &Store) {
        let Target::Location(id) = self.cursor.clone() else { return };
        if !self.holds_anything(store, &id) {
            return;
        }
        if self.depth(store, &id) >= 2 {
            self.root = store.locations.parent(&id).map(str::to_string);
            self.open = BTreeSet::from([id]);
        } else if !self.open.insert(id.clone()) {
            let inside = |target: &Target| match target {
                Target::Location(child) => store.locations.parent(child) == Some(id.as_str()),
                Target::More(at) => *at == id,
                Target::Root | Target::New | Target::Match(_) => false,
            };
            let next = self.selectable(store).into_iter().skip_while(|t| *t != self.cursor).nth(1);
            if let Some(next) = next.filter(inside) {
                self.cursor = next;
            }
        }
    }

    /// `←`: closes an open location, steps to the parent, or moves the root up.
    pub fn left(&mut self, store: &Store) {
        match self.cursor.clone() {
            Target::Root => {
                let Some(root) = self.root.clone() else { return };
                self.root = store.locations.parent(&root).map(str::to_string);
                self.open = BTreeSet::from([root.clone()]);
                self.cursor = Target::Location(root);
            }
            Target::New | Target::Match(_) => {}
            Target::Location(id) if self.open.remove(&id) => {}
            Target::Location(id) | Target::More(id) => {
                let parent = if matches!(self.cursor, Target::More(_)) {
                    Some(id.as_str())
                } else {
                    store.locations.parent(&id)
                };
                self.cursor = match parent {
                    Some(parent) if Some(parent) != self.root.as_deref() => {
                        Target::Location(parent.to_string())
                    }
                    _ => Target::Root,
                };
            }
        }
    }

    /// The locations directly inside `parent` that this mode shows.
    fn children<'a>(&self, store: &'a Store, parent: Option<&str>) -> Vec<&'a str> {
        store
            .locations
            .children(parent)
            .iter()
            .map(String::as_str)
            .filter(|child| !matches!(&self.mode, Mode::Move(moving) if moving == child))
            .collect()
    }

    /// The documents filed directly in `id`, listed only when filing.
    fn filed(&self, store: &Store, id: &str) -> Vec<usize> {
        if matches!(self.mode, Mode::Move(_)) {
            return Vec::new();
        }
        store
            .docs
            .iter()
            .enumerate()
            .filter(|(_, doc)| store.hard_copy(doc) == HardCopy::At(id))
            .map(|(index, _)| index)
            .collect()
    }

    fn level(
        &self,
        store: &Store,
        parent: Option<&str>,
        depth: usize,
        guide: &str,
        rows: &mut Vec<Row>,
    ) {
        let locations = self.children(store, parent);
        let docs = parent.map(|id| self.filed(store, id)).unwrap_or_default();
        let shown = match parent {
            Some(id) if !self.expanded.contains(id) && docs.len() > SHOWN => SHOWN,
            _ => docs.len(),
        };
        let more = docs.len() - shown;
        let total = locations.len() + shown + usize::from(more > 0);
        let mut n = 0;
        let connector = |n: &mut usize| {
            *n += 1;
            let last = *n == total;
            (
                format!("{guide}{}", if last { "└ " } else { "├ " }),
                format!("{guide}{}", if last { "  " } else { "│ " }),
            )
        };
        for id in locations {
            let (lead, below) = connector(&mut n);
            let holds = self.holds_anything(store, id);
            let open = holds && depth == 1 && self.open.contains(id);
            rows.push(Row::Location {
                id: id.to_string(),
                lead,
                depth,
                open: holds.then_some(open),
            });
            if open {
                self.level(store, Some(id), depth + 1, &below, rows);
            }
        }
        for &index in &docs[..shown] {
            let (lead, _) = connector(&mut n);
            rows.push(Row::Doc { index, lead });
        }
        if more > 0 {
            let (lead, _) = connector(&mut n);
            rows.push(Row::More { id: parent.unwrap_or_default().to_string(), lead, hidden: more });
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Doc, Location, Tree};

    fn place(id: &str, name: &str, parent: Option<&str>) -> Location {
        Location { id: id.into(), name: name.into(), parent: parent.map(str::to_string) }
    }

    fn doc(id: &str, at: &str) -> Doc {
        Doc { id: id.into(), name: id.into(), location: Some(at.into()), ..Doc::default() }
    }

    fn store() -> Store {
        Store {
            docs: vec![
                doc("passport", "pouch"),
                doc("coc", "slot3"),
                doc("eng1", "slot3"),
                doc("stcw", "slot3"),
                doc("yellow", "left"),
            ],
            locations: Tree::new([
                place("desk", "desk", None),
                place("folder", "leather folder", Some("desk")),
                place("slot1", "slot 1", Some("folder")),
                place("slot3", "slot 3", Some("folder")),
                place("pocket", "front pocket", Some("folder")),
                place("left", "left", Some("pocket")),
                place("pouch", "passport pouch", Some("desk")),
                place("bag", "ship bag", None),
            ]),
            ..Store::default()
        }
    }

    fn shape(picker: &LocationPicker, store: &Store) -> Vec<String> {
        picker
            .rows(store)
            .iter()
            .map(|row| match row {
                Row::Root => picker.root.clone().unwrap_or_else(|| "locations".into()),
                Row::Location { id, lead, open, .. } => {
                    format!(
                        "{lead}{}{id}",
                        match open {
                            Some(true) => "▾ ",
                            Some(false) => "▸ ",
                            None => "  ",
                        }
                    )
                }
                Row::Doc { index, lead } => format!("{lead}{}", store.docs[*index].id),
                Row::More { lead, hidden, .. } => format!("{lead}{hidden} more"),
                Row::New => format!("+ new {}", picker.new_name()),
                Row::Match(id) => id.clone(),
            })
            .collect()
    }

    #[test]
    fn it_opens_one_level_above_the_hard_copy() {
        let store = store();
        let picker = LocationPicker::file(&store, "passport");
        assert_eq!(picker.root.as_deref(), Some("desk"));
        assert_eq!(picker.cursor, Target::Location("pouch".into()));
        assert_eq!(shape(&picker, &store), ["desk", "├ ▸ folder", "└ ▾ pouch", "  └ passport"]);
    }

    #[test]
    fn an_unfiled_document_opens_at_the_top() {
        let mut store = store();
        store.docs[0].location = None;
        let picker = LocationPicker::file(&store, "passport");
        assert_eq!(picker.root, None);
        assert_eq!(picker.cursor, Target::Location("desk".into()));
    }

    #[test]
    fn a_first_level_location_opens_in_place() {
        let store = store();
        let mut picker = LocationPicker::file(&store, "passport");
        picker.cursor = Target::Location("folder".into());
        picker.right(&store);
        assert_eq!(
            shape(&picker, &store),
            [
                "desk",
                "├ ▾ folder",
                "│ ├ ▸ pocket",
                "│ ├   slot1",
                "│ └ ▸ slot3",
                "└ ▾ pouch",
                "  └ passport"
            ]
        );
    }

    #[test]
    fn a_third_level_steps_in() {
        let store = store();
        let mut picker = LocationPicker::file(&store, "passport");
        picker.cursor = Target::Location("folder".into());
        picker.right(&store);
        picker.cursor = Target::Location("slot3".into());
        picker.right(&store);
        assert_eq!(picker.root.as_deref(), Some("folder"));
        assert_eq!(
            shape(&picker, &store),
            ["folder", "├ ▸ pocket", "├   slot1", "└ ▾ slot3", "  ├ coc", "  ├ eng1", "  └ 1 more"]
        );

        picker.cursor = Target::Root;
        picker.left(&store);
        assert_eq!(picker.root.as_deref(), Some("desk"));
        assert_eq!(picker.cursor, Target::Location("folder".into()));
    }

    #[test]
    fn the_cursor_skips_documents_and_stops_on_more() {
        let store = store();
        let mut picker = LocationPicker::file(&store, "passport");
        picker.root = Some("folder".into());
        picker.open = BTreeSet::from(["slot3".to_string()]);
        let targets = picker.selectable(&store);
        let slot3 = targets.iter().position(|t| *t == Target::Location("slot3".into())).unwrap();
        assert_eq!(targets[slot3 + 1], Target::More("slot3".into()));
        picker.expanded.insert("slot3".into());
        assert!(shape(&picker, &store).contains(&"  └ stcw".to_string()));
    }

    #[test]
    fn typing_searches_with_new_pinned_above() {
        let store = store();
        let mut picker = LocationPicker::file(&store, "passport");
        for c in "slot".chars() {
            picker.type_char(&store, c);
        }
        assert_eq!(shape(&picker, &store), ["+ new slot", "slot1", "slot3"]);
        assert_eq!(picker.cursor, Target::Match("slot1".into()));
        assert_eq!(picker.anchor.as_deref(), Some("pouch"));
        for c in " 9".chars() {
            picker.type_char(&store, c);
        }
        assert_eq!(picker.cursor, Target::New, "nothing matches, so + new is selected");
        for _ in 0..7 {
            picker.rub_out(&store);
        }
        assert!(!picker.searching());
        assert_eq!(picker.cursor, Target::Location("pouch".into()));
    }

    #[test]
    fn a_moving_location_is_left_out() {
        let store = store();
        let mut picker = LocationPicker::moving("folder");
        picker.cursor = Target::Location("desk".into());
        picker.right(&store);
        assert_eq!(shape(&picker, &store), ["locations", "├ ▾ desk", "│ └   pouch", "└   bag"]);
    }
}
