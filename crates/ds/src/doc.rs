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

//! The view model: a folded journal turned into the rows the Find list shows.
//!
//! The `journal` crate deals in ops and untyped `serde_json` values, because
//! that is what the format is. Everything above it wants documents — with a
//! name, a place on a shelf, an expiry status and a search haystack. This module
//! is that boundary, and it is deliberately the *only* place that knows field
//! names like `expiry_date`.
//!
//! Two rules from the plan are implemented here rather than in the renderer,
//! because they are facts about the data and not about the screen:
//!
//! * **Shelf order** (REWRITE-UI.md §1): the location tree in sibling order,
//!   then name, then id, so the list never jitters between frames. Unfiled and
//!   digital-only documents come last.
//! * **The expiry watch is opt-out** (DESIGN §14): a document is tracked if it
//!   has an expiry date and is neither superseded by a newer document nor
//!   explicitly ignored. Being superseded is a *collection-level* fact — some
//!   other document's `supersedes` points here — so it can only be computed with
//!   the whole store in hand, which is why [`Store::build`] does it once.

use std::collections::{BTreeMap, BTreeSet};

use journal::{Entity, Fold};

use crate::place::{HardCopy, Location, Tree};
use serde_json::Value;

/// A document's expiry standing, as the row renders it.
///
/// rust: an enum with a `marker()`, not a colour. Colour is the renderer's
/// business; the *signal* is this, and REWRITE-UI.md §6 requires the glyph to
/// carry it so a monochrome terminal loses nothing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Status {
    /// Past its expiry date, still in use.
    Expired,
    /// Inside the warn window ([`WARN_DAYS`]).
    Soon,
    /// Tracked, but not yet worth attention.
    Ok,
    /// Not tracked: no expiry date, superseded, or explicitly ignored.
    Untracked,
}

impl Status {
    /// The ASCII marker. Never blank for a state that needs attention.
    #[must_use]
    pub fn marker(self) -> &'static str {
        match self {
            Status::Expired => "!",
            Status::Soon => "~",
            Status::Ok => " ",
            Status::Untracked => "·",
        }
    }
}

/// One file linked to a document. The word "rendition" is dropped (D9); the
/// capability — several files, one marked primary — is unchanged.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileRef {
    /// What this copy is: `complete`, `front`, `scan`.
    pub label: String,
    /// POSIX path relative to the Syncthing root — never absolute, never
    /// per-device (DESIGN §4/§6).
    pub path: String,
    /// The one to open by default.
    pub primary: bool,
}

/// One entry of a document in a bundle: this exact version.
///
/// Stored on the document, so two devices adding different documents to one
/// bundle never write the same field.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Membership {
    /// The bundle's id.
    pub bundle: String,
    /// The path of the one file the bundle uses, if not all of them.
    pub file: Option<String>,
}

/// A named set of document versions, kept for a purpose.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Bundle {
    /// The journal entity id.
    pub id: String,
    /// What it is called.
    pub name: String,
    /// ISO date it is for, if any.
    pub date: Option<String>,
    /// Free text.
    pub notes: String,
}

impl Bundle {
    /// Every stored field, as the journal holds it; the inverse of
    /// [`Store::build`]'s mapping, so a deleted bundle can be put back whole.
    #[must_use]
    pub fn as_fields(&self) -> Vec<(&'static str, Value)> {
        let mut fields: Vec<(&'static str, Value)> = vec![("name", self.name.clone().into())];
        if let Some(date) = &self.date {
            fields.push(("date", date.clone().into()));
        }
        if !self.notes.is_empty() {
            fields.push(("notes", self.notes.clone().into()));
        }
        fields
    }
}

/// A document as the browse surface needs it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Doc {
    /// Slug; the journal entity id.
    pub id: String,
    /// Display name — the left column and the main search target.
    pub name: String,
    /// Flat tags (hierarchical tags are dropped, §8).
    pub tags: Vec<String>,
    /// The bundles this version was added to.
    pub bundles: Vec<Membership>,
    /// ISO issue date.
    pub issue_date: Option<String>,
    /// ISO expiry date.
    pub expiry_date: Option<String>,
    /// Opt out of the expiry watch (the residual-noise escape hatch).
    pub ignore_expiry: bool,
    /// The id of the document this one replaces.
    pub supersedes: Option<String>,
    /// The `location` field as stored: a location id, or
    /// [`crate::place::DIGITAL_ONLY`]. Read it through [`Store::hard_copy`].
    pub location: Option<String>,
    /// Linked files.
    pub files: Vec<FileRef>,
    /// Free text.
    pub notes: String,
    /// Whether some *other* document supersedes this one.
    pub superseded: bool,
    /// A latest version beside a later-issued one: two devices each made a
    /// new version of the same document.
    pub conflicting: bool,
    /// Folded name + notes + tags + bundles, precomputed once.
    ///
    /// Search runs on every keystroke across the whole store, so the
    /// per-keystroke work has to be a scan of prepared strings rather than a
    /// thousand fresh allocations. Same trick the R0.2 spike measured.
    pub haystack: String,
}

impl Doc {
    /// Whether this document is in the expiry watch at all (opt-out, DESIGN §14).
    #[must_use]
    pub fn is_tracked(&self) -> bool {
        self.listed() && self.expiry_date.is_some() && !self.ignore_expiry
    }

    /// Whether the default list shows it: a document's latest version.
    #[must_use]
    pub fn listed(&self) -> bool {
        !self.superseded
    }

    /// The expiry standing, given today and the warn window.
    ///
    /// Dates are ISO strings and compare correctly as strings, so no date
    /// library is needed for the comparison — only for computing the window,
    /// which the caller passes in already resolved.
    #[must_use]
    pub fn status(&self, today: &str, warn_until: &str) -> Status {
        if !self.is_tracked() {
            return Status::Untracked;
        }
        let Some(expiry) = self.expiry_date.as_deref() else { return Status::Untracked };
        if expiry < today {
            Status::Expired
        } else if expiry <= warn_until {
            Status::Soon
        } else {
            Status::Ok
        }
    }

