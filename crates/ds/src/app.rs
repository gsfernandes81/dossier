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

//! The update half of the loop: message in, state changed, effect out.
//!
//! [`update`] is the only mutation. It returns an [`Effect`] because the model
//! cannot open files, flip the mouse mode or append to the journal, and staying
//! pure keeps every rule testable. The view writes back only the geometry it
//! drew, since hit tests must read what is on screen.

use crate::complete::Completion;
use crate::edit::{Field, Target};
use crate::layout;
use crate::pick::{Choice, Picker, Purpose};
use crate::{Doc, Status, Store};

/// One thing the user did, or a worker reported, stripped of terminal detail.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Msg {
    /// A worker finished reading the `enrich` namespace for scan-text search;
    /// an `Arc` so the transcripts are not copied across the thread.
    ScansLoaded(std::sync::Arc<crate::scans::Scans>),
    /// An append landed, and here is the store re-folded around it; boxed so
    /// every message is not the size of a store.
    Saved(Box<Store>),
    /// Another writer's ops arrived, and here is the store re-folded with them.
    Reloaded(Box<Store>),
    /// The date turned while the app was open.
    Day { today: String, warn_until: String },
    /// The platform opener could not open a file, and why.
    OpenFailed(String),
    /// The append did not land. The editor stays open with the typing intact:
    /// the screen must never claim a value the journal refused.
    SaveFailed {
        /// What went wrong, ready for the status band.
        reason: String,
        /// Whether it will fail the same way every time, as a held writer lock
        /// will, so editing goes off for the session; a full disk might not.
        permanent: bool,
    },
    /// A bare printable character.
    Char(char),
    /// Rubs out the character before the query cursor.
    Backspace,
    /// `Tab` — fills a path being typed from its live list.
    Tab,
    /// `Enter` — drills one layer: the list into the record, the record into a file.
    Enter,
    /// `←` — moves the query cursor one character left.
    Left,
    /// `→` — moves the query cursor one character right.
    Right,
    /// Cursor movement.
    Move(Motion),
    /// `Esc`: peels exactly one layer.
    Esc,
    /// `ctrl+q` / `ctrl+c` — leave now, from anywhere.
    Quit,
    /// Include scan text in the search.
    ToggleScans,
    /// The expiring filter: from the filter list, or a tap on the header's count.
    ToggleExpiring,
    /// `ctrl+z` — undo the last change this session wrote.
    Undo,
    /// `ctrl+y` — redo the last change undone.
    Redo,
    /// `Space` on an empty query, or the `SPC` chip: open the leader sheet.
    Leader,
    /// A tap or click at a terminal cell.
    Tap {
        /// Column, zero-based from the terminal's left edge.
        col: u16,
        /// Row, zero-based from the top.
        row: u16,
    },
    /// A wheel or finger scroll, in rows; negative is up.
    Scroll(i32),
    /// The terminal changed size (SIGWINCH, or the phone rotating).
    Resize {
        /// New width in columns.
        cols: u16,
        /// New height in rows.
        rows: u16,
    },
}

/// A view pushed over the Find view, anchored on an id so a save that
/// reorders or filters the list never changes what it shows.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum View {
    /// A document version's Details view, its selector on row `cursor`.
    Details {
        /// The document's id.
        doc: String,
        /// The selected row ([`crate::detail::rows`]).
        cursor: usize,
    },
    /// Every version of the document `doc` belongs to.
    Versions {
        /// The selected version's id.
        doc: String,
    },
    /// The bundles, listed in the Find list's place; the search bar searches
    /// them while it is open.
    Bundles {
        /// The selected entry.
        selected: crate::bundles::Entry,
        /// The Find view's search, put back when this view closes.
        query: String,
    },
    /// One bundle's Details view.
    Bundle {
        /// The bundle's id.
        id: String,
        /// The selected row.
        selected: crate::bundles::Row,
    },
}

impl View {
    /// Whether what the view shows is still in `store`.
    fn alive_in(&self, store: &Store) -> bool {
        match self {
            View::Details { doc, .. } | View::Versions { doc, .. } => store.index_of(doc).is_some(),
            View::Bundles { .. } => true,
            View::Bundle { id, .. } => store.bundle(id).is_some(),
        }
    }
}

/// Where the cursor should go.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Motion {
    /// One row up.
    Up,
    /// One row down.
    Down,
    /// One screenful up.
    PageUp,
    /// One screenful down.
    PageDown,
    /// The top of the list.
    Home,
    /// The bottom.
    End,
}

/// What the shell of the program should do after an update.
///
/// Everything except [`Effect::Idle`] implies a repaint — an effect exists
/// because state changed, and state that changed is state worth showing.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Effect {
    /// Nothing happened; do not even repaint. Key releases and finger drags land
    /// here, and on a phone not repainting is battery.
    Idle,
    /// Repaint.
    Redraw,
    /// Hand this path — relative to the Syncthing root — to the platform opener.
    Open(String),
    /// Read the `enrich` namespace on a worker thread and post the result back
    /// as [`Msg::ScansLoaded`].
    LoadScans,
    /// Append these ops to this device's journal in one commit, then post the
    /// re-read store back as [`Msg::Saved`]; ops that are only correct together
    /// stay adjacent.
    Append(Vec<journal::Draft>),
    /// Leave, restoring the terminal.
    Quit,
}

/// One write this session made, and the ops that put it back.
///
/// Redo appends `forward` again, the very ops first written, so it cannot drift.
/// `back` is a snapshot of the store at the time; another device's later edit
/// is settled by field-level LWW, not by it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Change {
    /// What was written.
    pub forward: Vec<journal::Draft>,
    /// What puts it back.
    pub back: Vec<journal::Draft>,
}

impl Change {
    /// Creates `ent` `id` with `fields`; the way back deletes it.
    fn create(ent: &str, id: &str, fields: Vec<(&str, serde_json::Value)>) -> Change {
        let sets =
            fields.into_iter().map(|(field, value)| journal::Draft::set(ent, id, field, value));
        Change {
            forward: std::iter::once(journal::Draft::create(ent, id)).chain(sets).collect(),
            back: vec![journal::Draft::delete(ent, id)],
        }
    }

    /// The change that undoes this one.
    fn reversed(self) -> Change {
        Change { forward: self.back, back: self.forward }
    }

    /// This change and then `next`, put back in the opposite order.
    fn then(mut self, next: Change) -> Change {
        self.forward.extend(next.forward);
        let mut back = next.back;
        back.extend(self.back);
        Change { forward: self.forward, back }
    }
}

/// Which way an append in flight is going.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Direction {
    /// An ordinary write: it becomes something to undo, and it clears the redo
    /// stack because history has branched.
    Forward,
    /// Putting a write back: it moves its change from the undo stack to the redo
    /// stack, and is not itself something to undo.
    Undo,
    /// Putting it back again: the mirror image.
    Redo,
}

/// An append in flight: the change it makes, which way it goes, and what the
/// screen does once it lands.
#[derive(Debug, Clone)]
struct Pending {
    change: Change,
    direction: Direction,
    landed: Landed,
}

/// What the screen does once a write lands.
#[derive(Debug, Clone, Default)]
struct Landed {
    /// The document the list keeps its cursor on; the current one when none.
    anchor: Option<String>,
    /// What the status line says; "saved" when nothing more particular.
    note: Option<String>,
    /// A view to open in front.
    open: Option<View>,
    /// Whether that view takes the place of the one in front.
    replace: bool,
}

impl Landed {
    /// Keeps the cursor on `doc` and says `note`.
    fn on(doc: &str, note: impl Into<String>) -> Self {
        Self { anchor: Some(doc.to_string()), note: Some(note.into()), ..Self::default() }
    }

    /// Says `note`.
    fn saying(note: impl Into<String>) -> Self {
        Self { note: Some(note.into()), ..Self::default() }
    }

    /// Opens the document just created on its Details view, the only place
    /// its other fields can be filled in.
    fn created(doc: &str) -> Self {
        Self {
            open: Some(View::Details { doc: doc.to_string(), cursor: 0 }),
            ..Self::on(doc, "created")
        }
    }
}

/// A press that has asked once and acts on the next one.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Armed {
    /// One more `Esc` quits.
    Esc,
    /// One more `d` deletes what is in front: the document, the bundle, or
    /// the picked location with everything inside it. `u` puts it back.
    Delete,
}

/// Whether this session can write, and the reason to show when it cannot.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WriteState {
    /// Editing is available under this device's name.
    Ready { device: String },
    /// Editing is off for this session, with the reason ready to show.
    Off(String),
}

impl WriteState {
    /// Whether an edit may be opened.
    #[must_use]
    pub fn ready(&self) -> bool {
        matches!(self, WriteState::Ready { .. })
    }

    /// The device to write under, when there is one.
    #[must_use]
    pub fn device(&self) -> Option<&str> {
        match self {
            WriteState::Ready { device } => Some(device),
            WriteState::Off(_) => None,
        }
    }

    /// Why not, for the status band.
    #[must_use]
    pub fn reason(&self) -> Option<&str> {
        match self {
            WriteState::Ready { .. } => None,
            WriteState::Off(reason) => Some(reason),
        }
    }
}

impl Default for WriteState {
    /// Read-only until a device name is known.
    fn default() -> Self {
        WriteState::Off("no device name — run `ds init` to enable editing".into())
    }
}

/// What narrows or widens the list beyond the query. The toggles combine.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Filter {
    /// Only documents in the expiry watch, soonest first.
    pub expiring: bool,
    /// Older versions as well as the latest.
    pub old_versions: bool,
}

impl Filter {
    /// No toggle on: the latest version of every document, in shelf order.
    pub const ALL: Self = Self { expiring: false, old_versions: false };
    /// Only what the expiry watch is tracking.
    pub const EXPIRING: Self = Self { expiring: true, ..Self::ALL };
}

/// Whether scan-text search is on, and whether the text it needs has arrived.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ScanSearch {
    /// Names, notes, tags and bundles only.
    #[default]
    Off,
    /// Asked for; a worker is reading the `enrich` namespace.
    Loading,
    /// On, with the text in hand.
    On,
}

/// A run of columns on one row that a tap can land in.
///
/// Published by the view for the same reason [`ListGeometry`] is: the hit test
/// must read the geometry that was really drawn, never re-derive it. The touch
/// affordances are small and few — the header's expiring count and the `SPC`
/// chip — and both move with the terminal's width.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct Zone {
    /// The terminal row it occupies.
    pub row: u16,
    /// First column, zero-based.
    pub col: u16,
    /// How many columns wide. Zero means "not drawn", which is how a keyboard
    /// layout says it has no touch affordances without a second flag.
    pub width: u16,
}

impl Zone {
    /// Whether a tap landed inside. A zero-width zone contains nothing, so an
    /// undrawn affordance can never be hit.
    #[must_use]
    pub const fn hit(self, col: u16, row: u16) -> bool {
        self.width > 0 && row == self.row && col >= self.col && col < self.col + self.width
    }
}

/// The rectangle the renderer last drew document rows into.
///
/// Published by the view so taps can be hit-tested against what is actually on
/// screen. Zeroed when the list is not drawn at all (too-small notice, or detail
/// covering it on a narrow terminal), which makes a tap in that state a no-op
/// rather than a guess.
#[derive(Clone, Copy, Default, PartialEq, Eq, Debug)]
pub struct ListGeometry {
    /// First terminal row of the list.
    pub top: u16,
    /// How many terminal rows it occupies.
    pub height: u16,
    /// Screen lines per document (1 or 2).
    pub row_height: u16,
}

/// Screen rows the renderer last drew items into, so a tap can name the item:
/// the row at `top` holds item `first`, and each further row the next one.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RowGeometry {
    /// The terminal row of the first item drawn.
    pub top: u16,
    /// The column the rows start at.
    pub left: u16,
    /// How many columns they span.
    pub width: u16,
    /// Which item each drawn row belongs to, top to bottom.
    pub items: Vec<usize>,
    /// The items of a second column, when the rows were drawn in two.
    pub right: Vec<usize>,
    /// The terminal column the second column starts at.
    pub split: u16,
}

impl RowGeometry {
    /// Rows of `items` drawn across `area` from terminal row `top` down.
    #[must_use]
    pub fn rows(area: ratatui::layout::Rect, top: u16, items: Vec<usize>) -> Self {
        Self { top, left: area.x, width: area.width, items, ..Self::default() }
    }

    /// The item drawn at a terminal cell, if any.
    #[must_use]
    pub fn at(&self, col: u16, row: u16) -> Option<usize> {
        if col < self.left || col >= self.left + self.width {
            return None;
        }
        let offset = usize::from(row.checked_sub(self.top)?);
        let column =
            if !self.right.is_empty() && col >= self.split { &self.right } else { &self.items };
        column.get(offset).copied()
    }
}

/// Everything the renderer reads and the event loop changes.
#[derive(Default)]
pub struct Model {
    /// The store, folded once at startup.
    pub store: Store,
    /// Today, ISO. Passed in rather than read from the clock, so a test can be
    /// written about an expiry without waiting for it to happen.
    pub today: String,
    /// The far edge of the warn window, ISO.
    pub warn_until: String,
    /// The search text. A bare printable anywhere on the list lands here.
    pub query: String,
    /// Whether scan text is part of the haystack, and whether it has arrived.
    pub scan_search: ScanSearch,
    /// The scan text, once a worker has read it. Kept even when the toggle is
    /// off, so turning it on again is instant.
    pub scans: Option<std::sync::Arc<crate::scans::Scans>>,
    /// Which documents the list shows.
    pub filter: Filter,
    /// Indices into `store.docs`, in list order — the result of filter + search.
    pub rows: Vec<usize>,
    /// Cursor position *within `rows`*.
    pub cursor: usize,
    /// First visible row. The app owns scrolling: Termux's mouse mode blocks the
    /// terminal's own scrollback (termux-app #4302), so if the list does not
    /// move the finger, nothing does.
    pub offset: usize,
    /// The cursor is on the `+ new` row pinned above the list.
    pub on_new: bool,
    /// Where the Bundles view's entries were last drawn.
    pub bundle_list: RowGeometry,
    /// The Syncthing folder root, which paths being attached are relative to.
    pub root: Option<std::path::PathBuf>,
    /// The screen row the `+ new` row was last drawn on.
    pub new_row: Option<u16>,
    /// The views pushed over the Find view, innermost last.
    pub views: Vec<View>,
    /// What the next press of the same key does without asking again.
    pub armed: Option<Armed>,
    /// Whether SGR mouse reporting is currently on.
    pub mouse_on: bool,
    /// The leader sheet, when it is open.
    pub sheet: bool,
    /// The field being edited, when one is.
    pub edit: Option<crate::edit::Edit>,
    /// This session's writes, each with the ops that put it back, computed
    /// from the store as it stood; the journal itself keeps everything, so a
    /// restart only empties the shortcut.
    pub undo: Vec<Change>,
    /// Undone writes, newest last, waiting to be put back.
    pub redo: Vec<Change>,
    /// The append in flight. Its change is promoted onto the stack its
    /// direction says when the journal confirms it and dropped when it
    /// refuses, so a write that never landed can never be taken back.
    pending: Option<Pending>,
    /// Whether this session can write, and why not when it cannot.
    pub write: WriteState,
    /// Where the view drew the header's pressable expiring count.
    pub count_zone: Zone,
    /// Where the view drew the `SPC` chip.
    pub leader_zone: Zone,
    /// A transient one-line message, cleared by the next key.
    pub flash: Option<String>,
    /// Where the next typed character lands in the query, in characters.
    pub query_cursor: usize,
    /// The checklist or picker panel, when one is open.
    pub picker: Option<crate::pick::Picker>,
    /// The location picker, when one is open.
    pub locpick: Option<crate::locpick::LocationPicker>,
    /// The journal directory that was looked for, when **nothing was there**
    /// yet. The empty list names it, because the first save creates it there —
    /// and a wrong root is better caught before that than after.
    pub missing_journal: Option<String>,
    /// Terminal width.
    pub cols: u16,
    /// Terminal height.
    pub rows_on_screen: u16,
    /// Where the rows were last drawn (see [`ListGeometry`]).
    pub list: ListGeometry,
    /// Where the location picker's rows were drawn.
    pub tree: RowGeometry,
    /// Where the Details view's rows were drawn.
    pub record: RowGeometry,
    /// Where the open panel's rows were drawn.
    pub panel: RowGeometry,
}

impl Model {
    /// The initial state: the whole store listed, mouse reporting on.
    #[must_use]
    pub fn new(store: Store, today: String, warn_until: String, cols: u16, rows: u16) -> Self {
        let mut model = Self {
            store,
            today,
            warn_until,
            cols,
            rows_on_screen: rows,
            mouse_on: true,
            ..Self::default()
        };
        model.requery();
        model
    }

    /// Forgets the geometry the last frame drew, before the next one draws.
    pub fn clear_geometry(&mut self) {
        self.list = ListGeometry::default();
        self.new_row = None;
        self.record = RowGeometry::default();
        self.bundle_list = RowGeometry::default();
        self.tree = RowGeometry::default();
        self.panel = RowGeometry::default();
        self.count_zone = Zone::default();
        self.leader_zone = Zone::default();
    }

    /// The highlighted document, if anything matched.
    #[must_use]
    pub fn current(&self) -> Option<&Doc> {
        match self.views.last() {
            Some(View::Details { doc, .. } | View::Versions { doc }) => self.store.get(doc),
            Some(View::Bundles { .. } | View::Bundle { .. }) => None,
            None if self.on_new => None,
            None => self.rows.get(self.cursor).map(|&i| &self.store.docs[i]),
        }
    }

    /// Whether a pane is in front — the Details, Versions or a bundle's Details
    /// view — where bare letters are verbs rather than search text.
    #[must_use]
    pub fn pane(&self) -> bool {
        matches!(
            self.views.last(),
            Some(View::Details { .. } | View::Versions { .. } | View::Bundle { .. })
        )
    }

    /// Whether typing goes into the search on the last row: only when no pane,
    /// panel or edit has taken the keys.
    #[must_use]
    pub fn typing_into_query(&self) -> bool {
        !self.pane()
            && self.edit.is_none()
            && !self.sheet
            && self.picker.is_none()
            && self.locpick.is_none()
    }

    /// Whether the Details view is the one in front.
    #[must_use]
    pub fn detail(&self) -> bool {
        matches!(self.views.last(), Some(View::Details { .. }))
    }

    /// The row the Details view's selector is on ([`crate::detail::rows`]).
    #[must_use]
    pub fn record_cursor(&self) -> usize {
        match self.views.last() {
            Some(View::Details { cursor, .. }) => *cursor,
            _ => 0,
        }
    }

    /// Puts the Details view's selector on row `at`.
    pub fn set_record_cursor(&mut self, at: usize) {
        if let Some(View::Details { cursor, .. }) = self.views.last_mut() {
            *cursor = at;
        }
    }

    /// Opens the Details view on the list's document, unless it is in front.
    fn show_details(&mut self) {
        if self.detail() {
            return;
        }
        if let Some(doc) = self.current().map(|doc| doc.id.clone()) {
            self.push_details(doc);
        }
    }

    /// Opens document `doc`'s Details view in front.
    fn push_details(&mut self, doc: String) -> Effect {
        self.views.push(View::Details { doc, cursor: 0 });
        Effect::Redraw
    }

    /// Opens the Versions view on the current document, its own version
    /// selected.
    fn open_versions(&mut self) -> Effect {
        let Some(doc) = self.current().map(|doc| doc.id.clone()) else { return Effect::Idle };
        self.views.push(View::Versions { doc });
        Effect::Redraw
    }

    /// Selects a version, or opens it when it is already selected.
    fn versions_tap(&mut self, index: usize) -> Effect {
        let Some(View::Versions { doc }) = self.views.last() else { return Effect::Idle };
        let rows = crate::versions::rows(&self.store, doc);
        let Some(tapped) = rows.get(index).map(|&i| self.store.docs[i].id.clone()) else {
            return Effect::Idle;
        };
        if tapped == *doc {
            return self.open_version();
        }
        self.views.pop();
        self.views.push(View::Versions { doc: tapped });
        Effect::Redraw
    }

    /// Opens the selected version's Details view.
    fn open_version(&mut self) -> Effect {
        let Some(doc) = self.current().map(|doc| doc.id.clone()) else { return Effect::Idle };
        self.push_details(doc)
    }

    /// Opens the Bundles view, keeping the Find view's search to put back.
    fn open_bundles(&mut self) -> Effect {
        let query = std::mem::take(&mut self.query);
        self.views.push(View::Bundles { selected: crate::bundles::Entry::New, query });
        self.query_cursor = 0;
        self.requery();
        self.reset_bundles();
        Effect::Redraw
    }

    /// Selects the Bundles view's first match, or `+ new`.
    fn reset_bundles(&mut self) {
        let first = crate::bundles::first(&crate::bundles::entries(&self.store, &self.query));
        if let Some(View::Bundles { selected, .. }) = self.views.last_mut() {
            *selected = first;
        }
    }

    /// `Enter` on the Bundles view: opens the selected bundle, or creates
    /// the one the search names.
    fn enter_bundles(&mut self) -> Effect {
        let Some(View::Bundles { selected, .. }) = self.views.last() else { return Effect::Idle };
        match selected.clone() {
            crate::bundles::Entry::Bundle(id) => {
                self.views.push(View::Bundle { id, selected: crate::bundles::Row::Name });
                Effect::Redraw
            }
            crate::bundles::Entry::New => {
                let name = self.query.trim().to_string();
                if !name.is_empty() {
                    return self.create_bundle(&name);
                }
                if let Some(effect) = self.refused() {
                    return effect;
                }
                let edit = crate::edit::Edit::new(Target::NewBundle, Field::Name, None);
                self.edit = Some(edit);
                Effect::Redraw
            }
        }
    }

