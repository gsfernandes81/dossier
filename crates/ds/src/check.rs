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

//! Checkbox lists: several things on at once, toggled by tap, `Space` or
//! `Enter`, searched by typing. The rules are REWRITE-UI.md §5c.

/// What a checklist is choosing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Purpose {
    /// The Find view's filters.
    Filter,
    /// The bundles a document version is in, by the version's id.
    Bundles(String),
}

/// What toggling a row does.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Toggle {
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
}

/// One row of a checklist.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    /// What it says, and what typing matches.
    pub label: String,
    /// `Some` for a box, `None` for an action row such as clear all.
    pub on: Option<bool>,
    /// What toggling it does.
    pub toggle: Toggle,
}

/// An open checklist.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CheckList {
    /// What it is for.
    pub purpose: Purpose,
    /// Typed text narrowing the rows.
    pub filter: String,
    /// The selected row among those matching.
    pub cursor: usize,
}

impl CheckList {
    /// Opens a checklist with nothing typed.
    #[must_use]
    pub fn new(purpose: Purpose) -> Self {
        Self { purpose, filter: String::new(), cursor: 0 }
    }

    /// The heading's verb.
    #[must_use]
    pub fn crumb(&self) -> &'static str {
        match self.purpose {
            Purpose::Filter => "SPC f  filter",
            Purpose::Bundles(_) => "SPC b  bundles",
        }
    }

    /// The rows the typed text leaves.
    #[must_use]
    pub fn matching(&self, model: &crate::Model) -> Vec<Entry> {
        let needle = crate::search::fold(&self.filter);
        let mut hits: Vec<Entry> = entries(&self.purpose, model)
            .into_iter()
            .filter(|entry| crate::search::fold(&entry.label).contains(&needle))
            .collect();
        if matches!(self.purpose, Purpose::Bundles(_)) {
            let typed = self.filter.trim();
            let label = if !typed.is_empty() {
                format!("+ new \"{typed}\"")
            } else if model.store.bundles.is_empty() {
                "type a name to make a bundle".into()
            } else {
                return hits;
            };
            hits.insert(0, Entry { label, on: None, toggle: Toggle::NewBundle });
        }
        hits
    }

    /// Where the cursor starts once something is typed: on the first match,
    /// or on `+ new` when nothing matches.
    #[must_use]
    pub fn first(&self, model: &crate::Model) -> usize {
        let hits = self.matching(model);
        usize::from(hits.len() > 1 && hits[0].toggle == Toggle::NewBundle)
    }
}

fn entries(purpose: &Purpose, model: &crate::Model) -> Vec<Entry> {
    let row = |label: &str, on: Option<bool>, toggle| Entry { label: label.into(), on, toggle };
    match purpose {
        Purpose::Bundles(doc) => {
            let Some(doc) = model.store.get(doc) else {
                return Vec::new();
            };
            model
                .store
                .bundles
                .iter()
                .map(|bundle| Entry {
                    label: bundle.name.clone(),
                    on: Some(doc.bundles.iter().any(|entry| entry.bundle == bundle.id)),
                    toggle: Toggle::Bundle(bundle.id.clone()),
                })
                .collect()
        }
        Purpose::Filter => vec![
            row("expiring only", Some(model.filter.expiring), Toggle::Expiring),
            row("include old versions", Some(model.filter.old_versions), Toggle::OldVersions),
            row(
                "search scan text",
                Some(model.scan_search != crate::app::ScanSearch::Off),
                Toggle::Scans,
            ),
            row("clear all", None, Toggle::ClearAll),
        ],
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The filter list shows every box whether on or off, then clear all.
    #[test]
    fn the_filter_list_shows_off_as_well_as_on() {
        let mut model = crate::app::tests::model();
        let list = CheckList::new(Purpose::Filter);
        let ons = |model: &crate::Model| {
            list.matching(model).iter().map(|entry| entry.on).collect::<Vec<_>>()
        };
        assert_eq!(ons(&model), [Some(false), Some(false), Some(false), None]);
        model.filter.expiring = true;
        assert_eq!(ons(&model), [Some(true), Some(false), Some(false), None]);
    }

    /// Typing narrows the rows as search folds them.
    #[test]
    fn typing_narrows_the_list() {
        let model = crate::app::tests::model();
        let mut list = CheckList::new(Purpose::Filter);
        list.filter = "OLD".into();
        let hits = list.matching(&model);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].toggle, Toggle::OldVersions);
    }
}
