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

//! Editing one field on the bottom line — the state, not the drawing.
//!
//! An edit names what it edits by id, never by row: a save re-folds the store,
//! which can reorder the list or drop the row, and an id cannot drift. Saving
//! is explicit, and a dirty edit takes two `Esc` to throw away.

/// Which field is being edited.
///
/// The fields whose whole value is what is typed. A location, a bundle
/// membership and `renews` are not here: each is a choice among other
/// records, made in a picker.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Field {
    /// What the record is called; the only field that may not be empty.
    Name,
    /// The expiry date, ISO `YYYY-MM-DD`; a bundle's date.
    Expiry,
    /// The issue date, same form.
    Issued,
    /// Flat tags, typed space-separated and stored as a list.
    Tags,
    /// Free text.
    Notes,
    /// A path to link as one more file, relative to the Syncthing root.
    Attach,
}

impl Field {
    /// What to write for a buffer, or why it cannot be written.
    ///
    /// `Ok(None)` clears the field: it becomes an `unset`, never a stored
    /// empty string, which would fold to a date no comparison can classify.
    ///
    /// # Errors
    /// The correction to put on the status band.
    pub fn validate(self, buffer: &str) -> Result<Option<serde_json::Value>, String> {
        let value = buffer.trim();
        if value.is_empty() {
            return Ok(None);
        }
        match self {
            Field::Expiry | Field::Issued => {
                if is_iso_date(value) {
                    Ok(Some(value.into()))
                } else {
                    Err(format!("{value:?} is not a date — write it as YYYY-MM-DD"))
                }
            }
            // A stored `"a b"` would be one tag with a space in it.
            Field::Tags => {
                Ok(Some(value.split_whitespace().map(str::to_string).collect::<Vec<_>>().into()))
            }
            Field::Name | Field::Notes => Ok(Some(value.into())),
            Field::Attach => relative_path(value).map(|path| Some(path.into())),
        }
    }
}

/// What an edit is about.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Target {
    /// A document, by id.
    Doc(String),
    /// A document the name being typed brings into existence.
    NewDoc,
    /// A bundle, by id.
    Bundle(String),
    /// A bundle the name being typed brings into existence.
    NewBundle,
    /// A physical location, by id; only its name is edited.
    Location(String),
}

impl Target {
    /// The journal entity it is.
    #[must_use]
    pub fn entity(&self) -> &'static str {
        match self {
            Target::Doc(_) | Target::NewDoc => "doc",
            Target::Bundle(_) | Target::NewBundle => "bundle",
            Target::Location(_) => "location",
        }
    }
}

/// A typed path as it is stored: POSIX, relative to the Syncthing root.
///
/// # Errors
/// A path that is absolute, names a drive, or climbs out with `..` — none of
/// which would mean the same thing on the other device.
pub fn relative_path(typed: &str) -> Result<String, String> {
    let path = typed.trim().replace('\\', "/");
    let path = path.trim_start_matches("./");
    let bytes = path.as_bytes();
    if path.starts_with('/') || (bytes.len() > 1 && bytes[1] == b':') {
        return Err(format!("{path:?} is absolute — write it relative to the Syncthing folder"));
    }
    let parts: Vec<&str> =
        path.split('/').filter(|part| !part.is_empty() && *part != ".").collect();
    if parts.is_empty() {
        return Err("a file needs a path".into());
    }
    if parts.contains(&"..") {
        return Err(format!("{path:?} leaves the Syncthing folder"));
    }
    Ok(parts.join("/"))
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
    /// The record being edited.
    pub target: Target,
    /// Which field.
    pub field: Field,
    /// What has been typed.
    pub buffer: String,
    /// What was there when the edit opened, so "dirty" is derived.
    pub original: String,
    /// One more `Esc` throws the typing away.
    pub armed_discard: bool,
    /// A save is in flight; a second `Enter` is refused until it lands.
    pub saving: bool,
    /// The live list under a path being typed.
    pub list: Option<crate::complete::Completion>,
}