    /// Selects an entry of the Bundles view, or acts on it when it is
    /// already selected. Beside an open view, a bundle opens at once.
    fn bundles_tap(&mut self, index: usize) -> Effect {
        let Some(at) = self.views.iter().position(|view| matches!(view, View::Bundles { .. }))
        else {
            return Effect::Idle;
        };
        let entries = crate::bundles::entries(&self.store, &self.query);
        let Some(entry) = entries.get(index).cloned() else { return Effect::Idle };
        let front = at + 1 == self.views.len();
        self.views.truncate(at + 1);
        let Some(View::Bundles { selected, .. }) = self.views.last_mut() else {
            return Effect::Idle;
        };
        if front && *selected == entry {
            return self.enter_bundles();
        }
        *selected = entry.clone();
        if let (false, crate::bundles::Entry::Bundle(id)) = (front, entry) {
            self.views.push(View::Bundle { id, selected: crate::bundles::Row::Name });
        }
        Effect::Redraw
    }

    /// Asks for `change` to be appended, and records what follows once it
    /// lands.
    fn append(&mut self, change: Change, landed: Landed) -> Effect {
        let forward = change.forward.clone();
        self.pending = Some(Pending { change, direction: Direction::Forward, landed });
        Effect::Append(forward)
    }

    /// Why a write cannot happen now: the session cannot write, or a save is
    /// still in flight and would be credited with this one's change.
    fn refusal(&self) -> Option<String> {
        match self.write.reason() {
            Some(reason) => Some(reason.to_string()),
            None => self.pending.is_some().then(|| "saving — one moment".to_string()),
        }
    }

    /// Says why a write cannot happen now, when it cannot.
    fn refused(&mut self) -> Option<Effect> {
        self.flash = Some(self.refusal()?);
        Some(Effect::Redraw)
    }

    /// What `ent` `id` stores in `field`, as the journal holds it.
    fn stored(&self, ent: &str, id: &str, field: &str) -> Option<serde_json::Value> {
        let fields = match ent {
            "doc" => self.store.get(id)?.as_fields(),
            "bundle" => self.store.bundle(id)?.as_fields(),
            _ => self.store.locations.get(id)?.as_fields(),
        };
        fields.into_iter().find(|(key, _)| *key == field).map(|(_, value)| value)
    }

    /// Sets `ent` `id`'s `field` to `new`, with the way back to what it holds.
    fn flip(&self, ent: &str, id: &str, field: &str, new: Option<serde_json::Value>) -> Change {
        let was = self.stored(ent, id, field);
        Change {
            forward: vec![journal::Draft::put(ent, id, field, new)],
            back: vec![journal::Draft::put(ent, id, field, was)],
        }
    }

    /// Opens the checklist of bundles the current version is in.
    fn open_bundle_checklist(&mut self) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let Some(doc) = self.current().map(|doc| doc.id.clone()) else { return Effect::Idle };
        self.picker = Some(Picker::new(Purpose::Bundles(doc)));
        Effect::Redraw
    }

    /// Adds version `doc` to `bundle`, or takes it out; with `None`, creates
    /// the bundle the checklist's typing names with the version in it. One
    /// change either way.
    fn tick_bundle(&mut self, doc: &str, bundle: Option<&str>) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let Some(version) = self.store.get(doc) else { return Effect::Idle };
        let mut now = version.bundles.clone();
        let (created, note) = if let Some(id) = bundle {
            let name = self.store.bundle(id).map_or(id, |bundle| bundle.name.as_str());
            let note = if let Some(at) = now.iter().position(|entry| entry.bundle == id) {
                now.remove(at);
                format!("taken out of {name}")
            } else {
                now.push(crate::Membership { bundle: id.to_string(), file: None });
                format!("added to {name}")
            };
            (None, note)
        } else {
            let name = self.picker.as_ref().map(|picker| picker.filter.trim().to_string());
            let Some(name) = name.filter(|name| !name.is_empty()) else {
                self.flash = Some("type the new bundle's name".into());
                return Effect::Redraw;
            };
            let id = self.mint("bundle", &name);
            let create = Change::create("bundle", &id, vec![("name", name.as_str().into())]);
            now.push(crate::Membership { bundle: id, file: None });
            if let Some(picker) = &mut self.picker {
                picker.filter.clear();
                picker.cursor = 0;
            }
            (Some(create), format!("added to {name}"))
        };
        let tick = self.flip("doc", doc, "bundles", crate::doc::memberships_value(&now));
        let change = match created {
            Some(create) => create.then(tick),
            None => tick,
        };
        self.append(change, Landed::on(doc, note))
    }

    /// Creates a bundle named `name` and opens it once it lands.
    fn create_bundle(&mut self, name: &str) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let id = self.mint("bundle", name);
        let change = Change::create("bundle", &id, vec![("name", name.into())]);
        let open = Some(View::Bundle { id, selected: crate::bundles::Row::Name });
        self.append(change, Landed { open, ..Landed::saying("created") })
    }

    /// Creates a document named `name` and opens it once it lands.
    fn create_doc(&mut self, name: &str) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let id = self.mint("doc", name);
        let change = Change::create("doc", &id, vec![("name", name.into())]);
        self.append(change, Landed::created(&id))
    }

    /// Flashes that `key` does nothing on this view.
    fn no_verb(&mut self, key: char) -> Effect {
        self.flash = Some(format!("no verb on `{key}` here — space for the menu"));
        Effect::Redraw
    }

    /// A bare letter on a bundle's Details view.
    fn bundle_verb(&mut self, key: char) -> Effect {
        let Some(View::Bundle { id, selected }) = self.views.last().cloned() else {
            return Effect::Idle;
        };
        match key {
            'e' => self.edit_bundle_row(id, selected),
            'd' => self.delete_bundle(),
            'u' => self.step(Direction::Undo),
            'r' => self.step(Direction::Redo),
            _ => self.no_verb(key),
        }
    }

    /// `e` on a row of bundle `id`: its own fields edit on the bottom line, and
    /// a document in it opens the picker for how it is held.
    fn edit_bundle_row(&mut self, id: String, row: crate::bundles::Row) -> Effect {
        use crate::bundles::Row;
        if let Some(effect) = self.refused() {
            return effect;
        }
        let Some(bundle) = self.store.bundle(&id) else { return Effect::Idle };
        let (field, current) = match row {
            Row::Name => (Field::Name, Some(bundle.name.clone())),
            Row::Date => (Field::Expiry, bundle.date.clone()),
            Row::Notes => (Field::Notes, Some(bundle.notes.clone())),
            Row::Member(doc) => return self.open_picker(Purpose::Member { doc, bundle: id }),
        };
        self.edit = Some(crate::edit::Edit::new(Target::Bundle(id), field, current.as_deref()));
        Effect::Redraw
    }

    /// `Enter` on a bundle's Details view: opens a document in it, or edits
    /// the bundle's own row.
    fn enter_bundle(&mut self) -> Effect {
        match self.views.last().cloned() {
            Some(View::Bundle { selected: crate::bundles::Row::Member(doc), .. }) => {
                self.push_details(doc)
            }
            Some(View::Bundle { .. }) => self.bundle_verb('e'),
            _ => Effect::Idle,
        }
    }

    /// Selects a row of a bundle's Details view, or acts on it when it is
    /// already selected.
    fn bundle_tap(&mut self, index: usize) -> Effect {
        let Some(View::Bundle { id, selected }) = self.views.last_mut() else {
            return Effect::Idle;
        };
        let Some(row) = crate::bundles::rows(&self.store, id).get(index).cloned() else {
            return Effect::Idle;
        };
        if *selected == row {
            return self.enter_bundle();
        }
        *selected = row;
        Effect::Redraw
    }

    /// Deletes the bundle in front on a second `d`. Its documents are never
    /// touched: their entries for it read as nothing until undo restores it.
    fn delete_bundle(&mut self) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let Some(View::Bundle { id, .. }) = self.views.last().cloned() else { return Effect::Idle };
        let Some(bundle) = self.store.bundle(&id) else { return Effect::Idle };
        if self.armed != Some(Armed::Delete) {
            self.armed = Some(Armed::Delete);
            return Effect::Redraw;
        }
        let change = Change::create("bundle", &id, bundle.as_fields()).reversed();
        self.armed = None;
        self.append(change, Landed::saying("deleted — u to undo"))
    }

    /// Drops every view whose record is gone from the store.
    fn prune_views(&mut self) {
        let store = &self.store;
        self.views.retain(|view| view.alive_in(store));
        // A selection is held by identity, so a reorder cannot move it; only
        // one whose row has gone needs a new one.
        let entries = crate::bundles::entries(store, &self.query);
        for view in &mut self.views {
            match view {
                View::Bundles { selected, .. } if !entries.contains(selected) => {
                    *selected = crate::bundles::first(&entries);
                }
                View::Bundle { id, selected }
                    if !crate::bundles::rows(store, id).contains(selected) =>
                {
                    *selected = crate::bundles::Row::Name;
                }
                _ => {}
            }
        }
    }

    /// Whether the list offers `+ new`: once something is typed, or always on
    /// a store with no documents, so the first launch is never a dead end.
    #[must_use]
    pub fn offers_new(&self) -> bool {
        !self.query.trim().is_empty() || self.store.docs.is_empty()
    }

    /// The expiry standing of a document, against today and the warn window.
    #[must_use]
    pub fn status(&self, doc: &Doc) -> Status {
        doc.status(&self.today, &self.warn_until)
    }

    /// Documents expired or due inside the warn window, soonest first.
    #[must_use]
    pub fn due(&self) -> Vec<usize> {
        self.store.due(&self.today, &self.warn_until)
    }

    /// How many documents the list holds with nothing typed and no filter but
    /// old versions: the denominator of the count beside the search.
    #[must_use]
    pub fn total(&self) -> usize {
        if self.filter.old_versions {
            self.store.docs.len()
        } else {
            self.store.listed()
        }
    }

    /// Rows that fit on screen right now.
    #[must_use]
    pub fn visible_rows(&self) -> usize {
        let pinned = u16::from(self.offers_new());
        layout::visible_rows(self.cols, self.rows_on_screen.saturating_sub(pinned))
    }

    /// Re-runs filter and search and clamps the cursor, on every keystroke: a
    /// scan of pre-folded haystacks, fast enough to need no index.
    fn requery(&mut self) {
        let base = self.filter.expiring.then(|| self.due());
        let mut matched = self.store.search(&self.query);
        // Scan text widens the haystack rather than replacing it: a document
        // whose *name* matches must never drop out of the list because its scan
        // text does not mention the word.
        if self.scan_search == ScanSearch::On && !self.query.is_empty() {
            if let Some(scans) = &self.scans {
                let needle = crate::search::fold(&self.query);
                let found: Vec<usize> = self
                    .store
                    .docs
                    .iter()
                    .enumerate()
                    .filter(|(i, doc)| {
                        !matched.contains(i)
                            && scans.any_matches(
                                doc.files.iter().map(|file| file.path.clone()),
                                &needle,
                            )
                    })
                    .map(|(i, _)| i)
                    .collect();
                matched.extend(found);
                matched.sort_unstable();
            }
        }
        let filter = self.filter;
        matched.retain(|&i| {
            let doc = &self.store.docs[i];
            filter.old_versions || !doc.superseded
        });
        self.rows = match base {
            None => matched,
            Some(expiring) if self.query.is_empty() => expiring,
            Some(expiring) => {
                // The filter decides the set *and* the order; the search then
                // narrows it. Running the search first would re-sort the list
                // back into shelf order and lose "soonest first".
                expiring.into_iter().filter(|i| matched.contains(i)).collect()
            }
        };
        self.cursor = self.cursor.min(self.rows.len().saturating_sub(1));
        self.offset = self.offset.min(self.cursor);
        self.on_new = self.offers_new() && (self.on_new || self.rows.is_empty());
    }

    /// Moves the list cursor, `+ new` sitting one up from the first row.
    fn move_cursor(&mut self, motion: Motion) {
        if self.rows.is_empty() {
            return;
        }
        let up = matches!(motion, Motion::Up | Motion::PageUp | Motion::Home);
        if self.offers_new() && up && (self.cursor == 0 || motion == Motion::Home) {
            self.cursor = 0;
            self.on_new = true;
            return;
        }
        if std::mem::take(&mut self.on_new) && !up {
            return;
        }
        self.cursor = moved(self.cursor, self.rows.len(), motion, self.visible_rows().max(1));
    }

    /// Goes one layer deeper: from the list into the record, from the record
    /// into the selected file row's file, or the primary file on any other row.
    /// It never mutates, so it can be pressed blind after typing.
    fn drill(&mut self) -> Effect {
        if self.on_new {
            return self.create_from_query();
        }
        let Some(doc) = self.current() else {
            self.flash = Some("nothing to open".into());
            return Effect::Redraw;
        };
        if !self.detail() {
            self.show_details();
            return Effect::Redraw;
        }
        let rows = crate::detail::rows(doc);
        let file = match rows.get(self.record_cursor().min(rows.len().saturating_sub(1))) {
            Some(crate::detail::Row::File(index)) => doc.files.get(*index),
            _ => doc.primary_file(),
        };
        let Some(file) = file else {
            self.flash = Some(format!("no file linked to {}", doc.name));
            return Effect::Redraw;
        };
        let path = file.path.clone();
        self.flash = Some(format!("opening {path}"));
        Effect::Open(path)
    }

    /// Moves the query cursor, clamped to the query. Idle when it cannot move.
    fn query_cursor_to(&mut self, position: usize) -> Effect {
        let position = position.min(self.query.chars().count());
        if position == self.query_cursor {
            return Effect::Idle;
        }
        self.query_cursor = position;
        Effect::Redraw
    }

    fn type_char(&mut self, c: char) {
        self.query_cursor = self.query_cursor.min(self.query.chars().count());
        self.query.insert(byte_index(&self.query, self.query_cursor), c);
        self.query_cursor += 1;
        self.on_new = false;
        self.requery();
    }

    fn rub_out(&mut self) {
        self.query_cursor = self.query_cursor.min(self.query.chars().count());
        if self.query_cursor == 0 {
            return;
        }
        let start = byte_index(&self.query, self.query_cursor - 1);
        let end = byte_index(&self.query, self.query_cursor);
        self.query.replace_range(start..end, "");
        self.query_cursor -= 1;
        self.on_new = false;
        self.requery();
    }

    fn open_picker(&mut self, purpose: Purpose) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        self.picker = Some(Picker::new(purpose));
        Effect::Redraw
    }

    /// Does what a panel row chose for `purpose`.
    fn choose(&mut self, purpose: &Purpose, choice: Choice) -> Effect {
        let (id, change) = match (purpose, choice) {
            (_, Choice::Expiring) => return update(self, Msg::ToggleExpiring),
            (_, Choice::Scans) => return update(self, Msg::ToggleScans),
            (_, Choice::OldVersions) => {
                self.filter.old_versions = !self.filter.old_versions;
                self.reset_list();
                return Effect::Redraw;
            }
            (_, Choice::ClearAll) => {
                self.filter = Filter::ALL;
                self.scan_search = ScanSearch::Off;
                self.reset_list();
                return Effect::Redraw;
            }
            (Purpose::Bundles(doc), Choice::Bundle(bundle)) => {
                return self.tick_bundle(doc, Some(&bundle))
            }
            (Purpose::Bundles(doc), Choice::NewBundle) => return self.tick_bundle(doc, None),
            (Purpose::Member { doc, bundle }, choice) => {
                return self.change_member(doc, bundle, choice)
            }
            (Purpose::File { .. }, Choice::Attach) => return self.open_edit(Field::Attach),
            (Purpose::File { doc, index }, choice @ (Choice::MakePrimary | Choice::Detach)) => {
                let Some(stored) = self.store.get(doc) else { return Effect::Redraw };
                let mut files = stored.files.clone();
                if choice == Choice::Detach {
                    if *index < files.len() {
                        files.remove(*index);
                    }
                } else {
                    for (i, file) in files.iter_mut().enumerate() {
                        file.primary = i == *index;
                    }
                }
                (doc, self.flip("doc", doc, "files", crate::doc::files_value(&files)))
            }
            (Purpose::Renews(doc), Choice::Renew(older)) => {
                (doc, self.flip("doc", doc, "supersedes", older.map(Into::into)))
            }
            _ => return Effect::Redraw,
        };
        if let Some(effect) = self.refused() {
            return effect;
        }
        self.append(change, Landed::on(id, "saved"))
    }

    /// Changes how version `doc` is in `bundle`: another version in its
    /// place, one soft copy or all, or out of it altogether.
    fn change_member(&mut self, doc: &str, bundle: &str, choice: Choice) -> Effect {
        let entries =
            |id: &str| self.store.get(id).map(|doc| doc.bundles.clone()).unwrap_or_default();
        let mut mine = entries(doc);
        let Some(at) = mine.iter().position(|entry| entry.bundle == bundle) else {
            return Effect::Redraw;
        };
        let name = self.store.bundle(bundle).map_or(bundle, |b| b.name.as_str()).to_string();
        let mut theirs = None;
        let note = match choice {
            Choice::UseVersion(other) => {
                mine.remove(at);
                let mut list = entries(&other);
                if !list.iter().any(|entry| entry.bundle == bundle) {
                    list.push(crate::Membership { bundle: bundle.to_string(), file: None });
                }
                theirs = Some((other, list));
                format!("{name} now holds that version")
            }
            Choice::UseFile(file) => {
                mine[at].file = file;
                "saved".to_string()
            }
            Choice::Leave => {
                mine.remove(at);
                format!("taken out of {name}")
            }
            _ => return Effect::Redraw,
        };
        let tick = |id: &str, list: &[crate::Membership]| {
            self.flip("doc", id, "bundles", crate::doc::memberships_value(list))
        };
        let mut change = tick(doc, &mine);
        if let Some((other, list)) = theirs {
            change = change.then(tick(&other, &list));
        }
        self.append(change, Landed::saying(note))
    }

    /// A tap on a Details row: the checkbox toggles at once, any other row is
    /// selected, and a tap on the selected row does what `Enter` does.
    fn record_tap(&mut self, index: usize) -> Effect {
        let Some(doc) = self.current() else { return Effect::Idle };
        let rows = crate::detail::rows(doc);
        let Some(row) = rows.get(index) else { return Effect::Idle };
        if *row == crate::detail::Row::DigitalOnly {
            self.set_record_cursor(index);
            self.toggle_digital_only()
        } else if index == self.record_cursor() {
            self.drill()
        } else {
            self.set_record_cursor(index);
            Effect::Redraw
        }
    }

    /// Ticks or unticks digital only on the current document.
    ///
    /// Ticking it takes the hard copy out of its location in the same write,
    /// because the two are one field.
    fn toggle_digital_only(&mut self) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let Some(doc) = self.current() else { return Effect::Idle };
        let id = doc.id.clone();
        let place = self.store.place(doc);
        let ticking = doc.location.as_deref() != Some(crate::place::DIGITAL_ONLY);
        let change =
            self.flip("doc", &id, "location", ticking.then(|| crate::place::DIGITAL_ONLY.into()));
        let note = match (ticking, place.is_empty()) {
            (true, false) => format!("digital only — no longer filed in {place}"),
            (true, true) => "digital only".into(),
            (false, _) => "has a hard copy — unfiled".into(),
        };
        self.append(change, Landed::on(&id, note))
    }

    /// Opens the location picker on the current document.
    fn open_locations(&mut self) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let Some(doc) = self.current() else { return Effect::Idle };
        self.locpick = Some(crate::locpick::LocationPicker::file(&self.store, &doc.id));
        self.show_details();
        self.flash = self.store.locations.loop_message();
        Effect::Redraw
    }

    /// Creates a location named `name` inside `parent` and files the document's
    /// hard copy in it, as one change; or says why it cannot.
    fn create_and_file(
        &mut self,
        doc: &str,
        name: &str,
        parent: Option<&str>,
    ) -> Result<Effect, String> {
        if let Some(reason) = self.refusal() {
            return Err(reason);
        }
        let tree = &self.store.locations;
        if name.is_empty() {
            return Err("type a name first".into());
        }
        tree.clash(parent, name, None)?;
        if self.store.get(doc).is_none() {
            return Ok(Effect::Redraw);
        }
        let id = self.mint("location", name);
        let path =
            parent.map_or_else(|| name.to_string(), |p| format!("{} › {name}", tree.path(p)));
        let location = crate::Location {
            id: id.clone(),
            name: name.to_string(),
            parent: parent.map(Into::into),
        };
        let create = Change::create("location", &id, location.as_fields());
        let change = create.then(self.flip("doc", doc, "location", Some(id.into())));
        Ok(self.append(change, Landed::on(doc, format!("filed in {path}"))))
    }

    /// The location the open picker's cursor stands for, if any.
    fn picked_location(&self) -> Option<String> {
        self.locpick.as_ref().and_then(|picker| picker.chosen()).map(str::to_string)
    }

    /// Opens the bottom line on the picked location's name.
    fn open_rename(&mut self) -> Effect {
        let Some(id) = self.picked_location() else {
            self.flash = Some("pick a location".into());
            return Effect::Redraw;
        };
        let name = self.store.locations.get(&id).map(|l| l.name.clone());
        let edit = crate::edit::Edit::new(Target::Location(id), Field::Name, name.as_deref());
        self.edit = Some(edit);
        Effect::Redraw
    }

    /// Renames location `id`, or says why it cannot be.
    fn rename(&self, id: &str, name: &str) -> Result<(Change, Landed), String> {
        let tree = &self.store.locations;
        let parent = tree.parent(id);
        tree.clash(parent, name, Some(id))?;
        let change = self.flip("location", id, "name", Some(name.into()));
        Ok((change, Landed::saying(format!("renamed to {name}"))))
    }

    /// Swaps the picker for the tree Move… chooses a destination in.
    fn open_move(&mut self) -> Effect {
        let Some(id) = self.picked_location() else {
            self.flash = Some("pick a location".into());
            return Effect::Redraw;
        };
        let mut moving = crate::locpick::LocationPicker::moving(&id);
        moving.back = self.locpick.take().map(Box::new);
        self.locpick = Some(moving);
        Effect::Redraw
    }

    /// Moves `id` into `into` (`None` for the top level), or says why not.
    fn move_location(&mut self, id: &str, into: Option<&str>) -> Result<Effect, String> {
        if let Some(reason) = self.refusal() {
            return Err(reason);
        }
        let tree = &self.store.locations;
        let Some(location) = tree.get(id) else { return Ok(Effect::Redraw) };
        if tree.parent(id) == into {
            return Err("it is already there".into());
        }
        if into.is_some_and(|into| tree.is_within(into, id)) {
            return Err("a location cannot go inside itself".into());
        }
        tree.clash(into, &location.name, Some(id))?;
        let place = tree.place(into);
        let note = format!("moved {} into {place}", location.name);
        let change = self.flip("location", id, "parent", into.map(Into::into));
        Ok(self.append(change, Landed::saying(note)))
    }

    /// Deletes the picked location at once when it is empty, or arms the
    /// deletion with a caution naming what it holds.
    fn remove_location(&mut self) -> Effect {
        let Some(id) = self.picked_location() else {
            self.flash = Some("pick a location".into());
            return Effect::Redraw;
        };
        let held = self.store.held(&id);
        let inside = self.store.locations.subtree(&id).len().saturating_sub(1);
        if held == 0 && inside == 0 {
            return self.remove(&id);
        }
        let name = self.store.locations.get(&id).map_or("", |l| l.name.as_str());
        let count = crate::layout::plural;
        let holds = match (inside, held) {
            (0, held) => count(held, "hard copy", "hard copies"),
            (inside, 0) => count(inside, "location", "locations"),
            (inside, held) => format!(
                "{} and {}",
                count(inside, "location", "locations"),
                count(held, "hard copy", "hard copies")
            ),
        };
        self.flash = Some(format!(
            "Caution: {name} holds {holds}. Press d again to delete and remove their location \
             attributes"
        ));
        self.armed = Some(Armed::Delete);
        Effect::Redraw
    }

    /// Deletes a location and everything inside it, as one change whose way
    /// back recreates each of them where it was.
    fn remove(&mut self, id: &str) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let tree = &self.store.locations;
        let doomed: Vec<&crate::Location> =
            tree.subtree(id).into_iter().filter_map(|at| tree.get(at)).collect();
        let Some(name) = doomed.first().map(|l| l.name.clone()) else { return Effect::Redraw };
        let Some(change) = doomed
            .iter()
            .rev()
            .map(|l| Change::create("location", &l.id, l.as_fields()).reversed())
            .reduce(Change::then)
        else {
            return Effect::Redraw;
        };
        let parent = tree.parent(id).map(str::to_string);
        let root_goes = self
            .locpick
            .as_ref()
            .and_then(|picker| picker.root.as_deref())
            .is_some_and(|root| tree.is_within(root, id));
        if let Some(picker) = &mut self.locpick {
            picker.cursor = parent
                .clone()
                .map_or(crate::locpick::Target::Root, crate::locpick::Target::Location);
            if root_goes {
                picker.root = parent;
            }
        }
        self.append(change, Landed::saying(format!("deleted {name}")))
    }

    /// Files a document's hard copy in `location`, recording the way back.
    fn file_in(&mut self, doc: &str, location: &str) -> Result<Effect, String> {
        if let Some(reason) = self.refusal() {
            return Err(reason);
        }
        let change = self.flip("doc", doc, "location", Some(location.into()));
        let note = format!("filed in {}", self.store.locations.path(location));
        Ok(self.append(change, Landed::on(doc, note)))
    }

    /// Links the typed path as one more soft copy of `doc`, the first being
    /// primary, or says why it cannot be.
    fn attach(&self, doc: &str, path: String, field: &str) -> Result<Change, String> {
        if self.root.as_ref().is_some_and(|root| root.join(&path).is_dir()) {
            return Err(format!("{path} is a folder — choose a file in it"));
        }
        let mut files = self.store.get(doc).map(|doc| doc.files.clone()).unwrap_or_default();
        if files.iter().any(|file| file.path == path) {
            return Err(format!("{path} is already attached"));
        }
        let primary = files.is_empty();
        files.push(crate::FileRef { label: String::new(), path, primary });
        Ok(self.flip("doc", doc, field, crate::doc::files_value(&files)))
    }

    /// Saves an open edit, or says why it cannot be saved. An edit that
    /// changes nothing closes without a write; one that writes stays open,
    /// marked saving, until the journal answers.
    fn save(&mut self, edit: &mut crate::edit::Edit) -> Result<Effect, String> {
        if let Some(reason) = self.refusal() {
            return Err(reason);
        }
        let (change, landed) = match (edit.target.clone(), edit.field) {
            (Target::Doc(id), Field::Attach) => {
                let path = edit
                    .value()?
                    .and_then(|value| value.as_str().map(str::to_owned))
                    .ok_or("type a path, or choose one from the list")?;
                (self.attach(&id, path, edit.journal_field())?, Landed::on(&id, "saved"))
            }
            (Target::Doc(id), _) => {
                let change = self.flip("doc", &id, edit.journal_field(), edit.value()?);
                (change, Landed::on(&id, "saved"))
            }
            (Target::Bundle(id), _) => {
                (self.flip("bundle", &id, edit.journal_field(), edit.value()?), Landed::default())
            }
            (Target::Location(id), _) => {
                edit.value()?;
                self.rename(&id, edit.buffer.trim())?
            }
            (Target::NewDoc | Target::NewBundle, _) => {
                edit.value()?;
                edit.saving = true;
                let name = edit.buffer.trim().to_string();
                return Ok(if edit.target == Target::NewDoc {
                    self.create_doc(&name)
                } else {
                    self.create_bundle(&name)
                });
            }
        };
        if change.forward == change.back {
            return Ok(Effect::Redraw);
        }
        edit.saving = true;
        Ok(self.append(change, landed))
    }

    /// Replaces the query, puts the cursor at its end, and requeries.
    fn set_query(&mut self, text: String) {
        self.query_cursor = text.chars().count();
        self.query = text;
        self.requery();
    }

    /// `Esc` peels one layer per press: a panel, the sheet, the Bundles search,
    /// a pushed view, the query, the filters, then arms the quit. On Termux
    /// `Esc` also dismisses the keyboard, so every press must undo something
    /// visible before one can quit.
    fn peel(&mut self, was_armed: bool) -> Effect {
        if let Some(picker) = &mut self.picker {
            if peel_filter(&mut picker.filter, &mut picker.cursor) {
                self.picker = None;
            }
            return Effect::Redraw;
        }
        if self.sheet {
            self.sheet = false;
            return Effect::Redraw;
        }
        if matches!(self.views.last(), Some(View::Bundles { .. })) && !self.query.is_empty() {
            self.set_query(String::new());
            self.reset_bundles();
        } else if let Some(View::Bundles { query, .. }) = self.views.last().cloned() {
            self.views.pop();
            self.set_query(query);
        } else if !self.views.is_empty() {
            self.views.pop();
        } else if !self.query.is_empty() {
            self.set_query(String::new());
        } else if self.filter != Filter::ALL {
            self.filter = Filter::ALL;
            self.requery();
        } else if was_armed {
            return Effect::Quit;
        } else {
            self.armed = Some(Armed::Esc);
        }
        Effect::Redraw
    }

    /// Scroll the window, dragging the cursor along so the selection never
    /// scrolls off screen.
    fn scroll(&mut self, delta: i32) {
        if self.rows.is_empty() {
            return;
        }
        let last = self.rows.len() - 1;
        let visible = self.visible_rows().max(1);
        let max_offset = self.rows.len().saturating_sub(visible);
        let next = i64::from(delta) + i64::try_from(self.offset).unwrap_or(i64::MAX);
        self.offset = usize::try_from(next.max(0)).unwrap_or(0).min(max_offset);
        self.cursor = self.cursor.clamp(self.offset, (self.offset + visible - 1).min(last));
    }

    /// Keep the cursor inside the visible window. Called by the renderer before
    /// it picks which rows to build, and by the update half after a jump.
    pub fn scroll_into_view(&mut self, visible: usize) {
        let visible = visible.max(1);
        if self.cursor < self.offset {
            self.offset = self.cursor;
        } else if self.cursor >= self.offset + visible {
            self.offset = self.cursor + 1 - visible;
        }
        self.offset = self.offset.min(self.rows.len().saturating_sub(visible));
    }

    /// Which document row a tap landed on, if any.
    fn row_at(&self, row: u16) -> Option<usize> {
        let list = self.list;
        if list.height == 0 || row < list.top || row >= list.top + list.height {
            return None;
        }
        let slot = (row - list.top) / list.row_height.max(1);
        let index = self.offset + slot as usize;
        (index < self.rows.len()).then_some(index)
    }

    /// Runs one verb of the Space sheet through the same paths the keyboard reaches.
    fn run(&mut self, act: crate::sheet::Act) -> Effect {
        self.sheet = false;
        match act {
            crate::sheet::Act::Filter => {
                self.picker = Some(Picker::new(Purpose::Filter));
                Effect::Redraw
            }
            crate::sheet::Act::Edit => {
                if matches!(self.views.last(), Some(View::Bundle { .. })) {
                    self.bundle_verb('e')
                } else {
                    self.record_verb('e')
                }
            }
            crate::sheet::Act::Bundles => {
                if self.detail() {
                    self.open_bundle_checklist()
                } else {
                    self.open_bundles()
                }
            }
            crate::sheet::Act::Undo => self.step(Direction::Undo),
            crate::sheet::Act::Redo => self.step(Direction::Redo),
            crate::sheet::Act::Rename => self.open_rename(),
            crate::sheet::Act::Move => self.open_move(),
            crate::sheet::Act::Remove => self.remove_location(),
            crate::sheet::Act::Location => self.open_locations(),
            crate::sheet::Act::Delete => {
                if matches!(self.views.last(), Some(View::Bundle { .. })) {
                    self.delete_bundle()
                } else {
                    self.delete()
                }
            }
            crate::sheet::Act::NewVersion => self.new_version(),
            crate::sheet::Act::Versions => self.open_versions(),
            crate::sheet::Act::Quit => Effect::Quit,
        }
    }

    /// Puts the list back at its top and filters it afresh.
    fn reset_list(&mut self) {
        self.cursor = 0;
        self.offset = 0;
        self.requery();
    }

    /// Move the record's selector. The same motions the list understands, over
    /// a much shorter list, so paging is clamped rather than wrapped.
    fn move_record(&mut self, motion: Motion) {
        let Some(doc) = self.current() else { return };
        let len = crate::detail::rows(doc).len();
        self.set_record_cursor(moved(self.record_cursor(), len, motion, len));
    }

    /// A bare letter on the record surface; `e` edits the selected row.
    ///
    /// An unknown letter says so rather than doing nothing: on this surface a
    /// letter is a verb, and silence would read as a dropped keypress.
    fn record_verb(&mut self, key: char) -> Effect {
        let Some(doc) = self.current() else { return Effect::Idle };
        let id = doc.id.clone();
        let rows = crate::detail::rows(doc);
        let row = rows.get(self.record_cursor().min(rows.len().saturating_sub(1))).copied();
        match (key, row) {
            ('e', Some(crate::detail::Row::Editable(field))) => self.open_edit(field),
            ('e', Some(crate::detail::Row::File(index))) => {
                self.open_picker(Purpose::File { doc: id, index })
            }
            ('e', Some(crate::detail::Row::Files)) => self.open_edit(Field::Attach),
            ('e', Some(crate::detail::Row::DigitalOnly)) => self.toggle_digital_only(),
            ('e', Some(crate::detail::Row::Location)) => self.open_locations(),
            ('e', Some(crate::detail::Row::Renews)) => self.open_picker(Purpose::Renews(id)),
            ('e', Some(crate::detail::Row::Bundles)) => self.open_bundle_checklist(),
            // Undo is about the session, not about the row — but it is bound
            // here because this is the surface where a bare letter is a verb,
            // and it is where a write has just been made.
            ('u', _) => self.step(Direction::Undo),
            ('r', _) => self.step(Direction::Redo),
            ('d', _) => self.delete(),
            _ => self.no_verb(key),
        }
    }

    /// Drops mouse reporting for one tap, which is the only way Termux raises
    /// the soft keyboard; the next key press turns it back on. The shell
    /// applies it by reconciling against [`Model::mouse_on`].
    fn raise_keyboard(&mut self) -> Effect {
        self.mouse_on = false;
        Effect::Redraw
    }

    /// Opens an edit on the current document's field, on its Details view,
    /// seeded with what is stored so an edit starts as a correction.
    pub fn open_edit(&mut self, field: Field) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let Some(doc) = self.current() else {
            self.flash = Some("nothing to edit".into());
            return Effect::Redraw;
        };
        let current = match field {
            Field::Name => Some(doc.name.clone()),
            Field::Expiry => doc.expiry_date.clone(),
            Field::Issued => doc.issue_date.clone(),
            Field::Tags => Some(doc.tags.join(" ")),
            Field::Notes => Some(doc.notes.clone()),
            Field::Attach => None,
        };
        let mut edit =
            crate::edit::Edit::new(Target::Doc(doc.id.clone()), field, current.as_deref());
        if field == Field::Attach {
            edit.list = self.root.clone().map(|root| Completion::new(root, false, None, ""));
        }
        self.edit = Some(edit);
        self.show_details();
        Effect::Redraw
    }

    /// Starts a new document by asking for its name, the one field it cannot
    /// exist without; everything else is filled in on its Details view.
    fn open_new(&mut self) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        self.edit = Some(crate::edit::Edit::new(Target::NewDoc, Field::Name, None));
        Effect::Redraw
    }

    /// Creates the document the query names and opens it, or asks for a name
    /// when nothing is typed.
    fn create_from_query(&mut self) -> Effect {
        let name = self.query.trim().to_string();
        if name.is_empty() {
            self.open_new()
        } else {
            self.create_doc(&name)
        }
    }

    /// Makes a new version of the current document: its name, tags and
    /// hard copy location carry over, and it replaces the current one.
    fn new_version(&mut self) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let Some(old) = self.current() else { return Effect::Idle };
        let id = self.mint("doc", &old.name);
        let carried = old
            .as_fields()
            .into_iter()
            .filter(|(field, _)| matches!(*field, "name" | "tags" | "location"))
            .chain([("supersedes", old.id.clone().into())])
            .collect();
        let change = Change::create("doc", &id, carried);
        let landed =
            Landed { note: Some("new version".into()), replace: true, ..Landed::created(&id) };
        self.append(change, landed)
    }

    /// Tombstones the record's document on the second `d`.
    ///
    /// The way back recreates it with every field it had. Other documents'
    /// references to it are left alone, so undo has only this one to restore.
    fn delete(&mut self) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let Some(doc) = self.current() else {
            self.flash = Some("nothing to delete".into());
            return Effect::Redraw;
        };
        if self.armed != Some(Armed::Delete) {
            self.armed = Some(Armed::Delete);
            return Effect::Redraw;
        }
        let id = doc.id.clone();
        let change = Change::create("doc", &id, doc.as_fields()).reversed();
        self.armed = None;
        self.append(change, Landed::saying("deleted — u to undo"))
    }

    /// Undoes the last write, or redoes the last undo: pops from one stack,
    /// appends, and lets [`Msg::Saved`] move the change to the other. Undo
    /// is not itself undoable, so `u u u` walks back three writes.
    fn step(&mut self, direction: Direction) -> Effect {
        if let Some(effect) = self.refused() {
            return effect;
        }
        let stack = if direction == Direction::Undo { &mut self.undo } else { &mut self.redo };
        let Some(change) = stack.pop() else {
            self.flash = Some(
                if direction == Direction::Undo {
                    "nothing to undo — this session has not written yet"
                } else {
                    "nothing to redo — nothing has been undone"
                }
                .into(),
            );
            return Effect::Redraw;
        };
        let drafts =
            if direction == Direction::Undo { change.back.clone() } else { change.forward.clone() };
        // The step need not be about the document under the cursor.
        let landed = Landed {
            anchor: drafts.first().map(|draft| draft.id.clone()),
            note: Some(if direction == Direction::Undo { "undone" } else { "redone" }.into()),
            ..Landed::default()
        };
        self.pending = Some(Pending { change, direction, landed });
        Effect::Append(drafts)
    }

    /// A new id for a record of `ent` named `name`, unused by any of its kind.
    fn mint(&self, ent: &str, name: &str) -> String {
        let mut taken: std::collections::BTreeSet<&str> = match ent {
            "doc" => self.store.docs.iter().map(|doc| doc.id.as_str()).collect(),
            "bundle" => self.store.bundles.iter().map(|bundle| bundle.id.as_str()).collect(),
            _ => self.store.locations.iter().map(|location| location.id.as_str()).collect(),
        };
        taken.extend(
            self.store.retired.iter().filter(|(kind, _)| kind == ent).map(|(_, id)| id.as_str()),
        );
        crate::id::mint(name, self.write.device().unwrap_or_default(), &taken)
    }

    /// Take a re-folded store, keeping the user's place in it.
    ///
    /// A save can reorder the list — an expiry edit moves a row under the
    /// `expiring` filter — or push the document out of it entirely, so the row
    /// index the cursor held before the fold means nothing after it. The anchor
    /// is therefore the **document id**.
    fn adopt(&mut self, store: Store, anchor: &str) {
        self.store = store;
        self.requery();
        let found = self.rows.iter().position(|&i| self.store.docs[i].id == anchor);
        if let Some(position) = found {
            self.on_new = false;
            self.cursor = position;
            self.scroll_into_view(self.visible_rows());
        }
    }
}

