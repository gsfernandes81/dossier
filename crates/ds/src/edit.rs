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

//! Editing one field of one document — the state, not the drawing.
//!
//! R4's first slice. REWRITE-UI.md §2 fixes where editing lives (*"detail is
//! the only editing surface"*) and how it is spelled (*"explicit save,
//! double-`Esc` discards an edit in progress"*); this module is that contract as
//! a small state machine, and [`crate::find`] draws it.
//!
//! # Why expiry is the first field
//!
//! It is the field this app exists for, and it exercises more of the write path
//! than any other single one. A date that parses becomes a `set` op; an **empty
//! buffer becomes an `unset`**, so one field proves both halves of the §3.2
//! contract. And the consequence is visible everywhere at once — the row's
//! marker and colour, the header's attention count, membership of the expiry
//! watch, the order of the `expiring` filter — so a save that folded wrongly
//! cannot hide.
//!
//! # Why the edit is bound to a document id, not to the cursor
//!
//! A save re-folds the store, and a re-fold can reorder the list: change an
//! expiry under the `expiring` filter and the row moves, or leaves. If the edit
//! remembered a row index it would be pointing at a different document by the
//! time the save landed. It remembers the id instead, which cannot drift.

/// Which field is being edited.
///
/// The seam R4's fields arrive through: each one adds a variant and the compiler
/// names every `match` that has to learn about it.
///
/// **These are the record's simple fields — the ones whose whole value is what
/// you type.** `location` and `slot` are not here because a slot move shifts its
/// neighbours, and `bundles` and `renews` are not because they are memberships
/// of another entity. Those need their own surfaces, not a text buffer.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Field {
    /// What the document is called. The only field that may not be empty.
    Name,
    /// The expiry date, ISO `YYYY-MM-DD`.
    Expiry,
    /// The issue date, same form.
    Issued,
    /// Flat tags, written as a list (§8 — hierarchical tags are dropped).
    Tags,
    /// Free text.
    Notes,
}

impl Field {
    /// The journal field name this writes (`doc` entity, REWRITE.md §3.2).
    #[must_use]
    pub fn journal_field(self) -> &'static str {
        match self {
            Field::Name => "name",
            Field::Expiry => "expiry_date",
            Field::Issued => "issue_date",
            Field::Tags => "tags",
            Field::Notes => "notes",
        }
    }

    /// The prompt the entry line shows while this field is being edited.
    ///
    /// It names the field rather than the surface, which is what makes a
    /// minibuffer legible: the same row asks a different question depending on
    /// what is being asked for.
    #[must_use]
    pub fn prompt(self) -> &'static str {
        match self {
            Field::Name => "name",
            Field::Expiry => "expiry",
            Field::Issued => "issued",
            Field::Tags => "tags",
            Field::Notes => "notes",
        }
    }

    /// What to write for a buffer, or why it cannot be written.
    ///
    /// `Ok(None)` means "clear this field" — an empty buffer is a real
    /// intention, not a mistake, and it becomes an `unset` op rather than a
    /// stored empty string. A stored `""` would fold to a document with an
    /// expiry that no comparison can classify.
    ///
    /// # Errors
    /// The message to put on the status band, phrased as the correction rather
    /// than the complaint.
    pub fn validate(self, buffer: &str) -> Result<Option<serde_json::Value>, String> {
        let value = buffer.trim();
        if value.is_empty() {
            // A name is the one field with nothing sensible to fall back to: a
            // document called nothing cannot be found, listed or talked about.
            return if self == Field::Name {
                Err("a document needs a name".into())
            } else {
                Ok(None)
            };
        }
        match self {
            Field::Expiry | Field::Issued => {
                if is_iso_date(value) {
                    Ok(Some(value.into()))
                } else {
                    Err(format!("{value:?} is not a date — write it as YYYY-MM-DD"))
                }
            }
            // Whitespace-separated, and written as a **list** because that is
            // what the fold reads. A stored `"a b"` would be one tag with a
            // space in it, which nothing would ever match.
            Field::Tags => {
                Ok(Some(value.split_whitespace().map(str::to_string).collect::<Vec<_>>().into()))
            }
            Field::Name | Field::Notes => Ok(Some(value.into())),
        }
    }
}

/// Whether a string is a calendar date in ISO form.
///
/// Hand-checked rather than parsed with `jiff`, for the same reason the rest of
/// the crate compares dates as strings: the stored format *is* ISO, and every
/// comparison in `doc.rs` depends on that being true. Parsing to a date type and
/// formatting back would accept `2026-9-3` and silently rewrite it, which is a
/// store that no longer sorts.
///
/// The day is checked against the month's real length, leap years included: a
/// `2026-02-30` that folded would be an expiry that never arrives.
#[must_use]
pub fn is_iso_date(value: &str) -> bool {
    let bytes = value.as_bytes();
    if bytes.len() != 10 || bytes[4] != b'-' || bytes[7] != b'-' {
        return false;
    }
    let digits = |range: std::ops::Range<usize>| {
        value[range.clone()]
            .bytes()
            .all(|b| b.is_ascii_digit())
            .then(|| value[range].parse::<u32>().unwrap_or(0))
    };
    let (Some(year), Some(month), Some(day)) = (digits(0..4), digits(5..7), digits(8..10)) else {
        return false;
    };
    if !(1..=12).contains(&month) {
        return false;
    }
    let leap = year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
    let length = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        _ if leap => 29,
        _ => 28,
    };
    (1..=length).contains(&day)
}