impl Edit {
    /// Opens an edit on `target`'s field, seeded with its current value.
    #[must_use]
    pub fn new(target: Target, field: Field, current: Option<&str>) -> Self {
        let original = current.unwrap_or_default().to_string();
        Self {
            target,
            field,
            buffer: original.clone(),
            original,
            armed_discard: false,
            saving: false,
            list: None,
        }
    }

    /// Lets the live list follow the line after it was typed into.
    pub fn typed(&mut self) {
        if let Some(list) = &mut self.list {
            list.typed(&self.buffer);
        }
    }

    /// The live list's rows for the line.
    #[must_use]
    pub fn matches(&self) -> Vec<&crate::complete::Entry> {
        self.list.as_ref().map(|list| list.matches(&self.buffer)).unwrap_or_default()
    }

    /// The journal field it writes; a bundle's date is `date`.
    #[must_use]
    pub fn journal_field(&self) -> &'static str {
        match (self.target.entity(), self.field) {
            (_, Field::Name) => "name",
            ("bundle", Field::Expiry) => "date",
            (_, Field::Expiry) => "expiry_date",
            (_, Field::Issued) => "issue_date",
            (_, Field::Tags) => "tags",
            (_, Field::Notes) => "notes",
            (_, Field::Attach) => "files",
        }
    }

    /// What the entry line asks for.
    #[must_use]
    pub fn prompt(&self) -> &'static str {
        match (&self.target, self.field) {
            (Target::NewDoc, _) => "new document",
            (Target::Location(_), _) => "rename",
            (Target::Bundle(_) | Target::NewBundle, Field::Expiry) => "date",
            (_, Field::Name) => "name",
            (_, Field::Expiry) => "expiry",
            (_, Field::Issued) => "issued",
            (_, Field::Tags) => "tags",
            (_, Field::Notes) => "notes",
            (_, Field::Attach) => "attach",
        }
    }

    /// The value to write, or why it cannot be written: a name may not be
    /// cleared.
    ///
    /// # Errors
    /// The correction to put on the status band.
    pub fn value(&self) -> Result<Option<serde_json::Value>, String> {
        let value = self.field.validate(&self.buffer)?;
        if value.is_none() && self.field == Field::Name {
            let what = match self.target.entity() {
                "bundle" => "bundle",
                "location" => "location",
                _ => "document",
            };
            return Err(format!("a {what} needs a name"));
        }
        Ok(value)
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
        let mut edit = Edit::new(Target::Doc("coc".into()), Field::Expiry, Some("2026-09-28"));
        assert!(!edit.dirty());
        edit.buffer.pop();
        assert!(edit.dirty());
        edit.buffer.push('8');
        assert!(!edit.dirty(), "back to what it was is not an edit");
    }

    /// A name cannot be cleared, and the refusal names what needs one.
    #[test]
    fn a_name_cannot_be_cleared() {
        let edit = Edit::new(Target::Bundle("trip".into()), Field::Name, Some("Trip"));
        let cleared = Edit { buffer: " ".into(), ..edit };
        assert_eq!(cleared.value(), Err("a bundle needs a name".into()));
        assert_eq!(cleared.journal_field(), "name");
    }

    /// A typed path is stored POSIX and relative, or refused with the reason.
    #[test]
    fn a_typed_path_must_stay_inside_the_root() {
        assert_eq!(relative_path(r"Marine\coc.pdf").as_deref(), Ok("Marine/coc.pdf"));
        assert_eq!(relative_path("./Marine//coc.pdf ").as_deref(), Ok("Marine/coc.pdf"));
        assert!(relative_path("/home/g/coc.pdf").is_err());
        assert!(relative_path(r"C:\Users\coc.pdf").is_err());
        assert!(relative_path("Marine/../../etc").is_err());
        assert!(relative_path("./").is_err());
    }
}