/// The byte offset of the `chars`-th character, or the end of `text`.
fn byte_index(text: &str, chars: usize) -> usize {
    text.char_indices().nth(chars).map_or(text.len(), |(index, _)| index)
}

impl Model {
    /// A save landed: the change goes on the stack that reverses it, and the
    /// store and views follow what was written.
    fn saved(&mut self, store: Store) -> Effect {
        // The edit whose save this is closes now, not at `Enter`: until the
        // journal answers, the value on screen is a hope.
        self.edit.take_if(|edit| edit.saving);
        self.armed = self.armed.filter(|armed| *armed == Armed::Esc);
        let landed = match self.pending.take() {
            Some(Pending { change, direction, landed }) => {
                match direction {
                    Direction::Forward | Direction::Redo => self.undo.push(change),
                    Direction::Undo => self.redo.push(change),
                }
                if direction == Direction::Forward {
                    self.redo.clear();
                }
                landed
            }
            None => Landed::default(),
        };
        let anchor = landed.anchor.or_else(|| self.current().map(|doc| doc.id.clone()));
        self.adopt(store, anchor.as_deref().unwrap_or_default());
        self.prune_views();
        if let Some(view) = landed.open.filter(|view| view.alive_in(&self.store)) {
            if landed.replace {
                self.views.pop();
            }
            self.views.push(view);
        }
        self.flash = Some(landed.note.unwrap_or_else(|| "saved".into()));
        Effect::Redraw
    }

    /// A save did not land; the typing survives.
    fn save_failed(&mut self, reason: String, permanent: bool) -> Effect {
        if let Some(edit) = &mut self.edit {
            edit.saving = false;
            edit.armed_discard = false;
        }
        // A write that never landed cannot be taken back, so the change is
        // dropped rather than left on a stack to reverse something nobody
        // did. A refused *undo* likewise stays on the undo stack — it is
        // still the last thing this session wrote.
        if let Some(Pending { change, direction, .. }) = self.pending.take() {
            match direction {
                Direction::Undo => self.undo.push(change),
                Direction::Redo => self.redo.push(change),
                Direction::Forward => {}
            }
        }
        self.armed = self.armed.filter(|armed| *armed == Armed::Esc);
        self.flash = Some(reason.clone());
        // A refusal that will refuse again takes editing off the table for
        // the session, rather than inviting the same disappointment on
        // every save. The typing survives either way.
        if permanent {
            self.write = WriteState::Off(reason);
        }
        Effect::Redraw
    }

    /// Turns scan-text search off, or on, reading the scan text first.
    fn toggle_scans(&mut self) -> Effect {
        match self.scan_search {
            ScanSearch::On | ScanSearch::Loading => {
                self.scan_search = ScanSearch::Off;
                self.requery();
                Effect::Redraw
            }
            // Already read once: turning it back on costs nothing.
            ScanSearch::Off if self.scans.is_some() => {
                self.scan_search = ScanSearch::On;
                self.requery();
                Effect::Redraw
            }
            ScanSearch::Off => {
                self.scan_search = ScanSearch::Loading;
                Effect::LoadScans
            }
        }
    }

    fn scans_loaded(&mut self, scans: std::sync::Arc<crate::scans::Scans>) -> Effect {
        // A load that finished after the user changed their mind is kept,
        // not applied: the work is done, and turning it on again is instant.
        let count = scans.len();
        self.scans = Some(scans);
        if self.scan_search == ScanSearch::Loading {
            self.scan_search = ScanSearch::On;
            self.flash = Some(if count == 0 {
                "no scan text yet — the desktop satellite writes it".into()
            } else {
                format!("searching inside {count} scanned files")
            });
            self.requery();
        }
        Effect::Redraw
    }