    /// Every journal field this document holds, in the shape the fold reads.
    ///
    /// **This is what makes a delete undoable.** §3.2 keeps a tombstone forever
    /// and makes a later `create` start from *empty*, so putting a deleted
    /// document back means re-sending every field it had — and the fold is the
    /// only authority on what those are.
    ///
    /// It is the exact inverse of [`Store::build`]'s per-document mapping, and
    /// the pair is checked by a round-trip test rather than by eye: a field
    /// added to one side and forgotten on this one would be data that vanishes
    /// on undo, which is the worst kind of bug this program could have.
    ///
    /// `superseded` and `haystack` are absent on purpose — they are derived from
    /// the collection and from the other fields, never stored.
    #[must_use]
    pub fn as_fields(&self) -> Vec<(&'static str, Value)> {
        let mut fields: Vec<(&'static str, Value)> = vec![("name", self.name.clone().into())];
        let mut push = |key: &'static str, value: Option<Value>| {
            if let Some(value) = value {
                fields.push((key, value));
            }
        };
        push("notes", (!self.notes.is_empty()).then(|| self.notes.clone().into()));
        push("tags", (!self.tags.is_empty()).then(|| self.tags.clone().into()));
        push("bundles", memberships_value(&self.bundles));
        push("issue_date", self.issue_date.clone().map(Into::into));
        push("expiry_date", self.expiry_date.clone().map(Into::into));
        push("ignore_expiry", self.ignore_expiry.then(|| true.into()));
        push("supersedes", self.supersedes.clone().map(Into::into));
        push("location", self.location.clone().map(Into::into));
        push("files", files_value(&self.files));
        fields
    }

    /// The file to open when `Enter` is pressed: the primary if one is marked,
    /// else the first. `None` means `Enter` falls through to the record —
    /// invariant 2, and the reason that verb can never fail.
    #[must_use]
    pub fn primary_file(&self) -> Option<&FileRef> {
        self.files.iter().find(|f| f.primary).or_else(|| self.files.first())
    }
}

/// A document version as a bundle lists it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Member {
    /// Index into [`Store::docs`].
    pub doc: usize,
    /// The one file the bundle uses, if not all of them.
    pub file: Option<String>,
}

/// The journal value of a files list; an empty list is stored as no field.
#[must_use]
pub fn files_value(files: &[FileRef]) -> Option<Value> {
    (!files.is_empty()).then(|| {
        files
            .iter()
            .map(|file| {
                serde_json::json!({ "label": file.label, "path": file.path, "primary": file.primary })
            })
            .collect()
    })
}

/// The journal value of a bundles list; an empty list is stored as no field.
#[must_use]
pub fn memberships_value(memberships: &[Membership]) -> Option<Value> {
    (!memberships.is_empty()).then(|| {
        memberships
            .iter()
            .map(|entry| {
                let mut object = serde_json::Map::new();
                object.insert("bundle".into(), entry.bundle.clone().into());
                if let Some(file) = &entry.file {
                    object.insert("file".into(), file.clone().into());
                }
                Value::Object(object)
            })
            .collect()
    })
}

/// The whole browsable store, built once per load.
///
/// `PartialEq`/`Eq` so it can ride inside a [`crate::Msg`], which derives them
/// for the same reason every other message does: a test asserts on messages.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Store {
    /// Documents in shelf order.
    pub docs: Vec<Doc>,
    /// The physical locations.
    pub locations: Tree,
    /// Bundles, newest first; undated ones last, by name.
    pub bundles: Vec<Bundle>,
}

/// Days before expiry that count as expiring.
pub const WARN_DAYS: i64 = 90;

fn string(entity: &Entity, field: &str) -> Option<String> {
    entity.fields.get(field).and_then(Value::as_str).map(str::to_string)
}

fn flag(entity: &Entity, field: &str) -> bool {
    entity.fields.get(field).and_then(Value::as_bool).unwrap_or(false)
}

fn strings(entity: &Entity, field: &str) -> Vec<String> {
    entity
        .fields
        .get(field)
        .and_then(Value::as_array)
        .map(|items| items.iter().filter_map(Value::as_str).map(str::to_string).collect())
        .unwrap_or_default()
}

/// A bare string reads as an entry with no file chosen.
fn memberships(entity: &Entity) -> Vec<Membership> {
    let Some(items) = entity.fields.get("bundles").and_then(Value::as_array) else {
        return Vec::new();
    };
    items
        .iter()
        .filter_map(|item| match item {
            Value::String(bundle) => Some(Membership { bundle: bundle.clone(), file: None }),
            Value::Object(object) => Some(Membership {
                bundle: object.get("bundle").and_then(Value::as_str)?.to_string(),
                file: object.get("file").and_then(Value::as_str).map(str::to_string),
            }),
            _ => None,
        })
        .collect()
}

