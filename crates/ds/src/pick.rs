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

//! Choosing one thing from a list: what `e` opens on a record row whose value
//! is a choice rather than typed text. Drawn in the Space sheet's panel.

use crate::Store;

/// A picker open over a record row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Picker {
    /// The document whose row it was opened on.
    pub doc: String,
    /// What is being chosen.
    pub purpose: Purpose,
    /// Typed text narrowing the entries.
    pub filter: String,
    /// The selected entry among those matching.
    pub cursor: usize,
}

/// What a picker is choosing for.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Purpose {
    /// What to do with one linked file, by index into `Doc::files`.
    File(usize),
}

/// What choosing an entry does.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Choice {
    /// Make this file the one `Enter` opens.
    MakePrimary,
    /// Unlink this file; the file itself is untouched.
    Detach,
    /// Type the path of another file to link.
    Attach,
}

/// One line of a picker.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    /// What the line says, and what typing matches.
    pub label: String,
    /// What it does.
    pub choice: Choice,
}

impl Picker {
    /// Opens a picker on `doc` for `purpose`.
    #[must_use]
    pub fn new(doc: &str, purpose: Purpose) -> Self {
        Self { doc: doc.to_string(), purpose, filter: String::new(), cursor: 0 }
    }

    /// The panel's heading: what is being changed.
    #[must_use]
    pub fn crumb(&self, store: &Store) -> String {
        let doc = store.index_of(&self.doc).map(|i| &store.docs[i]);
        match self.purpose {
            Purpose::File(index) => {
                let path = doc.and_then(|doc| doc.files.get(index)).map_or("", |file| &file.path);
                let name = path.rsplit('/').next().unwrap_or(path);
                format!("file {name}")
            }
        }
    }

    /// Every entry, before any typed narrowing.
    #[must_use]
    pub fn entries(&self, store: &Store) -> Vec<Entry> {
        let Some(doc) = store.index_of(&self.doc).map(|i| &store.docs[i]) else {
            return Vec::new();
        };
        match self.purpose {
            Purpose::File(index) => {
                let mut entries = Vec::new();
                let is_primary = doc
                    .primary_file()
                    .zip(doc.files.get(index))
                    .is_some_and(|(primary, file)| primary.path == file.path);
                if !is_primary {
                    entries.push(entry("make primary", Choice::MakePrimary));
                }
                entries.push(entry("detach", Choice::Detach));
                entries.push(entry("attach another file", Choice::Attach));
                entries
            }
        }
    }

    /// The entries the typed text leaves.
    #[must_use]
    pub fn matching(&self, store: &Store) -> Vec<Entry> {
        let needle = crate::search::fold(&self.filter);
        self.entries(store)
            .into_iter()
            .filter(|entry| crate::search::fold(&entry.label).contains(&needle))
            .collect()
    }
}

fn entry(label: &str, choice: Choice) -> Entry {
    Entry { label: label.to_string(), choice }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::FileRef;

    fn two_files() -> Store {
        let mut store = crate::app::tests::model().store;
        store.docs[0].files.push(FileRef {
            label: String::new(),
            path: "Marine/coc-back.pdf".into(),
            primary: false,
        });
        store
    }

    /// The primary file is not offered "make primary"; the other one is.
    #[test]
    fn only_a_secondary_file_offers_make_primary() {
        let store = two_files();
        let choices = |index| {
            Picker::new("coc", Purpose::File(index))
                .entries(&store)
                .into_iter()
                .map(|entry| entry.choice)
                .collect::<Vec<_>>()
        };
        assert_eq!(choices(0), [Choice::Detach, Choice::Attach]);
        assert_eq!(choices(1), [Choice::MakePrimary, Choice::Detach, Choice::Attach]);
    }

    /// Typing narrows on the label, folded like the document search.
    #[test]
    fn typing_narrows_the_entries() {
        let store = two_files();
        let mut picker = Picker::new("coc", Purpose::File(1));
        picker.filter = "DET".into();
        let hits = picker.matching(&store);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].choice, Choice::Detach);
        assert_eq!(picker.crumb(&store), "file coc-back.pdf");
    }
}
