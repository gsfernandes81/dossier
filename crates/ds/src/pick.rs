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

//! The panel that chooses from a list, searched by typing: a checklist whose
//! rows toggle and stay open, or a picker that closes on its choice.

use crate::{Model, Store};

/// An open panel.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Picker {
    /// What it is for.
    pub purpose: Purpose,
    /// Typed text narrowing the rows.
    pub filter: String,
    /// The selected row among those matching.
    pub cursor: usize,
}

/// What a panel is choosing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Purpose {
    /// The Find view's filters.
    Filter,
    /// The bundles a document version is in, by the version's id.
    Bundles(String),
    /// What to do with one linked file, by index into `Doc::files`.
    File { doc: String, index: usize },
    /// Which older document `doc` replaces.
    Renews(String),
    /// How `doc` is in `bundle`.
    Member { doc: String, bundle: String },
}

/// What choosing a row does.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Choice {
    /// Expiring only.
    Expiring,
    /// Include old versions.
    OldVersions,
    /// Search scan text.
    Scans,
    /// Turn every filter off.
    ClearAll,
    /// Add the version to this bundle, or take it out.
    Bundle(String),
    /// Create the bundle the typed text names, with the version in it.
    NewBundle,
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

/// One row of a panel.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    /// What it says, and what typing matches.
    pub label: String,
    /// `Some` for a checkbox row.
    pub on: Option<bool>,
    /// What choosing it does.
    pub choice: Choice,
}

impl Picker {
    /// Opens a panel for `purpose` with nothing typed.
    #[must_use]
    pub fn new(purpose: Purpose) -> Self {
        Self { purpose, filter: String::new(), cursor: 0 }
    }

    /// Whether its rows toggle and leave it open, rather than choosing once.
    #[must_use]
    pub fn checklist(&self) -> bool {
        matches!(self.purpose, Purpose::Filter | Purpose::Bundles(_))
    }

    /// The heading: what is being changed.
    #[must_use]
    pub fn crumb(&self, store: &Store) -> String {
        match &self.purpose {
            Purpose::Filter => "SPC f  filter".into(),
            Purpose::Bundles(_) => "SPC b  bundles".into(),
            Purpose::File { doc, index } => {
                let path =
                    store.get(doc).and_then(|doc| doc.files.get(*index)).map_or("", |f| &f.path);
                format!("file {}", path.rsplit('/').next().unwrap_or(path))
            }
            Purpose::Renews(_) | Purpose::Member { .. } => "e  edit".into(),
        }
    }

    /// What the panel acts on, its kind, and what it is now, when the panel
    /// names them under its heading.
    #[must_use]
    pub fn subject(&self, store: &Store) -> Option<(String, &'static str, String)> {
        match &self.purpose {
            Purpose::Member { doc, bundle } => {
                let doc = store.get(doc)?;
                let file = doc.bundles.iter().find(|entry| entry.bundle == *bundle)?.file.clone();
                let issued = doc
                    .issue_date
                    .as_deref()
                    .map_or("issue date unknown".into(), |date| format!("issued {date}"));
                let copies = file
                    .map_or_else(|| "all soft copies".into(), |file| format!("soft copy {file}"));
                Some((doc.name.clone(), "document", format!("{issued} · {copies}")))
            }
            Purpose::Renews(doc) => {
                let doc = store.get(doc)?;
                let now = crate::detail::renews(store, doc);
                let now =
                    if now.is_empty() { "renews nothing".into() } else { format!("renews {now}") };
                Some((doc.name.clone(), "document", now))
            }
            _ => None,
        }
    }

    /// The rows the typed text leaves.
    #[must_use]
    pub fn matching(&self, model: &Model) -> Vec<Entry> {
        let store = &model.store;
        let all = match &self.purpose {
            Purpose::Bundles(doc) => return bundle_entries(store, doc, &self.filter),
            Purpose::Filter => vec![
                check("expiring only", model.filter.expiring, Choice::Expiring),
                check("include old versions", model.filter.old_versions, Choice::OldVersions),
                check(
                    "search scan text",
                    model.scan_search != crate::app::ScanSearch::Off,
                    Choice::Scans,
                ),
                entry("clear all", Choice::ClearAll),
            ],
            Purpose::File { doc, index } => file_entries(store, doc, *index),
            Purpose::Renews(doc) => renew_entries(store, doc),
            Purpose::Member { doc, bundle } => {
                store.get(doc).map(|doc| member_entries(store, doc, bundle)).unwrap_or_default()
            }
        };
        let needle = crate::search::fold(&self.filter);
        all.into_iter()
            .filter(|entry| crate::search::fold(&entry.label).contains(&needle))
            .collect()
    }

    /// Where the cursor starts once something is typed: on the first match,
    /// or on `+ new` when nothing matches.
    #[must_use]
    pub fn first(&self, model: &Model) -> usize {
        let hits = self.matching(model);
        usize::from(hits.len() > 1 && hits[0].choice == Choice::NewBundle)
    }
}