/// An edit in progress.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Edit {
    /// The document being edited — an id, never a row (see the module header).
    pub doc: String,
    /// Which field.
    pub field: Field,
    /// What has been typed.
    pub buffer: String,
    /// What was there when the edit opened, so "dirty" is a fact and not a flag
    /// somebody has to remember to set.
    pub original: String,
    /// One more `Esc` and the typing is thrown away (REWRITE-UI.md §2).
    pub armed_discard: bool,
    /// A save is in flight. The editor stays open and refuses a second `Enter`
    /// until the journal has answered, so the screen never shows a value the
    /// store does not hold.
    pub saving: bool,
    /// **This edit is naming a document that does not exist yet**, so saving it
    /// appends a `create` before the field.
    ///
    /// The id cannot be decided when the edit opens, because it is minted from
    /// the name and the name is what is being typed — so [`Edit::doc`] is empty
    /// until `Enter`, and filled in with the id that was actually written. That
    /// is what lets everything downstream stay ignorant of the difference: the
    /// save path anchors on `doc`, and by the time it looks there is one.
    pub creating: bool,
}

impl Edit {
    /// Open an edit on a document's field, seeded with its current value.
    #[must_use]
    pub fn new(doc: impl Into<String>, field: Field, current: Option<&str>) -> Self {
        let original = current.unwrap_or_default().to_string();
        Self {
            doc: doc.into(),
            field,
            buffer: original.clone(),
            original,
            armed_discard: false,
            saving: false,
            creating: false,
        }
    }

    /// Open the edit that names a document into existence.
    ///
    /// It starts empty and therefore **dirty the moment anything is typed**,
    /// which is what makes `Esc` ask twice before throwing away a name — the
    /// same rule every other edit follows, for free.
    #[must_use]
    pub fn creating() -> Self {
        Self { creating: true, ..Self::new(String::new(), Field::Name, None) }
    }

    /// What the entry line asks for.
    ///
    /// A create asks for *the document*, not for a field: the same buffer means
    /// something different, and the prompt is the only thing on screen that
    /// says which — the record behind it still shows whatever was selected.
    #[must_use]
    pub fn prompt(&self) -> &'static str {
        if self.creating {
            "new document"
        } else {
            self.field.prompt()
        }
    }

    /// Whether anything has been typed since it opened.
    #[must_use]
    pub fn dirty(&self) -> bool {
        self.buffer != self.original
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// **An empty buffer clears the field rather than storing a blank.** A
    /// stored `""` folds to an expiry no comparison can classify; an `unset` op
    /// folds to a document that is simply not in the watch.
    #[test]
    fn an_empty_buffer_clears_the_field() {
        assert_eq!(Field::Expiry.validate(""), Ok(None));
        assert_eq!(Field::Expiry.validate("   "), Ok(None));
    }

    /// A date that parses is written exactly as typed, trimmed.
    #[test]
    fn a_valid_date_is_stored_verbatim() {
        assert_eq!(Field::Expiry.validate(" 2026-09-28 "), Ok(Some("2026-09-28".into())));
    }

    /// **The stored format is the sort order.** Everything above `doc.rs`
    /// compares expiry dates as strings, so a shape that would not sort — a
    /// one-digit month, a slash, a two-digit year — has to be refused at the
    /// door rather than normalized behind the user's back.
    #[test]
    fn a_date_that_would_not_sort_is_refused() {
        for bad in ["2026-9-28", "28/09/2026", "26-09-28", "2026-09-28T00:00", "soon", "2026-13-01"]
        {
            assert!(Field::Expiry.validate(bad).is_err(), "{bad} must be refused");
        }
    }

    /// A day that does not exist is not a date, leap years included — an expiry
    /// of `2026-02-30` is one that never arrives.
    #[test]
    fn a_day_the_month_does_not_have_is_refused() {
        assert!(is_iso_date("2024-02-29"), "2024 is a leap year");
        assert!(!is_iso_date("2026-02-29"), "2026 is not");
        assert!(!is_iso_date("2000-02-30"));
        assert!(!is_iso_date("2026-04-31"));
        assert!(!is_iso_date("2026-01-00"));
        assert!(is_iso_date("2000-02-29"), "a century divisible by 400 is a leap year");
        assert!(!is_iso_date("1900-02-29"), "one divisible by 100 and not 400 is not");
    }

    /// Dirtiness is derived from the buffer, never tracked separately — typing a
    /// character and rubbing it out again leaves a clean edit, which is what
    /// decides whether `Esc` needs one press or two.
    #[test]
    fn dirtiness_is_derived_and_so_it_can_go_back_to_clean() {
        let mut edit = Edit::new("coc", Field::Expiry, Some("2026-09-28"));
        assert!(!edit.dirty());
        edit.buffer.pop();
        assert!(edit.dirty());
        edit.buffer.push('8');
        assert!(!edit.dirty(), "back to what it was is not an edit");
    }
}