    /// A tap, routed to whatever is drawn under it.
    fn tap(&mut self, col: u16, row: u16) -> Effect {
        self.flash = None;
        // A pushed record covers the list, so the chrome under it belongs to
        // a surface you cannot see. Tapping it would mutate that surface
        // blind — the stack metaphor has to hold for touch too.
        let pushed = self.pane() && !crate::layout::splits(self.cols);
        let bundles = self.views.iter().any(|view| matches!(view, View::Bundles { .. }));
        let (top, bottom) = search_zone(self);
        if self.leader_zone.hit(col, row) {
            if self.sheet {
                self.sheet = false;
                Effect::Redraw
            } else {
                update(self, Msg::Leader)
            }
        } else if self.count_zone.hit(col, row) && !pushed && !bundles {
            // You tap the number that told you there were three; a second
            // tap turns the filter off again.
            update(self, Msg::ToggleExpiring)
        } else if row >= top && row <= bottom {
            if pushed {
                Effect::Idle
            } else {
                // Tapping the field is how every phone app says "I want to
                // type", so it is what drops mouse reporting for one tap.
                self.raise_keyboard()
            }
        } else if let Some(index) = self.record.at(col, row) {
            match self.views.last() {
                Some(View::Details { .. }) => self.record_tap(index),
                Some(View::Versions { .. }) => self.versions_tap(index),
                Some(View::Bundle { .. }) => self.bundle_tap(index),
                Some(View::Bundles { .. }) | None => Effect::Idle,
            }
        } else if let Some(index) = self.bundle_list.at(col, row) {
            self.bundles_tap(index)
        } else if self.new_row == Some(row) && !pushed {
            if self.on_new {
                self.drill()
            } else {
                self.on_new = true;
                Effect::Redraw
            }
        } else if let Some(index) = self.row_at(row) {
            // Two taps, never a double-tap timer: timing gestures are
            // miserable on a laggy terminal.
            let id = self.store.docs[self.rows[index]].id.clone();
            let shown = match self.views.as_slice() {
                [] => true,
                [View::Details { doc, .. }] => *doc == id,
                _ => false,
            };
            if index == self.cursor && !self.on_new && shown {
                self.drill()
            } else {
                self.on_new = false;
                self.cursor = index;
                // Beside the list, the views give way to the tapped row's
                // Details view.
                if !self.views.is_empty() {
                    self.views = vec![View::Details { doc: id, cursor: 0 }];
                }
                Effect::Redraw
            }
        } else {
            Effect::Idle
        }
    }
}

/// Restores mouse reporting and disarms on a press, and returns whether the
/// quit was armed before it.
fn note_press(model: &mut Model, msg: &Msg) -> bool {
    let was_armed = model.armed == Some(Armed::Esc);
    // A key press means the IME affordance has done its job. Restoring mouse
    // reporting here, once, keeps the drop from becoming a mode.
    if is_key(msg) {
        model.mouse_on = true;
        model.flash = None;
    }
    // `d d` and `Esc Esc` act only on consecutive presses: any other key or
    // tap disarms. A resize does not, since Termux's `Esc` also drops the
    // keyboard and resizes the terminal.
    match model.armed {
        Some(Armed::Delete) if *msg != Msg::Char('d') && disarms(msg) => {
            model.armed = None;
            model.flash = None;
        }
        Some(Armed::Esc) if *msg != Msg::Esc && disarms(msg) => model.armed = None,
        _ => {}
    }
    was_armed
}

/// The keys of whatever is in front, innermost first: an edit, the location
/// picker, a panel, the sheet, then the view. `ctrl+q`/`ctrl+c` and worker
/// messages fall through every one, so nothing is trapped.
fn in_front(model: &mut Model, msg: &Msg) -> Option<Effect> {
    edit_key(model, msg)
        .or_else(|| locpick_key(model, msg))
        .or_else(|| picker_key(model, msg))
        .or_else(|| sheet_key(model, msg))
        .or_else(|| match model.views.last() {
            Some(View::Versions { .. }) => versions_key(model, msg),
            Some(View::Bundles { .. }) => bundles_key(model, msg),
            Some(View::Bundle { .. }) => bundle_key(model, msg),
            Some(View::Details { .. }) | None => None,
        })
}

/// Applies one message: the only entry point to state change.
pub fn update(model: &mut Model, msg: Msg) -> Effect {
    let was_armed = note_press(model, &msg);
    if let Some(effect) = in_front(model, &msg) {
        return effect;
    }

    match msg {
        Msg::Quit => Effect::Quit,
        Msg::Tab => Effect::Idle,
        Msg::Esc => model.peel(was_armed),
        Msg::Saved(store) => model.saved(*store),
        Msg::OpenFailed(reason) => {
            model.flash = Some(reason);
            Effect::Redraw
        }
        Msg::Day { today, warn_until } => {
            model.today = today;
            model.warn_until = warn_until;
            model.requery();
            Effect::Redraw
        }
        Msg::Reloaded(store) => {
            if *store == model.store {
                return Effect::Idle;
            }
            let anchor = model.current().map(|doc| doc.id.clone()).unwrap_or_default();
            model.adopt(*store, &anchor);
            model.prune_views();
            model.flash = Some("updated from another device".into());
            Effect::Redraw
        }
        Msg::SaveFailed { reason, permanent } => model.save_failed(reason, permanent),
        Msg::Leader => {
            model.sheet = true;
            Effect::Redraw
        }
        Msg::Enter => model.drill(),
        Msg::Left | Msg::Right if model.detail() => Effect::Idle,
        Msg::Left => model.query_cursor_to(model.query_cursor.saturating_sub(1)),
        Msg::Right => model.query_cursor_to(model.query_cursor + 1),
        // Home and End belong to the query once there is one, and to the list
        // until then — the same rule that makes Space the leader.
        Msg::Move(Motion::Home) if !model.detail() && !model.query.is_empty() => {
            model.query_cursor_to(0)
        }
        Msg::Move(Motion::End) if !model.detail() && !model.query.is_empty() => {
            model.query_cursor_to(usize::MAX)
        }
        // The record owns `↑`/`↓` while it is open, so it never becomes a
        // different document while it is being read.
        Msg::Move(motion) => {
            if model.detail() {
                model.move_record(motion);
            } else {
                model.move_cursor(motion);
            }
            Effect::Redraw
        }
        Msg::Backspace => {
            model.rub_out();
            Effect::Redraw
        }
        // On the record a letter is a verb; on the list every letter is search
        // text, and Space on an empty query opens the leader.
        Msg::Char(' ') if model.detail() => update(model, Msg::Leader),
        Msg::Char(c) if model.detail() => model.record_verb(c),
        Msg::Char(' ') if model.query.is_empty() => update(model, Msg::Leader),
        Msg::Char(c) => {
            model.type_char(c);
            Effect::Redraw
        }
        Msg::ToggleScans => model.toggle_scans(),
        Msg::ScansLoaded(scans) => model.scans_loaded(scans),
        Msg::ToggleExpiring => {
            model.filter.expiring = !model.filter.expiring;
            model.reset_list();
            Effect::Redraw
        }
        Msg::Undo => {
            model.sheet = false;
            model.step(Direction::Undo)
        }
        Msg::Redo => {
            model.sheet = false;
            model.step(Direction::Redo)
        }
        Msg::Resize { cols, rows } => {
            model.cols = cols;
            model.rows_on_screen = rows;
            Effect::Redraw
        }
        Msg::Scroll(delta) => {
            model.scroll(delta);
            Effect::Redraw
        }
        Msg::Tap { col, row } => model.tap(col, row),
    }
}

/// Keys while a field is being edited. `None` falls through, which is how
/// `ctrl+q`/`ctrl+c` and worker messages still work from inside an edit.
///
/// Every printable is the value; `Enter` saves and `Esc` discards, a dirty
/// edit taking two; arrows, taps and scrolls are swallowed, as they would act
/// on the surface the edit covers. The live list under a path is the one
/// exception, in [`attach_key`].
fn edit_key(model: &mut Model, msg: &Msg) -> Option<Effect> {
    let mut edit = model.edit.take()?;
    let (effect, open) = edit_step(model, &mut edit, msg);
    if open {
        model.edit = Some(edit);
    }
    effect
}

/// One key on an open edit: what it did, and whether the edit stays open.
fn edit_step(model: &mut Model, edit: &mut crate::edit::Edit, msg: &Msg) -> (Option<Effect>, bool) {
    // Any other key disarms, as it does for quitting.
    if !matches!(msg, Msg::Esc | Msg::Quit) {
        edit.armed_discard = false;
    }
    if let Some(effect) = attach_key(model, edit, msg) {
        return (Some(effect), true);
    }
    // A tap on a file row picked it; saving it is `Enter`.
    if let (Msg::Tap { .. }, Some(_)) = (msg, &edit.list) {
        return edit_step(model, edit, &Msg::Enter);
    }
    let effect = match msg {
        Msg::Char(c) => {
            edit.buffer.push(*c);
            crate::complete::follow(edit.list.as_mut(), &edit.buffer);
            Effect::Redraw
        }
        Msg::Backspace => {
            edit.buffer.pop();
            crate::complete::follow(edit.list.as_mut(), &edit.buffer);
            Effect::Redraw
        }
        // A second append of the same op is harmless to the fold but a lie
        // in the history.
        Msg::Enter if edit.saving => Effect::Idle,
        Msg::Enter => match model.save(edit) {
            Ok(effect) if edit.saving => effect,
            Ok(effect) => return (Some(effect), false),
            Err(complaint) => {
                // The typing survives a refusal: it is what needs correcting.
                model.flash = Some(complaint);
                Effect::Redraw
            }
        },
        Msg::Quit if edit.dirty() && !edit.armed_discard => {
            edit.armed_discard = true;
            model.flash = Some("unsaved edit — ^q again to quit without saving".into());
            Effect::Redraw
        }
        Msg::Esc if edit.saving => {
            model.flash = Some("saving — one moment".into());
            Effect::Redraw
        }
        Msg::Esc if edit.dirty() && !edit.armed_discard => {
            edit.armed_discard = true;
            Effect::Redraw
        }
        Msg::Esc => return (Some(Effect::Redraw), false),
        // Swallowed: they would act on the surface under the editor, and the
        // verb pressed again must not reseed the buffer.
        Msg::Undo
        | Msg::Redo
        | Msg::Move(_)
        | Msg::Left
        | Msg::Right
        | Msg::Leader
        | Msg::Tab
        | Msg::Tap { .. }
        | Msg::Scroll(_) => Effect::Idle,
        _ => return (None, true),
    };
    (Some(effect), true)
}

/// Keys for the live list under a path being attached: `↑`/`↓` choose a
/// row, `Tab` fills the chosen or top one, and `Enter` or a tap on a row picks
/// it — a folder opens, a file fills the line to be saved. A tap anywhere else
/// is inert. `None` leaves the key to the line.
fn attach_key(model: &Model, edit: &mut crate::edit::Edit, msg: &Msg) -> Option<Effect> {
    let list = edit.list.as_mut()?;
    let line = &mut edit.buffer;
    if let Some(finished) = list.key(line, msg) {
        return (!finished).then_some(Effect::Redraw);
    }
    match msg {
        Msg::Enter => {
            // A folder typed whole opens, as a chosen one does: a folder is
            // never a soft copy.
            let typed = line.trim();
            if typed.is_empty() || !model.root.as_ref()?.join(typed).is_dir() {
                return None;
            }
            if !typed.ends_with(['/', '\\']) {
                line.push('/');
            }
            list.typed(line);
        }
        Msg::Tap { col, row } => {
            let Some(at) = model.panel.at(*col, *row) else { return Some(Effect::Idle) };
            if list.pick(line, at) {
                return None;
            }
        }
        _ => return None,
    }
    Some(Effect::Redraw)
}

/// Keys while the Space sheet is open: a letter runs its verb, and nothing
/// searches it. `None` falls through, so `Esc` and `ctrl` keys keep their meaning.
fn sheet_key(model: &mut Model, msg: &Msg) -> Option<Effect> {
    if !model.sheet {
        return None;
    }
    match msg {
        Msg::Char(c) => {
            if let Some(item) = crate::sheet::items(model).into_iter().find(|item| item.key == *c) {
                return Some(model.run(item.act));
            }
            model.flash = Some(format!("no verb on `{c}` here"));
            Some(Effect::Redraw)
        }
        Msg::Tap { col, row } => {
            let tapped = model.panel.at(*col, *row);
            if let Some(item) = tapped.and_then(|at| crate::sheet::items(model).get(at).copied()) {
                return Some(model.run(item.act));
            }
            // The chip that opened the sheet closes it in the shared handler.
            if model.leader_zone.hit(*col, *row) {
                return None;
            }
            model.sheet = false;
            Some(Effect::Redraw)
        }
        Msg::Backspace | Msg::Enter | Msg::Move(_) | Msg::Left | Msg::Right => Some(Effect::Idle),
        _ => None,
    }
}

/// The index of `selected` in `items`, or the first when it is not there.
#[must_use]
pub fn position<T: PartialEq>(items: &[T], selected: &T) -> usize {
    items.iter().position(|item| item == selected).unwrap_or(0)
}

/// Where `motion` lands from `at` in a list of `len`, `page` rows to a page:
/// clamped, never wrapping, since a wrapping list on a phone is a way to lose
/// your place with a fat thumb.
fn moved(at: usize, len: usize, motion: Motion, page: usize) -> usize {
    let last = len.saturating_sub(1);
    match motion {
        Motion::Up => at.saturating_sub(1),
        Motion::Down => (at + 1).min(last),
        Motion::PageUp => at.saturating_sub(page),
        Motion::PageDown => (at + page).min(last),
        Motion::Home => 0,
        Motion::End => last,
    }
}

/// Moves `selected` within `items` by `motion`; a page is the whole list, as
/// these lists are short.
fn select<T: Clone + PartialEq>(items: &[T], selected: &mut T, motion: Motion) {
    let at = moved(position(items, selected), items.len(), motion, items.len());
    if let Some(next) = items.get(at) {
        *selected = next.clone();
    }
}

/// Clears a panel's search, or says the panel should close when there is none.
fn peel_filter(filter: &mut String, cursor: &mut usize) -> bool {
    *cursor = 0;
    if filter.is_empty() {
        return true;
    }
    filter.clear();
    false
}

/// Keys on the Bundles view: typing searches it, the arrows walk it, and
/// `Enter` opens a bundle or creates the one the search names.
fn bundles_key(model: &mut Model, msg: &Msg) -> Option<Effect> {
    let Some(View::Bundles { .. }) = model.views.last() else { return None };
    let entries = crate::bundles::entries(&model.store, &model.query);
    let effect = match msg {
        Msg::Char(' ') if model.query.is_empty() => update(model, Msg::Leader),
        Msg::Char(c) => {
            model.type_char(*c);
            model.reset_bundles();
            Effect::Redraw
        }
        Msg::Backspace => {
            model.rub_out();
            model.reset_bundles();
            Effect::Redraw
        }
        Msg::Move(Motion::Home | Motion::End) if !model.query.is_empty() => return None,
        Msg::Move(motion) => {
            if let Some(View::Bundles { selected, .. }) = model.views.last_mut() {
                select(&entries, selected, *motion);
            }
            Effect::Redraw
        }
        Msg::Enter => model.enter_bundles(),
        _ => return None,
    };
    Some(effect)
}

/// Keys on a bundle's Details view: the arrows walk its rows, `Enter` opens
/// a document in it, and letters are its verbs.
fn bundle_key(model: &mut Model, msg: &Msg) -> Option<Effect> {
    let Some(View::Bundle { id, .. }) = model.views.last() else { return None };
    let rows = crate::bundles::rows(&model.store, id);
    Some(match msg {
        Msg::Move(motion) => {
            if let Some(View::Bundle { selected, .. }) = model.views.last_mut() {
                select(&rows, selected, *motion);
            }
            Effect::Redraw
        }
        Msg::Enter => model.enter_bundle(),
        Msg::Char(' ') => update(model, Msg::Leader),
        Msg::Char(c) => model.bundle_verb(*c),
        Msg::Backspace | Msg::Left | Msg::Right => Effect::Idle,
        _ => return None,
    })
}

/// Keys on the Versions view: the arrows walk the versions, `Enter` opens one,
/// and the only letters are undo and redo.
fn versions_key(model: &mut Model, msg: &Msg) -> Option<Effect> {
    let Some(View::Versions { doc }) = model.views.last() else { return None };
    Some(match msg {
        Msg::Move(motion) => {
            let ids: Vec<String> = crate::versions::rows(&model.store, doc)
                .into_iter()
                .map(|i| model.store.docs[i].id.clone())
                .collect();
            if let Some(View::Versions { doc }) = model.views.last_mut() {
                select(&ids, doc, *motion);
            }
            Effect::Redraw
        }
        Msg::Enter => model.open_version(),
        Msg::Char(' ') => update(model, Msg::Leader),
        Msg::Char('u') => model.step(Direction::Undo),
        Msg::Char('r') => model.step(Direction::Redo),
        Msg::Char(c) => model.no_verb(*c),
        Msg::Backspace | Msg::Left | Msg::Right => Effect::Idle,
        _ => return None,
    })
}

/// Keys while the location picker is open. `None` falls through, so `ctrl+q`,
/// `ctrl+z` and worker messages keep their meaning.
fn locpick_key(model: &mut Model, msg: &Msg) -> Option<Effect> {
    model.locpick.as_ref()?;
    if model.sheet {
        if *msg == Msg::Esc {
            model.sheet = false;
            return Some(Effect::Redraw);
        }
        return None;
    }
    if model.armed == Some(Armed::Delete) && *msg == Msg::Char('d') {
        model.armed = None;
        model.flash = None;
        if let Some(id) = model.picked_location() {
            return Some(model.remove(&id));
        }
    }
    let mut picker = model.locpick.take()?;
    let effect = match msg {
        Msg::Move(motion) => {
            select(&picker.selectable(&model.store), &mut picker.cursor, *motion);
            Effect::Redraw
        }
        Msg::Right => {
            picker.right(&model.store);
            Effect::Redraw
        }
        Msg::Left => {
            picker.left(&model.store);
            Effect::Redraw
        }
        Msg::Esc if !picker.filter.is_empty() => {
            picker.clear_search(&model.store);
            Effect::Redraw
        }
        Msg::Esc => {
            model.locpick = picker.back.map(|back| *back);
            return Some(Effect::Redraw);
        }
        Msg::Char(' ') | Msg::Leader if picker.filter.is_empty() => {
            if picker.chosen().is_some() {
                model.sheet = true;
            } else {
                model.flash = Some("pick a location".into());
            }
            Effect::Redraw
        }
        Msg::Char(c) => {
            picker.type_char(&model.store, *c);
            Effect::Redraw
        }
        Msg::Backspace => {
            picker.rub_out(&model.store);
            Effect::Redraw
        }
        Msg::Enter => return Some(locpick_enter(model, picker)),
        Msg::Tap { col, row } => 'tap: {
            let rows = picker.rows(&model.store);
            let Some(tapped) = model.tree.at(*col, *row).and_then(|index| rows.get(index)) else {
                break 'tap Effect::Idle;
            };
            let chevron = match tapped {
                crate::locpick::Row::Location { lead, open: Some(open), id, .. } => {
                    let at = model.tree.left
                        + 1
                        + u16::try_from(crate::layout::width(lead)).unwrap_or(0);
                    (*col >= at && *col < at + 2).then(|| (id.clone(), *open))
                }
                _ => None,
            };
            match (chevron, tapped.target()) {
                (Some((id, true)), _) => {
                    picker.open.remove(&id);
                    picker.cursor = crate::locpick::Target::Location(id);
                }
                (Some((id, false)), _) => {
                    picker.cursor = crate::locpick::Target::Location(id);
                    picker.right(&model.store);
                }
                (None, Some(target)) if target == picker.cursor => {
                    return Some(locpick_enter(model, picker));
                }
                (None, Some(target)) => picker.cursor = target,
                (None, None) => {}
            }
            Effect::Redraw
        }
        Msg::Scroll(_) => Effect::Idle,
        _ => {
            model.locpick = Some(picker);
            return None;
        }
    };
    model.locpick = Some(picker);
    Some(effect)
}

/// `Enter` in the location picker: show a location's documents, create, file,
/// or move, keeping the picker open only when nothing was written.
fn locpick_enter(model: &mut Model, mut picker: crate::locpick::LocationPicker) -> Effect {
    use crate::locpick::{Mode, Target};
    let written = match (&picker.cursor, picker.chosen(), &picker.mode) {
        (Target::More(id), _, _) => {
            picker.expanded.insert(id.clone());
            Err(None)
        }
        (Target::New, _, Mode::File(doc)) => {
            let (doc, name) = (doc.clone(), picker.new_name().to_string());
            model.create_and_file(&doc, &name, picker.anchor.clone().as_deref()).map_err(Some)
        }
        (_, Some(location), Mode::File(doc)) => {
            let (doc, location) = (doc.clone(), location.to_string());
            model.file_in(&doc, &location).map_err(Some)
        }
        (_, None, Mode::File(_)) => Err(Some("pick a location".to_string())),
        (Target::Root | Target::Location(_) | Target::Match(_), into, Mode::Move(moving)) => {
            let (moving, into) = (moving.clone(), into.map(str::to_string));
            model.move_location(&moving, into.as_deref()).map_err(Some)
        }
        (Target::New, _, Mode::Move(_)) => Err(None),
    };
    match written {
        Ok(effect) => {
            model.locpick = picker.back.map(|back| *back);
            effect
        }
        Err(reason) => {
            if reason.is_some() {
                model.flash = reason;
            }
            model.locpick = Some(picker);
            Effect::Redraw
        }
    }
}