/// The bundles `doc` can be ticked into, behind `+ new` as the Bundles view
/// pins it.
fn bundle_entries(store: &Store, doc: &str, typed: &str) -> Vec<Entry> {
    let Some(doc) = store.get(doc) else { return Vec::new() };
    crate::bundles::entries(store, typed)
        .into_iter()
        .filter_map(|row| match row {
            crate::bundles::Entry::New if typed.trim().is_empty() => {
                Some(entry("type a name to make a bundle", Choice::NewBundle))
            }
            crate::bundles::Entry::New => {
                Some(entry(&crate::layout::new_label(typed, "bundle"), Choice::NewBundle))
            }
            crate::bundles::Entry::Bundle(id) => store.bundle(&id).map(|bundle| {
                let on = doc.bundles.iter().any(|entry| entry.bundle == id);
                check(&bundle.name, on, Choice::Bundle(id.clone()))
            }),
        })
        .collect()
}

fn file_entries(store: &Store, doc: &str, index: usize) -> Vec<Entry> {
    let Some(doc) = store.get(doc) else { return Vec::new() };
    let is_primary = doc
        .primary_file()
        .zip(doc.files.get(index))
        .is_some_and(|(primary, file)| primary.path == file.path);
    let mut entries = Vec::new();
    if !is_primary {
        entries.push(entry("make primary", Choice::MakePrimary));
    }
    entries.push(entry("detach", Choice::Detach));
    entries.push(entry("attach another file", Choice::Attach));
    entries
}

fn renew_entries(store: &Store, doc: &str) -> Vec<Entry> {
    let Some(doc) = store.get(doc) else { return Vec::new() };
    let mut entries = Vec::new();
    if doc.supersedes.is_some() {
        entries.push(entry("none", Choice::Renew(None)));
    }
    entries.extend(store.renewable(&doc.id).into_iter().map(|i| {
        entry(
            &crate::detail::version_name(&store.docs[i]),
            Choice::Renew(Some(store.docs[i].id.clone())),
        )
    }));
    entries
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
            entry(
                &format!("use {}{latest}", crate::detail::version_name(version)),
                Choice::UseVersion(version.id.clone()),
            )
        })
        .collect();
    let file = doc.bundles.iter().find(|entry| entry.bundle == bundle).and_then(|e| e.file.clone());
    if file.is_some() && doc.files.len() > 1 {
        entries.push(entry("use all soft copies", Choice::UseFile(None)));
    }
    if doc.files.len() > 1 {
        entries.extend(doc.files.iter().filter(|f| Some(&f.path) != file.as_ref()).map(|f| {
            entry(&format!("use only {}", f.path), Choice::UseFile(Some(f.path.clone())))
        }));
    }
    entries.push(entry("remove from this bundle", Choice::Leave));
    entries
}

fn entry(label: &str, choice: Choice) -> Entry {
    Entry { label: label.to_string(), on: None, choice }
}

fn check(label: &str, on: bool, choice: Choice) -> Entry {
    Entry { label: label.to_string(), on: Some(on), choice }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::FileRef;

    fn two_files() -> Model {
        let mut model = crate::app::tests::model();
        model.store.docs[0].files.push(FileRef {
            label: String::new(),
            path: "Marine/coc-back.pdf".into(),
            primary: false,
        });
        model
    }

    fn choices(model: &Model, purpose: Purpose) -> Vec<Choice> {
        Picker::new(purpose).matching(model).into_iter().map(|entry| entry.choice).collect()
    }

    #[test]
    fn renews_offers_only_documents_that_keep_the_chain() {
        let model = crate::app::tests::with_versions();
        let renew = |id: &str| Choice::Renew(Some(id.into()));
        assert_eq!(
            choices(&model, Purpose::Renews("passport".into())),
            [renew("coc"), renew("eng1"), renew("testimonial")],
            "not its newer versions"
        );
        assert_eq!(
            choices(&model, Purpose::Renews("passport-desk".into())),
            [
                Choice::Renew(None),
                renew("coc"),
                renew("eng1"),
                renew("testimonial"),
                renew("passport-phone")
            ],
            "none first, and not the version it already replaces"
        );
    }

    #[test]
    fn only_a_secondary_file_offers_make_primary() {
        let model = two_files();
        let file = |index| choices(&model, Purpose::File { doc: "coc".into(), index });
        assert_eq!(file(0), [Choice::Detach, Choice::Attach]);
        assert_eq!(file(1), [Choice::MakePrimary, Choice::Detach, Choice::Attach]);
    }

    #[test]
    fn typing_narrows_the_entries() {
        let model = two_files();
        let mut picker = Picker::new(Purpose::File { doc: "coc".into(), index: 1 });
        picker.filter = "DET".into();
        let hits = picker.matching(&model);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].choice, Choice::Detach);
        assert_eq!(picker.crumb(&model.store), "file coc-back.pdf");
    }

    #[test]
    fn the_filter_list_shows_off_as_well_as_on() {
        let mut model = crate::app::tests::model();
        let ons = |model: &Model| {
            Picker::new(Purpose::Filter).matching(model).iter().map(|e| e.on).collect::<Vec<_>>()
        };
        assert_eq!(ons(&model), [Some(false), Some(false), Some(false), None]);
        model.filter.expiring = true;
        assert_eq!(ons(&model), [Some(true), Some(false), Some(false), None]);
    }
}
