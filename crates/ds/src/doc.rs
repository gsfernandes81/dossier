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
//! names like `perm_location`.
//!
//! Two rules from the plan are implemented here rather than in the renderer,
//! because they are facts about the data and not about the screen:
//!
//! * **Shelf order** (REWRITE-UI.md §1): location → slot → subslot → name, with
//!   every tiebreaker explicit so the list never jitters between frames. U2
//!   drops the location *headers*, not the ordering they used to imply.
//! * **The expiry watch is opt-out** (DESIGN §14): a document is tracked if it
//!   has an expiry date and is neither superseded by a newer document nor
//!   explicitly ignored. Being superseded is a *collection-level* fact — some
//!   other document's `supersedes` points here — so it can only be computed with
//!   the whole store in hand, which is why [`Store::build`] does it once.

use std::collections::{BTreeMap, BTreeSet};

use journal::{Entity, Fold};
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
    /// Inside the warn window (default 90 days, a synced setting).
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

/// A document as the browse surface needs it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Doc {
    /// Slug; the journal entity id.
    pub id: String,
    /// Display name — the left column and the main search target.
    pub name: String,
    /// Flat tags (hierarchical tags are dropped, §8).
    pub tags: Vec<String>,
    /// Bundle slugs this document belongs to.
    pub bundles: Vec<String>,
    /// ISO issue date.
    pub issue_date: Option<String>,
    /// ISO expiry date.
    pub expiry_date: Option<String>,
    /// Opt out of the expiry watch (the residual-noise escape hatch).
    pub ignore_expiry: bool,
    /// The id of the document this one replaces.
    pub supersedes: Option<String>,
    /// Location slug — where it physically lives, or `softcopy`.
    pub location: Option<String>,
    /// Slot within the location.
    pub slot: Option<u32>,
    /// Subslot within the slot.
    pub subslot: Option<u32>,
    /// Linked files.
    pub files: Vec<FileRef>,
    /// Free text.
    pub notes: String,
    /// Whether some *other* document supersedes this one.
    pub superseded: bool,
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
        self.expiry_date.is_some() && !self.superseded && !self.ignore_expiry
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
        push("bundles", (!self.bundles.is_empty()).then(|| self.bundles.clone().into()));
        push("issue_date", self.issue_date.clone().map(Into::into));
        push("expiry_date", self.expiry_date.clone().map(Into::into));
        push("ignore_expiry", self.ignore_expiry.then(|| true.into()));
        push("supersedes", self.supersedes.clone().map(Into::into));
        push("perm_location", self.location.clone().map(Into::into));
        push("perm_slot", self.slot.map(Into::into));
        push("perm_subslot", self.subslot.map(Into::into));
        push(
            "files",
            (!self.files.is_empty()).then(|| {
                Value::Array(
                    self.files
                        .iter()
                        .map(|file| {
                            serde_json::json!({
                                "label": file.label,
                                "path": file.path,
                                "primary": file.primary,
                            })
                        })
                        .collect(),
                )
            }),
        );
        fields
    }

    /// `cert-file 8` / `cert-file 8.2` / `softcopy` — the dim right-hand column.
    #[must_use]
    pub fn place(&self) -> String {
        let Some(location) = &self.location else { return String::new() };
        match (self.slot, self.subslot) {
            (Some(slot), Some(sub)) => format!("{location} {slot}.{sub}"),
            (Some(slot), None) => format!("{location} {slot}"),
            _ => location.clone(),
        }
    }

    /// The file to open when `Enter` is pressed: the primary if one is marked,
    /// else the first. `None` means `Enter` falls through to the record —
    /// invariant 2, and the reason that verb can never fail.
    #[must_use]
    pub fn primary_file(&self) -> Option<&FileRef> {
        self.files.iter().find(|f| f.primary).or_else(|| self.files.first())
    }
}

/// A physical location: a folder, a pouch, a file box.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Location {
    /// Slug — how documents reference it.
    pub id: String,
    /// Display name.
    pub title: String,
    /// Free text.
    pub notes: String,
}

/// The whole browsable store, built once per load.
///
/// `PartialEq`/`Eq` so it can ride inside a [`crate::Msg`], which derives them
/// for the same reason every other message does: a test asserts on messages.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Store {
    /// Documents in shelf order.
    pub docs: Vec<Doc>,
    /// Locations by slug.
    pub locations: BTreeMap<String, Location>,
    /// Synced settings, as folded (`expiry_threshold_days`, scope globs, …).
    pub settings: BTreeMap<String, Value>,
}

/// Default warn window, matching v2's `DEFAULT_EXPIRY_THRESHOLD_DAYS`.
pub const DEFAULT_WARN_DAYS: i64 = 90;

fn string(entity: &Entity, field: &str) -> Option<String> {
    entity.fields.get(field).and_then(Value::as_str).map(str::to_string)
}

