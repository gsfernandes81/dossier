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
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Purpose {
    /// What to do with one linked file, by index into `Doc::files`.
    File(usize),
    /// Which older document this one replaces.
    Renews,
    /// How the document is in this bundle, by the bundle's id.
    Member(String),
}

/// What choosing an entry does.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Choice {
    /// Make this file the one `Enter` opens.
    MakePrimary,
    /// Unlink this file; the file itself is untouched.
    Detach,
    /// Type the path of another file to link.
    Attach,
    /// Replace this older document, or none.
    Renew(Option<String>),
    /// Put this version in the bundle in place of the one there now.
    UseVersion(String),
    /// Have the bundle use this one soft copy, or every soft copy.
    UseFile(Option<String>),
    /// Take the document out of the bundle.
    Leave,
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
        let doc = store.get(&self.doc);
        match self.purpose {
            Purpose::File(index) => {
                let path = doc.and_then(|doc| doc.files.get(index)).map_or("", |file| &file.path);
                let name = path.rsplit('/').next().unwrap_or(path);
                format!("file {name}")
            }
            Purpose::Renews | Purpose::Member(_) => "e  edit".into(),
        }
    }

    /// What the picker acts on, its kind, and what it is now, when the panel
    /// names them under its heading.
    #[must_use]
    pub fn subject(&self, store: &Store) -> Option<(String, &'static str, String)> {
        let doc = store.get(&self.doc)?;
        match &self.purpose {
            Purpose::File(_) => None,
            Purpose::Member(bundle) => {
                let file = doc.bundles.iter().find(|entry| entry.bundle == *bundle)?.file.clone();
                let issued = doc
                    .issue_date
                    .as_deref()
                    .map_or("issue date unknown".into(), |date| format!("issued {date}"));
                let copies = file
                    .map_or_else(|| "all soft copies".into(), |file| format!("soft copy {file}"));
                Some((doc.name.clone(), "document", format!("{issued} · {copies}")))
            }
            Purpose::Renews => {
                let now = crate::detail::renews(store, doc);
                let now =
                    if now.is_empty() { "renews nothing".into() } else { format!("renews {now}") };
                Some((doc.name.clone(), "document", now))
            }
        }
    }

    /// Every entry, before any typed narrowing.
    #[must_use]
    pub fn entries(&self, store: &Store) -> Vec<Entry> {
        let Some(doc) = store.get(&self.doc) else {
            return Vec::new();
        };
        match &self.purpose {
            Purpose::File(index) => {
                let index = *index;
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
            Purpose::Member(bundle) => member_entries(store, doc, bundle),
            Purpose::Renews => {
                let mut entries = Vec::new();
                if doc.supersedes.is_some() {
                    entries.push(entry("none", Choice::Renew(None)));
                }
                entries.extend(store.renewable(&doc.id).into_iter().map(|i| Entry {
                    label: crate::detail::version_name(&store.docs[i]),
                    choice: Choice::Renew(Some(store.docs[i].id.clone())),
                }));
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

/// What can be done with a document version in a bundle: another version of
/// it in its place (the newer one first), which soft copy the bundle uses, or
/// taking it out.
fn member_entries(store: &Store, doc: &crate::Doc, bundle: &str) -> Vec<Entry> {
    let mut entries: Vec<Entry> = crate::versions::rows(store, &doc.id)
        .into_iter()
        .filter(|&i| store.docs[i].id != doc.id)
        .map(|i| {
            let version = &store.docs[i];
            let latest = if version.superseded { "" } else { "  (latest)" };
            Entry {
                label: format!("use {}{latest}", crate::detail::version_name(version)),
                choice: Choice::UseVersion(version.id.clone()),
            }
        })
        .collect();
    let file = doc.bundles.iter().find(|entry| entry.bundle == bundle).and_then(|e| e.file.clone());
    if file.is_some() && doc.files.len() > 1 {
        entries.push(entry("use all soft copies", Choice::UseFile(None)));
    }
    if doc.files.len() > 1 {
        entries.extend(doc.files.iter().filter(|f| Some(&f.path) != file.as_ref()).map(|f| {
            Entry {
                label: format!("use only {}", f.path),
                choice: Choice::UseFile(Some(f.path.clone())),
            }
        }));
    }
    entries.push(entry("remove from this bundle", Choice::Leave));
    entries
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

    /// A chain of three passports beside two unrelated documents.
    fn chain() -> Store {
        let mut store = crate::app::tests::model().store;
        let mut add = |id: &str, supersedes: &str| {
            let mut version = store.docs[2].clone();
            version.id = id.into();
            version.supersedes = Some(supersedes.into());
            store.docs.push(version);
        };
        add("passport-2", "passport");
        add("passport-3", "passport-2");
        store.docs[2].superseded = true;
        let middle = store.index_of("passport-2").unwrap();
        store.docs[middle].superseded = true;
        store
    }

    fn labels(store: &Store, id: &str) -> Vec<Choice> {
        Picker::new(id, Purpose::Renews)
            .entries(store)
            .into_iter()
            .map(|entry| entry.choice)
            .collect()
    }

    /// The renews picker offers only documents that keep the chain a chain:
    /// never the document itself, never one of its newer versions, and never
    /// one something else already replaces.
    #[test]
    fn renews_offers_only_documents_that_keep_the_chain() {
        let store = chain();
        let renew = |id: &str| Choice::Renew(Some(id.into()));
        assert_eq!(
            labels(&store, "passport"),
            [renew("coc"), renew("eng1"), renew("testimonial")],
            "not its newer versions"
        );
        assert_eq!(
            labels(&store, "passport-2"),
            [Choice::Renew(None), renew("coc"), renew("eng1"), renew("testimonial")],
            "none first, and not the version it already replaces"
        );
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