/// Keys while a checklist or picker is open: typing narrows it, and `Enter`
/// or a tap on a row chooses. A checklist row toggles on its first tap and
/// with `Space` before anything is typed; a picker row is selected first.
/// `Backspace` with nothing typed closes it.
fn picker_key(model: &mut Model, msg: &Msg) -> Option<Effect> {
    let mut picker = model.picker.take()?;
    let hits = picker.matching(model);
    let effect = match msg {
        Msg::Char(' ') | Msg::Leader if picker.checklist() && picker.filter.is_empty() => {
            return Some(act(model, picker, &hits));
        }
        Msg::Enter => return Some(act(model, picker, &hits)),
        Msg::Char(c) => {
            picker.filter.push(*c);
            picker.cursor = picker.first(model);
            Effect::Redraw
        }
        Msg::Backspace => {
            if picker.filter.pop().is_none() {
                drop(picker);
                return Some(Effect::Redraw);
            }
            picker.cursor = picker.first(model);
            Effect::Redraw
        }
        Msg::Move(motion) => {
            picker.cursor = moved(picker.cursor, hits.len(), *motion, hits.len());
            Effect::Redraw
        }
        Msg::Tap { col, row } => match model.panel.at(*col, *row) {
            None => {
                drop(picker);
                return Some(Effect::Redraw);
            }
            Some(index) if picker.checklist() || index == picker.cursor => {
                picker.cursor = index;
                return Some(act(model, picker, &hits));
            }
            Some(index) => {
                picker.cursor = index;
                Effect::Redraw
            }
        },
        Msg::Left | Msg::Right | Msg::Leader | Msg::Scroll(_) => Effect::Idle,
        _ => {
            model.picker = Some(picker);
            return None;
        }
    };
    model.picker = Some(picker);
    Some(effect)
}

/// Does what the selected row says. A checklist stays open to show the
/// change; a picker closes. With no row selected, nothing happens.
fn act(model: &mut Model, picker: Picker, hits: &[crate::pick::Entry]) -> Effect {
    let Some(choice) = hits.get(picker.cursor).map(|entry| entry.choice.clone()) else {
        model.picker = Some(picker);
        return Effect::Idle;
    };
    let purpose = picker.purpose.clone();
    if picker.checklist() {
        model.picker = Some(picker);
    }
    match model.choose(&purpose, choice) {
        Effect::Idle => Effect::Redraw,
        effect => effect,
    }
}

/// Whether this message came from the keyboard.
///
/// A result posted back by a worker is not a keystroke, however it arrives — so
/// a save landing must not disarm a pending quit or restore mouse reporting the
/// IME affordance dropped, any more than a finished scan load does.
fn is_key(msg: &Msg) -> bool {
    !from_worker(msg) && !matches!(msg, Msg::Tap { .. } | Msg::Scroll(_) | Msg::Resize { .. })
}

/// Whether `msg` ends a `d d` or `Esc Esc` pair.
fn disarms(msg: &Msg) -> bool {
    !from_worker(msg) && !matches!(msg, Msg::Resize { .. })
}

/// Whether a thread of the program sent `msg`, rather than the person.
fn from_worker(msg: &Msg) -> bool {
    matches!(
        msg,
        Msg::ScansLoaded(_)
            | Msg::Saved(_)
            | Msg::Reloaded(_)
            | Msg::Day { .. }
            | Msg::SaveFailed { .. }
            | Msg::OpenFailed(_)
    )
}