fn files(entity: &Entity) -> Vec<FileRef> {
    entity
        .fields
        .get("files")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let object = item.as_object()?;
                    Some(FileRef {
                        label: object
                            .get("label")
                            .and_then(Value::as_str)
                            .unwrap_or("")
                            .to_string(),
                        path: object.get("path").and_then(Value::as_str)?.to_string(),
                        primary: object.get("primary").and_then(Value::as_bool).unwrap_or(false),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

/// Which document replaces which, resolved once for one set of documents.
struct Chains {
    /// The document each one replaces, when it is in the set.
    older: Vec<Option<usize>>,
    /// The documents that replace each one, in set order.
    newer: Vec<Vec<usize>>,
}

impl Chains {
    fn new(docs: &[Doc]) -> Self {
        let index: BTreeMap<&str, usize> =
            docs.iter().enumerate().map(|(i, doc)| (doc.id.as_str(), i)).collect();
        let older: Vec<Option<usize>> = docs
            .iter()
            .map(|doc| doc.supersedes.as_deref().and_then(|id| index.get(id).copied()))
            .collect();
        let mut newer = vec![Vec::new(); docs.len()];
        for (i, replaced) in older.iter().enumerate() {
            if let Some(replaced) = replaced {
                newer[*replaced].push(i);
            }
        }
        Self { older, newer }
    }

    /// The oldest version `i` leads back to; in a loop, the last one before
    /// the walk would repeat.
    fn root(&self, i: usize) -> usize {
        let mut at = i;
        let mut seen = BTreeSet::from([i]);
        while let Some(next) = self.older[at] {
            if !seen.insert(next) {
                break;
            }
            at = next;
        }
        at
    }

    /// Whether `i`'s older versions lead back to `i`.
    fn is_loop(&self, i: usize) -> bool {
        let mut at = i;
        let mut seen = BTreeSet::new();
        while let Some(next) = self.older[at] {
            if next == i {
                return true;
            }
            if !seen.insert(next) {
                return false;
            }
            at = next;
        }
        false
    }

    /// `i` and every version made from it, breadth first.
    fn descendants(&self, i: usize) -> Vec<usize> {
        let mut out = Vec::new();
        let mut seen = BTreeSet::new();
        let mut queue = std::collections::VecDeque::from([i]);
        while let Some(at) = queue.pop_front() {
            if seen.insert(at) {
                out.push(at);
                queue.extend(&self.newer[at]);
            }
        }
        out
    }
}

/// Marks each latest version that shares its oldest version with another
/// latest one issued later, the id breaking a tie.
fn mark_conflicts(docs: &mut [Doc]) {
    let chains = Chains::new(docs);
    let mut groups: BTreeMap<usize, Vec<usize>> = BTreeMap::new();
    for (i, doc) in docs.iter().enumerate() {
        if !doc.superseded {
            groups.entry(chains.root(i)).or_default().push(i);
        }
    }
    let mut losers = Vec::new();
    for latest in groups.into_values().filter(|latest| latest.len() > 1) {
        let key = |&i: &usize| (docs[i].issue_date.clone(), docs[i].id.clone());
        let winner = latest.iter().max_by_key(|i| key(i)).copied();
        losers.extend(latest.into_iter().filter(|&i| Some(i) != winner));
    }
    for i in losers {
        docs[i].conflicting = true;
    }
}

impl Store {
    /// Build the browsable store from a folded journal.
    ///
    /// A document with no `name` field is still built — with an empty name — on
    /// purpose: hiding it would make a half-written record invisible instead of
    /// fixable, and `ds status` exists to surface exactly that.
    #[must_use]
    pub fn build(fold: &Fold) -> Self {
        // Superseded-ness is a fact about the collection, not the document:
        // it is true when some *other* document's `supersedes` points here. One
        // pass to collect it, so the per-document check is a set lookup.
        let superseded: BTreeSet<String> =
            fold.kind("doc").filter_map(|(_, entity)| string(entity, "supersedes")).collect();

        let mut bundles: Vec<Bundle> = fold
            .kind("bundle")
            .map(|(id, entity)| Bundle {
                id: id.to_string(),
                name: string(entity, "name").unwrap_or_else(|| id.to_string()),
                date: string(entity, "date"),
                notes: string(entity, "notes").unwrap_or_default(),
            })
            .collect();
        let bundle_names: BTreeMap<&str, &str> =
            bundles.iter().map(|bundle| (bundle.id.as_str(), bundle.name.as_str())).collect();

        let mut docs: Vec<Doc> = fold
            .kind("doc")
            .map(|(id, entity)| {
                let name = string(entity, "name").unwrap_or_default();
                let notes = string(entity, "notes").unwrap_or_default();
                let tags = strings(entity, "tags");
                let bundles = memberships(entity);
                let haystack = crate::search::fold(
                    &[name.as_str(), notes.as_str()]
                        .into_iter()
                        .chain(tags.iter().map(String::as_str))
                        .chain(
                            bundles
                                .iter()
                                .filter_map(|entry| bundle_names.get(entry.bundle.as_str()))
                                .copied(),
                        )
                        .collect::<Vec<_>>()
                        .join(" "),
                );
                Doc {
                    id: id.to_string(),
                    name,
                    tags,
                    bundles,
                    issue_date: string(entity, "issue_date"),
                    expiry_date: string(entity, "expiry_date"),
                    ignore_expiry: flag(entity, "ignore_expiry"),
                    supersedes: string(entity, "supersedes"),
                    location: string(entity, "location"),
                    files: files(entity),
                    notes,
                    superseded: superseded.contains(id),
                    conflicting: false,
                    haystack,
                }
            })
            .collect();

        mark_conflicts(&mut docs);

        let locations = Tree::new(fold.kind("location").map(|(id, entity)| Location {
            id: id.to_string(),
            name: string(entity, "name").unwrap_or_else(|| id.to_string()),
            parent: string(entity, "parent"),
        }));

        let shelf: BTreeMap<&str, usize> =
            locations.shelf().into_iter().enumerate().map(|(rank, id)| (id, rank)).collect();
        let rank = |doc: &Doc| match locations.hard_copy(doc.location.as_deref()) {
            HardCopy::At(id) => shelf.get(id).copied().unwrap_or(usize::MAX),
            HardCopy::Unfiled | HardCopy::DigitalOnly => usize::MAX,
        };
        docs.sort_by_cached_key(|doc| (rank(doc), doc.name.to_lowercase(), doc.id.clone()));
        bundles.sort_by_cached_key(|bundle| {
            (
                bundle.date.is_none(),
                std::cmp::Reverse(bundle.date.clone()),
                bundle.name.to_lowercase(),
                bundle.id.clone(),
            )
        });

        Self { docs, locations, bundles }
    }

    /// How many latest versions conflict with another.
    #[must_use]
    pub fn conflicts(&self) -> usize {
        self.docs.iter().filter(|doc| doc.conflicting).count()
    }

    /// How many records the default list shows.
    #[must_use]
    pub fn listed(&self) -> usize {
        self.docs.iter().filter(|doc| doc.listed()).count()
    }

    /// Where a document's hard copy is.
    #[must_use]
    pub fn hard_copy(&self, doc: &Doc) -> HardCopy<'_> {
        self.locations.hard_copy(doc.location.as_deref())
    }

    /// The location document `id`'s hard copy is filed in, if any.
    #[must_use]
    pub fn filed_at(&self, id: &str) -> Option<&str> {
        match self.hard_copy(self.get(id)?) {
            HardCopy::At(location) => Some(location),
            HardCopy::Unfiled | HardCopy::DigitalOnly => None,
        }
    }

    /// The path of a document's hard copy location, or empty when it has none.
    #[must_use]
    pub fn place(&self, doc: &Doc) -> String {
        match self.hard_copy(doc) {
            HardCopy::At(id) => self.locations.path(id),
            HardCopy::Unfiled | HardCopy::DigitalOnly => String::new(),
        }
    }

    /// How many documents have their hard copy in `id` or anywhere inside it,
    /// old versions included.
    #[must_use]
    pub fn held(&self, id: &str) -> usize {
        self.docs
            .iter()
            .filter(|doc| match self.hard_copy(doc) {
                HardCopy::At(at) => self.locations.is_within(at, id),
                HardCopy::Unfiled | HardCopy::DigitalOnly => false,
            })
            .count()
    }

    /// Documents whose older versions lead back to themselves, so none of
    /// that loop is anyone's latest version.
    #[must_use]
    pub fn version_loops(&self) -> Vec<usize> {
        let chains = Chains::new(&self.docs);
        (0..self.docs.len()).filter(|&i| chains.is_loop(i)).collect()
    }

    /// The document with this id.
    #[must_use]
    pub fn get(&self, id: &str) -> Option<&Doc> {
        self.docs.iter().find(|doc| doc.id == id)
    }

    /// The position of the record with this id.
    #[must_use]
    pub fn index_of(&self, id: &str) -> Option<usize> {
        self.docs.iter().position(|doc| doc.id == id)
    }

    /// Every version of the document `id` belongs to, oldest first.
    ///
    /// Two versions that both replace one older version are both included, so
    /// an offline duplicate stays visible rather than hiding one of them.
    #[must_use]
    pub fn versions(&self, id: &str) -> Vec<usize> {
        let Some(i) = self.index_of(id) else { return Vec::new() };
        let chains = Chains::new(&self.docs);
        chains.descendants(chains.root(i))
    }

    /// The documents `id` may replace without breaking a chain: never itself
    /// or one of its own newer versions, and never one something else replaces.
    #[must_use]
    pub fn renewable(&self, id: &str) -> Vec<usize> {
        let newer = self.index_of(id).map(|i| Chains::new(&self.docs).descendants(i));
        (0..self.docs.len())
            .filter(|&i| !self.docs[i].superseded)
            .filter(|i| !newer.as_ref().is_some_and(|newer| newer.contains(i)))
            .collect()
    }

    /// The bundle with this id.
    #[must_use]
    pub fn bundle(&self, id: &str) -> Option<&Bundle> {
        self.bundles.iter().find(|bundle| bundle.id == id)
    }

    /// The document versions in bundle `id`, in shelf order.
    #[must_use]
    pub fn members(&self, id: &str) -> Vec<Member> {
        self.docs
            .iter()
            .enumerate()
            .flat_map(|(doc, entry)| {
                entry
                    .bundles
                    .iter()
                    .filter(|entry| entry.bundle == id)
                    .map(move |entry| Member { doc, file: entry.file.clone() })
            })
            .collect()
    }

    /// The bundles a document version is in, skipping entries whose bundle
    /// was deleted.
    pub fn bundles_of<'a>(&'a self, doc: &'a Doc) -> impl Iterator<Item = &'a Bundle> + 'a {
        doc.bundles.iter().filter_map(|entry| self.bundle(&entry.bundle))
    }

    /// Row indices matching `query`, in list order.
    ///
    /// Exact pass first; the fuzzy pass runs only if the exact one came up empty
    /// **and** some term is long enough to forgive an edit — so a precise hit is
    /// never displaced by a forgiving one (§8, v2's contract).
    #[must_use]
    pub fn search(&self, query: &str) -> Vec<usize> {
        let exact: Vec<usize> = self
            .docs
            .iter()
            .enumerate()
            .filter_map(|(i, doc)| crate::search::matches(&doc.haystack, query, false).then_some(i))
            .collect();
        if !exact.is_empty() || !crate::search::can_fuzz(query) {
            return exact;
        }
        self.docs
            .iter()
            .enumerate()
            .filter_map(|(i, doc)| crate::search::matches(&doc.haystack, query, true).then_some(i))
            .collect()
    }

    /// Documents expired or due inside the warn window, soonest first: the
    /// header's count and the expiring filter both, so they always agree.
    ///
    /// Superseded and ignored documents are out of the watch, which is the
    /// whole point of an opt-out watch: a renewal removes the old document
    /// without anyone re-starring anything.
    #[must_use]
    pub fn due(&self, today: &str, warn_until: &str) -> Vec<usize> {
        let mut rows: Vec<usize> = self
            .docs
            .iter()
            .enumerate()
            .filter(|(_, doc)| {
                matches!(doc.status(today, warn_until), Status::Expired | Status::Soon)
            })
            .map(|(i, _)| i)
            .collect();
        rows.sort_by(|&a, &b| {
            self.docs[a]
                .expiry_date
                .cmp(&self.docs[b].expiry_date)
                .then_with(|| self.docs[a].name.cmp(&self.docs[b].name))
        });
        rows
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use journal::{fold as fold_lines, parse_line, Line};

    /// One op as a tuple: `(ts, op, ent, id, field, value)`. Named because
    /// these tests read as tables, and a builder would bury that.
    type OwnedOp = (i64, String, String, String, String, Value);

    /// Build a fold from `(ts, op, ent, id, field, value)` tuples.
    fn store(ops: &[(i64, &str, &str, &str, &str, Value)]) -> Store {
        let lines: Vec<Line> = ops
            .iter()
            .map(|(ts, op, ent, id, field, value)| {
                let mut object = serde_json::Map::new();
                object.insert("v".into(), Value::from(1));
                object.insert("ts".into(), Value::from(*ts));
                object.insert("w".into(), Value::from("desk-core"));
                object.insert("op".into(), Value::from(*op));
                object.insert("ent".into(), Value::from(*ent));
                object.insert("id".into(), Value::from(*id));
                if !field.is_empty() {
                    object.insert("f".into(), Value::from(*field));
                }
                if !value.is_null() {
                    object.insert("val".into(), value.clone());
                }
                parse_line(&serde_json::to_string(&Value::Object(object)).unwrap())
            })
            .collect();
        Store::build(&fold_lines(&lines))
    }

    fn doc(ts: i64, id: &str, fields: &[(&str, Value)]) -> Vec<OwnedOp> {
        entity(ts, "doc", id, fields)
    }

    fn entity(ts: i64, ent: &str, id: &str, fields: &[(&str, Value)]) -> Vec<OwnedOp> {
        let mut ops = vec![(
            ts,
            "create".to_string(),
            ent.to_string(),
            id.to_string(),
            String::new(),
            Value::Null,
        )];
        for (i, (field, value)) in fields.iter().enumerate() {
            ops.push((
                ts + 1 + i64::try_from(i).expect("test fixtures are small"),
                "set".to_string(),
                ent.to_string(),
                id.to_string(),
                (*field).to_string(),
                value.clone(),
            ));
        }
        ops
    }

    fn build(all: Vec<Vec<OwnedOp>>) -> Store {
        let flat: Vec<OwnedOp> = all.into_iter().flatten().collect();
        let refs: Vec<(i64, &str, &str, &str, &str, Value)> = flat
            .iter()
            .map(|(ts, op, ent, id, f, v)| {
                (*ts, op.as_str(), ent.as_str(), id.as_str(), f.as_str(), v.clone())
            })
            .collect();
        store(&refs)
    }

    /// **Every field survives a round trip through the journal**, which is what
    /// makes a delete undoable: §3.2's `create`-after-tombstone starts from
    /// empty, so restoring a document means re-sending everything it had.
    ///
    /// The fixture is written as an **exhaustive struct literal on purpose** —
    /// no `..Default::default()`. A field added to `Doc` will not compile here
    /// until somebody decides what it round-trips as, which is the only way this
    /// test can keep catching the bug it exists for: a field mapped on the way
    /// in, forgotten on the way out, and silently lost the first time anyone
    /// undoes a deletion.
    #[test]
    fn a_document_survives_a_round_trip_through_its_own_fields() {
        let original = Doc {
            id: "coc".into(),
            name: "COC Certificate".into(),
            tags: vec!["marine".into(), "ticket".into()],
            bundles: vec![
                Membership { bundle: "sea-service".into(), file: Some("Marine/coc-b.pdf".into()) },
                Membership { bundle: "joining".into(), file: None },
            ],
            issue_date: Some("2021-09-28".into()),
            expiry_date: Some("2026-09-28".into()),
            ignore_expiry: true,
            supersedes: Some("coc-2019".into()),
            location: Some("cert-file".into()),
            files: vec![
                FileRef { label: "complete".into(), path: "Marine/coc.pdf".into(), primary: true },
                FileRef { label: "back".into(), path: "Marine/coc-b.pdf".into(), primary: false },
            ],
            notes: "the one with the stamp".into(),
            // Derived, never stored: `superseded` is a fact about the
            // collection and `haystack` is built from the fields above. Neither
            // bundle exists here, so their names are not in it.
            superseded: false,
            conflicting: false,
            haystack: crate::search::fold("COC Certificate the one with the stamp marine ticket"),
        };

        let fields: Vec<(&str, Value)> = original.as_fields();
        let rebuilt = build(vec![doc(100, "coc", &fields)]);
        assert_eq!(rebuilt.docs.len(), 1);
        assert_eq!(rebuilt.docs[0], original, "a field was mapped in but not back out");
    }

    /// A bundle and a location survive a round trip through the journal too.
    #[test]
    fn bundles_and_locations_round_trip() {
        let bundle = Bundle {
            id: "trip".into(),
            name: "Trip".into(),
            date: Some("2027-01-05".into()),
            notes: "visa".into(),
        };
        let place = crate::Location {
            id: "pouch".into(),
            name: "Pouch".into(),
            parent: Some("desk".into()),
        };
        let rebuilt = build(vec![
            entity(100, "bundle", "trip", &bundle.as_fields()),
            entity(200, "location", "desk", &[("name", "Desk".into())]),
            entity(300, "location", "pouch", &place.as_fields()),
        ]);
        assert_eq!(rebuilt.bundles, [bundle]);
        assert_eq!(rebuilt.locations.get("pouch"), Some(&place));
    }

    fn named(ts: i64, id: &str, name: &str, more: &[(&str, Value)]) -> Vec<OwnedOp> {
        let mut fields = vec![("name", Value::from(name))];
        fields.extend(more.iter().cloned());
        doc(ts, id, &fields)
    }

    fn ids(store: &Store, indices: &[usize]) -> Vec<String> {
        indices.iter().map(|&i| store.docs[i].id.clone()).collect()
    }

    fn bundle(ts: i64, id: &str, name: &str, date: Option<&str>) -> Vec<OwnedOp> {
        let mut ops = vec![
            (ts, "create".into(), "bundle".into(), id.into(), String::new(), Value::Null),
            (ts + 1, "set".into(), "bundle".into(), id.into(), "name".into(), name.into()),
        ];
        if let Some(date) = date {
            ops.push((
                ts + 2,
                "set".into(),
                "bundle".into(),
                id.into(),
                "date".into(),
                date.into(),
            ));
        }
        ops
    }

    /// A bundle is its own record, never a document.
    #[test]
    fn a_bundle_is_not_a_document() {
        let store = build(vec![bundle(100, "joining", "Joining", Some("2026-11-01"))]);
        assert!(store.docs.is_empty(), "{:?}", store.docs);
        let joining = store.bundle("joining").expect("built");
        assert_eq!(joining.name, "Joining");
        assert_eq!(joining.date.as_deref(), Some("2026-11-01"));
    }

    /// Bundles run newest first, and undated ones come last by name.
    #[test]
    fn bundles_run_newest_first_and_undated_last() {
        let store = build(vec![
            bundle(100, "old", "Old trip", Some("2025-03-01")),
            bundle(110, "zeta", "Zeta", None),
            bundle(120, "new", "New trip", Some("2026-09-01")),
            bundle(130, "alpha", "alpha", None),
        ]);
        let order: Vec<&str> = store.bundles.iter().map(|b| b.id.as_str()).collect();
        assert_eq!(order, ["new", "old", "alpha", "zeta"]);
    }

    /// A bare bundle id in the list reads as an entry with no file chosen.
    #[test]
    fn a_bare_bundle_entry_reads_as_the_whole_version() {
        let store =
            build(vec![named(100, "coc", "COC", &[("bundles", serde_json::json!(["joining"]))])]);
        assert_eq!(store.docs[0].bundles, [Membership { bundle: "joining".into(), file: None }]);
    }

    /// Versions come back oldest first, and only the newest is listed.
    #[test]
    fn versions_run_oldest_first_and_only_the_latest_is_listed() {
        let store = build(vec![
            named(100, "pp-2009", "Passport (IN)", &[]),
            named(200, "pp-2019", "Passport (IN)", &[("supersedes", "pp-2009".into())]),
            named(300, "pp-2029", "Passport (IN)", &[("supersedes", "pp-2019".into())]),
        ]);
        let i = store.index_of("pp-2019").unwrap();
        assert_eq!(ids(&store, &store.versions("pp-2019")), ["pp-2009", "pp-2019", "pp-2029"]);
        assert!(!store.docs[i].listed());
        assert!(store.docs[store.index_of("pp-2029").unwrap()].listed());
        assert_eq!(store.listed(), 1);
    }

    /// Versions that replace each other in a loop are found, and a newer
    /// version replacing one of them is not part of the loop.
    #[test]
    fn a_loop_of_versions_is_found() {
        let store = build(vec![
            named(100, "a", "Passport", &[("supersedes", "b".into())]),
            named(200, "b", "Passport", &[("supersedes", "a".into())]),
            named(300, "c", "Passport", &[("supersedes", "a".into())]),
            named(400, "d", "Visa", &[]),
        ]);
        let mut found = ids(&store, &store.version_loops());
        found.sort();
        assert_eq!(found, ["a", "b"]);
    }

    /// Two versions replacing one older version are both kept and both listed —
    /// an offline duplicate stays visible until it is merged.
    #[test]
    fn two_versions_of_one_document_are_both_latest() {
        let store = build(vec![
            named(100, "pp", "Passport", &[]),
            named(200, "pp-desk", "Passport", &[("supersedes", "pp".into())]),
            named(300, "pp-phone", "Passport", &[("supersedes", "pp".into())]),
        ]);
        let versions = ids(&store, &store.versions("pp-phone"));
        assert_eq!(versions.len(), 3);
        assert_eq!(store.listed(), 2);
    }

    /// Of two latest versions, the later-issued one is the latest and the
    /// other conflicts, however far down the chain they branch.
    #[test]
    fn the_earlier_issued_of_two_latest_versions_conflicts() {
        let store = build(vec![
            named(100, "pp", "Passport", &[]),
            named(110, "pp-2", "Passport", &[("supersedes", "pp".into())]),
            named(
                200,
                "pp-desk",
                "Passport",
                &[("supersedes", "pp-2".into()), ("issue_date", "2026-02-10".into())],
            ),
            named(
                300,
                "pp-phone",
                "Passport",
                &[("supersedes", "pp".into()), ("issue_date", "2026-01-05".into())],
            ),
            named(400, "coc", "COC", &[]),
        ]);
        let conflicting: Vec<&str> =
            store.docs.iter().filter(|d| d.conflicting).map(|d| d.id.as_str()).collect();
        assert_eq!(conflicting, ["pp-phone"]);
        assert_eq!(store.conflicts(), 1);
    }

    /// A cycle of versions cannot hang the walk.
    #[test]
    fn a_cycle_of_versions_terminates() {
        let store = build(vec![
            named(100, "a", "A", &[("supersedes", "b".into())]),
            named(200, "b", "B", &[("supersedes", "a".into())]),
        ]);
        assert_eq!(store.versions("a").len(), 2);
    }

    /// **A bundle holds exact versions**: a newer version is not a member,
    /// and an entry whose bundle was deleted reads as nothing.
    #[test]
    fn a_bundle_holds_the_exact_version_it_was_given() {
        let store = build(vec![
            bundle(100, "joining", "Joining Documents", None),
            named(110, "pp-2019", "Passport (IN)", &[("bundles", serde_json::json!(["joining"]))]),
            named(120, "pp-2029", "Passport (IN)", &[("supersedes", "pp-2019".into())]),
            named(
                130,
                "coc",
                "COC",
                &[(
                    "bundles",
                    serde_json::json!([
                        {"bundle": "joining", "file": "coc-front.pdf"},
                        {"bundle": "gone"},
                    ]),
                )],
            ),
        ]);
        let members: Vec<(String, Option<String>)> = store
            .members("joining")
            .into_iter()
            .map(|m| (store.docs[m.doc].id.clone(), m.file))
            .collect();
        assert_eq!(
            members,
            [("coc".into(), Some("coc-front.pdf".into())), ("pp-2019".into(), None)],
            "the old passport stays; its newer version is not added"
        );
        let coc = &store.docs[store.index_of("coc").unwrap()];
        let names: Vec<&str> = store.bundles_of(coc).map(|b| b.name.as_str()).collect();
        assert_eq!(names, ["Joining Documents"], "the deleted bundle reads as nothing");
    }

    /// A version is found by the name of a bundle it is in, and only that
    /// version: the bundle does not follow to a newer one.
    #[test]
    fn search_finds_a_version_by_its_bundle() {
        let store = build(vec![
            bundle(100, "joining", "Joining Documents", None),
            named(110, "pp-2019", "Passport", &[("bundles", serde_json::json!(["joining"]))]),
            named(120, "pp-2029", "Passport", &[("supersedes", "pp-2019".into())]),
        ]);
        assert_eq!(ids(&store, &store.search("joining")), ["pp-2019"]);
    }

    /// The expiry watch covers latest versions only, though each version keeps
    /// its own date.
    #[test]
    fn the_watch_covers_latest_documents_only() {
        let store = build(vec![
            named(110, "pp-2019", "Passport", &[("expiry_date", "2029-01-01".into())]),
            named(
                120,
                "pp-2029",
                "Passport",
                &[("supersedes", "pp-2019".into()), ("expiry_date", "2039-01-01".into())],
            ),
        ]);
        assert_eq!(ids(&store, &store.due("2026-10-20", "2040-01-01")), ["pp-2029"]);
        let old = &store.docs[store.index_of("pp-2019").unwrap()];
        assert_eq!(old.expiry_date.as_deref(), Some("2029-01-01"));
    }

    /// A location reads its name and parent; one with no name shows its id.
    #[test]
    fn a_location_reads_its_name_and_parent() {
        let ops = [
            (100, "create", "location", "desk", "", Value::Null),
            (101, "set", "location", "desk", "name", Value::from("desk")),
            (102, "create", "location", "folder", "", Value::Null),
            (103, "set", "location", "folder", "parent", Value::from("desk")),
        ];
        let tree = store(&ops).locations;
        assert_eq!(tree.get("folder").map(|l| l.name.as_str()), Some("folder"));
        assert_eq!(tree.parent("folder"), Some("desk"));
    }

    /// A document with nothing but a name round-trips too — the absent fields
    /// stay absent rather than coming back as empty strings.
    #[test]
    fn an_empty_document_round_trips_without_inventing_fields() {
        let fields = Doc {
            id: "bare".into(),
            name: "Bare".into(),
            tags: Vec::new(),
            bundles: Vec::new(),
            issue_date: None,
            expiry_date: None,
            ignore_expiry: false,
            supersedes: None,
            location: None,
            files: Vec::new(),
            notes: String::new(),
            superseded: false,
            conflicting: false,
            haystack: crate::search::fold("Bare"),
        }
        .as_fields();
        assert_eq!(fields.len(), 1, "only the name: {fields:?}");

        let rebuilt = build(vec![doc(100, "bare", &fields)]);
        assert_eq!(rebuilt.docs[0].location, None);
        assert!(rebuilt.docs[0].files.is_empty(), "{:?}", rebuilt.docs[0].files);
    }

    fn location(ts: i64, id: &str, name: &str, parent: Option<&str>) -> Vec<OwnedOp> {
        let mut ops = vec![
            (ts, "create".into(), "location".into(), id.into(), String::new(), Value::Null),
            (ts + 1, "set".into(), "location".into(), id.into(), "name".into(), name.into()),
        ];
        if let Some(parent) = parent {
            ops.push((
                ts + 2,
                "set".into(),
                "location".into(),
                id.into(),
                "parent".into(),
                parent.into(),
            ));
        }
        ops
    }

    fn filed(ts: i64, id: &str, name: &str, at: Option<&str>) -> Vec<OwnedOp> {
        let mut fields = vec![("name", Value::from(name))];
        if let Some(at) = at {
            fields.push(("location", at.into()));
        }
        doc(ts, id, &fields)
    }

    /// **Shelf order** follows the location tree in sibling order, then the
    /// name; unfiled and digital-only documents come last.
    #[test]
    fn documents_sort_in_shelf_order() {
        let s = build(vec![
            location(10, "cert", "cert file", None),
            location(20, "s8", "slot 8", Some("cert")),
            location(30, "s3", "slot 3", Some("cert")),
            location(40, "blue", "blue folder", None),
            filed(100, "b", "B", Some("s8")),
            filed(200, "a", "A", Some("s3")),
            filed(300, "z", "Z", Some(crate::place::DIGITAL_ONLY)),
            filed(400, "c", "C", Some("blue")),
            filed(500, "y", "Y", None),
            filed(600, "x", "X", Some("cert")),
        ]);
        let order: Vec<&str> = s.docs.iter().map(|d| d.id.as_str()).collect();
        assert_eq!(order, ["c", "x", "a", "b", "y", "z"]);
    }

    /// Inside one location the name breaks ties, then the id.
    #[test]
    fn name_then_id_break_the_tie() {
        let s = build(vec![
            location(10, "f", "f", None),
            filed(100, "second", "Zulu", Some("f")),
            filed(200, "third", "Alpha", Some("f")),
            filed(300, "first", "Alpha", Some("f")),
        ]);
        let order: Vec<&str> = s.docs.iter().map(|d| d.id.as_str()).collect();
        assert_eq!(order, ["first", "third", "second"]);
    }

    /// A count of hard copies takes in everything inside a location, old
    /// versions included.
    #[test]
    fn a_location_counts_every_hard_copy_inside_it() {
        let s = build(vec![
            location(10, "desk", "desk", None),
            location(20, "folder", "leather folder", Some("desk")),
            filed(100, "old", "Passport", Some("folder")),
            doc(
                200,
                "new",
                &[
                    ("name", "Passport".into()),
                    ("supersedes", "old".into()),
                    ("location", "desk".into()),
                ],
            ),
        ]);
        assert_eq!(s.held("desk"), 2);
        assert_eq!(s.held("folder"), 1);
        assert_eq!(s.place(&s.docs[s.index_of("old").unwrap()]), "desk › leather folder");
    }

    /// **The watch is opt-out.** A renewal removes the old document from it
    /// automatically — nobody re-stars anything.
    #[test]
    fn superseded_and_ignored_documents_leave_the_watch() {
        let s = build(vec![
            doc(
                100,
                "coc-2019",
                &[("name", "COC 2019".into()), ("expiry_date", "2026-09-28".into())],
            ),
            doc(
                200,
                "coc-2025",
                &[
                    ("name", "COC 2025".into()),
                    ("expiry_date", "2030-01-01".into()),
                    ("supersedes", "coc-2019".into()),
                ],
            ),
            doc(
                300,
                "old-cdc",
                &[
                    ("name", "Old CDC".into()),
                    ("expiry_date", "2026-01-01".into()),
                    ("ignore_expiry", true.into()),
                ],
            ),
            doc(400, "eng1", &[("name", "ENG-1".into()), ("expiry_date", "2027-01-13".into())]),
        ]);
        let due: Vec<&str> =
            s.due("2026-10-20", "2027-01-18").into_iter().map(|i| s.docs[i].id.as_str()).collect();
        assert_eq!(due, ["eng1"], "superseded, ignored and not-yet-due are all out");

        let by_id = |id: &str| s.docs.iter().find(|d| d.id == id).unwrap();
        assert!(by_id("coc-2019").superseded);
        assert_eq!(by_id("coc-2019").status("2026-10-20", "2027-01-18"), Status::Untracked);
    }

    /// Status is the expiry against today and the warn window, and every state
    /// has a marker — colour is never the only signal.
    #[test]
    fn status_classifies_against_today_and_the_window() {
        let s = build(vec![
            doc(100, "past", &[("name", "Past".into()), ("expiry_date", "2026-09-28".into())]),
            doc(200, "soon", &[("name", "Soon".into()), ("expiry_date", "2026-12-01".into())]),
            doc(300, "far", &[("name", "Far".into()), ("expiry_date", "2031-01-01".into())]),
            doc(400, "never", &[("name", "Never".into())]),
        ]);
        let by_id = |id: &str| s.docs.iter().find(|d| d.id == id).unwrap();
        let (today, warn_until) = ("2026-10-20", "2027-01-18");
        assert_eq!(by_id("past").status(today, warn_until), Status::Expired);
        assert_eq!(by_id("soon").status(today, warn_until), Status::Soon);
        assert_eq!(by_id("far").status(today, warn_until), Status::Ok);
        assert_eq!(by_id("never").status(today, warn_until), Status::Untracked);
        for status in [Status::Expired, Status::Soon, Status::Ok, Status::Untracked] {
            assert_eq!(status.marker().chars().count(), 1);
        }
    }

    /// The place column is the hard copy's path, and empty when there is none.
    #[test]
    fn the_place_column_reads_as_the_path() {
        let s = build(vec![
            location(10, "cert", "cert file", None),
            location(20, "s8", "slot 8", Some("cert")),
            filed(100, "a", "A", Some("s8")),
            filed(200, "c", "C", Some(crate::place::DIGITAL_ONLY)),
            filed(300, "d", "D", None),
            filed(400, "e", "E", Some("deleted")),
        ]);
        let place = |id: &str| s.place(&s.docs[s.index_of(id).unwrap()]);
        assert_eq!(place("a"), "cert file › slot 8");
        assert_eq!(place("c"), "");
        assert_eq!(place("d"), "");
        assert_eq!(place("e"), "");
    }

    /// **Enter never dies.** With no file linked there is nothing to open, and
    /// the caller falls through to the record (invariant 2).
    #[test]
    fn the_primary_file_is_the_one_enter_opens() {
        let s = build(vec![doc(
            100,
            "a",
            &[
                ("name", "A".into()),
                (
                    "files",
                    serde_json::json!([
                        {"label": "front", "path": "Scans/a-front.jpg", "primary": false},
                        {"label": "complete", "path": "Scans/a.pdf", "primary": true}
                    ]),
                ),
            ],
        )]);
        assert_eq!(s.docs[0].primary_file().unwrap().path, "Scans/a.pdf");

        let bare = build(vec![doc(100, "b", &[("name", "B".into())])]);
        assert!(bare.docs[0].primary_file().is_none());
    }

    /// Search runs over name, notes, tags and bundles — and an exact hit is
    /// never displaced by a fuzzy one.
    #[test]
    fn search_covers_the_whole_record_and_prefers_exact() {
        let s = build(vec![
            doc(
                100,
                "coc",
                &[("name", "COC Certificate".into()), ("tags", serde_json::json!(["marine"]))],
            ),
            doc(200, "eng1", &[("name", "ENG-1 Medical".into()), ("notes", "MMD Mumbai".into())]),
            bundle(250, "bike-transfer", "bike transfer", None),
            doc(
                300,
                "bike",
                &[("name", "Insurance".into()), ("bundles", serde_json::json!(["bike-transfer"]))],
            ),
        ]);
        let ids = |q: &str| -> Vec<String> {
            s.search(q).into_iter().map(|i| s.docs[i].id.clone()).collect()
        };
        assert_eq!(ids("marine"), ["coc"], "tags are searchable");
        assert_eq!(ids("mumbai"), ["eng1"], "notes are searchable");
        assert_eq!(ids("transfer"), ["bike"], "bundle names are searchable");
        assert_eq!(ids("certificate"), ["coc"]);
        assert_eq!(ids("").len(), 3, "an empty query is the whole list");
    }

    /// The fuzzy pass only runs when the exact one found nothing.
    #[test]
    fn a_typo_falls_back_but_a_hit_does_not() {
        let s = build(vec![
            doc(100, "medical", &[("name", "ENG-1 Medical".into())]),
            doc(200, "mechanic", &[("name", "Mechanical Survey".into())]),
        ]);
        let ids = |q: &str| -> Vec<String> {
            s.search(q).into_iter().map(|i| s.docs[i].id.clone()).collect()
        };
        assert_eq!(ids("medical"), ["medical"], "an exact hit stands alone");
        assert_eq!(ids("medicla"), ["medical"], "the typo falls back to fuzzy");
        assert!(ids("zzzz").is_empty(), "{:?}", ids("zzzz"));
    }

    /// Locations come through the same fold as documents.
    #[test]
    fn locations_are_read_from_the_fold() {
        let s = store(&[
            (10, "create", "location", "cert-file", "", Value::Null),
            (11, "set", "location", "cert-file", "name", "Cert File".into()),
        ]);
        assert_eq!(s.locations.get("cert-file").map(|l| l.name.as_str()), Some("Cert File"));
    }

    /// A document with no name is still built — hiding it would make a
    /// half-written record invisible instead of fixable.
    #[test]
    fn a_nameless_document_still_appears() {
        let s = build(vec![doc(100, "orphan", &[("location", "cert-file".into())])]);
        assert_eq!(s.docs.len(), 1);
        assert_eq!(s.docs[0].name, "");
    }
}