fn number(entity: &Entity, field: &str) -> Option<u32> {
    entity.fields.get(field).and_then(Value::as_u64).and_then(|n| u32::try_from(n).ok())
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

impl Store {
    /// Build the browsable store from a folded journal.
    ///
    /// A document with no `name` field is still built — with an empty name — on
    /// purpose: hiding it would make a half-written record invisible instead of
    /// fixable, and the review surface exists to surface exactly that.
    #[must_use]
    pub fn build(fold: &Fold) -> Self {
        // Superseded-ness is a fact about the collection, not the document:
        // it is true when some *other* document's `supersedes` points here. One
        // pass to collect it, so the per-document check is a set lookup.
        let superseded: BTreeSet<String> =
            fold.kind("doc").filter_map(|(_, entity)| string(entity, "supersedes")).collect();

        let mut docs: Vec<Doc> = fold
            .kind("doc")
            .map(|(id, entity)| {
                let name = string(entity, "name").unwrap_or_default();
                let notes = string(entity, "notes").unwrap_or_default();
                let tags = strings(entity, "tags");
                let bundles = strings(entity, "bundles");
                let haystack = crate::search::fold(
                    &[name.as_str(), notes.as_str()]
                        .into_iter()
                        .chain(tags.iter().map(String::as_str))
                        .chain(bundles.iter().map(String::as_str))
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
                    location: string(entity, "perm_location"),
                    slot: number(entity, "perm_slot"),
                    subslot: number(entity, "perm_subslot"),
                    files: files(entity),
                    notes,
                    superseded: superseded.contains(id),
                    haystack,
                }
            })
            .collect();

        // Shelf order, with every tiebreaker spelled out. Documents with no
        // location sort last — they are the softcopy-only ones, and putting them
        // first would push the physical shelf off the top of a phone screen.
        docs.sort_by(|a, b| {
            let key = |d: &Doc| {
                (
                    d.location.is_none(),
                    d.location.clone().unwrap_or_default(),
                    d.slot,
                    d.subslot,
                    d.name.to_lowercase(),
                    d.id.clone(),
                )
            };
            key(a).cmp(&key(b))
        });

        let locations = fold
            .kind("location")
            .map(|(id, entity)| {
                (
                    id.to_string(),
                    Location {
                        id: id.to_string(),
                        title: string(entity, "title").unwrap_or_else(|| id.to_string()),
                        notes: string(entity, "notes").unwrap_or_default(),
                    },
                )
            })
            .collect();

        let settings =
            fold.get("settings", "synced").map(|entity| entity.fields.clone()).unwrap_or_default();

        Self { docs, locations, settings }
    }

    /// The warn window in days, from synced settings.
    #[must_use]
    pub fn warn_days(&self) -> i64 {
        self.settings
            .get("expiry_threshold_days")
            .and_then(Value::as_i64)
            .unwrap_or(DEFAULT_WARN_DAYS)
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

    /// Documents in the expiry watch, soonest first — the `:expiring` filter.
    ///
    /// Superseded and ignored documents are hidden, which is the whole point of
    /// an opt-out watch: a renewal removes the old document from the list
    /// without anyone re-starring anything.
    #[must_use]
    pub fn expiring(&self) -> Vec<usize> {
        let mut rows: Vec<usize> = self
            .docs
            .iter()
            .enumerate()
            .filter_map(|(i, doc)| doc.is_tracked().then_some(i))
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
        let mut ops = vec![(
            ts,
            "create".to_string(),
            "doc".to_string(),
            id.to_string(),
            String::new(),
            Value::Null,
        )];
        for (i, (field, value)) in fields.iter().enumerate() {
            ops.push((
                ts + 1 + i64::try_from(i).expect("test fixtures are small"),
                "set".to_string(),
                "doc".to_string(),
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
            bundles: vec!["sea-service".into()],
            issue_date: Some("2021-09-28".into()),
            expiry_date: Some("2026-09-28".into()),
            ignore_expiry: true,
            supersedes: Some("coc-2019".into()),
            location: Some("cert-file".into()),
            slot: Some(8),
            subslot: Some(2),
            files: vec![
                FileRef { label: "complete".into(), path: "Marine/coc.pdf".into(), primary: true },
                FileRef { label: "back".into(), path: "Marine/coc-b.pdf".into(), primary: false },
            ],
            notes: "the one with the stamp".into(),
            // Derived, never stored: `superseded` is a fact about the
            // collection and `haystack` is built from the fields above.
            superseded: false,
            haystack: crate::search::fold(
                "COC Certificate the one with the stamp marine ticket sea-service",
            ),
        };

        let fields: Vec<(&str, Value)> = original.as_fields();
        let rebuilt = build(vec![doc(100, "coc", &fields)]);
        assert_eq!(rebuilt.docs.len(), 1);
        assert_eq!(rebuilt.docs[0], original, "a field was mapped in but not back out");
    }

    /// A document with nothing but a name round-trips too — the absent fields
    /// stay absent rather than coming back as empty strings and zero slots.
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
            slot: None,
            subslot: None,
            files: Vec::new(),
            notes: String::new(),
            superseded: false,
            haystack: crate::search::fold("Bare"),
        }
        .as_fields();
        assert_eq!(fields.len(), 1, "only the name: {fields:?}");

        let rebuilt = build(vec![doc(100, "bare", &fields)]);
        assert_eq!(rebuilt.docs[0].location, None);
        assert_eq!(rebuilt.docs[0].slot, None);
        assert!(rebuilt.docs[0].files.is_empty());
    }

    /// **Shelf order, with explicit tiebreakers.** U2 drops the location
    /// headers; it does not drop the ordering they implied — the list still
    /// reads in the order the documents physically sit.
    #[test]
    fn documents_sort_in_shelf_order() {
        let s = build(vec![
            doc(
                100,
                "b",
                &[
                    ("name", "B".into()),
                    ("perm_location", "cert-file".into()),
                    ("perm_slot", 8.into()),
                ],
            ),
            doc(
                200,
                "a",
                &[
                    ("name", "A".into()),
                    ("perm_location", "cert-file".into()),
                    ("perm_slot", 3.into()),
                ],
            ),
            doc(300, "z", &[("name", "Z softcopy".into())]),
            doc(
                400,
                "c",
                &[
                    ("name", "C".into()),
                    ("perm_location", "blue-folder".into()),
                    ("perm_slot", 1.into()),
                ],
            ),
        ]);
        let order: Vec<&str> = s.docs.iter().map(|d| d.id.as_str()).collect();
        assert_eq!(
            order,
            ["c", "a", "b", "z"],
            "blue-folder, then cert-file 3, 8, then no location"
        );
    }

    /// Sub-slots break ties inside a slot, and the name breaks ties inside that.
    #[test]
    fn subslot_then_name_break_the_tie() {
        let s = build(vec![
            doc(
                100,
                "second",
                &[
                    ("name", "Zulu".into()),
                    ("perm_location", "f".into()),
                    ("perm_slot", 1.into()),
                    ("perm_subslot", 2.into()),
                ],
            ),
            doc(
                200,
                "first",
                &[
                    ("name", "Alpha".into()),
                    ("perm_location", "f".into()),
                    ("perm_slot", 1.into()),
                    ("perm_subslot", 1.into()),
                ],
            ),
            doc(
                300,
                "third",
                &[
                    ("name", "Alpha".into()),
                    ("perm_location", "f".into()),
                    ("perm_slot", 1.into()),
                    ("perm_subslot", 2.into()),
                ],
            ),
        ]);
        let order: Vec<&str> = s.docs.iter().map(|d| d.id.as_str()).collect();
        assert_eq!(order, ["first", "third", "second"]);
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
        let tracked: Vec<&str> = s.expiring().into_iter().map(|i| s.docs[i].id.as_str()).collect();
        assert_eq!(tracked, ["eng1", "coc-2025"], "soonest first; superseded and ignored are gone");

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

    /// The place column reads the way the mockups show it.
    #[test]
    fn the_place_column_reads_as_location_slot() {
        let s = build(vec![
            doc(
                100,
                "a",
                &[
                    ("name", "A".into()),
                    ("perm_location", "cert-file".into()),
                    ("perm_slot", 8.into()),
                ],
            ),
            doc(
                200,
                "b",
                &[
                    ("name", "B".into()),
                    ("perm_location", "file-4096".into()),
                    ("perm_slot", 1.into()),
                    ("perm_subslot", 2.into()),
                ],
            ),
            doc(300, "c", &[("name", "C".into()), ("perm_location", "softcopy".into())]),
            doc(400, "d", &[("name", "D".into())]),
        ]);
        let place = |id: &str| s.docs.iter().find(|d| d.id == id).unwrap().place();
        assert_eq!(place("a"), "cert-file 8");
        assert_eq!(place("b"), "file-4096 1.2");
        assert_eq!(place("c"), "softcopy");
        assert_eq!(place("d"), "");
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
        assert_eq!(ids("bike-transfer"), ["bike"], "bundles are searchable");
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
        assert!(ids("zzzz").is_empty());
    }

    /// Locations and synced settings come through the same fold.
    #[test]
    fn locations_and_settings_are_read_from_the_fold() {
        let s = store(&[
            (10, "create", "location", "cert-file", "", Value::Null),
            (11, "set", "location", "cert-file", "title", "Cert File".into()),
            (20, "create", "settings", "synced", "", Value::Null),
            (21, "set", "settings", "synced", "expiry_threshold_days", 270.into()),
        ]);
        assert_eq!(s.locations["cert-file"].title, "Cert File");
        assert_eq!(s.warn_days(), 270);
    }

    /// With no settings entity the default window applies, rather than zero.
    #[test]
    fn the_warn_window_defaults_when_unset() {
        assert_eq!(Store::default().warn_days(), DEFAULT_WARN_DAYS);
    }

    /// A document with no name is still built — hiding it would make a
    /// half-written record invisible instead of fixable.
    #[test]
    fn a_nameless_document_still_appears() {
        let s = build(vec![doc(100, "orphan", &[("perm_location", "cert-file".into())])]);
        assert_eq!(s.docs.len(), 1);
        assert_eq!(s.docs[0].name, "");
    }
}