/// The rows the search bar occupies, inclusive, which raise the keyboard when
/// tapped: two on a touch layout, against the screen edge, for a thumb.
fn search_zone(model: &Model) -> (u16, u16) {
    let last = model.rows_on_screen.saturating_sub(1);
    if crate::layout::touch_layout(model.cols) {
        (last.saturating_sub(1), last)
    } else {
        (last, last)
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::{FileRef, Store};

    fn doc(id: &str, name: &str, expiry: Option<&str>, file: Option<&str>) -> Doc {
        Doc {
            id: id.into(),
            name: name.into(),
            expiry_date: expiry.map(str::to_string),
            location: Some("cert-file".into()),
            files: file
                .map(|path| {
                    vec![FileRef { label: "complete".into(), path: path.into(), primary: true }]
                })
                .unwrap_or_default(),
            ..Doc::default()
        }
    }

    pub(crate) fn model() -> Model {
        let mut store = Store {
            docs: vec![
                doc("coc", "COC Certificate", Some("2026-01-01"), Some("Marine/coc.pdf")),
                doc("eng1", "ENG-1 Medical", Some("2027-01-13"), Some("Marine/eng1.pdf")),
                doc("passport", "Passport (IN)", Some("2031-05-31"), None),
                doc("testimonial", "Sea Service Testimonial", None, None),
            ],
            ..Store::default()
        };
        store.derive();
        Model::new(store, "2026-08-16".into(), "2026-11-14".into(), 45, 28)
    }

    /// A model that is allowed to write, which the default is not.
    fn writable() -> Model {
        let mut model = model();
        model.write = WriteState::Ready { device: "desk".into() };
        model
    }

    /// Types `text` a character at a time.
    fn type_str(m: &mut Model, text: &str) {
        for c in text.chars() {
            update(m, Msg::Char(c));
        }
    }

    /// Backspaces until the open edit's buffer is empty.
    fn clear_buffer(m: &mut Model) {
        let typed = m.edit.as_ref().map_or(0, |edit| edit.buffer.chars().count());
        for _ in 0..typed {
            update(m, Msg::Backspace);
        }
    }

    /// Lands the save in flight, with the store as the model already holds it.
    fn land(m: &mut Model) {
        let store = m.store.clone();
        land_as(m, store);
    }

    /// Lands the save in flight, with `store` as the journal read it back.
    fn land_as(m: &mut Model, store: Store) {
        update(m, Msg::Saved(Box::new(store)));
    }

    /// The store with `id`'s expiry set to `expiry`, as the journal thread posts it.
    fn restored(from: &Model, id: &str, expiry: Option<&str>) -> Store {
        let mut store = from.store.clone();
        for doc in &mut store.docs {
            if doc.id == id {
                doc.expiry_date = expiry.map(str::to_string);
            }
        }
        store
    }

    /// Detail is the only editing surface, so the verb opens the record rather
    /// than refusing until it is open.
    #[test]
    fn the_edit_verb_opens_the_record_and_seeds_the_field() {
        let mut m = writable();
        assert!(!m.detail());
        assert_eq!(m.open_edit(Field::Expiry), Effect::Redraw);
        assert!(m.detail(), "the record came with it");
        let edit = m.edit.as_ref().expect("an edit is open");
        assert_eq!(edit.target, Target::Doc("coc".into()));
        assert_eq!(edit.buffer, "2026-01-01", "seeded with the stored value");
        assert!(!edit.dirty());
    }

    #[test]
    fn a_read_only_session_explains_itself_instead_of_editing() {
        let mut m = model();
        assert_eq!(m.write, WriteState::default(), "no device, no writing");
        m.open_edit(Field::Expiry);
        assert!(m.edit.is_none());
        assert!(m.flash.unwrap().contains("ds init"), "and it names the fix");
    }

    #[test]
    fn typing_in_an_edit_never_reaches_the_query() {
        let mut m = writable();
        m.open_edit(Field::Expiry);
        clear_buffer(&mut m);
        type_str(&mut m, "2027-04-01");
        assert_eq!(m.edit.as_ref().unwrap().buffer, "2027-04-01");
        assert!(m.query.is_empty(), "the query was never touched");
    }

    #[test]
    fn saving_a_date_appends_a_set_op_and_waits_for_it() {
        let mut m = writable();
        m.open_edit(Field::Expiry);
        clear_buffer(&mut m);
        type_str(&mut m, "2027-04-01");
        let effect = update(&mut m, Msg::Enter);
        assert_eq!(
            effect,
            Effect::Append(vec![journal::Draft::set("doc", "coc", "expiry_date", "2027-04-01")])
        );
        assert!(m.edit.as_ref().unwrap().saving, "still open, still unsaved");
        assert_eq!(m.store.docs[0].expiry_date.as_deref(), Some("2026-01-01"), "unchanged so far");

        let store = restored(&m, "coc", Some("2027-04-01"));
        land_as(&mut m, store);
        assert!(m.edit.is_none(), "the journal answered, so the editor closed");
        assert_eq!(m.current().unwrap().expiry_date.as_deref(), Some("2027-04-01"));
        assert_eq!(m.flash.as_deref(), Some("saved"));
    }

    #[test]
    fn another_devices_edit_arrives_in_place() {
        let mut m = writable();
        update(&mut m, Msg::Move(Motion::Down));
        m.open_edit(Field::Notes);
        let store = restored(&m, "eng1", Some("2028-01-01"));
        assert_eq!(update(&mut m, Msg::Reloaded(Box::new(store))), Effect::Redraw);
        assert_eq!(m.current().unwrap().id, "eng1");
        assert_eq!(m.current().unwrap().expiry_date.as_deref(), Some("2028-01-01"));
        assert_eq!(m.flash.as_deref(), Some("updated from another device"));
        assert!(m.edit.is_some(), "the typing is kept");
        assert!(m.undo.is_empty(), "nothing this session did");
    }

    #[test]
    fn an_unchanged_read_is_silent() {
        let mut m = model();
        let same = m.store.clone();
        assert_eq!(update(&mut m, Msg::Reloaded(Box::new(same))), Effect::Idle);
        assert_eq!(m.flash, None);
    }

    #[test]
    fn a_document_deleted_elsewhere_closes_its_views() {
        let mut m = model();
        update(&mut m, Msg::Enter);
        assert!(m.detail());
        let mut store = m.store.clone();
        store.docs.retain(|doc| doc.id != "coc");
        update(&mut m, Msg::Reloaded(Box::new(store)));
        assert!(!m.detail(), "{:?}", m.views);
        assert_eq!(m.current().unwrap().id, "eng1");
    }

    #[test]
    fn each_editable_field_opens_on_its_stored_value() {
        for (field, expected) in [
            (Field::Name, "COC Certificate"),
            (Field::Expiry, "2026-01-01"),
            (Field::Issued, ""),
            (Field::Tags, ""),
            (Field::Notes, ""),
        ] {
            let mut m = writable();
            m.open_edit(field);
            let edit = m.edit.as_ref().expect("the editor opened");
            assert_eq!(edit.buffer, expected, "{field:?} seeds from the store");
            assert!(!edit.dirty(), "and opening is not itself an edit");
        }
    }

    #[test]
    fn tags_are_typed_with_spaces_and_stored_as_a_list() {
        let mut m = writable();
        m.open_edit(Field::Tags);
        type_str(&mut m, "marine  ticket");
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![journal::Draft::set(
                "doc",
                "coc",
                "tags",
                serde_json::json!(["marine", "ticket"])
            )])
        );
    }

    /// A document called nothing cannot be found, listed or talked about.
    #[test]
    fn a_name_cannot_be_cleared_but_the_others_can() {
        let mut m = writable();
        m.open_edit(Field::Name);
        clear_buffer(&mut m);
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw, "nothing was appended");
        assert!(m.flash.is_some(), "and it said why");
        assert!(m.edit.is_some(), "with the editor still open on the empty buffer");

        let mut m = writable();
        m.open_edit(Field::Expiry);
        clear_buffer(&mut m);
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![journal::Draft::unset("doc", "coc", "expiry_date")])
        );
    }

    /// The fold drops a `set` on an entity not yet created, so a new document's
    /// `create` and `set name` go in one append.
    #[test]
    fn creating_a_document_appends_the_create_before_the_name() {
        let mut m = writable();
        assert_eq!(
            create(&mut m, "Seaman Book"),
            Effect::Append(vec![
                journal::Draft::create("doc", "seaman-book-desk"),
                journal::Draft::set("doc", "seaman-book-desk", "name", "Seaman Book"),
            ])
        );
        assert!(!m.detail(), "the record waits for the journal to answer");
    }

    /// Types `name` into the search and presses `Enter` on `+ new`.
    fn create(m: &mut Model, name: &str) -> Effect {
        type_str(m, name);
        while !m.on_new {
            update(m, Msg::Move(Motion::Up));
        }
        update(m, Msg::Enter)
    }

    /// The cursor starts on `+ new` only when nothing matches, so what already
    /// exists is seen first.
    #[test]
    fn new_is_offered_above_the_matches_once_something_is_typed() {
        let mut m = writable();
        assert!(!m.offers_new(), "an empty search lists every document");
        update(&mut m, Msg::Char('c'));
        assert!(m.offers_new());
        assert!(!m.on_new, "the cursor starts on the first match");
        update(&mut m, Msg::Move(Motion::Up));
        assert!(m.on_new, "one up from it");
        assert!(m.current().is_none());
        update(&mut m, Msg::Move(Motion::Down));
        assert!(!m.on_new);
        assert_eq!(m.cursor, 0, "and back down lands on the first match");
        type_str(&mut m, "zzzz");
        assert!(m.on_new, "nothing matches, so + new is selected");
    }

    /// Coming back from the created document lands where the user left.
    #[test]
    fn the_search_survives_creating_from_it() {
        let mut m = writable();
        create(&mut m, "Seaman Book");
        let mut store = m.store.clone();
        let mut fresh = store.docs[0].clone();
        fresh.id = "seaman-book-desk".into();
        fresh.name = "Seaman Book".into();
        store.docs.push(fresh);
        store.derive();
        land_as(&mut m, store);
        assert!(m.detail());
        update(&mut m, Msg::Esc);
        assert!(!m.detail(), "Esc closes the record first");
        assert_eq!(m.query, "Seaman Book", "and the search is still there");
    }

    #[test]
    fn an_empty_store_offers_a_new_document_and_asks_for_its_name() {
        let mut m = writable();
        m.store.docs.clear();
        m.requery();
        assert!(m.on_new);
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw);
        assert!(
            m.edit.as_ref().is_some_and(|edit| edit.target == Target::NewDoc),
            "the name is being asked for"
        );
    }

    #[test]
    fn a_new_id_avoids_every_id_already_in_the_store() {
        let mut m = writable();
        m.store.docs[0].id = "passport-desk".into();
        let Effect::Append(drafts) = create(&mut m, "Passport") else { panic!("no append") };
        assert_eq!(drafts[0], journal::Draft::create("doc", "passport-desk-2"));
    }

    #[test]
    fn a_new_id_avoids_a_deleted_one() {
        let mut m = writable();
        m.store.retired.insert(("doc".into(), "passport-desk".into()));
        let Effect::Append(drafts) = create(&mut m, "Passport") else { panic!("no append") };
        assert_eq!(drafts[0], journal::Draft::create("doc", "passport-desk-2"));
    }

    /// The record is the only place the rest of its fields can be filled in.
    #[test]
    fn a_created_document_opens_on_its_record() {
        let mut m = writable();
        create(&mut m, "Seaman Book");

        // What the journal thread posts back once the ops have landed.
        let mut store = m.store.clone();
        let mut fresh = store.docs[0].clone();
        fresh.id = "seaman-book-desk".into();
        fresh.name = "Seaman Book".into();
        fresh.expiry_date = None;
        fresh.files.clear();
        store.docs.push(fresh);
        store.derive();
        land_as(&mut m, store);

        assert!(m.edit.is_none(), "the journal answered, so the editor closed");
        assert!(m.detail(), "and the record is open");
        assert_eq!(m.record_cursor(), 0, "on its first row");
        assert_eq!(m.current().map(|doc| doc.id.as_str()), Some("seaman-book-desk"));
        assert_eq!(m.flash.as_deref(), Some("created"));
    }

    #[test]
    fn a_new_version_copies_the_name_tags_and_location() {
        let mut m = writable();
        m.store.docs[0].tags = vec!["marine".into()];
        m.store.docs[0].location = Some("cert-file".into());
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::Char(' '));
        let id = "coc-certificate-desk";
        assert_eq!(
            update(&mut m, Msg::Char('n')),
            Effect::Append(vec![
                journal::Draft::create("doc", id),
                journal::Draft::set("doc", id, "name", "COC Certificate"),
                journal::Draft::set("doc", id, "tags", serde_json::json!(["marine"])),
                journal::Draft::set("doc", id, "location", "cert-file"),
                journal::Draft::set("doc", id, "supersedes", "coc"),
            ])
        );
    }

    #[test]
    fn a_new_version_opens_in_place_of_the_old_one() {
        let mut m = writable();
        update(&mut m, Msg::Enter);
        m.set_record_cursor(3);
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('n'));

        let mut store = m.store.clone();
        let mut fresh = store.docs[0].clone();
        fresh.id = "coc-certificate-desk".into();
        fresh.supersedes = Some("coc".into());
        fresh.expiry_date = None;
        fresh.files.clear();
        store.docs.push(fresh);
        store.derive();
        land_as(&mut m, store);

        assert_eq!(m.current().map(|doc| doc.id.as_str()), Some("coc-certificate-desk"));
        assert_eq!(m.views.len(), 1, "it replaced the old version's view");
        assert_eq!(m.record_cursor(), 0, "on its name");
        assert_eq!(m.flash.as_deref(), Some("new version"));
        assert_eq!(
            m.undo.last().map(|change| change.back.clone()),
            Some(vec![journal::Draft::delete("doc", "coc-certificate-desk")])
        );
    }

    #[test]
    fn creating_is_refused_with_a_reason_when_the_session_cannot_write() {
        let mut m = model();
        create(&mut m, "Seaman Book");
        assert!(m.edit.is_none());
        assert!(m.flash.is_some());
    }

    /// The inverse is computed when the append is asked for but becomes undoable
    /// only when the journal confirms it; otherwise a refused save would leave a
    /// stack entry that puts back something nobody ever changed.
    #[test]
    fn a_refused_save_leaves_nothing_to_undo() {
        let mut m = writable();
        m.open_edit(Field::Expiry);
        type_str(&mut m, "-x");
        update(&mut m, Msg::Backspace);
        update(&mut m, Msg::Backspace);
        type_str(&mut m, "2027-04-01");
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::SaveFailed { reason: "the disk said no".into(), permanent: false });
        assert!(m.undo.is_empty(), "nothing was written, so there is nothing to take back");
    }

    /// The inverse is the value the store holds, not the buffer: built from the
    /// typing, it would restore a string where a list had been.
    #[test]
    fn the_inverse_of_a_tag_edit_restores_the_list() {
        let mut m = writable();
        m.store.docs[0].tags = vec!["marine".into(), "ticket".into()];
        m.open_edit(Field::Tags);
        type_str(&mut m, " extra");
        update(&mut m, Msg::Enter);
        land(&mut m);

        assert_eq!(
            m.undo.last().map(|change| change.back.clone()),
            Some(vec![journal::Draft::set(
                "doc",
                "coc",
                "tags",
                serde_json::json!(["marine", "ticket"])
            )])
        );
    }

    /// The fold keeps a `create` forever, so the way back is a tombstone.
    #[test]
    fn creating_a_document_inverts_to_a_delete() {
        let mut m = writable();
        create(&mut m, "Seaman Book");
        land(&mut m);
        assert_eq!(
            m.undo.last().map(|change| change.back.clone()),
            Some(vec![journal::Draft::delete("doc", "seaman-book-desk")])
        );
    }

    /// `u u u` walks back three writes rather than toggling the last one.
    #[test]
    fn an_undo_does_not_become_something_to_undo() {
        let mut m = writable();
        m.open_edit(Field::Expiry);
        update(&mut m, Msg::Backspace);
        update(&mut m, Msg::Char('2'));
        update(&mut m, Msg::Enter);
        land(&mut m);
        assert_eq!(m.undo.len(), 1);

        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('u'));
        land(&mut m);
        assert!(m.undo.is_empty(), "the undo consumed the entry and added none");
        assert_eq!(m.flash.as_deref(), Some("undone"));
    }

    #[test]
    fn redo_appends_the_original_ops() {
        let mut m = saved_edit("2027-04-01");
        let forward = m.undo.last().expect("something to undo").forward.clone();

        update(&mut m, Msg::Char('u'));
        land(&mut m);
        assert!(m.undo.is_empty(), "the change left the undo stack");
        assert_eq!(m.redo.len(), 1, "and joined the redo stack");

        assert_eq!(update(&mut m, Msg::Char('r')), Effect::Append(forward));
        land(&mut m);
        assert_eq!(m.flash.as_deref(), Some("redone"));
        assert!(m.redo.is_empty(), "and it went back where it came from");
        assert_eq!(m.undo.len(), 1, "so it can be undone again");
    }

    #[test]
    fn a_write_waits_for_the_one_in_flight() {
        let mut m = writable();
        update(&mut m, Msg::Enter);
        let Effect::Append(_) = m.toggle_digital_only() else { panic!("the first write") };
        assert_eq!(m.toggle_digital_only(), Effect::Redraw);
        assert_eq!(m.flash.as_deref(), Some("saving — one moment"));
        land(&mut m);
        assert_eq!(m.undo.len(), 1, "the first write, and only it");
        assert!(matches!(m.toggle_digital_only(), Effect::Append(_)), "free again");
    }

    #[test]
    fn digital_only_is_one_write_with_its_way_back() {
        let mut m = writable();
        update(&mut m, Msg::Enter);
        select_row(&mut m, crate::detail::Row::DigitalOnly);

        let set = |value: &str| {
            journal::Draft::set("doc", "coc", "location", serde_json::Value::from(value))
        };
        assert_eq!(update(&mut m, Msg::Char('e')), Effect::Append(vec![set("none")]));
        assert_eq!(
            m.pending.as_ref().map(|pending| pending.change.back.clone()),
            Some(vec![set("cert-file")])
        );
        let mut store = m.store.clone();
        store.docs[0].location = Some("none".into());
        land_as(&mut m, store);
        assert_eq!(m.flash.as_deref(), Some("digital only"));
        assert!(
            !crate::detail::rows(m.current().unwrap()).contains(&crate::detail::Row::Location),
            "no hard copy location row once there is no hard copy"
        );

        select_row(&mut m, crate::detail::Row::DigitalOnly);
        assert_eq!(
            update(&mut m, Msg::Char('e')),
            Effect::Append(vec![journal::Draft::unset("doc", "coc", "location")])
        );
        assert_eq!(
            m.pending.as_ref().map(|pending| pending.change.back.clone()),
            Some(vec![set("none")])
        );
    }

    fn with_locations(mut m: Model) -> Model {
        let at = |id: &str, parent: Option<&str>| crate::Location {
            id: id.into(),
            name: id.into(),
            parent: parent.map(str::to_string),
        };
        m.store.locations = crate::Tree::new([
            at("shelf", None),
            at("cert-file", Some("shelf")),
            at("drawer", None),
        ]);
        m
    }

    #[test]
    fn the_location_picker_files_the_hard_copy() {
        let mut m = with_locations(writable());
        picking(&mut m);
        let picker = m.locpick.as_ref().expect("the picker is open");
        assert_eq!(picker.root.as_deref(), Some("shelf"));
        assert_eq!(picker.cursor, crate::locpick::Target::Location("cert-file".into()));

        update(&mut m, Msg::Left);
        update(&mut m, Msg::Move(Motion::Up));
        update(&mut m, Msg::Left);
        assert_eq!(m.locpick.as_ref().unwrap().root, None, "← on the root moves it up");
        update(&mut m, Msg::Move(Motion::Up));
        let set = |value: &str| {
            journal::Draft::set("doc", "coc", "location", serde_json::Value::from(value))
        };
        assert_eq!(update(&mut m, Msg::Enter), Effect::Append(vec![set("drawer")]));
        assert!(m.locpick.is_none(), "filing closes the picker");
        assert_eq!(
            m.pending.as_ref().map(|pending| pending.change.back.clone()),
            Some(vec![set("cert-file")])
        );
        land(&mut m);
        assert_eq!(m.flash.as_deref(), Some("filed in drawer"));
    }

    /// The new location goes inside the one selected when typing began, and one
    /// undo takes back both it and the filing.
    #[test]
    fn new_creates_a_location_and_files_into_it() {
        let mut m = with_locations(writable());
        picking(&mut m);
        type_str(&mut m, "box");
        assert_eq!(m.locpick.as_ref().unwrap().cursor, crate::locpick::Target::New);
        let Effect::Append(drafts) = update(&mut m, Msg::Enter) else { panic!("an append") };
        let id = match &drafts[0] {
            journal::Draft { op: journal::OpKind::Create, ent, id, .. } if ent == "location" => {
                id.clone()
            }
            other => panic!("the location is created first: {other:?}"),
        };
        assert!(drafts.contains(&journal::Draft::set(
            "location",
            &id,
            "parent",
            serde_json::Value::from("cert-file")
        )));
        assert_eq!(
            drafts.last(),
            Some(&journal::Draft::set(
                "doc",
                "coc",
                "location",
                serde_json::Value::from(id.as_str())
            ))
        );
        let back = m.pending.as_ref().unwrap().change.back.clone();
        assert_eq!(back.last(), Some(&journal::Draft::delete("location", &id)));
    }

    #[test]
    fn new_refuses_a_name_a_sibling_has() {
        let mut m = with_locations(writable());
        picking(&mut m);
        update(&mut m, Msg::Move(Motion::Up));
        type_str(&mut m, "Cert-File");
        update(&mut m, Msg::Move(Motion::Up));
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw);
        assert_eq!(m.flash.as_deref(), Some("shelf already has a Cert-File"));
        assert_eq!(m.locpick.as_ref().map(|p| p.filter.as_str()), Some("Cert-File"));
        update(&mut m, Msg::Esc);
        assert!(
            m.locpick.as_ref().is_some_and(|p| p.filter.is_empty()),
            "Esc clears the search first"
        );
    }

    fn picking(m: &mut Model) {
        update(m, Msg::Enter);
        update(m, Msg::Leader);
        update(m, Msg::Char('l'));
    }

    #[test]
    fn a_location_is_renamed_on_the_bottom_line() {
        let mut m = with_locations(writable());
        picking(&mut m);
        update(&mut m, Msg::Char(' '));
        let keys: Vec<char> = crate::sheet::items(&m).iter().map(|item| item.key).collect();
        assert_eq!(keys, ['r', 'm', 'd', 'u', crate::sheet::NO_KEY, 'q']);
        update(&mut m, Msg::Char('r'));
        let edit = m.edit.as_ref().expect("the bottom line is open");
        assert_eq!(edit.target, Target::Location("cert-file".into()));
        assert_eq!(edit.buffer, "cert-file");
        clear_buffer(&mut m);
        type_str(&mut m, "drawer");
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![journal::Draft::set(
                "location",
                "cert-file",
                "name",
                serde_json::Value::from("drawer"),
            )])
        );
        assert!(m.edit.as_ref().is_some_and(|edit| edit.saving), "open until the journal answers");
        assert!(m.locpick.is_some(), "over the tree");

        let mut m = with_locations(writable());
        picking(&mut m);
        update(&mut m, Msg::Move(Motion::Up));
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('r'));
        clear_buffer(&mut m);
        type_str(&mut m, "Drawer");
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw);
        assert_eq!(m.flash.as_deref(), Some("the top level already has a Drawer"));
    }

    #[test]
    fn a_location_moves_and_the_picker_comes_back() {
        let mut m = with_locations(writable());
        picking(&mut m);
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('m'));
        let picker = m.locpick.as_ref().unwrap();
        assert_eq!(picker.mode, crate::locpick::Mode::Move("cert-file".into()));
        assert_eq!(picker.cursor, crate::locpick::Target::Root);
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![journal::Draft::unset("location", "cert-file", "parent")])
        );
        assert_eq!(
            m.pending.as_ref().map(|pending| pending.change.back.clone()),
            Some(vec![journal::Draft::set(
                "location",
                "cert-file",
                "parent",
                serde_json::Value::from("shelf")
            )])
        );
        assert_eq!(
            m.locpick.as_ref().map(|p| &p.mode),
            Some(&crate::locpick::Mode::File("coc".into()))
        );
    }

    #[test]
    fn a_tap_disarms_a_location_delete() {
        let mut m = with_locations(writable());
        picking(&mut m);
        update(&mut m, Msg::Move(Motion::Up));
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('d'));
        assert_eq!(m.armed, Some(Armed::Delete));
        update(&mut m, Msg::Tap { col: 0, row: 0 });
        assert!(
            m.armed != Some(Armed::Delete) && m.flash.is_none(),
            "the caution goes with the arm"
        );
        assert!(!matches!(update(&mut m, Msg::Char('d')), Effect::Append(_)));
    }

    #[test]
    fn a_full_location_needs_a_second_d() {
        let mut m = with_locations(writable());
        picking(&mut m);
        update(&mut m, Msg::Move(Motion::Up));
        update(&mut m, Msg::Char(' '));
        assert_eq!(update(&mut m, Msg::Char('d')), Effect::Redraw);
        assert_eq!(
            m.flash.as_deref(),
            Some(
                "Caution: shelf holds 1 location and 4 hard copies. Press d again to delete and \
                 remove their location attributes"
            )
        );
        update(&mut m, Msg::Move(Motion::Down));
        assert_ne!(m.armed, Some(Armed::Delete), "any other key cancels");

        update(&mut m, Msg::Move(Motion::Up));
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('d'));
        let delete = |id: &str| journal::Draft::delete("location", id);
        assert_eq!(
            update(&mut m, Msg::Char('d')),
            Effect::Append(vec![delete("cert-file"), delete("shelf")])
        );
        let back = m.pending.as_ref().unwrap().change.back.clone();
        assert_eq!(back.first(), Some(&journal::Draft::create("location", "shelf")));
        assert!(back.contains(&journal::Draft::set(
            "location",
            "cert-file",
            "parent",
            serde_json::Value::from("shelf")
        )));
    }

    #[test]
    fn an_empty_location_goes_at_once() {
        let mut m = with_locations(writable());
        picking(&mut m);
        update(&mut m, Msg::Left);
        update(&mut m, Msg::Move(Motion::Up));
        update(&mut m, Msg::Left);
        update(&mut m, Msg::Move(Motion::Up));
        assert_eq!(
            m.locpick.as_ref().unwrap().cursor,
            crate::locpick::Target::Location("drawer".into())
        );
        update(&mut m, Msg::Char(' '));
        assert_eq!(
            update(&mut m, Msg::Char('d')),
            Effect::Append(vec![journal::Draft::delete("location", "drawer")])
        );
    }

    #[test]
    fn the_top_of_the_tree_is_not_a_place() {
        let mut m = with_locations(writable());
        m.store.docs[0].location = None;
        picking(&mut m);
        update(&mut m, Msg::Move(Motion::Up));
        assert_eq!(m.locpick.as_ref().unwrap().cursor, crate::locpick::Target::Root);
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw);
        assert_eq!(m.flash.as_deref(), Some("pick a location"));
        update(&mut m, Msg::Esc);
        assert!(m.locpick.is_none());
        assert!(m.detail(), "Esc closed the picker, not the Details view");
    }

    #[test]
    fn ctrl_z_and_ctrl_y_work_everywhere_but_an_open_field() {
        let mut m = saved_edit("2027-04-01");
        let forward = m.undo.last().expect("something to undo").forward.clone();

        m.open_edit(Field::Notes);
        assert_eq!(update(&mut m, Msg::Undo), Effect::Idle, "the field keeps the keyboard");
        assert_eq!(m.undo.len(), 1, "and nothing was undone");
        for _ in 0..2 {
            if m.edit.is_some() {
                update(&mut m, Msg::Esc);
            }
        }
        assert!(m.edit.is_none());
        update(&mut m, Msg::Esc);
        assert!(!m.detail(), "back on the Find view");

        assert!(matches!(update(&mut m, Msg::Undo), Effect::Append(_)));
        land(&mut m);
        assert_eq!(update(&mut m, Msg::Redo), Effect::Append(forward));
    }

    /// Redoing past a branch would write an old edit over a document that moved on.
    #[test]
    fn writing_something_new_drops_what_could_have_been_redone() {
        let mut m = saved_edit("2027-04-01");
        update(&mut m, Msg::Char('u'));
        land(&mut m);
        assert_eq!(m.redo.len(), 1);

        m.open_edit(Field::Notes);
        type_str(&mut m, "elsewhere");
        update(&mut m, Msg::Enter);
        land(&mut m);
        assert!(m.redo.is_empty(), "the branch that was not taken is gone");
        assert_eq!(m.undo.len(), 1, "and the new write is the thing to take back");
    }

    #[test]
    fn redo_with_nothing_undone_says_so() {
        let mut m = writable();
        update(&mut m, Msg::Enter);
        assert_eq!(update(&mut m, Msg::Char('r')), Effect::Redraw);
        assert!(m.flash.as_deref().is_some_and(|say| say.contains("nothing to redo")));
    }

    /// A model with one confirmed edit behind it, on the record.
    fn saved_edit(value: &str) -> Model {
        let mut m = writable();
        m.open_edit(Field::Expiry);
        clear_buffer(&mut m);
        type_str(&mut m, value);
        update(&mut m, Msg::Enter);
        land(&mut m);
        m
    }

    #[test]
    fn delete_takes_two_presses() {
        let mut m = writable();
        update(&mut m, Msg::Enter);

        assert_eq!(update(&mut m, Msg::Char('d')), Effect::Redraw, "the first press only asks");
        assert_eq!(m.armed, Some(Armed::Delete));

        assert_eq!(
            update(&mut m, Msg::Char('d')),
            Effect::Append(vec![journal::Draft::delete("doc", "coc")]),
            "the second press writes the tombstone"
        );
        assert_ne!(m.armed, Some(Armed::Delete));
    }

    #[test]
    fn any_other_key_disarms_a_pending_delete() {
        let mut m = writable();
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::Char('d'));
        assert_eq!(m.armed, Some(Armed::Delete));

        update(&mut m, Msg::Move(Motion::Down));
        assert_ne!(m.armed, Some(Armed::Delete), "moving the selector is not consent");
        assert_eq!(update(&mut m, Msg::Char('d')), Effect::Redraw, "so this asks again");
    }

    #[test]
    fn deleting_inverts_to_a_create_with_every_field() {
        let mut m = writable();
        m.store.docs[0].tags = vec!["marine".into()];
        m.store.docs[0].notes = "the one with the stamp".into();
        update(&mut m, Msg::Enter);
        let expected = m.current().expect("a document").as_fields().len();

        update(&mut m, Msg::Char('d'));
        update(&mut m, Msg::Char('d'));
        let mut store = m.store.clone();
        store.docs.retain(|doc| doc.id != "coc");
        land_as(&mut m, store);
        assert_eq!(m.flash.as_deref(), Some("deleted — u to undo"));
        assert!(!m.detail(), "there is nothing left to look at");

        let back = m.undo.last().expect("something to undo").back.clone();
        assert_eq!(back.first(), Some(&journal::Draft::create("doc", "coc")));
        assert_eq!(back.len(), expected + 1, "the create, then every field it had");
        assert!(
            back.contains(&journal::Draft::set("doc", "coc", "notes", "the one with the stamp")),
            "including the ones no other verb touches: {back:?}"
        );
    }

    #[test]
    fn delete_is_refused_with_a_reason_when_the_session_cannot_write() {
        let mut m = model();
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::Char('d'));
        assert_ne!(m.armed, Some(Armed::Delete), "it did not even arm");
        assert!(m.flash.is_some());
    }

    #[test]
    fn undo_is_refused_with_a_reason_when_the_session_cannot_write() {
        let mut m = model();
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::Char('u'));
        assert!(m.flash.is_some());
    }

    #[test]
    fn clearing_the_field_appends_an_unset_op() {
        let mut m = writable();
        m.open_edit(Field::Expiry);
        clear_buffer(&mut m);
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![journal::Draft::unset("doc", "coc", "expiry_date")])
        );
    }

    #[test]
    fn an_unparseable_date_is_refused_and_the_typing_survives() {
        let mut m = writable();
        m.open_edit(Field::Expiry);
        type_str(&mut m, "-ish");
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw, "no append");
        assert_eq!(m.edit.as_ref().unwrap().buffer, "2026-01-01-ish");
        assert!(m.flash.as_deref().unwrap().contains("YYYY-MM-DD"));
    }

    #[test]
    fn esc_discards_an_edit_in_one_press_when_clean_and_two_when_dirty() {
        let mut m = writable();
        m.open_edit(Field::Expiry);
        update(&mut m, Msg::Esc);
        assert!(m.edit.is_none(), "nothing was typed, so nothing needed confirming");

        m.open_edit(Field::Expiry);
        update(&mut m, Msg::Char('9'));
        update(&mut m, Msg::Esc);
        assert!(m.edit.as_ref().unwrap().armed_discard, "armed, not discarded");
        update(&mut m, Msg::Esc);
        assert!(m.edit.is_none(), "the second press threw it away");

        m.open_edit(Field::Expiry);
        update(&mut m, Msg::Char('9'));
        update(&mut m, Msg::Esc);
        update(&mut m, Msg::Char('9'));
        assert!(!m.edit.as_ref().unwrap().armed_discard, "any other key disarms");
    }

    /// The record being edited has to stay the record on screen.
    #[test]
    fn the_list_does_not_move_under_an_open_edit() {
        let mut m = writable();
        m.open_edit(Field::Expiry);
        let before = m.cursor;
        for motion in [Motion::Down, Motion::PageDown, Motion::End, Motion::Up] {
            assert_eq!(update(&mut m, Msg::Move(motion)), Effect::Idle);
        }
        assert_eq!(update(&mut m, Msg::Tap { col: 2, row: 5 }), Effect::Idle);
        assert_eq!(m.cursor, before);
        assert_eq!(
            m.edit.as_ref().unwrap().target,
            Target::Doc("coc".into()),
            "and it is still the same document"
        );
    }

    #[test]
    fn quitting_works_from_inside_an_edit() {
        let mut m = writable();
        m.open_edit(Field::Expiry);
        assert_eq!(update(&mut m, Msg::Quit), Effect::Quit);
    }

    /// Under the expiring filter a changed date moves the row, so a remembered
    /// index would point at another document.
    #[test]
    fn a_save_keeps_the_cursor_on_the_document_it_edited() {
        let mut m = writable();
        m.warn_until = "2031-12-31".into();
        update(&mut m, Msg::ToggleExpiring);
        update(&mut m, Msg::Move(Motion::Down));
        let edited = m.current().unwrap().id.clone();
        assert_eq!(edited, "eng1", "second-soonest under the filter");

        m.open_edit(Field::Expiry);
        update(&mut m, Msg::Enter);
        // Earlier than `coc`'s 2026-01-01, so the row moves to the top.
        let store = restored(&m, &edited, Some("2025-12-01"));
        land_as(&mut m, store);
        assert_eq!(m.current().unwrap().id, edited, "the cursor followed the document");
        assert_eq!(m.cursor, 0, "which is now the first row");
    }

    #[test]
    fn a_save_that_leaves_the_filter_keeps_the_record() {
        let mut m = writable();
        m.warn_until = "2031-12-31".into();
        update(&mut m, Msg::ToggleExpiring);
        let edited = m.current().unwrap().id.clone();
        m.open_edit(Field::Expiry);
        assert!(m.detail());

        // Cleared: no expiry means it is not in the watch at all.
        let store = restored(&m, &edited, None);
        land_as(&mut m, store);
        assert_eq!(m.current().map(|doc| doc.id.as_str()), Some(edited.as_str()));
        assert_eq!(m.flash.as_deref(), Some("saved"));
        assert!(m.rows.iter().all(|&i| m.store.docs[i].id != edited));
    }

    #[test]
    fn a_failed_save_keeps_the_typing_and_a_permanent_one_stops_offering() {
        let mut m = writable();
        m.open_edit(Field::Expiry);
        update(&mut m, Msg::Char('9'));
        update(&mut m, Msg::Enter);

        update(&mut m, Msg::SaveFailed { reason: "disk full".into(), permanent: false });
        assert_eq!(m.edit.as_ref().unwrap().buffer, "2026-01-019", "the typing survived");
        assert!(!m.edit.as_ref().unwrap().saving, "and it can be tried again");
        assert!(m.write.ready(), "a transient failure is not a verdict");

        let locked = "another process is already writing as `desk-core`";
        update(&mut m, Msg::SaveFailed { reason: locked.into(), permanent: true });
        assert!(!m.write.ready());
        assert_eq!(m.write.reason(), Some(locked));
        update(&mut m, Msg::Esc);
        update(&mut m, Msg::Esc);
        m.open_edit(Field::Expiry);
        assert!(m.edit.is_none(), "it does not offer again");
    }

    #[test]
    fn a_save_result_is_not_a_keypress() {
        let mut m = writable();
        // Arm first: `Esc` is a real keystroke, so it would restore the mouse
        // reporting the affordance drops — the drop has to come after it.
        update(&mut m, Msg::Esc);
        assert_eq!(m.armed, Some(Armed::Esc));
        m.raise_keyboard();
        assert!(!m.mouse_on);

        let store = restored(&m, "coc", Some("2027-04-01"));
        land_as(&mut m, store);
        assert_eq!(m.armed, Some(Armed::Esc), "a worker message did not disarm the quit");
        assert!(!m.mouse_on, "nor did it restore mouse reporting");
    }

    /// Nothing on this surface may swallow a letter, the first included.
    #[test]
    fn a_bare_letter_starts_the_search_and_keeps_it() {
        let mut m = model();
        for c in "coc".chars() {
            assert_eq!(update(&mut m, Msg::Char(c)), Effect::Redraw);
        }
        assert_eq!(m.query, "coc");
        assert_eq!(m.rows.len(), 1);
        assert_eq!(m.current().unwrap().id, "coc");
    }

    #[test]
    fn five_keystrokes_open_a_file_from_a_cold_start() {
        let mut m = model();
        let keys = [Msg::Char('e'), Msg::Char('n'), Msg::Char('g'), Msg::Enter, Msg::Enter];
        let opened = keys.into_iter().find_map(|key| match update(&mut m, key) {
            Effect::Open(path) => Some(path),
            _ => None,
        });
        assert_eq!(opened.as_deref(), Some("Marine/eng1.pdf"));
    }

    #[test]
    fn enter_drills_one_layer_and_esc_peels_it() {
        let mut m = model();
        update(&mut m, Msg::Move(Motion::Down));
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw);
        assert!(m.detail(), "the list drills into the record, never straight to a file");
        update(&mut m, Msg::Esc);
        assert!(!m.detail());
        assert_eq!(m.cursor, 1, "and the list is where it was");
    }

    #[test]
    fn enter_on_a_record_without_a_file_says_so() {
        let mut m = model();
        type_str(&mut m, "passport");
        update(&mut m, Msg::Enter);
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw, "no open effect, and no panic");
        assert!(m.detail());
        assert!(m.flash.unwrap().contains("no file linked"));
    }

    #[test]
    fn enter_opens_the_file_row_it_is_on() {
        let mut m = model();
        with_second_file(&mut m.store);
        update(&mut m, Msg::Enter);
        select_row(&mut m, crate::detail::Row::File(1));
        assert_eq!(update(&mut m, Msg::Enter), Effect::Open("Marine/coc-back.pdf".into()));
        m.set_record_cursor(0);
        assert_eq!(update(&mut m, Msg::Enter), Effect::Open("Marine/coc.pdf".into()));
    }

    #[test]
    fn arrows_move_the_query_cursor_and_typing_lands_there() {
        let mut m = model();
        update(&mut m, Msg::Char('c'));
        update(&mut m, Msg::Char('c'));
        update(&mut m, Msg::Left);
        update(&mut m, Msg::Char('o'));
        assert_eq!((m.query.as_str(), m.query_cursor), ("coc", 2));
        assert_eq!(m.current().unwrap().id, "coc");

        update(&mut m, Msg::Backspace);
        assert_eq!((m.query.as_str(), m.query_cursor), ("cc", 1), "it rubs out before the cursor");
        assert_eq!(update(&mut m, Msg::Right), Effect::Redraw);
        assert_eq!(update(&mut m, Msg::Right), Effect::Idle, "the end is the end");
    }

    #[test]
    fn the_query_cursor_counts_characters_not_bytes() {
        let mut m = model();
        type_str(&mut m, "né");
        update(&mut m, Msg::Left);
        update(&mut m, Msg::Char('x'));
        assert_eq!(m.query, "nxé");
        update(&mut m, Msg::Right);
        update(&mut m, Msg::Backspace);
        assert_eq!(m.query, "nx");
    }

    #[test]
    fn home_and_end_follow_the_query() {
        let mut m = model();
        update(&mut m, Msg::Move(Motion::End));
        assert_eq!(m.cursor, m.rows.len() - 1, "an empty query leaves them to the list");

        type_str(&mut m, "co");
        let cursor = m.cursor;
        update(&mut m, Msg::Move(Motion::Home));
        assert_eq!(m.query_cursor, 0);
        update(&mut m, Msg::Move(Motion::End));
        assert_eq!(m.query_cursor, 2);
        assert_eq!(m.cursor, cursor, "the list did not move");
    }

    #[test]
    fn peeling_the_query_resets_its_cursor() {
        let mut m = model();
        update(&mut m, Msg::Char('c'));
        update(&mut m, Msg::Esc);
        assert_eq!(m.query_cursor, 0);
        update(&mut m, Msg::Char('e'));
        assert_eq!(m.query, "e");
    }

    /// On the record the arrows are inert: there is no query to move through.
    #[test]
    fn arrows_do_nothing_on_the_record() {
        let mut m = model();
        update(&mut m, Msg::Enter);
        assert_eq!(update(&mut m, Msg::Left), Effect::Idle);
        assert_eq!(update(&mut m, Msg::Right), Effect::Idle);
        assert!(m.detail(), "`←` leaves the record open; `Esc` closes it");
    }

    #[test]
    fn esc_peels_one_layer_at_a_time_and_quits_only_at_the_end() {
        let mut m = model();
        update(&mut m, Msg::Char('c'));
        update(&mut m, Msg::Enter);

        assert_eq!(update(&mut m, Msg::Esc), Effect::Redraw);
        assert!(!m.detail(), "first press closed the record");
        assert_eq!(m.query, "c", "and nothing else");
        assert_ne!(m.armed, Some(Armed::Esc), "closing something is not arming");

        assert_eq!(update(&mut m, Msg::Esc), Effect::Redraw);
        assert!(m.query.is_empty(), "second press cleared the search");

        assert_eq!(update(&mut m, Msg::Esc), Effect::Redraw);
        assert_eq!(m.armed, Some(Armed::Esc), "at base state it arms");

        assert_eq!(update(&mut m, Msg::Esc), Effect::Quit);
    }

    #[test]
    fn a_tap_beside_the_details_view_moves_it_to_that_row() {
        let mut m = model();
        m.cols = 120;
        m.list = ListGeometry { top: 1, height: 24, row_height: 1 };
        update(&mut m, Msg::Enter);
        assert_eq!(m.current().map(|doc| doc.id.as_str()), Some("coc"));
        m.set_record_cursor(2);
        update(&mut m, Msg::Tap { col: 5, row: 2 });
        assert_eq!(m.current().map(|doc| doc.id.as_str()), Some("eng1"));
        assert_eq!(m.record_cursor(), 0, "a different document starts at its top row");
        assert_eq!(m.views.len(), 1, "it replaced the view rather than stacking one");
    }

    /// A model whose passport has an older version and two latest ones.
    pub(crate) fn with_versions() -> Model {
        let mut m = writable();
        let mut add = |id: &str, issued: &str, supersedes: &str| {
            let mut version = m.store.docs[2].clone();
            version.id = id.into();
            version.issue_date = Some(issued.into());
            version.supersedes = Some(supersedes.into());
            m.store.docs.push(version);
        };
        add("passport-desk", "2026-02-10", "passport");
        add("passport-phone", "2026-01-05", "passport");
        m.store.derive();
        m.requery();
        m
    }

    #[test]
    fn versions_run_latest_then_conflicting_then_older() {
        let m = with_versions();
        let ids: Vec<&str> = crate::versions::rows(&m.store, "passport")
            .into_iter()
            .map(|i| m.store.docs[i].id.as_str())
            .collect();
        assert_eq!(ids, ["passport-desk", "passport-phone", "passport"]);
    }

    #[test]
    fn the_versions_view_opens_any_version() {
        let mut m = with_versions();
        m.cursor = m.rows.iter().position(|&i| m.store.docs[i].id == "passport-desk").unwrap();
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('v'));
        assert!(matches!(m.views.last(), Some(View::Versions { doc }) if doc == "passport-desk"));
        assert_eq!(m.current().map(|doc| doc.id.as_str()), Some("passport-desk"));

        update(&mut m, Msg::Move(Motion::End));
        update(&mut m, Msg::Enter);
        assert!(m.detail());
        assert_eq!(m.current().map(|doc| doc.id.as_str()), Some("passport"));

        update(&mut m, Msg::Esc);
        assert!(matches!(m.views.last(), Some(View::Versions { doc }) if doc == "passport"));
        update(&mut m, Msg::Esc);
        assert_eq!(m.current().map(|doc| doc.id.as_str()), Some("passport-desk"));
        update(&mut m, Msg::Esc);
        assert!(m.views.is_empty(), "{:?}", m.views);
    }

    #[test]
    fn renews_links_a_document_to_an_older_one() {
        let mut m = writable();
        update(&mut m, Msg::Enter);
        select_row(&mut m, crate::detail::Row::Renews);
        update(&mut m, Msg::Char('e'));
        type_str(&mut m, "testim");
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![journal::Draft::set("doc", "coc", "supersedes", "testimonial")])
        );
        land(&mut m);
        assert_eq!(
            m.undo.last().map(|change| change.back.clone()),
            Some(vec![journal::Draft::unset("doc", "coc", "supersedes")])
        );
    }

    #[test]
    fn the_versions_view_has_no_other_letters() {
        let mut m = with_versions();
        m.views.push(View::Versions { doc: "passport".into() });
        update(&mut m, Msg::Char('e'));
        assert!(m.flash.as_deref().is_some_and(|flash| flash.contains("no verb on `e`")));
        assert!(m.edit.is_none());
    }

    /// A model with two bundles, the newer one holding `coc` and `eng1`.
    fn with_bundles() -> Model {
        let mut m = writable();
        let bundle = |id: &str, name: &str, date: Option<&str>| crate::Bundle {
            id: id.into(),
            name: name.into(),
            date: date.map(Into::into),
            ..crate::Bundle::default()
        };
        m.store.bundles = vec![
            bundle("joining", "Joining", Some("2026-11-01")),
            bundle("visa", "US visa", Some("2026-03-27")),
        ];
        for doc in &mut m.store.docs[..2] {
            doc.bundles.push(crate::Membership { bundle: "joining".into(), file: None });
        }
        m.store.derive();
        m
    }

    #[test]
    fn the_bundles_view_has_its_own_search() {
        let mut m = with_bundles();
        type_str(&mut m, "eng");
        m.run(crate::sheet::Act::Bundles);
        assert!(matches!(
            m.views.last(),
            Some(View::Bundles { selected: crate::bundles::Entry::Bundle(_), .. })
        ));
        assert!(m.query.is_empty(), "the Bundles view starts with nothing typed");

        type_str(&mut m, "visa");
        let entries = crate::bundles::entries(&m.store, &m.query);
        let visa = crate::bundles::Entry::Bundle("visa".into());
        assert_eq!(entries, [crate::bundles::Entry::New, visa.clone()]);
        assert!(
            matches!(m.views.last(), Some(View::Bundles { selected, .. }) if *selected == visa),
            "on the match"
        );

        update(&mut m, Msg::Esc);
        assert!(m.query.is_empty(), "the first Esc clears the search");
        update(&mut m, Msg::Esc);
        assert!(m.views.is_empty(), "{:?}", m.views);
        assert_eq!(m.query, "eng", "and the second puts the Find view's back");
    }

    #[test]
    fn a_resort_keeps_the_selected_bundle() {
        let mut m = with_bundles();
        m.run(crate::sheet::Act::Bundles);
        update(&mut m, Msg::Move(Motion::Down));
        update(&mut m, Msg::Enter);
        assert!(matches!(m.views.last(), Some(View::Bundle { id, .. }) if id == "visa"));

        let mut store = m.store.clone();
        store.bundles.reverse();
        land_as(&mut m, store);
        let visa = crate::bundles::Entry::Bundle("visa".into());
        assert!(matches!(&m.views[0], View::Bundles { selected, .. } if *selected == visa));
    }

    #[test]
    fn a_bundle_is_created_from_the_search() {
        let mut m = with_bundles();
        m.run(crate::sheet::Act::Bundles);
        type_str(&mut m, "Panama");
        let id = "panama-desk";
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![
                journal::Draft::create("bundle", id),
                journal::Draft::set("bundle", id, "name", "Panama"),
            ])
        );
        let mut store = m.store.clone();
        store.bundles.push(crate::Bundle {
            id: id.into(),
            name: "Panama".into(),
            ..crate::Bundle::default()
        });
        land_as(&mut m, store);
        assert!(matches!(m.views.last(), Some(View::Bundle { id, .. }) if id == "panama-desk"));
        assert_eq!(m.flash.as_deref(), Some("created"));
        assert_eq!(m.query, "Panama", "the search is still there");
    }

    #[test]
    fn the_first_bundle_asks_for_its_name() {
        let mut m = writable();
        m.run(crate::sheet::Act::Bundles);
        update(&mut m, Msg::Enter);
        assert!(m.edit.as_ref().is_some_and(|edit| edit.target == Target::NewBundle));
        type_str(&mut m, "Joining");
        let Effect::Append(drafts) = update(&mut m, Msg::Enter) else { panic!("no append") };
        assert_eq!(drafts[0], journal::Draft::create("bundle", "joining-desk"));
        assert!(m.edit.as_ref().is_some_and(|edit| edit.saving));
    }

    #[test]
    fn a_bundle_edits_its_rows_and_opens_its_documents() {
        let mut m = with_bundles();
        m.run(crate::sheet::Act::Bundles);
        update(&mut m, Msg::Enter);
        assert!(matches!(m.views.last(), Some(View::Bundle { id, .. }) if id == "joining"));

        update(&mut m, Msg::Move(Motion::Down));
        update(&mut m, Msg::Char('e'));
        clear_buffer(&mut m);
        type_str(&mut m, "2026-12-01");
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![journal::Draft::set("bundle", "joining", "date", "2026-12-01")])
        );
        assert_eq!(
            m.pending.as_ref().map(|pending| pending.change.back.clone()),
            Some(vec![journal::Draft::set("bundle", "joining", "date", "2026-11-01")])
        );
        land(&mut m);
        assert_eq!(m.flash.as_deref(), Some("saved"));

        update(&mut m, Msg::Move(Motion::End));
        update(&mut m, Msg::Enter);
        assert!(m.detail());
        assert_eq!(m.current().map(|doc| doc.id.as_str()), Some("eng1"));
    }

    #[test]
    fn a_bundle_is_deleted_on_a_second_d() {
        let mut m = with_bundles();
        m.store.bundles[0].notes = "for the ship".into();
        m.run(crate::sheet::Act::Bundles);
        update(&mut m, Msg::Enter);
        assert_eq!(update(&mut m, Msg::Char('d')), Effect::Redraw);
        assert_eq!(m.armed, Some(Armed::Delete));
        assert_eq!(
            update(&mut m, Msg::Char('d')),
            Effect::Append(vec![journal::Draft::delete("bundle", "joining")])
        );
        let mut store = m.store.clone();
        store.bundles.remove(0);
        land_as(&mut m, store);
        assert!(matches!(m.views.last(), Some(View::Bundles { .. })), "back on the list");
        assert_eq!(m.flash.as_deref(), Some("deleted — u to undo"));
        assert_eq!(
            m.undo.last().map(|change| change.back.clone()),
            Some(vec![
                journal::Draft::create("bundle", "joining"),
                journal::Draft::set("bundle", "joining", "name", "Joining"),
                journal::Draft::set("bundle", "joining", "date", "2026-11-01"),
                journal::Draft::set("bundle", "joining", "notes", "for the ship"),
            ])
        );
    }

    #[test]
    fn the_bundles_checklist_adds_and_removes_this_version() {
        let mut m = with_bundles();
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('b'));
        let check = m.picker.clone().expect("the checklist opened");
        let ons: Vec<Option<bool>> = check.matching(&m).iter().map(|entry| entry.on).collect();
        assert_eq!(ons, [Some(true), Some(false)], "coc is in joining, not in visa");

        update(&mut m, Msg::Move(Motion::Down));
        let both = serde_json::json!([{"bundle": "joining"}, {"bundle": "visa"}]);
        assert_eq!(
            update(&mut m, Msg::Char(' ')),
            Effect::Append(vec![journal::Draft::set("doc", "coc", "bundles", both)]),
            "Space with nothing typed toggles"
        );
        assert_eq!(
            m.pending.as_ref().map(|pending| pending.change.back.clone()),
            Some(vec![journal::Draft::set(
                "doc",
                "coc",
                "bundles",
                serde_json::json!([{"bundle": "joining"}])
            )])
        );
        land(&mut m);
        assert!(m.picker.is_some(), "the list stays open");
        assert_eq!(m.flash.as_deref(), Some("added to US visa"));

        update(&mut m, Msg::Move(Motion::Up));
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![journal::Draft::unset("doc", "coc", "bundles")]),
            "taking out the last entry clears the field"
        );
    }

    #[test]
    fn a_new_bundle_from_the_checklist_holds_this_version() {
        let mut m = with_bundles();
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('b'));
        type_str(&mut m, "Panama");
        assert_eq!(m.picker.as_ref().map(|picker| picker.cursor), Some(0), "on + new");
        let id = "panama-desk";
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![
                journal::Draft::create("bundle", id),
                journal::Draft::set("bundle", id, "name", "Panama"),
                journal::Draft::set(
                    "doc",
                    "coc",
                    "bundles",
                    serde_json::json!([{"bundle": "joining"}, {"bundle": id}])
                ),
            ])
        );
        assert_eq!(
            m.pending.as_ref().map(|pending| pending.change.back.clone()),
            Some(vec![
                journal::Draft::set(
                    "doc",
                    "coc",
                    "bundles",
                    serde_json::json!([{"bundle": "joining"}])
                ),
                journal::Draft::delete("bundle", id),
            ])
        );
    }

    #[test]
    fn e_on_a_bundled_document_offers_versions_copies_and_removal() {
        let mut m = with_bundles();
        let mut newer = m.store.docs[0].clone();
        newer.id = "coc-2".into();
        newer.supersedes = Some("coc".into());
        newer.bundles.clear();
        with_second_file(&mut m.store);
        m.store.docs.push(newer);
        m.store.derive();
        m.run(crate::sheet::Act::Bundles);
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::Move(Motion::End));
        update(&mut m, Msg::Move(Motion::Up));
        update(&mut m, Msg::Char('e'));
        let picker = m.picker.clone().expect("the picker opened");
        let labels: Vec<String> =
            picker.matching(&m).into_iter().map(|entry| entry.label).collect();
        assert_eq!(
            labels,
            [
                "use COC Certificate  (latest)",
                "use only Marine/coc.pdf",
                "use only Marine/coc-back.pdf",
                "remove from this bundle"
            ]
        );
        let subject = picker.subject(&m.store).expect("a heading");
        assert_eq!(subject.2, "issue date unknown · all soft copies");

        let set =
            |id: &str, value: serde_json::Value| journal::Draft::set("doc", id, "bundles", value);
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![
                journal::Draft::unset("doc", "coc", "bundles"),
                set("coc-2", serde_json::json!([{"bundle": "joining"}])),
            ]),
            "the newer version takes the old one's place"
        );
        assert_eq!(
            m.pending.as_ref().map(|pending| pending.change.back.clone()),
            Some(vec![
                journal::Draft::unset("doc", "coc-2", "bundles"),
                set("coc", serde_json::json!([{"bundle": "joining"}])),
            ])
        );
    }

    #[test]
    fn a_bundled_document_can_use_one_soft_copy_or_leave() {
        let mut m = with_bundles();
        with_second_file(&mut m.store);
        let pick = |m: &mut Model, choice: Choice| {
            let member = Purpose::Member { doc: "coc".into(), bundle: "joining".into() };
            m.choose(&member, choice)
        };
        assert_eq!(
            pick(&mut m, Choice::UseFile(Some("Marine/coc-back.pdf".into()))),
            Effect::Append(vec![journal::Draft::set(
                "doc",
                "coc",
                "bundles",
                serde_json::json!([{"bundle": "joining", "file": "Marine/coc-back.pdf"}])
            )])
        );
        m.pending = None;
        assert_eq!(
            pick(&mut m, Choice::Leave),
            Effect::Append(vec![journal::Draft::unset("doc", "coc", "bundles")])
        );
    }

    /// Termux sends `Esc` to close the soft keyboard, so a stray press must not
    /// compound into an exit.
    #[test]
    fn any_other_key_disarms_the_quit() {
        let mut m = model();
        update(&mut m, Msg::Esc);
        assert_eq!(m.armed, Some(Armed::Esc));
        update(&mut m, Msg::Move(Motion::Down));
        assert_ne!(m.armed, Some(Armed::Esc));
        assert_eq!(update(&mut m, Msg::Esc), Effect::Redraw, "arms again rather than quitting");
    }

    #[test]
    fn a_new_day_reclassifies_expiry() {
        let mut m = model();
        let eng1 = m.store.get("eng1").unwrap().clone();
        assert_eq!(m.status(&eng1), Status::Ok);
        let day = Msg::Day { today: "2026-11-01".into(), warn_until: "2027-01-30".into() };
        assert_eq!(update(&mut m, day), Effect::Redraw);
        assert_eq!(m.status(&eng1), Status::Soon);
        assert!(m.due().iter().any(|&i| m.store.docs[i].id == "eng1"));
    }

    #[test]
    fn a_resize_between_the_escs_still_quits() {
        let mut m = model();
        update(&mut m, Msg::Esc);
        update(&mut m, Msg::Resize { cols: 47, rows: 45 });
        assert_eq!(update(&mut m, Msg::Esc), Effect::Quit);
    }

    #[test]
    fn the_list_shows_latest_documents_unless_a_toggle_widens_it() {
        let mut m = model();
        let mut newer = m.store.get("passport").unwrap().clone();
        newer.id = "passport-2".into();
        newer.supersedes = Some("passport".into());
        m.store.docs.push(newer);
        m.store.derive();
        m.requery();
        let shown =
            |m: &Model| m.rows.iter().map(|&i| m.store.docs[i].id.clone()).collect::<Vec<_>>();
        assert!(!shown(&m).contains(&"passport".to_string()));

        m.filter.old_versions = true;
        m.requery();
        assert!(shown(&m).contains(&"passport".to_string()));

        update(&mut m, Msg::Esc);
        assert_eq!(m.filter, Filter::ALL, "Esc clears every toggle in one peel");
        assert!(!shown(&m).contains(&"passport".to_string()));
    }

    #[test]
    fn toggles_compose() {
        let mut m = model();
        m.filter.old_versions = true;
        update(&mut m, Msg::ToggleExpiring);
        assert!(m.filter.expiring && m.filter.old_versions);
        update(&mut m, Msg::ToggleExpiring);
        assert!(!m.filter.expiring && m.filter.old_versions);
    }

    /// Links a second file, `Marine/coc-back.pdf`, to COC.
    pub(crate) fn with_second_file(store: &mut Store) {
        let i = store.index_of("coc").unwrap();
        store.docs[i].files.push(FileRef {
            label: String::new(),
            path: "Marine/coc-back.pdf".into(),
            primary: false,
        });
    }

    /// A writable model on COC's record, with a second file linked.
    fn on_coc_with_two_files() -> Model {
        let mut m = writable();
        with_second_file(&mut m.store);
        update(&mut m, Msg::Enter);
        m
    }

    fn select_row(m: &mut Model, wanted: crate::detail::Row) {
        let rows = crate::detail::rows(m.current().unwrap());
        m.set_record_cursor(rows.iter().position(|row| *row == wanted).expect("the row exists"));
    }

    fn files_written(effect: &Effect) -> Option<serde_json::Value> {
        let Effect::Append(drafts) = effect else { return None };
        drafts.iter().find(|d| d.f.as_deref() == Some("files")).and_then(|d| d.val.clone())
    }

    #[test]
    fn a_file_row_can_be_made_primary_and_undone() {
        let mut m = on_coc_with_two_files();
        select_row(&mut m, crate::detail::Row::File(1));
        update(&mut m, Msg::Char('e'));
        assert!(m.picker.is_some());
        let effect = update(&mut m, Msg::Enter);
        let written = files_written(&effect).expect("a files write");
        assert_eq!(written[1]["primary"], true);
        assert_eq!(written[0]["primary"], false);
        assert!(m.picker.is_none(), "choosing closes the picker");
        let back = &m.pending.as_ref().unwrap().change.back[0];
        assert_eq!(back.val.as_ref().unwrap()[0]["primary"], true, "undo restores the old primary");
    }

    #[test]
    fn detaching_the_last_file_unsets_the_list() {
        let mut m = writable();
        update(&mut m, Msg::Enter);
        select_row(&mut m, crate::detail::Row::File(0));
        update(&mut m, Msg::Char('e'));
        type_str(&mut m, "detach");
        let Effect::Append(drafts) = update(&mut m, Msg::Enter) else { panic!("no append") };
        assert_eq!(drafts, [journal::Draft::unset("doc", "coc", "files")]);
    }

    #[test]
    fn attaching_a_first_file_makes_it_primary() {
        let mut m = writable();
        type_str(&mut m, "passport");
        update(&mut m, Msg::Enter);
        select_row(&mut m, crate::detail::Row::Files);
        update(&mut m, Msg::Char('e'));
        assert_eq!(m.edit.as_ref().map(|edit| edit.field), Some(Field::Attach));
        type_str(&mut m, "Identity\\passport.pdf");
        let written = files_written(&update(&mut m, Msg::Enter)).expect("a files write");
        assert_eq!(written[0]["path"], "Identity/passport.pdf", "stored POSIX");
        assert_eq!(written[0]["primary"], true);
    }

    /// A model on `passport`'s empty files row, attaching under a real folder.
    fn attaching_under() -> (tempfile::TempDir, Model) {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.path().to_path_buf();
        std::fs::create_dir_all(root.join("Identity")).expect("mkdir");
        std::fs::create_dir_all(root.join("Marine")).expect("mkdir");
        for file in ["Identity/passport.pdf", "Identity/pan.pdf"] {
            std::fs::write(root.join(file), "").expect("write");
        }
        let mut m = writable();
        m.root = Some(root);
        type_str(&mut m, "passport");
        update(&mut m, Msg::Enter);
        select_row(&mut m, crate::detail::Row::Files);
        update(&mut m, Msg::Char('e'));
        (dir, m)
    }

    fn listed(m: &Model) -> Vec<String> {
        m.edit
            .as_ref()
            .map(|edit| edit.matches().into_iter().map(crate::complete::Entry::label).collect())
            .unwrap_or_default()
    }

    #[test]
    fn the_attach_list_opens_folders_and_tab_fills() {
        let (_dir, mut m) = attaching_under();
        assert_eq!(listed(&m), ["Identity/", "Marine/"]);
        update(&mut m, Msg::Move(Motion::Down));
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw, "a folder opens; nothing is saved");
        assert_eq!(m.edit.as_ref().unwrap().buffer, "Identity/");
        assert_eq!(listed(&m), ["pan.pdf", "passport.pdf"]);

        type_str(&mut m, "pas");
        assert_eq!(listed(&m), ["passport.pdf"]);
        update(&mut m, Msg::Tab);
        assert_eq!(m.edit.as_ref().unwrap().buffer, "Identity/passport.pdf");
        let written = files_written(&update(&mut m, Msg::Enter)).expect("a files write");
        assert_eq!(written[0]["path"], "Identity/passport.pdf");
    }

    #[test]
    fn an_empty_line_or_a_folder_is_never_attached() {
        let (_dir, mut m) = attaching_under();
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw);
        assert!(m.flash.as_deref().is_some_and(|flash| flash.contains("type a path")));
        assert!(m.edit.is_some(), "the line stays open");

        type_str(&mut m, "Identity");
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw, "nothing written");
        assert_eq!(m.edit.as_ref().unwrap().buffer, "Identity/", "the folder opened");
        assert_eq!(listed(&m), ["pan.pdf", "passport.pdf"]);
    }

    #[test]
    fn only_a_tap_on_a_row_does_anything_while_attaching() {
        let (_dir, mut m) = attaching_under();
        type_str(&mut m, "Identity/pas");
        m.panel = RowGeometry { top: 5, left: 0, width: 40, items: vec![0], ..Default::default() };
        assert_eq!(update(&mut m, Msg::Tap { col: 3, row: 1 }), Effect::Idle);
        assert_eq!(m.edit.as_ref().unwrap().buffer, "Identity/pas", "nothing saved or changed");
        let written = files_written(&update(&mut m, Msg::Tap { col: 3, row: 5 }));
        assert_eq!(written.expect("a files write")[0]["path"], "Identity/passport.pdf");
    }

    #[test]
    fn quitting_an_unsaved_edit_asks_first() {
        let mut m = writable();
        m.open_edit(Field::Name);
        update(&mut m, Msg::Char('!'));
        assert_eq!(update(&mut m, Msg::Quit), Effect::Redraw, "the first press asks");
        assert!(m.flash.as_deref().is_some_and(|flash| flash.contains("unsaved edit")));
        assert_eq!(update(&mut m, Msg::Quit), Effect::Quit);
    }

    #[test]
    fn enter_on_a_chosen_file_attaches_it() {
        let (_dir, mut m) = attaching_under();
        type_str(&mut m, "Identity/");
        update(&mut m, Msg::Move(Motion::Up));
        assert_eq!(
            m.edit.as_ref().unwrap().list.as_ref().unwrap().chosen,
            Some(1),
            "↑ from the line is the last row"
        );
        let written = files_written(&update(&mut m, Msg::Enter)).expect("a files write");
        assert_eq!(written[0]["path"], "Identity/passport.pdf");
    }

    #[test]
    fn attaching_a_file_twice_is_refused() {
        let mut m = on_coc_with_two_files();
        select_row(&mut m, crate::detail::Row::File(0));
        update(&mut m, Msg::Char('e'));
        type_str(&mut m, "attach");
        update(&mut m, Msg::Enter);
        type_str(&mut m, "Marine/coc.pdf");
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw, "nothing appended");
        assert!(m.flash.as_deref().unwrap_or_default().contains("already attached"));
        assert_eq!(m.edit.as_ref().unwrap().buffer, "Marine/coc.pdf");
    }

    #[test]
    fn esc_peels_the_picker() {
        let mut m = on_coc_with_two_files();
        select_row(&mut m, crate::detail::Row::File(1));
        update(&mut m, Msg::Char('e'));
        update(&mut m, Msg::Char('d'));
        update(&mut m, Msg::Esc);
        assert_eq!(m.picker.as_ref().map(|p| p.filter.as_str()), Some(""));
        update(&mut m, Msg::Esc);
        assert!(m.picker.is_none());
        assert!(m.detail());
    }

    #[test]
    fn a_read_only_session_gets_no_picker() {
        let mut m = model();
        update(&mut m, Msg::Enter);
        select_row(&mut m, crate::detail::Row::File(0));
        update(&mut m, Msg::Char('e'));
        assert!(m.picker.is_none());
        assert!(m.flash.is_some());
    }

    #[test]
    fn tap_then_tap_drills() {
        let mut m = model();
        m.list = ListGeometry { top: 1, height: 24, row_height: 2 };
        // Row 1 of the list is the second document (two screen lines each).
        assert_eq!(update(&mut m, Msg::Tap { col: 5, row: 3 }), Effect::Redraw);
        assert_eq!(m.cursor, 1);
        assert!(!m.detail());
        assert_eq!(update(&mut m, Msg::Tap { col: 5, row: 3 }), Effect::Redraw);
        assert!(m.detail());
    }

    #[test]
    fn a_tap_on_nothing_is_idle() {
        let mut m = model();
        m.list = ListGeometry { top: 1, height: 24, row_height: 2 };
        assert_eq!(update(&mut m, Msg::Tap { col: 5, row: 20 }), Effect::Idle);
        assert_eq!(m.cursor, 0);
    }

    /// Mouse reporting must never stay off unnoticed.
    #[test]
    fn the_ime_affordance_restores_itself_on_the_next_key() {
        let mut m = model();
        m.raise_keyboard();
        assert!(!m.mouse_on);

        update(&mut m, Msg::Char('c'));
        assert!(m.mouse_on);
        assert_eq!(m.query, "c", "and the keystroke still counted");
    }

    /// Checked at both ends of the drawn zone: a rounding mistake shows up at a
    /// boundary first.
    #[test]
    fn tapping_the_header_count_filters_to_what_is_expiring() {
        let mut m = model();
        crate::find::draw_for_test(&mut m, 45, 28);
        let zone = m.count_zone;
        assert!(zone.width > 0, "the count is pressable on a touch layout");

        for col in [zone.col, zone.col + zone.width - 1] {
            let mut m = model();
            crate::find::draw_for_test(&mut m, 45, 28);
            update(&mut m, Msg::Tap { col, row: 0 });
            assert_eq!(m.filter, Filter::EXPIRING, "col {col} filters");
            // A toggle, not a jump: the second tap peels it off.
            update(&mut m, Msg::Tap { col, row: 0 });
            assert_eq!(m.filter, Filter::ALL, "col {col} toggles back");
        }

        // A column outside it is not a button.
        let mut m = model();
        crate::find::draw_for_test(&mut m, 45, 28);
        update(&mut m, Msg::Tap { col: zone.col - 1, row: 0 });
        assert_eq!(m.filter, Filter::ALL);
    }

    #[test]
    fn a_wide_terminal_draws_no_touch_affordances() {
        let mut m = model();
        crate::find::draw_for_test(&mut m, 120, 40);
        assert_eq!(m.count_zone.width, 0);
        assert_eq!(m.leader_zone.width, 0);
    }

    /// The query is the mode, which is how a modeless surface gets a prefix key.
    #[test]
    fn space_leads_when_the_query_is_empty_and_types_when_it_is_not() {
        let mut m = model();
        update(&mut m, Msg::Char(' '));
        assert!(m.sheet, "the sheet opened");
        assert!(m.query.is_empty(), "and nothing was typed");

        update(&mut m, Msg::Esc);
        assert!(!m.sheet, "esc peels the sheet first");

        update(&mut m, Msg::Char('c'));
        update(&mut m, Msg::Char(' '));
        assert_eq!(m.query, "c ", "mid-query it is just a space");
        assert!(!m.sheet);
    }

    /// A live filter does not make an empty query "typing" — the query decides,
    /// and nothing else.
    #[test]
    fn space_still_leads_with_a_filter_up() {
        let mut m = model();
        update(&mut m, Msg::ToggleExpiring);
        update(&mut m, Msg::Char(' '));
        assert!(m.sheet);
    }

    #[test]
    fn the_filter_list_toggles_with_space_and_stays_open() {
        let mut m = model();
        type_str(&mut m, " f");
        assert!(!m.sheet && m.picker.is_some(), "the sheet gave way to the checklist");
        update(&mut m, Msg::Char(' '));
        assert!(m.filter.expiring, "Space ticked expiring only");
        update(&mut m, Msg::Move(Motion::Down));
        update(&mut m, Msg::Enter);
        assert!(m.filter.expiring && m.filter.old_versions, "Enter ticked the next one");
        assert!(m.picker.is_some(), "and the list is still open");
    }

    #[test]
    fn typing_searches_the_filter_list_and_esc_peels_it() {
        let mut m = model();
        type_str(&mut m, " fold");
        assert_eq!(m.picker.as_ref().map(|p| p.filter.as_str()), Some("old"));
        update(&mut m, Msg::Enter);
        assert!(m.filter.old_versions);
        update(&mut m, Msg::Esc);
        assert_eq!(m.picker.as_ref().map(|p| p.filter.as_str()), Some(""), "the typing went first");
        update(&mut m, Msg::Esc);
        assert!(m.picker.is_none());
        assert_ne!(m.armed, Some(Armed::Esc), "closing it did not arm the quit");
    }

    #[test]
    fn backspace_with_nothing_typed_closes_the_checklist() {
        let mut m = model();
        type_str(&mut m, " fo");
        update(&mut m, Msg::Backspace);
        assert!(m.picker.is_some(), "the typing went first");
        update(&mut m, Msg::Backspace);
        assert!(m.picker.is_none());
    }

    #[test]
    fn end_selects_the_last_row_of_a_panel() {
        let mut m = model();
        type_str(&mut m, " f");
        update(&mut m, Msg::Move(Motion::End));
        assert_eq!(m.picker.as_ref().map(|p| p.cursor), Some(3), "on clear all");
    }

    #[test]
    fn a_letter_with_no_verb_says_so() {
        let mut m = model();
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('x'));
        assert!(m.sheet, "still open");
        assert_eq!(m.flash.as_deref(), Some("no verb on `x` here"));
        update(&mut m, Msg::Esc);
        assert!(!m.sheet, "one Esc closes it");
    }

    #[test]
    fn a_pushed_record_makes_the_chrome_untappable() {
        let mut m = model();
        crate::find::draw_for_test(&mut m, 45, 28);
        let zone = m.count_zone;
        update(&mut m, Msg::Enter);
        crate::find::draw_for_test(&mut m, 45, 28);

        update(&mut m, Msg::Tap { col: zone.col, row: 0 });
        assert_eq!(m.filter, Filter::ALL, "the count is not a button here");
        assert_eq!(update(&mut m, Msg::Tap { col: 3, row: 26 }), Effect::Idle);
        assert!(m.mouse_on, "and the field did not drop reporting either");
    }

    #[test]
    fn arrows_move_the_record_selector_and_not_the_list() {
        let mut m = model();
        let before = m.cursor;
        update(&mut m, Msg::Enter);
        assert_eq!(m.record_cursor(), 0, "drilling in starts at the top");

        update(&mut m, Msg::Move(Motion::Down));
        update(&mut m, Msg::Move(Motion::Down));
        assert_eq!(m.record_cursor(), 2);
        assert_eq!(m.cursor, before, "the document underneath never moved");

        // And back out, the list has them again.
        update(&mut m, Msg::Esc);
        update(&mut m, Msg::Move(Motion::Down));
        assert_ne!(m.cursor, before);
    }

    #[test]
    fn the_record_selector_cannot_run_off_either_end() {
        let mut m = model();
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::Move(Motion::Up));
        assert_eq!(m.record_cursor(), 0);

        let rows = crate::detail::rows(m.current().unwrap()).len();
        for _ in 0..rows + 5 {
            update(&mut m, Msg::Move(Motion::Down));
        }
        assert_eq!(m.record_cursor(), rows - 1);
    }

    /// Find-fast is scoped to the browse surface, which frees the record to have
    /// letter keys, so editing needs no control key.
    #[test]
    fn letters_are_verbs_on_the_record_not_query_text() {
        let mut m = model();
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::Char('z'));
        assert!(m.query.is_empty(), "nothing reached the query");
        assert!(m.flash.is_some(), "and an unknown verb says so rather than doing nothing");
    }

    #[test]
    fn e_edits_the_selected_row() {
        let mut m = writable();
        update(&mut m, Msg::Enter);
        select_row(&mut m, crate::detail::Row::Editable(Field::Expiry));
        update(&mut m, Msg::Char('e'));
        assert_eq!(
            m.edit.as_ref().map(|edit| edit.field),
            Some(Field::Expiry),
            "on the editable row it opens the editor"
        );
    }

    /// The same verb through the sheet, because a chord is a shortcut for a verb
    /// and never a second implementation of it.
    #[test]
    fn the_sheet_offers_the_record_verb_too() {
        let mut m = writable();
        update(&mut m, Msg::Enter);
        select_row(&mut m, crate::detail::Row::Editable(Field::Expiry));

        update(&mut m, Msg::Char(' '));
        assert!(m.sheet, "space still opens the sheet on the record");
        let listed = crate::sheet::items(&m);
        assert!(
            listed.iter().any(|item| item.act == crate::sheet::Act::Edit),
            "and it lists the record's verb: {listed:?}"
        );

        update(&mut m, Msg::Char('e'));
        assert_eq!(m.edit.as_ref().map(|edit| edit.field), Some(Field::Expiry));
        assert!(!m.sheet, "running an item closes the sheet");
    }

    /// A thumb opens the highlighted row by tapping it again, so `Enter` needs no
    /// button.
    #[test]
    fn opening_needs_no_button() {
        let mut m = model();
        m.list = ListGeometry { top: 1, height: 24, row_height: 2 };
        update(&mut m, Msg::Tap { col: 5, row: 1 });
        assert_eq!(
            update(&mut m, Msg::Tap { col: 5, row: 1 }),
            Effect::Open("Marine/coc.pdf".into()),
            "tap, then tap again"
        );
    }

    #[test]
    fn the_margins_are_not_buttons() {
        let bar = model().rows_on_screen - 3;
        for col in [0, 1, 44] {
            let mut m = model();
            assert_eq!(update(&mut m, Msg::Tap { col, row: bar }), Effect::Idle, "col {col}");
            assert!(m.flash.is_none());
        }
    }

    #[test]
    fn tapping_the_search_bar_drops_mouse_reporting_for_the_ime() {
        let mut m = model();
        // **Both** rows of the touch search bar, right down to the screen edge.
        for row in [m.rows_on_screen - 2, m.rows_on_screen - 1] {
            m.mouse_on = true;
            update(&mut m, Msg::Tap { col: 3, row });
            assert!(!m.mouse_on, "row {row} is part of the target");
        }

        // And the next keystroke puts reporting back.
        update(&mut m, Msg::Char('c'));
        assert!(m.mouse_on);
        assert_eq!(m.query, "c");
    }

    #[test]
    fn the_expiring_filter_narrows_and_peels() {
        let mut m = model();
        m.warn_until = "2031-12-31".into();
        update(&mut m, Msg::ToggleExpiring);
        let ids: Vec<&str> = m.rows.iter().map(|&i| m.store.docs[i].id.as_str()).collect();
        assert_eq!(ids, ["coc", "eng1", "passport"], "soonest first, untracked gone");

        update(&mut m, Msg::Char('e'));
        update(&mut m, Msg::Char('n'));
        update(&mut m, Msg::Char('g'));
        assert_eq!(m.rows.len(), 1, "search narrows inside the filter");

        update(&mut m, Msg::Esc);
        assert_eq!(m.filter, Filter::EXPIRING, "the first peel took the search");
        update(&mut m, Msg::Esc);
        assert_eq!(m.filter, Filter::ALL);
        assert_eq!(m.rows.len(), 4);
    }

    #[test]
    fn cursor_movement_clamps_at_both_ends() {
        let mut m = model();
        update(&mut m, Msg::Move(Motion::Up));
        assert_eq!(m.cursor, 0, "up from the top stays");
        update(&mut m, Msg::Move(Motion::End));
        assert_eq!(m.cursor, 3);
        update(&mut m, Msg::Move(Motion::PageDown));
        assert_eq!(m.cursor, 3, "down from the bottom stays");
        update(&mut m, Msg::Move(Motion::Home));
        assert_eq!(m.cursor, 0);
    }

    #[test]
    fn an_empty_result_is_a_valid_state() {
        let mut m = model();
        type_str(&mut m, "zzzz");
        assert!(m.rows.is_empty(), "{:?}", m.rows);
        assert!(m.current().is_none());
        assert!(m.on_new);
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw);
        assert!(m.flash.is_some());
        assert!(m.edit.is_none());

        update(&mut m, Msg::Backspace);
        assert_eq!(m.query, "zzz");
    }

    /// On a phone the highlight is where the next tap goes, so nothing scrolls
    /// away from it.
    #[test]
    fn scrolling_keeps_the_cursor_on_screen() {
        let mut m = model();
        m.rows_on_screen = 16; // 12 chrome-free lines → 6 two-line rows
        update(&mut m, Msg::Scroll(2));
        assert_eq!(m.offset, 0, "a four-row list cannot scroll past its end");

        let many: Vec<Doc> =
            (0..50).map(|i| doc(&format!("d{i}"), &format!("Doc {i}"), None, None)).collect();
        m.store = Store { docs: many, ..Store::default() };
        m.store.derive();
        m.filter = Filter::ALL;
        m.query.clear();
        m.requery();
        update(&mut m, Msg::Scroll(10));
        assert_eq!(m.offset, 10);
        assert!(m.cursor >= m.offset, "the cursor came along");
        update(&mut m, Msg::Scroll(-100));
        assert_eq!(m.offset, 0);
    }

    #[test]
    fn the_scan_search_loads_on_a_worker_and_arrives_as_a_message() {
        let mut m = model();
        assert_eq!(update(&mut m, Msg::ToggleScans), Effect::LoadScans);
        assert_eq!(m.scan_search, ScanSearch::Loading, "and it says so on screen");

        let scans = std::sync::Arc::new(crate::scans::Scans {
            by_path: [("Marine/coc.pdf".to_string(), "master mariner".to_string())]
                .into_iter()
                .collect(),
        });
        update(&mut m, Msg::ScansLoaded(scans));
        assert_eq!(m.scan_search, ScanSearch::On);

        type_str(&mut m, "mariner");
        assert_eq!(m.rows.len(), 1, "found by what the page says, not by its name");
        assert_eq!(m.current().unwrap().id, "coc");

        // Off again, and the word is nowhere in any name.
        update(&mut m, Msg::ToggleScans);
        assert_eq!(m.scan_search, ScanSearch::Off);
        assert!(m.rows.is_empty(), "{:?}", m.rows);
    }

    #[test]
    fn scan_matches_are_added_to_name_matches_in_list_order() {
        let mut m = model();
        let scans = std::sync::Arc::new(crate::scans::Scans {
            by_path: [("Marine/eng1.pdf".to_string(), "coc reference".to_string())]
                .into_iter()
                .collect(),
        });
        update(&mut m, Msg::ToggleScans);
        update(&mut m, Msg::ScansLoaded(scans));
        type_str(&mut m, "coc");
        let ids: Vec<&str> = m.rows.iter().map(|&i| m.store.docs[i].id.as_str()).collect();
        assert_eq!(ids, ["coc", "eng1"], "the name match first, in list order");
    }

    #[test]
    fn the_second_toggle_needs_no_second_load() {
        let mut m = model();
        update(&mut m, Msg::ToggleScans);
        update(&mut m, Msg::ScansLoaded(std::sync::Arc::new(crate::scans::Scans::default())));
        update(&mut m, Msg::ToggleScans);
        assert_eq!(update(&mut m, Msg::ToggleScans), Effect::Redraw, "no second load");
        assert_eq!(m.scan_search, ScanSearch::On);
    }

    #[test]
    fn a_late_load_does_not_reopen_the_toggle_or_disarm_the_quit() {
        let mut m = model();
        update(&mut m, Msg::ToggleScans);
        update(&mut m, Msg::ToggleScans); // changed their mind while it loaded
        update(&mut m, Msg::Esc);
        assert_eq!(m.armed, Some(Armed::Esc));

        update(&mut m, Msg::ScansLoaded(std::sync::Arc::new(crate::scans::Scans::default())));
        assert_eq!(m.scan_search, ScanSearch::Off, "not turned on behind their back");
        assert!(m.scans.is_some(), "but the work is kept");
        assert_eq!(m.armed, Some(Armed::Esc), "a worker message is not a keystroke");
    }

    #[test]
    fn the_attention_count_is_expired_plus_soon() {
        let m = model();
        assert_eq!(m.due().len(), 1, "COC is expired; ENG-1 is outside the window");
    }
}
