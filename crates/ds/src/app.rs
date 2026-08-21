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
//! REWRITE.md §11 names "Ratatui immediate-mode state management sprawl" as the
//! top implementation risk and fixes the cure at the start of R3: a `msg →
//! update → view` loop. This module is `update`; [`crate::find`] and
//! [`crate::detail`] are `view`. **Nothing here draws, and nothing in the view
//! decides.** The one exception is deliberate and documented below.
//!
//! # Why messages and effects, when the spike used a bool
//!
//! The R0.2 spike mutated state directly and returned "redraw?" — right for a
//! throwaway. The real app has to open files, flip the terminal's mouse mode and
//! (from R5) receive results from worker threads, none of which the model can do
//! itself. So `update` returns an [`Effect`]: a description of what the shell of
//! the program should do next. The model stays pure and directly testable, and
//! every test in this file is a rule from REWRITE.md §4.5 rather than a
//! rendering.
//!
//! # The one place the view writes back
//!
//! The renderer publishes the rectangle it actually drew the rows into
//! ([`Model::list`]), because a tap has to be hit-tested against the layout that
//! is on screen. Recomputing that geometry here would make two sources of truth
//! and they would drift.

use crate::layout;
use crate::{Doc, Status, Store};

/// One thing the user did, already stripped of terminal detail.
///
/// rust: an enum, not a struct with an option per field. Exhaustive `match` in
/// [`update`] then means the compiler tells us when a new message has no
/// handler — the state-machine tool Rust gives us that Python does not.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Msg {
    /// A worker finished reading the `enrich` namespace (`ctrl+t`).
    ///
    /// rust: an `Arc`, so handing the result to the model costs a pointer copy
    /// rather than cloning a store's worth of transcripts across the thread
    /// boundary. This variant is also why `Msg` is not `Copy` — messages from
    /// workers carry data, and that is the whole point of having them.
    ScansLoaded(std::sync::Arc<crate::scans::Scans>),
    /// `ctrl+e` — edit a field of the highlighted document (R4).
    EditField(crate::edit::Field),
    /// An append landed, and here is the store re-folded around it.
    ///
    /// rust: `Box`ed because a `Store` is much larger than every other variant,
    /// and an enum is as big as its widest arm — every `Msg` in the queue would
    /// otherwise carry a store's worth of space around with it.
    Saved(Box<Store>),
    /// The append did not land. The editor stays open with the typing intact:
    /// the screen must never claim a value the journal refused.
    SaveFailed {
        /// What went wrong, ready for the status band.
        reason: String,
        /// Whether it will fail the same way every time.
        ///
        /// A held writer lock will (another `ds` has the journal, §3.1), so
        /// editing goes off for the session; a full disk might not, so it does
        /// not. The shell decides this, because the shell is what knows which
        /// error it caught — a caller matching on the *text* of a message would
        /// break the first time the wording improved.
        permanent: bool,
    },
    /// A bare printable character. On the Find surface every one of these is
    /// search text (invariant 1) — the surface binds no letter keys at all.
    Char(char),
    /// Rub out the last character of the query.
    Backspace,
    /// `Enter` — open the file (invariant 2).
    Enter,
    /// `→` — open the record.
    OpenDetail,
    /// `←` — close it again.
    CloseDetail,
    /// Cursor movement.
    Move(Motion),
    /// `Esc` — peel exactly one layer (invariant 3).
    Esc,
    /// `ctrl+q` / `ctrl+c` — leave now, from anywhere.
    Quit,
    /// `ctrl+t` — include scan text in the search.
    ToggleScans,
    /// `ctrl+x` — the expiring filter (a filter, never a mode).
    ToggleExpiring,
    /// The `⌨` affordance: drop mouse reporting so the next tap raises the IME.
    RaiseKeyboard,
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
    /// as [`Msg::ScansLoaded`]. **Never on the render loop** (invariant 7).
    LoadScans,
    /// Append these ops to this device's journal, then re-fold and post the new
    /// store back as [`Msg::Saved`].
    ///
    /// A `Vec` rather than one draft because the contract already needs runs
    /// that are only correct together — an id rename is create-new, copy,
    /// fixups, delete-old (§3.2) — and one call is what keeps them adjacent in
    /// one writer's file. This slice only ever sends one.
    ///
    /// Like every other effect, it names what should happen and not how: the
    /// writer, its lock and its fsync all live on the far side of the shell,
    /// off the render loop (invariant 7).
    Append(Vec<journal::Draft>),
    /// Leave, restoring the terminal.
    Quit,
}

/// One write this session made, and the ops that put it back.
///
/// **Both halves are kept, and that is what makes redo possible at all.** Undo
/// appends `back`; redo appends `forward` — the very ops that were written the
/// first time, so redo needs no re-derivation and cannot drift from what it is
/// putting back.
///
/// The `back` half is a **snapshot**, not a rule: it records what the store held
/// when the change was made. That is the right thing for the undo/redo dance
/// (undo, redo, undo returns to the same place), and it is deliberately not a
/// promise about a document the *other* device has since edited — field-level
/// LWW settles that, and the loser is still in the journal (§3.2).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Change {
    /// What was written.
    pub forward: Vec<journal::Draft>,
    /// What puts it back.
    pub back: Vec<journal::Draft>,
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

/// Whether this session can write, and what to say when it cannot.
///
/// Not a `bool`: every way of *not* being able to write comes with a reason the
/// user needs, and a reason that is not carried next to the state is a reason
/// that gets lost. REWRITE.md §3.1 is explicit that a second process must
/// "continue read-only with a visible notice" rather than fail.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WriteState {
    /// Editing is available, under this device's name.
    ///
    /// **The device travels with the permission** rather than beside it: a
    /// session may write exactly when it knows who it is, since the device is
    /// the first half of the writer id every op is appended under — and, since
    /// [`crate::id::mint`], part of the id of every document created here. A
    /// separate `Option<String>` would let those two facts drift apart, and the
    /// state that could then exist — allowed to write, no idea as whom — is one
    /// nothing downstream could do anything sensible with.
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
    /// A model that nobody told about a device cannot write.
    ///
    /// The default is the *safe* state rather than the convenient one: a `Model`
    /// built in a test, or before `main` has read the config, must not offer an
    /// edit it has no writer id to perform.
    fn default() -> Self {
        WriteState::Off("no device name — run `ds init` to enable editing".into())
    }
}

/// Which documents the list is showing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum Filter {
    /// Everything, in shelf order.
    #[default]
    All,
    /// Only documents in the expiry watch, soonest first (`:expiring`).
    Expiring,
}

/// Whether `ctrl+t` is on, and whether the text it needs has arrived.
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

/// Where the leader sheet is, and what has been typed into it.
///
/// See [`crate::sheet`] for what it contains and why it exists at all.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SheetState {
    /// The group entered so far — `None` is the top level.
    pub group: Option<char>,
    /// Text typed into the sheet, which turns it into a command picker.
    pub filter: String,
    /// Which of the matching items is selected.
    pub cursor: usize,
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

/// Everything the renderer reads and the event loop changes.
///
/// The flags are independent facts about the screen — detail open, quit armed,
/// mouse reporting on, keyboard hint showing — not a state machine. Packing them
/// into an enum would have to encode combinations that do not exist and would
/// lose the ones that do (detail open *while* armed *while* reporting is
/// dropped is a real state a phone reaches).
#[allow(clippy::struct_excessive_bools)]
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
    /// `ctrl+t`: whether scan text is part of the haystack, and whether it has
    /// arrived yet.
    pub scan_search: ScanSearch,
    /// The scan text, once a worker has read it. Kept even when the toggle is
    /// off, so a second `ctrl+t` is instant.
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
    /// Detail is a sticky toggle (U3): once open it follows the cursor until
    /// closed.
    pub detail: bool,
    /// One more `Esc` and we quit.
    pub esc_armed: bool,
    /// Whether SGR mouse reporting is currently on.
    pub mouse_on: bool,
    /// Reporting was dropped on purpose so the next tap raises the keyboard.
    pub keyboard_hint: bool,
    /// The leader sheet, when it is open.
    pub sheet: Option<SheetState>,
    /// Which row of the open record the selector is on ([`crate::detail::rows`]).
    ///
    /// Zeroed whenever the record opens or changes document, so drilling in
    /// always starts at the top rather than wherever the last record left it.
    pub record_cursor: usize,
    /// The field being edited, when one is (R4).
    pub edit: Option<crate::edit::Edit>,
    /// **This session's own writes, newest last** — each with the way back.
    ///
    /// §3.3 makes the journal the history, and undo an *inverse op* rather than
    /// a rewrite — nothing is ever removed from a journal. What is kept here is
    /// the [`Change`]: the ops that were written and the ops that put them back,
    /// the second computed at the moment of the write from the store as it then
    /// stood. The inverse of a `set` is the value that was there before it,
    /// which is knowable then and only awkwardly afterwards. Reconstructing it
    /// later means re-folding the journal to a point in time, which is a
    /// *history browser* — §8's "30-day horizon", a later phase — and not this.
    ///
    /// So this covers **this session's writes**, and a restart empties it. The
    /// journal still holds everything; only the shortcut back is per-session.
    pub undo: Vec<Change>,
    /// Undone writes, newest last, waiting to be put back.
    ///
    /// **Cleared by any ordinary write**, which is what makes redo mean what it
    /// means everywhere: once history has branched, the future this described is
    /// one the store never took, and offering it would put back an edit against
    /// a document that has moved on since.
    pub redo: Vec<Change>,
    /// The change the append currently in flight represents, promoted onto the
    /// stack the direction says when the journal confirms it and dropped when it
    /// refuses — so a write that never landed can never be taken back.
    pending: Option<Change>,
    /// The document an in-flight append is about, when no edit is open to name
    /// it. An undo can be about a document the cursor is nowhere near.
    pending_anchor: Option<String>,
    /// Which way the append in flight is going. This is what stops an undo from
    /// stacking itself as something to undo, and decides which stack the
    /// confirmed change lands on.
    direction: Direction,
    /// Whether the forward append in flight is a deletion — the one write that
    /// leaves nothing to look at afterwards, and so needs its own word for it.
    pending_delete: bool,
    /// One more `d` and the record's document is tombstoned.
    ///
    /// The same arming idiom `Esc` and quit already use, for the same reason:
    /// on a phone the thumb that meant `e` is one row from the key that means
    /// this. It is armed rather than confirmed with a dialog because a dialog
    /// would be a fourth surface, and because the write **is** reversible — `u`
    /// puts the document back, fields and all.
    pub delete_armed: bool,
    /// Whether this session can write, and why not when it cannot.
    pub write: WriteState,
    /// Where the view drew the header's pressable expiring count.
    pub count_zone: Zone,
    /// Where the view drew the `SPC` chip.
    pub leader_zone: Zone,
    /// A transient one-line message, cleared by the next key.
    pub flash: Option<String>,
    /// Terminal width.
    pub cols: u16,
    /// Terminal height.
    pub rows_on_screen: u16,
    /// Where the rows were last drawn (see [`ListGeometry`]).
    pub list: ListGeometry,
}

impl Model {
    /// Build the initial state: whole store, no query, cursor at the top.
    #[must_use]
    pub fn new(store: Store, today: String, warn_until: String, cols: u16, rows: u16) -> Self {
        let mut model = Self {
            store,
            today,
            warn_until,
            query: String::new(),
            scan_search: ScanSearch::Off,
            scans: None,
            filter: Filter::All,
            rows: Vec::new(),
            cursor: 0,
            offset: 0,
            detail: false,
            esc_armed: false,
            mouse_on: true,
            keyboard_hint: false,
            sheet: None,
            record_cursor: 0,
            edit: None,
            write: WriteState::default(),
            undo: Vec::new(),
            redo: Vec::new(),
            pending: None,
            pending_anchor: None,
            direction: Direction::Forward,
            pending_delete: false,
            delete_armed: false,
            count_zone: Zone::default(),
            leader_zone: Zone::default(),
            flash: None,
            cols,
            rows_on_screen: rows,
            list: ListGeometry::default(),
        };
        model.requery();
        model
    }

    /// The highlighted document, if anything matched.
    #[must_use]
    pub fn current(&self) -> Option<&Doc> {
        self.rows.get(self.cursor).map(|&i| &self.store.docs[i])
    }

    /// The expiry standing of a document, against today and the warn window.
    #[must_use]
    pub fn status(&self, doc: &Doc) -> Status {
        doc.status(&self.today, &self.warn_until)
    }

    /// How many documents are in the expiry watch and want attention — the
    /// header count that names `:expiring`.
    #[must_use]
    pub fn attention_count(&self) -> usize {
        self.store
            .docs
            .iter()
            .filter(|doc| matches!(self.status(doc), Status::Expired | Status::Soon))
            .count()
    }

    /// Rows that fit on screen right now.
    #[must_use]
    pub fn visible_rows(&self) -> usize {
        layout::visible_rows(self.cols, self.rows_on_screen)
    }

    /// Re-run filter + search and clamp the cursor.
    ///
    /// Called on every keystroke. The whole cost is a scan of pre-folded
    /// haystacks — R0.2 measured 0.33 ms for filter-plus-repaint at store scale
    /// on the phone, which is why there is no index and no debounce.
    fn requery(&mut self) {
        let base = match self.filter {
            Filter::All => None,
            Filter::Expiring => Some(self.store.expiring()),
        };
        let mut matched = self.store.search(&self.query);
        // `ctrl+t` widens the haystack rather than replacing it: a document
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
    }

    /// Move the cursor, clamped. Never wraps: a wrapping list on a phone is a
    /// way to lose your place with a fat thumb.
    fn move_cursor(&mut self, motion: Motion) {
        if self.rows.is_empty() {
            return;
        }
        let last = self.rows.len() - 1;
        let page = self.visible_rows().max(1);
        self.cursor = match motion {
            Motion::Up => self.cursor.saturating_sub(1),
            Motion::Down => (self.cursor + 1).min(last),
            Motion::PageUp => self.cursor.saturating_sub(page),
            Motion::PageDown => (self.cursor + page).min(last),
            Motion::Home => 0,
            Motion::End => last,
        };
    }

    /// The `Enter` verb: open the file.
    ///
    /// **It never mutates and never dies** (invariant 2). With no file linked
    /// there is nothing to open, so it falls through to the record — which is
    /// the useful thing to do next, and is why this verb can be pressed blind
    /// after typing three characters.
    fn activate(&mut self) -> Effect {
        let Some(doc) = self.current() else {
            self.flash = Some("nothing to open".into());
            return Effect::Redraw;
        };
        let Some(file) = doc.primary_file() else {
            let name = doc.name.clone();
            self.flash = Some(format!("no file linked — showing the record for {name}"));
            self.detail = true;
            return Effect::Redraw;
        };
        let path = file.path.clone();
        self.flash = Some(format!("opening {path}"));
        Effect::Open(path)
    }

    /// `Esc` peels exactly one layer per press (invariant 3).
    ///
    /// The order is REWRITE-UI.md §8's: search, then the pushed surface, then
    /// arm, then quit. It matters most on Termux, where `Esc` is also how the
    /// soft keyboard is dismissed: every press must consume something visible
    /// before it can ever reach "quit", or the app dies on an IME dismissal.
    fn peel(&mut self, was_armed: bool) -> Effect {
        // The sheet peels the same way everything else does — one layer per
        // press, outermost first — so `Esc` never needs a second meaning.
        if let Some(sheet) = &mut self.sheet {
            if !sheet.filter.is_empty() {
                sheet.filter.clear();
                sheet.cursor = 0;
            } else if sheet.group.is_some() {
                sheet.group = None;
                sheet.cursor = 0;
            } else {
                self.sheet = None;
            }
            return Effect::Redraw;
        }
        if !self.query.is_empty() {
            self.query.clear();
            self.requery();
        } else if self.detail {
            self.detail = false;
        } else if self.filter != Filter::All {
            self.filter = Filter::All;
            self.requery();
        } else if was_armed {
            return Effect::Quit;
        } else {
            self.esc_armed = true;
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

    /// Run one item of the leader sheet.
    ///
    /// A chord is a shortcut for a verb, never a second implementation of it:
    /// every arm here goes through the same [`update`] the keyboard reaches, so
    /// `SPC f x` and `ctrl+x` cannot drift apart.
    fn run(&mut self, act: crate::sheet::Act) -> Effect {
        match act {
            crate::sheet::Act::Enter(group) => {
                self.sheet = Some(SheetState { group: Some(group), ..SheetState::default() });
                Effect::Redraw
            }
            crate::sheet::Act::Expiring => {
                self.sheet = None;
                update(self, Msg::ToggleExpiring)
            }
            crate::sheet::Act::Scans => {
                self.sheet = None;
                update(self, Msg::ToggleScans)
            }
            crate::sheet::Act::Clear => {
                self.sheet = None;
                self.filter = Filter::All;
                self.scan_search = ScanSearch::Off;
                self.cursor = 0;
                self.offset = 0;
                self.requery();
                Effect::Redraw
            }
            crate::sheet::Act::Edit => {
                self.sheet = None;
                self.record_verb('e')
            }
            crate::sheet::Act::New => {
                self.sheet = None;
                self.open_new()
            }
            crate::sheet::Act::Undo => {
                self.sheet = None;
                self.undo()
            }
            crate::sheet::Act::Redo => {
                self.sheet = None;
                self.redo()
            }
            crate::sheet::Act::Delete => {
                self.sheet = None;
                self.delete()
            }
            crate::sheet::Act::Quit => Effect::Quit,
        }
    }

    /// Move the record's selector. The same motions the list understands, over
    /// a much shorter list, so paging is clamped rather than wrapped.
    fn move_record(&mut self, motion: Motion) {
        let Some(doc) = self.current() else { return };
        let last = crate::detail::rows(doc).len().saturating_sub(1);
        self.record_cursor = match motion {
            Motion::Up => self.record_cursor.saturating_sub(1),
            Motion::Down => (self.record_cursor + 1).min(last),
            Motion::PageUp | Motion::Home => 0,
            Motion::PageDown | Motion::End => last,
        };
    }

    /// A bare letter on the record surface.
    ///
    /// **`e` edits the row you are on**, which is why this surface needs no
    /// control keys: one verb covers every field, and the selector says which.
    /// A `ctrl+`combination could never be taught — Termux latches `CTRL` in its
    /// own UI, so the app sees only the finished keystroke and has no moment to
    /// offer what follows it.
    ///
    /// An unknown letter says so rather than doing nothing: on this surface a
    /// letter is a verb, and silence would read as a dropped keypress.
    fn record_verb(&mut self, key: char) -> Effect {
        let Some(doc) = self.current() else { return Effect::Idle };
        let rows = crate::detail::rows(doc);
        let row = rows.get(self.record_cursor.min(rows.len().saturating_sub(1))).copied();
        match (key, row) {
            ('e', Some(crate::detail::Row::Editable(field))) => self.open_edit(field),
            ('e', Some(_)) => {
                self.flash = Some("that row cannot be edited yet".into());
                Effect::Redraw
            }
            // Undo is about the session, not about the row — but it is bound
            // here because this is the surface where a bare letter is a verb,
            // and it is where a write has just been made.
            ('u', _) => self.undo(),
            ('r', _) => self.redo(),
            ('d', _) => self.delete(),
            _ => {
                self.flash = Some(format!("no verb on `{key}` here — space for the menu"));
                Effect::Redraw
            }
        }
    }

    /// The Termux IME affordance (invariant 6, DESIGN §14).
    ///
    /// Termux raises the soft keyboard on a tap only while mouse tracking is
    /// **off**, and no escape sequence can raise it directly. So this drops
    /// reporting for exactly one tap: that tap raises the keyboard, and the
    /// first key press turns reporting back on.
    ///
    /// It is reached by **tapping the search bar** — REWRITE-UI.md §5's own
    /// wording ("focusing the bar drops mouse reporting so the next tap raises
    /// the IME"), and the thing a thumb does anyway when it wants to type. It
    /// had a quarter of the action bar until the device said otherwise: Termux's
    /// own extra-keys row can carry a keyboard toggle, which makes a second
    /// button for it a waste of a quarter of the only touch chrome there is.
    ///
    /// This flips state only. Reporting is a *terminal* command, so the shell
    /// performs it by reconciling itself against [`Model::mouse_on`] after every
    /// update — one source of truth, no second mechanism to drift from it.
    fn raise_keyboard(&mut self) -> Effect {
        self.mouse_on = false;
        self.keyboard_hint = true;
        Effect::Redraw
    }

    /// Open an edit on the highlighted document's field.
    ///
    /// **The record comes with it.** REWRITE-UI.md §2 makes detail the only
    /// editing surface, so the verb shows the record as part of doing its job
    /// rather than refusing until you have opened it yourself — the same shape
    /// invariant 2 already gives `Enter`, which falls through to the record
    /// when there is no file.
    fn open_edit(&mut self, field: crate::edit::Field) -> Effect {
        if let Some(reason) = self.write.reason() {
            self.flash = Some(reason.to_string());
            return Effect::Redraw;
        }
        let Some(doc) = self.current() else {
            self.flash = Some("nothing to edit".into());
            return Effect::Redraw;
        };
        // Seeded with what is stored, so an edit starts as a correction rather
        // than a re-typing. Tags collapse to the space-separated spelling
        // `Field::validate` reads back, which is the one place the list form and
        // the typed form have to agree.
        let current = match field {
            crate::edit::Field::Name => Some(doc.name.clone()),
            crate::edit::Field::Expiry => doc.expiry_date.clone(),
            crate::edit::Field::Issued => doc.issue_date.clone(),
            crate::edit::Field::Tags => Some(doc.tags.join(" ")),
            crate::edit::Field::Notes => Some(doc.notes.clone()),
        };
        self.edit = Some(crate::edit::Edit::new(doc.id.clone(), field, current.as_deref()));
        self.sheet = None;
        self.detail = true;
        Effect::Redraw
    }

    /// Start a new document by asking for its name.
    ///
    /// **The name is the whole creation gesture**, and deliberately so: it is
    /// the one field `Field::validate` refuses to leave empty, so a document
    /// cannot be brought into existence nameless and then abandoned — which is
    /// exactly the shape a multi-field "new document form" would produce on a
    /// phone, one interruption in. Everything else is a field on the record,
    /// reached by the same `e` as every other edit.
    ///
    /// The record is *not* opened here. There is nothing to show until the
    /// journal has answered, and a record for a document that does not exist
    /// yet would be a screen full of `—` with no way to tell whether it saved.
    fn open_new(&mut self) -> Effect {
        if let Some(reason) = self.write.reason() {
            self.flash = Some(reason.to_string());
            return Effect::Redraw;
        }
        self.edit = Some(crate::edit::Edit::creating());
        self.sheet = None;
        Effect::Redraw
    }

    /// Put the last write this session made back the way it was.
    ///
    /// **An undo is an ordinary append**, never a rewrite: §3.1's whole
    /// guarantee is that a journal is append-only and single-writer, so "taking
    /// something back" is writing the op that says so. The other device sees an
    /// edit, which is exactly what happened.
    ///
    /// **An undo does not stack its own inverse**, so `u u u` walks back three
    /// writes rather than toggling the last one. Redo is therefore not free, and
    /// is not built: pressing undo twice must mean what it means everywhere.
    fn undo(&mut self) -> Effect {
        // Named for what it is rather than "nothing to undo": the stack is this
        // session's, and a user who edited yesterday is owed the reason it is
        // empty rather than the impression the key is broken.
        self.step(Direction::Undo, "nothing to undo — this session has not written yet")
    }

    /// Tombstone the record's document — on the second `d`.
    ///
    /// **Delete is a record-only verb**, because you should be able to see what
    /// you are deleting. It is also why the confirmation can be the same key:
    /// on the list, `d` is search text, and a verb that had to be confirmed
    /// differently depending on where it was invoked from would be a worse
    /// safeguard than none.
    ///
    /// §3.2's tombstone is retained forever and nothing older than it survives
    /// the fold, so this genuinely removes the document from the store. It is
    /// still reversible: the change records every field the document had (see
    /// [`crate::Doc::as_fields`]), so `u` puts it back whole rather than as a
    /// bare recreate with a name.
    ///
    /// References other documents hold to this one are deliberately left alone.
    /// §3.2 says a stale `supersedes` is harmless after a tombstone, and
    /// rewriting other documents as a side effect of deleting this one is the
    /// kind of thing an undo could not honestly reverse.
    fn delete(&mut self) -> Effect {
        if let Some(reason) = self.write.reason() {
            self.flash = Some(reason.to_string());
            return Effect::Redraw;
        }
        let Some(doc) = self.current() else {
            self.flash = Some("nothing to delete".into());
            return Effect::Redraw;
        };
        if !self.delete_armed {
            // Named, because on a 47-column screen the record above may have
            // scrolled and "delete this?" would be a question about nothing in
            // particular.
            let asking = format!("delete {:?}? press d again", doc.name);
            self.delete_armed = true;
            self.flash = Some(asking);
            return Effect::Redraw;
        }
        let id = doc.id.clone();
        // The way back is built here, while the document is still in the store:
        // after the tombstone there is nothing left to read the fields off.
        let restore: Vec<journal::Draft> = std::iter::once(journal::Draft::create("doc", &id))
            .chain(
                doc.as_fields()
                    .into_iter()
                    .map(|(field, value)| journal::Draft::set("doc", &id, field, value)),
            )
            .collect();
        self.delete_armed = false;
        self.pending =
            Some(Change { forward: vec![journal::Draft::delete("doc", &id)], back: restore });
        self.direction = Direction::Forward;
        self.pending_anchor = Some(id.clone());
        self.pending_delete = true;
        self.sheet = None;
        Effect::Append(vec![journal::Draft::delete("doc", &id)])
    }

    /// Put back the last write this session took back.
    ///
    /// **A separate verb on a separate key**, which is the whole reason
    /// [`Model::undo`] does not stack its own inverse: `u u u` has to walk back
    /// three writes, so putting one forward again needs somewhere else to live.
    /// It appends the ops that were written the first time — no re-derivation,
    /// so a redo cannot drift from the thing it is putting back.
    fn redo(&mut self) -> Effect {
        self.step(Direction::Redo, "nothing to redo — nothing has been undone")
    }

    /// The shared body of the two: pop from one stack, append, and let
    /// [`Msg::Saved`] move the change to the other once the journal agrees.
    fn step(&mut self, direction: Direction, empty: &str) -> Effect {
        if let Some(reason) = self.write.reason() {
            self.flash = Some(reason.to_string());
            return Effect::Redraw;
        }
        let stack = if direction == Direction::Undo { &mut self.undo } else { &mut self.redo };
        let Some(change) = stack.pop() else {
            self.flash = Some(empty.to_string());
            return Effect::Redraw;
        };
        let drafts =
            if direction == Direction::Undo { change.back.clone() } else { change.forward.clone() };
        // The document a step is about need not be the one under the cursor, so
        // the anchor travels with the append rather than being guessed at when
        // it lands.
        self.pending_anchor = drafts.first().map(|draft| draft.id.clone());
        self.pending = Some(change);
        self.direction = direction;
        self.sheet = None;
        Effect::Append(drafts)
    }

    /// The ops that would put this edit back the way it was.
    ///
    /// A create inverts to a tombstone; a field inverts to whatever the store
    /// holds for it right now — a `set` when it has a value, an `unset` when it
    /// does not, which is the same pair the forward write chooses between.
    fn inverse_of(&self, edit: &crate::edit::Edit) -> Vec<journal::Draft> {
        if edit.creating {
            return vec![journal::Draft::delete("doc", &edit.doc)];
        }
        let field = edit.field.journal_field();
        let was = self
            .store
            .docs
            .iter()
            .find(|doc| doc.id == edit.doc)
            .and_then(|doc| edit.field.stored(doc));
        vec![match was {
            Some(value) => journal::Draft::set("doc", &edit.doc, field, value),
            None => journal::Draft::unset("doc", &edit.doc, field),
        }]
    }

    /// The id for a document being created here and now.
    ///
    /// Every id in the store is a candidate collision, not merely the ones this
    /// device made: a name that would land on a document synced from the other
    /// device must still count up. [`crate::id::mint`] is what makes that a
    /// *local* question again — the device is already in the id, so the only
    /// ids that can be in the way are ones this device can see.
    fn mint_id(&self, name: &str) -> String {
        let taken = self.store.docs.iter().map(|doc| doc.id.as_str()).collect();
        crate::id::mint(name, self.write.device().unwrap_or_default(), &taken)
    }

    /// Take a re-folded store, keeping the user's place in it.
    ///
    /// A save can reorder the list — an expiry edit moves a row under the
    /// `expiring` filter — or push the document out of it entirely, so the row
    /// index the cursor held before the fold means nothing after it. The anchor
    /// is therefore the **document id**, and the return value says whether it
    /// survived: a detail pane still showing the document that just left the
    /// list would be showing something the list no longer contains.
    fn adopt(&mut self, store: Store, anchor: &str) -> bool {
        self.store = store;
        self.requery();
        let found = self.rows.iter().position(|&i| self.store.docs[i].id == anchor);
        if let Some(position) = found {
            self.cursor = position;
            self.scroll_into_view(self.visible_rows());
        }
        found.is_some()
    }
}

/// Apply one message. The only entry point to state change.
#[allow(clippy::too_many_lines)] // One flat table of rules reads better than five helpers.
pub fn update(model: &mut Model, msg: Msg) -> Effect {
    // A key press means the user is at the keyboard, so the IME affordance has
    // done its job: restore mouse reporting. Doing it here, once, is why the
    // drop can never become a mode you get stuck in.
    if model.keyboard_hint && is_key(&msg) {
        model.mouse_on = true;
        model.keyboard_hint = false;
    }

    // Delete arms only on a *consecutive* `d`; any other key disarms it, the
    // same rule Esc and quit follow. Worker messages are not keystrokes and so
    // do not disarm — a scan landing mid-decision must not silently make the
    // next `d` mean something different from what the screen is offering.
    if !matches!(msg, Msg::Char('d') | Msg::ScansLoaded(_) | Msg::Saved(_) | Msg::SaveFailed { .. })
        && is_key(&msg)
    {
        model.delete_armed = false;
    }

    // Esc arms only on a *consecutive* Esc; any other key disarms it.
    let was_armed = model.esc_armed;
    if !matches!(msg, Msg::Esc | Msg::ScansLoaded(_) | Msg::Saved(_) | Msg::SaveFailed { .. }) {
        model.esc_armed = false;
    }
    if is_key(&msg) {
        model.flash = None;
    }

    // An open edit owns the keyboard before anything else does — it is the
    // innermost layer, and §8's Esc chain starts there. `ctrl+q`/`ctrl+c` and
    // worker messages still fall through, so nothing is trapped.
    if model.edit.is_some() {
        if let Some(effect) = edit_key(model, &msg) {
            return effect;
        }
    }

    // While the sheet is open it owns the keyboard, because its whole purpose is
    // to be somewhere letters mean something. `Esc` still peels (see
    // `Model::peel`) and `ctrl+`-anything still fires, so nothing is trapped.
    if model.sheet.is_some() {
        if let Some(effect) = sheet_key(model, &msg) {
            return effect;
        }
    }

    match msg {
        Msg::Quit => Effect::Quit,
        Msg::Esc => model.peel(was_armed),
        Msg::EditField(field) => model.open_edit(field),
        Msg::Saved(store) => {
            // The edit is closed *here* and not when `Enter` was pressed: until
            // the journal has answered, the value on screen is a hope.
            let closed = model.edit.take();
            let created = closed.as_ref().is_some_and(|edit| edit.creating);
            let direction = std::mem::replace(&mut model.direction, Direction::Forward);
            model.delete_armed = false;
            // The write landed, so the change is real and belongs on the stack
            // that can reverse it: a write becomes something to undo, an undo
            // becomes something to redo, and a redo something to undo again.
            if let Some(change) = model.pending.take() {
                match direction {
                    Direction::Forward | Direction::Redo => model.undo.push(change),
                    Direction::Undo => model.redo.push(change),
                }
            }
            // **An ordinary write clears the redo stack.** Once history has
            // branched, the future those changes described is one the store
            // never took, and putting one back would write an old edit against a
            // document that has moved on since.
            if direction == Direction::Forward {
                model.redo.clear();
            }
            let anchor = closed
                .map(|edit| edit.doc)
                .or_else(|| model.pending_anchor.take())
                .or_else(|| model.current().map(|doc| doc.id.clone()));
            model.pending_anchor = None;
            let kept = model.adopt(*store, anchor.as_deref().unwrap_or_default());
            if direction != Direction::Forward {
                // An undo of a create leaves nothing to look at, and either
                // direction may move a row out of the filter. Neither is a
                // surprise worth a different word for.
                model.detail &= kept;
                model.flash =
                    Some(if direction == Direction::Undo { "undone" } else { "redone" }.into());
            } else if std::mem::take(&mut model.pending_delete) {
                // There is nothing left to look at, and the word for it is
                // neither "saved" nor a complaint about the filter.
                model.detail = false;
                model.flash = Some("deleted — u to undo".into());
            } else if kept && created {
                // **A new document opens on its record**, which is the only
                // place its remaining fields can be filled in — creating one and
                // being left on the list would make the next step invisible.
                model.detail = true;
                model.record_cursor = 0;
                model.flash = Some("created".into());
            } else if kept {
                model.flash = Some("saved".into());
            } else {
                // The document is no longer in the list the query and filter
                // describe, so the record above it would be showing something
                // the list does not contain.
                model.detail = false;
                model.flash = Some("saved — it no longer matches the filter".into());
            }
            Effect::Redraw
        }
        Msg::SaveFailed { reason, permanent } => {
            if let Some(edit) = &mut model.edit {
                edit.saving = false;
                edit.armed_discard = false;
            }
            // A write that never landed cannot be taken back, so the change is
            // dropped rather than left on a stack to reverse something nobody
            // did. A refused *undo* likewise stays on the undo stack — it is
            // still the last thing this session wrote.
            if let Some(change) = model.pending.take() {
                match model.direction {
                    Direction::Undo => model.undo.push(change),
                    Direction::Redo => model.redo.push(change),
                    Direction::Forward => {}
                }
            }
            model.pending_anchor = None;
            model.direction = Direction::Forward;
            model.pending_delete = false;
            model.delete_armed = false;
            model.flash = Some(reason.clone());
            // A refusal that will refuse again takes editing off the table for
            // the session, rather than inviting the same disappointment on
            // every save. The typing survives either way.
            if permanent {
                model.write = WriteState::Off(reason);
            }
            Effect::Redraw
        }
        Msg::Leader => {
            model.sheet = Some(SheetState::default());
            Effect::Redraw
        }
        Msg::Enter => model.activate(),
        Msg::OpenDetail => {
            model.detail = true;
            model.record_cursor = 0;
            Effect::Redraw
        }
        Msg::CloseDetail => {
            model.detail = false;
            Effect::Redraw
        }
        // **The record owns `↑`/`↓` while it is open.** They used to move the
        // list cursor underneath it, so the record silently became a different
        // document while you were reading it — unfollowable at 47 columns, and
        // the reason this selector exists.
        Msg::Move(motion) => {
            if model.detail {
                model.move_record(motion);
            } else {
                model.move_cursor(motion);
            }
            Effect::Redraw
        }
        Msg::Backspace => {
            if model.query.pop().is_some() {
                model.requery();
            }
            Effect::Redraw
        }
        // Find-fast (invariant 1): a bare printable starts the search and the
        // **first character is kept**. This is the whole reason the surface
        // binds no letters.
        // A query never usefully begins with a space, so `Space` on an empty
        // one is free — and mid-query it still types a space, which is what
        // multi-word searches need. That is the normal-vs-insert split without
        // modes: **the query is the mode.**
        // **Search is a browse-surface verb** (invariant 1 scopes find-fast to
        // it), so on the record a letter is free to be a verb — which is what
        // lets this surface have keys at all without reaching for `ctrl`.
        Msg::Char(' ') if model.detail => update(model, Msg::Leader),
        Msg::Char(c) if model.detail => model.record_verb(c),
        Msg::Char(' ') if model.query.is_empty() => update(model, Msg::Leader),
        Msg::Char(c) => {
            model.query.push(c);
            model.requery();
            Effect::Redraw
        }
        // `ctrl+t` on the browse surface. The modifier combination Termux's
        // keyboard variants are least reliable at delivering, which is why R0.2
        // probed it on the real device before anything depended on it.
        Msg::ToggleScans => match model.scan_search {
            ScanSearch::On | ScanSearch::Loading => {
                model.scan_search = ScanSearch::Off;
                model.requery();
                Effect::Redraw
            }
            // Already read once: turning it back on costs nothing.
            ScanSearch::Off if model.scans.is_some() => {
                model.scan_search = ScanSearch::On;
                model.requery();
                Effect::Redraw
            }
            ScanSearch::Off => {
                model.scan_search = ScanSearch::Loading;
                Effect::LoadScans
            }
        },
        Msg::ScansLoaded(scans) => {
            // A load that finished after the user changed their mind is kept,
            // not applied: the work is done, and the next `ctrl+t` is instant.
            let count = scans.len();
            model.scans = Some(scans);
            if model.scan_search == ScanSearch::Loading {
                model.scan_search = ScanSearch::On;
                model.flash = Some(if count == 0 {
                    "no scan text yet — the desktop satellite writes it".into()
                } else {
                    format!("searching inside {count} scanned files")
                });
                model.requery();
            }
            Effect::Redraw
        }
        Msg::ToggleExpiring => {
            model.filter =
                if model.filter == Filter::Expiring { Filter::All } else { Filter::Expiring };
            model.cursor = 0;
            model.offset = 0;
            model.requery();
            Effect::Redraw
        }
        Msg::RaiseKeyboard => model.raise_keyboard(),
        Msg::Resize { cols, rows } => {
            model.cols = cols;
            model.rows_on_screen = rows;
            Effect::Redraw
        }
        Msg::Scroll(delta) => {
            model.scroll(delta);
            Effect::Redraw
        }
        Msg::Tap { col, row } => {
            model.flash = None;
            // A pushed record covers the list, so the chrome under it belongs to
            // a surface you cannot see. Tapping it would mutate that surface
            // blind — the stack metaphor has to hold for touch too.
            let pushed = model.detail && !crate::layout::splits(model.cols);
            let (top, bottom) = search_zone(model);
            if model.sheet.is_some() && !model.leader_zone.hit(col, row) {
                // Anywhere else dismisses it, the way a menu should.
                model.sheet = None;
                Effect::Redraw
            } else if model.leader_zone.hit(col, row) {
                if model.sheet.is_some() {
                    model.sheet = None;
                    Effect::Redraw
                } else {
                    update(model, Msg::Leader)
                }
            } else if model.count_zone.hit(col, row) && !pushed {
                // You tap the number that told you there were three. The count
                // names its command (REWRITE-UI §1), and it toggles rather than
                // jumps so a second tap peels it off — the same verb `ctrl+x`
                // has, reached the way a thumb reaches things.
                update(model, Msg::ToggleExpiring)
            } else if row >= top && row <= bottom {
                if pushed {
                    Effect::Idle
                } else {
                    // Tapping the field is how every phone app says "I want to
                    // type", so it is what drops mouse reporting for one tap.
                    model.raise_keyboard()
                }
            } else if let Some(index) = model.row_at(row) {
                // Tap selects; a tap on the already-selected row opens
                // (invariant 6). Two taps, never a double-tap timer — timing
                // gestures are miserable on a laggy terminal.
                if index == model.cursor {
                    model.activate()
                } else {
                    model.cursor = index;
                    Effect::Redraw
                }
            } else {
                Effect::Idle
            }
        }
    }
}

/// Keys while a field is being edited. `None` falls through, which is how
/// `ctrl+q`/`ctrl+c` and worker messages still work from inside an edit.
///
/// The rules, and each one is somebody's requirement:
///
/// * **Every printable is the value**, not search text. An edit is the one place
///   on this app's surfaces where that is true, and it is why detail can bind
///   letters at all (REWRITE-UI.md §2) while Find never may.
/// * **`Enter` saves and `Esc` discards** — explicit save, and a *dirty* edit
///   takes two `Esc`s (§2). The arming is a real layer, so invariant 3's "one
///   layer per press" holds: an edit with typing in it is one press further from
///   the base state than an untouched one.
/// * **Arrows are swallowed.** They would otherwise move the list cursor under
///   the record, and the record you are editing would stop being the record you
///   are looking at.
/// * **Taps and scrolls are inert**, the same rule a pushed record already
///   follows: you cannot act on a surface the current one is covering.
fn edit_key(model: &mut Model, msg: &Msg) -> Option<Effect> {
    // rust: the edit is cloned out, worked on, and put back, exactly as
    // `sheet_key` does with the sheet. Holding `model.edit.as_mut()` across a
    // write to `model.flash` would be two mutable borrows of one struct, and the
    // borrow checker is right to refuse: a message can change both. The clone is
    // two short strings.
    let mut edit = model.edit.clone()?;

    // "Any other key disarms" — the same rule the quit arming follows, applied
    // to the discard so the two behave identically.
    if !matches!(msg, Msg::Esc) {
        edit.armed_discard = false;
    }

    let effect = match msg {
        Msg::Char(c) => {
            edit.buffer.push(*c);
            Effect::Redraw
        }
        Msg::Backspace => {
            edit.buffer.pop();
            Effect::Redraw
        }
        Msg::Enter => {
            // A second `Enter` while the first is still in flight would append
            // the same op twice. The journal would survive it — LWW on identical
            // values is a no-op — but the history would carry a lie about what
            // the user did.
            if edit.saving {
                Effect::Idle
            } else {
                match edit.field.validate(&edit.buffer) {
                    Ok(value) => {
                        // **The id is minted here, not when the edit opened**,
                        // because it is made from the name and the name is what
                        // was being typed. Writing it back into `edit.doc` is
                        // what lets the save path below — and `Msg::Saved`,
                        // which anchors on it — stay ignorant of the difference
                        // between creating and editing.
                        if edit.creating {
                            edit.doc = model.mint_id(edit.buffer.trim());
                        }
                        // **The inverse is computed now, from the store as it
                        // stands**, because "what was there before" is knowable
                        // here and only by re-folding history afterwards. It is
                        // held aside until the journal confirms the write.
                        model.pending = None; // filled in below, once `drafts` exists
                        let field = edit.field.journal_field();
                        let write = match value {
                            Some(value) => journal::Draft::set("doc", &edit.doc, field, value),
                            // An empty buffer clears the field: `unset`, never a
                            // stored empty string (see
                            // [`crate::edit::Field::validate`]).
                            None => journal::Draft::unset("doc", &edit.doc, field),
                        };
                        // `create` first, and in the *same* append: §3.2's fold
                        // orphans a `set` on an entity that is not alive yet, so
                        // a name that arrived before its create would be
                        // silently dropped. One batch, one writer, so the two
                        // ops cannot be separated by anything.
                        let drafts = if edit.creating {
                            vec![journal::Draft::create("doc", &edit.doc), write]
                        } else {
                            vec![write]
                        };
                        // **The change is recorded now, from the store as it
                        // stands**, because "what was there before" is knowable
                        // here and only by re-folding history afterwards. It is
                        // held aside until the journal confirms the write.
                        model.pending =
                            Some(Change { forward: drafts.clone(), back: model.inverse_of(&edit) });
                        model.direction = Direction::Forward;
                        edit.saving = true;
                        Effect::Append(drafts)
                    }
                    Err(complaint) => {
                        // The typing is never destroyed by a refusal — it is the
                        // thing that needs correcting.
                        model.flash = Some(complaint);
                        Effect::Redraw
                    }
                }
            }
        }
        Msg::Esc => {
            if edit.saving {
                // Cancelling an append already on its way would leave the screen
                // and the journal disagreeing about what happened.
                model.flash = Some("saving — one moment".into());
                Effect::Redraw
            } else if edit.dirty() && !edit.armed_discard {
                edit.armed_discard = true;
                Effect::Redraw
            } else {
                model.edit = None;
                return Some(Effect::Redraw);
            }
        }
        // Swallowed: they belong to the list, and the list is not what is being
        // edited. `Home`/`End` come back as text motions with §5b's query
        // cursor, which is when this arm gets something to do.
        // Pressing the verb again from inside its own editor must not reseed
        // the buffer — that would throw away typing with a key that reads like
        // it should do nothing.
        Msg::EditField(_)
        | Msg::Move(_)
        | Msg::OpenDetail
        | Msg::CloseDetail
        | Msg::Leader
        | Msg::Tap { .. }
        | Msg::Scroll(_) => Effect::Idle,
        _ => return None,
    };
    model.edit = Some(edit);
    Some(effect)
}

/// Keys while the leader sheet is open. `None` means "the sheet does not want
/// this one" — it falls through to the surface underneath.
///
/// The rule that makes typing and chording coexist: **a printable runs an item
/// while nothing has been typed yet; after that every printable is filter
/// text.** So `SPC f x` is three keys, and `SPC exp` then `Enter` is a search —
/// and neither can be mistaken for the other halfway through.
fn sheet_key(model: &mut Model, msg: &Msg) -> Option<Effect> {
    let sheet = model.sheet.clone()?;
    let all = crate::sheet::items(sheet.group, model);
    let hits = crate::sheet::matching(&all, &sheet.filter);
    match msg {
        Msg::Char(c) => {
            if sheet.filter.is_empty() {
                if let Some(item) = all.iter().find(|item| item.key == *c) {
                    return Some(model.run(item.act));
                }
            }
            let state = model.sheet.as_mut()?;
            state.filter.push(*c);
            state.cursor = 0;
            Some(Effect::Redraw)
        }
        Msg::Backspace => {
            let state = model.sheet.as_mut()?;
            if state.filter.pop().is_none() {
                model.sheet = None;
            }
            Some(Effect::Redraw)
        }
        Msg::Enter => hits.get(sheet.cursor).map(|item| model.run(item.act)),
        Msg::Move(Motion::Up | Motion::Down) => {
            let last = hits.len().saturating_sub(1);
            let state = model.sheet.as_mut()?;
            state.cursor = match msg {
                Msg::Move(Motion::Up) => state.cursor.saturating_sub(1),
                _ => (state.cursor + 1).min(last),
            };
            Some(Effect::Redraw)
        }
        // `←` closes the sheet, matching what it does to a record.
        Msg::CloseDetail => {
            model.sheet = None;
            Some(Effect::Redraw)
        }
        _ => None,
    }
}

/// Whether this message came from the keyboard.
///
/// A result posted back by a worker is not a keystroke, however it arrives — so
/// a save landing must not disarm a pending quit or restore mouse reporting the
/// IME affordance dropped, any more than a finished scan load does.
fn is_key(msg: &Msg) -> bool {
    !matches!(
        msg,
        Msg::Tap { .. }
            | Msg::Scroll(_)
            | Msg::Resize { .. }
            | Msg::ScansLoaded(_)
            | Msg::Saved(_)
            | Msg::SaveFailed { .. }
    )
}

/// The rows the search bar occupies, inclusive — and therefore the rows that
/// raise the keyboard when tapped.
///
/// **Two rows on a touch layout**, sitting against the bottom edge of the
/// screen. One terminal row is too small a target for a thumb, and against the
/// screen edge an overshoot downwards hits nothing at all. Upwards it now finds
/// the list's last row rather than a button that opened files — which is the
/// quiet win from deleting the action bar: a stray tap moves the selection, and
/// opening needs the row to be selected already.
fn search_zone(model: &Model) -> (u16, u16) {
    let last = model.rows_on_screen.saturating_sub(1);
    if crate::layout::touch_layout(model.cols) {
        (last.saturating_sub(1), last)
    } else {
        // The entry line is the final row on a keyboard layout too — Emacs's
        // minibuffer and Vim's `:` both live there, with the status line above.
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
            tags: Vec::new(),
            bundles: Vec::new(),
            issue_date: None,
            expiry_date: expiry.map(str::to_string),
            ignore_expiry: false,
            supersedes: None,
            location: Some("cert-file".into()),
            slot: None,
            subslot: None,
            files: file
                .map(|path| {
                    vec![FileRef { label: "complete".into(), path: path.into(), primary: true }]
                })
                .unwrap_or_default(),
            notes: String::new(),
            superseded: false,
            haystack: crate::search::fold(name),
        }
    }

    pub(crate) fn model() -> Model {
        let store = Store {
            docs: vec![
                doc("coc", "COC Certificate", Some("2026-01-01"), Some("Marine/coc.pdf")),
                doc("eng1", "ENG-1 Medical", Some("2027-01-13"), Some("Marine/eng1.pdf")),
                doc("passport", "Passport (IN)", Some("2031-05-31"), None),
                doc("testimonial", "Sea Service Testimonial", None, None),
            ],
            ..Store::default()
        };
        Model::new(store, "2026-08-16".into(), "2026-11-14".into(), 45, 28)
    }

    /// A model that is allowed to write, which is not the default — the default
    /// is the safe state, because a `Model` nobody has told about a device has
    /// no writer id to append under.
    fn writable() -> Model {
        let mut model = model();
        model.write = WriteState::Ready { device: "desk".into() };
        model
    }

    /// The store as it would fold after `coc`'s expiry became `2027-04-01` —
    /// what the writer thread posts back, built the same way it builds it.
    fn restored(from: &Model, id: &str, expiry: Option<&str>) -> Store {
        let mut store = from.store.clone();
        for doc in &mut store.docs {
            if doc.id == id {
                doc.expiry_date = expiry.map(str::to_string);
            }
        }
        store
    }

    /// **`ctrl+e` opens the editor on the record, seeded with what is stored.**
    /// The record comes with it: detail is the only editing surface, so the verb
    /// shows it rather than refusing until you have opened it yourself.
    #[test]
    fn the_edit_verb_opens_the_record_and_seeds_the_field() {
        let mut m = writable();
        assert!(!m.detail);
        assert_eq!(update(&mut m, Msg::EditField(crate::edit::Field::Expiry)), Effect::Redraw);
        assert!(m.detail, "the record came with it");
        let edit = m.edit.as_ref().expect("an edit is open");
        assert_eq!(edit.doc, "coc");
        assert_eq!(edit.buffer, "2026-01-01", "seeded with the stored value");
        assert!(!edit.dirty());
    }

    /// **A session that cannot write never opens an editor**, and says why
    /// instead — REWRITE.md §3.1's "read-only with a visible notice".
    #[test]
    fn a_read_only_session_explains_itself_instead_of_editing() {
        let mut m = model();
        assert_eq!(m.write, WriteState::default(), "no device, no writing");
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        assert!(m.edit.is_none());
        assert!(m.flash.unwrap().contains("ds init"), "and it names the fix");
    }

    /// **Typing goes into the field, not into the query.** An edit is the one
    /// place on these surfaces where a printable is not search text.
    #[test]
    fn typing_in_an_edit_never_reaches_the_query() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        for _ in 0..10 {
            update(&mut m, Msg::Backspace);
        }
        for c in "2027-04-01".chars() {
            update(&mut m, Msg::Char(c));
        }
        assert_eq!(m.edit.as_ref().unwrap().buffer, "2027-04-01");
        assert!(m.query.is_empty(), "the query was never touched");
    }

    /// **A valid date becomes a `set` op** — and nothing changes on screen until
    /// the journal has answered, because until then the new value is a hope.
    #[test]
    fn saving_a_date_appends_a_set_op_and_waits_for_it() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        for _ in 0..10 {
            update(&mut m, Msg::Backspace);
        }
        for c in "2027-04-01".chars() {
            update(&mut m, Msg::Char(c));
        }
        let effect = update(&mut m, Msg::Enter);
        assert_eq!(
            effect,
            Effect::Append(vec![journal::Draft::set("doc", "coc", "expiry_date", "2027-04-01")])
        );
        assert!(m.edit.as_ref().unwrap().saving, "still open, still unsaved");
        assert_eq!(m.store.docs[0].expiry_date.as_deref(), Some("2026-01-01"), "unchanged so far");

        let store = restored(&m, "coc", Some("2027-04-01"));
        update(&mut m, Msg::Saved(Box::new(store)));
        assert!(m.edit.is_none(), "the journal answered, so the editor closed");
        assert_eq!(m.current().unwrap().expiry_date.as_deref(), Some("2027-04-01"));
        assert_eq!(m.flash.as_deref(), Some("saved"));
    }

    /// **Every simple field goes through the one verb**, seeded with what is
    /// stored — an edit starts as a correction, not a re-typing.
    #[test]
    fn each_editable_field_opens_on_its_stored_value() {
        for (field, expected) in [
            (crate::edit::Field::Name, "COC Certificate"),
            (crate::edit::Field::Expiry, "2026-01-01"),
            (crate::edit::Field::Issued, ""),
            (crate::edit::Field::Tags, ""),
            (crate::edit::Field::Notes, ""),
        ] {
            let mut m = writable();
            update(&mut m, Msg::EditField(field));
            let edit = m.edit.as_ref().expect("the editor opened");
            assert_eq!(edit.buffer, expected, "{field:?} seeds from the store");
            assert!(!edit.dirty(), "and opening is not itself an edit");
        }
    }

    /// **Tags are typed as words and stored as a list.** The space-separated
    /// spelling is the only form a text buffer can offer; a stored `"a b"` would
    /// be one tag with a space in it, which nothing would ever match.
    #[test]
    fn tags_are_typed_with_spaces_and_stored_as_a_list() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Tags));
        for c in "marine  ticket".chars() {
            update(&mut m, Msg::Char(c));
        }
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

    /// **A name may not be emptied.** Every other field clears to an `unset`;
    /// a document called nothing cannot be found, listed or talked about, so the
    /// refusal is the only one `validate` makes on content rather than form.
    #[test]
    fn a_name_cannot_be_cleared_but_the_others_can() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Name));
        for _ in 0.."COC Certificate".len() {
            update(&mut m, Msg::Backspace);
        }
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw, "nothing was appended");
        assert!(m.flash.is_some(), "and it said why");
        assert!(m.edit.is_some(), "with the editor still open on the empty buffer");

        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Notes));
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![journal::Draft::unset("doc", "coc", "notes")])
        );
    }

    /// **Creating a document is `create` then `set name`, in one append.**
    /// §3.2's fold orphans a `set` on an entity that is not alive yet, so a name
    /// arriving before its create would be silently dropped — and the two ops
    /// cannot be separated by anything if they are one batch from one writer.
    #[test]
    fn creating_a_document_appends_the_create_before_the_name() {
        let mut m = writable();
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('n'));
        assert!(m.edit.as_ref().is_some_and(|edit| edit.creating), "the name is being asked for");
        assert!(!m.detail, "and the record is not opened on a document that does not exist");

        for c in "Seaman Book".chars() {
            update(&mut m, Msg::Char(c));
        }
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![
                journal::Draft::create("doc", "seaman-book-desk"),
                journal::Draft::set("doc", "seaman-book-desk", "name", "Seaman Book"),
            ])
        );
    }

    /// **The id is minted from the store the user can see** — a name that would
    /// land on a document synced from the other device still has to count up,
    /// even though the device half means it could only be one of this device's.
    #[test]
    fn a_new_id_avoids_every_id_already_in_the_store() {
        let mut m = writable();
        m.store.docs[0].id = "passport-desk".into();
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('n'));
        for c in "Passport".chars() {
            update(&mut m, Msg::Char(c));
        }
        let Effect::Append(drafts) = update(&mut m, Msg::Enter) else { panic!("no append") };
        assert_eq!(drafts[0], journal::Draft::create("doc", "passport-desk-2"));
    }

    /// **A new document lands on its record**, which is the only place the rest
    /// of its fields can be filled in. Being dropped back on the list would make
    /// the next step invisible.
    #[test]
    fn a_created_document_opens_on_its_record() {
        let mut m = writable();
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('n'));
        for c in "Seaman Book".chars() {
            update(&mut m, Msg::Char(c));
        }
        update(&mut m, Msg::Enter);

        // What the writer thread posts back once the ops have landed.
        let mut store = m.store.clone();
        let mut fresh = store.docs[0].clone();
        fresh.id = "seaman-book-desk".into();
        fresh.name = "Seaman Book".into();
        fresh.expiry_date = None;
        fresh.files.clear();
        fresh.haystack = crate::search::fold(&fresh.name);
        store.docs.push(fresh);
        update(&mut m, Msg::Saved(Box::new(store)));

        assert!(m.edit.is_none(), "the journal answered, so the editor closed");
        assert!(m.detail, "and the record is open");
        assert_eq!(m.record_cursor, 0, "on its first row");
        assert_eq!(m.current().map(|doc| doc.id.as_str()), Some("seaman-book-desk"));
        assert_eq!(m.flash.as_deref(), Some("created"));
    }

    /// A session that cannot write cannot create either, and says the same thing
    /// it says about editing rather than doing nothing.
    #[test]
    fn creating_is_refused_with_a_reason_when_the_session_cannot_write() {
        let mut m = model();
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('n'));
        assert!(m.edit.is_none());
        assert!(m.flash.is_some());
    }

    /// **A write that never landed cannot be undone.** The inverse is computed
    /// when the append is asked for, but it only becomes undoable when the
    /// journal confirms it — otherwise a refused save would leave a stack entry
    /// that puts back something nobody ever changed.
    #[test]
    fn a_refused_save_leaves_nothing_to_undo() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        for c in "-x".chars() {
            update(&mut m, Msg::Char(c));
        }
        update(&mut m, Msg::Backspace);
        update(&mut m, Msg::Backspace);
        for c in "2027-04-01".chars() {
            update(&mut m, Msg::Char(c));
        }
        update(&mut m, Msg::Enter);
        update(&mut m, Msg::SaveFailed { reason: "the disk said no".into(), permanent: false });
        assert!(m.undo.is_empty(), "nothing was written, so there is nothing to take back");
    }

    /// **The inverse of an edit is the value the store holds, not the buffer.**
    /// Tags are typed as words and stored as a list; an inverse built from the
    /// typing would restore a string where a list had been.
    #[test]
    fn the_inverse_of_a_tag_edit_restores_the_list() {
        let mut m = writable();
        m.store.docs[0].tags = vec!["marine".into(), "ticket".into()];
        update(&mut m, Msg::EditField(crate::edit::Field::Tags));
        for c in " extra".chars() {
            update(&mut m, Msg::Char(c));
        }
        update(&mut m, Msg::Enter);
        let store = m.store.clone();
        update(&mut m, Msg::Saved(Box::new(store)));

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

    /// **Creating inverts to a tombstone**, which is the only op that can
    /// un-create anything: §3.2 keeps a `create` forever, so the way back is to
    /// write the delete rather than to pretend the create never happened.
    #[test]
    fn creating_a_document_inverts_to_a_delete() {
        let mut m = writable();
        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('n'));
        for c in "Seaman Book".chars() {
            update(&mut m, Msg::Char(c));
        }
        update(&mut m, Msg::Enter);
        let store = m.store.clone();
        update(&mut m, Msg::Saved(Box::new(store)));
        assert_eq!(
            m.undo.last().map(|change| change.back.clone()),
            Some(vec![journal::Draft::delete("doc", "seaman-book-desk")])
        );
    }

    /// An undo does not stack its own inverse — `u u u` walks back three writes
    /// rather than toggling the last one. Putting one forward again is `r`, a
    /// separate verb on a separate key, which is what lets this one mean what it
    /// means everywhere.
    #[test]
    fn an_undo_does_not_become_something_to_undo() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        update(&mut m, Msg::Backspace);
        update(&mut m, Msg::Char('2'));
        update(&mut m, Msg::Enter);
        let store = m.store.clone();
        update(&mut m, Msg::Saved(Box::new(store)));
        assert_eq!(m.undo.len(), 1);

        update(&mut m, Msg::Char(' '));
        update(&mut m, Msg::Char('u'));
        let store = m.store.clone();
        update(&mut m, Msg::Saved(Box::new(store)));
        assert!(m.undo.is_empty(), "the undo consumed the entry and added none");
        assert_eq!(m.flash.as_deref(), Some("undone"));
    }

    /// **Redo puts back the very ops that were written**, rather than deriving
    /// them again — so it cannot drift from the thing it is putting back.
    #[test]
    fn redo_appends_the_original_ops() {
        let mut m = saved_edit("2027-04-01");
        let forward = m.undo.last().expect("something to undo").forward.clone();

        update(&mut m, Msg::Char('u'));
        let store = m.store.clone();
        update(&mut m, Msg::Saved(Box::new(store)));
        assert!(m.undo.is_empty(), "the change left the undo stack");
        assert_eq!(m.redo.len(), 1, "and joined the redo stack");

        assert_eq!(update(&mut m, Msg::Char('r')), Effect::Append(forward));
        let store = m.store.clone();
        update(&mut m, Msg::Saved(Box::new(store)));
        assert_eq!(m.flash.as_deref(), Some("redone"));
        assert!(m.redo.is_empty(), "and it went back where it came from");
        assert_eq!(m.undo.len(), 1, "so it can be undone again");
    }

    /// **An ordinary write clears the redo stack.** Once history has branched,
    /// the future those changes described is one the store never took, and
    /// putting one back would write an old edit over a document that moved on.
    #[test]
    fn writing_something_new_drops_what_could_have_been_redone() {
        let mut m = saved_edit("2027-04-01");
        update(&mut m, Msg::Char('u'));
        let store = m.store.clone();
        update(&mut m, Msg::Saved(Box::new(store)));
        assert_eq!(m.redo.len(), 1);

        update(&mut m, Msg::EditField(crate::edit::Field::Notes));
        for c in "elsewhere".chars() {
            update(&mut m, Msg::Char(c));
        }
        update(&mut m, Msg::Enter);
        let store = m.store.clone();
        update(&mut m, Msg::Saved(Box::new(store)));
        assert!(m.redo.is_empty(), "the branch that was not taken is gone");
        assert_eq!(m.undo.len(), 1, "and the new write is the thing to take back");
    }

    /// Redo says why it has nothing to do, rather than doing nothing.
    #[test]
    fn redo_with_nothing_undone_says_so() {
        let mut m = writable();
        update(&mut m, Msg::OpenDetail);
        assert_eq!(update(&mut m, Msg::Char('r')), Effect::Redraw);
        assert!(m.flash.as_deref().is_some_and(|say| say.contains("nothing to redo")));
    }

    /// A model with one confirmed edit behind it, on the record.
    fn saved_edit(value: &str) -> Model {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        for _ in 0..10 {
            update(&mut m, Msg::Backspace);
        }
        for c in value.chars() {
            update(&mut m, Msg::Char(c));
        }
        update(&mut m, Msg::Enter);
        let store = m.store.clone();
        update(&mut m, Msg::Saved(Box::new(store)));
        m
    }

    /// **The first `d` asks, the second does it.** The same arming idiom `Esc`
    /// and quit already use — on a phone the thumb that meant `e` is one row
    /// from the key that means this.
    #[test]
    fn delete_takes_two_presses_and_names_what_it_would_remove() {
        let mut m = writable();
        update(&mut m, Msg::OpenDetail);

        assert_eq!(update(&mut m, Msg::Char('d')), Effect::Redraw, "the first press only asks");
        assert!(m.delete_armed);
        let asking = m.flash.clone().expect("it asked");
        assert!(asking.contains("COC Certificate"), "and named the document: {asking:?}");

        assert_eq!(
            update(&mut m, Msg::Char('d')),
            Effect::Append(vec![journal::Draft::delete("doc", "coc")]),
            "the second press writes the tombstone"
        );
        assert!(!m.delete_armed);
    }

    /// Any other key disarms it, so a `d` left hanging from a moment ago cannot
    /// be completed by a keystroke meant for something else.
    #[test]
    fn any_other_key_disarms_a_pending_delete() {
        let mut m = writable();
        update(&mut m, Msg::OpenDetail);
        update(&mut m, Msg::Char('d'));
        assert!(m.delete_armed);

        update(&mut m, Msg::Move(Motion::Down));
        assert!(!m.delete_armed, "moving the selector is not consent");
        assert_eq!(update(&mut m, Msg::Char('d')), Effect::Redraw, "so this asks again");
    }

    /// **Undoing a delete restores the document whole**, not as a bare recreate
    /// with a name: §3.2's create-after-tombstone starts from empty, so the way
    /// back has to re-send every field the document had.
    #[test]
    fn deleting_inverts_to_a_create_with_every_field() {
        let mut m = writable();
        m.store.docs[0].tags = vec!["marine".into()];
        m.store.docs[0].notes = "the one with the stamp".into();
        update(&mut m, Msg::OpenDetail);
        let expected = m.current().expect("a document").as_fields().len();

        update(&mut m, Msg::Char('d'));
        update(&mut m, Msg::Char('d'));
        let mut store = m.store.clone();
        store.docs.retain(|doc| doc.id != "coc");
        update(&mut m, Msg::Saved(Box::new(store)));
        assert_eq!(m.flash.as_deref(), Some("deleted — u to undo"));
        assert!(!m.detail, "there is nothing left to look at");

        let back = m.undo.last().expect("something to undo").back.clone();
        assert_eq!(back.first(), Some(&journal::Draft::create("doc", "coc")));
        assert_eq!(back.len(), expected + 1, "the create, then every field it had");
        assert!(
            back.contains(&journal::Draft::set("doc", "coc", "notes", "the one with the stamp")),
            "including the ones no other verb touches: {back:?}"
        );
    }

    /// A session that cannot write cannot delete either, and never arms.
    #[test]
    fn delete_is_refused_with_a_reason_when_the_session_cannot_write() {
        let mut m = model();
        update(&mut m, Msg::OpenDetail);
        update(&mut m, Msg::Char('d'));
        assert!(!m.delete_armed, "it did not even arm");
        assert!(m.flash.is_some());
    }

    /// A session that cannot write says so rather than doing nothing, the same
    /// way editing and creating do.
    #[test]
    fn undo_is_refused_with_a_reason_when_the_session_cannot_write() {
        let mut m = model();
        update(&mut m, Msg::OpenDetail);
        update(&mut m, Msg::Char('u'));
        assert!(m.flash.is_some());
    }

    /// **An empty buffer clears the field with an `unset`**, never a stored
    /// empty string — so one field exercises both halves of §3.2's contract.
    #[test]
    fn clearing_the_field_appends_an_unset_op() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        for _ in 0..10 {
            update(&mut m, Msg::Backspace);
        }
        assert_eq!(
            update(&mut m, Msg::Enter),
            Effect::Append(vec![journal::Draft::unset("doc", "coc", "expiry_date")])
        );
    }

    /// **A refusal never destroys the typing.** The buffer is the thing that
    /// needs correcting, so it stays exactly as it was.
    #[test]
    fn an_unparseable_date_is_refused_and_the_typing_survives() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        for c in "-ish".chars() {
            update(&mut m, Msg::Char(c));
        }
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw, "no append");
        assert_eq!(m.edit.as_ref().unwrap().buffer, "2026-01-01-ish");
        assert!(m.flash.as_deref().unwrap().contains("YYYY-MM-DD"));
    }

    /// **`Esc` closes a clean edit in one press and arms before discarding a
    /// dirty one** (REWRITE-UI.md §2), and any other key disarms — the same
    /// rule the quit arming follows, so the two cannot behave differently.
    #[test]
    fn esc_discards_an_edit_in_one_press_when_clean_and_two_when_dirty() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        update(&mut m, Msg::Esc);
        assert!(m.edit.is_none(), "nothing was typed, so nothing needed confirming");

        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        update(&mut m, Msg::Char('9'));
        update(&mut m, Msg::Esc);
        assert!(m.edit.as_ref().unwrap().armed_discard, "armed, not discarded");
        update(&mut m, Msg::Esc);
        assert!(m.edit.is_none(), "the second press threw it away");

        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        update(&mut m, Msg::Char('9'));
        update(&mut m, Msg::Esc);
        update(&mut m, Msg::Char('9'));
        assert!(!m.edit.as_ref().unwrap().armed_discard, "any other key disarms");
    }

    /// **Arrows inside an edit never move the list underneath.** The record you
    /// are editing has to stay the record you are looking at.
    #[test]
    fn the_list_does_not_move_under_an_open_edit() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        let before = m.cursor;
        for motion in [Motion::Down, Motion::PageDown, Motion::End, Motion::Up] {
            assert_eq!(update(&mut m, Msg::Move(motion)), Effect::Idle);
        }
        assert_eq!(update(&mut m, Msg::Tap { col: 2, row: 5 }), Effect::Idle);
        assert_eq!(m.cursor, before);
        assert_eq!(m.edit.as_ref().unwrap().doc, "coc", "and it is still the same document");
    }

    /// **`ctrl+q` still quits from inside an edit.** Nothing may trap the user
    /// on a surface, however modal it is.
    #[test]
    fn quitting_works_from_inside_an_edit() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        assert_eq!(update(&mut m, Msg::Quit), Effect::Quit);
    }

    /// **The cursor is re-anchored by document id, not by row.** A save re-folds
    /// the store, and under the expiring filter a changed date moves the row —
    /// so a remembered index would be pointing at somebody else.
    #[test]
    fn a_save_keeps_the_cursor_on_the_document_it_edited() {
        let mut m = writable();
        update(&mut m, Msg::ToggleExpiring);
        update(&mut m, Msg::Move(Motion::Down));
        let edited = m.current().unwrap().id.clone();
        assert_eq!(edited, "eng1", "second-soonest under the filter");

        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        update(&mut m, Msg::Enter);
        // Now the soonest of all — earlier than `coc`'s 2026-01-01 — so the row
        // moves to the top of the filter, which is the whole point of the test.
        let store = restored(&m, &edited, Some("2025-12-01"));
        update(&mut m, Msg::Saved(Box::new(store)));
        assert_eq!(m.current().unwrap().id, edited, "the cursor followed the document");
        assert_eq!(m.cursor, 0, "which is now the first row");
    }

    /// **A save that pushes the document out of the list says so, and closes the
    /// record.** A record above a list that no longer contains it is a lie about
    /// what is on screen.
    #[test]
    fn a_save_that_leaves_the_filter_closes_the_record_and_says_so() {
        let mut m = writable();
        update(&mut m, Msg::ToggleExpiring);
        let edited = m.current().unwrap().id.clone();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        assert!(m.detail);

        // Cleared: no expiry means it is not in the watch at all.
        let store = restored(&m, &edited, None);
        update(&mut m, Msg::Saved(Box::new(store)));
        assert!(!m.detail, "the record closed");
        assert!(m.flash.as_deref().unwrap().contains("no longer matches"));
        assert!(m.rows.iter().all(|&i| m.store.docs[i].id != edited));
    }

    /// **A failed save keeps the editor and the typing**, and a failure that
    /// will recur takes editing off the table rather than inviting it again.
    #[test]
    fn a_failed_save_keeps_the_typing_and_a_permanent_one_stops_offering() {
        let mut m = writable();
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
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
        update(&mut m, Msg::EditField(crate::edit::Field::Expiry));
        assert!(m.edit.is_none(), "it does not offer again");
    }

    /// **A save landing is not a keystroke.** Like a finished scan load, it must
    /// not disarm a pending quit or undo the IME affordance's dropped mouse
    /// reporting — the user did not touch the keyboard.
    #[test]
    fn a_save_result_is_not_a_keypress() {
        let mut m = writable();
        // Arm first: `Esc` is a real keystroke, so it would restore the mouse
        // reporting the affordance drops — the drop has to come after it.
        update(&mut m, Msg::Esc);
        assert!(m.esc_armed);
        m.raise_keyboard();
        assert!(!m.mouse_on);

        let store = restored(&m, "coc", Some("2027-04-01"));
        update(&mut m, Msg::Saved(Box::new(store)));
        assert!(m.esc_armed, "a worker message did not disarm the quit");
        assert!(!m.mouse_on, "nor did it restore mouse reporting");
    }

    /// **Find-fast, invariant 1.** A bare printable is search text — the first
    /// character included. Nothing on this surface may swallow a letter.
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

    /// **Cold start → type → `Enter` → open, in five keystrokes** (the R-UI
    /// acceptance check). Four here, and the fifth is spare.
    #[test]
    fn four_keystrokes_open_a_file_from_a_cold_start() {
        let mut m = model();
        let keys = [Msg::Char('e'), Msg::Char('n'), Msg::Char('g'), Msg::Enter];
        let mut opened = None;
        for key in keys {
            if let Effect::Open(path) = update(&mut m, key) {
                opened = Some(path);
            }
        }
        assert_eq!(opened.as_deref(), Some("Marine/eng1.pdf"));
    }

    /// **`Enter` never dies** (invariant 2). No file linked is not an error; it
    /// is a reason to show the record.
    #[test]
    fn enter_falls_through_to_the_record_when_there_is_no_file() {
        let mut m = model();
        for c in "passport".chars() {
            update(&mut m, Msg::Char(c));
        }
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw, "no open effect, and no panic");
        assert!(m.detail, "it fell through to the record");
        assert!(m.flash.unwrap().contains("no file linked"));
    }

    /// **Esc peels exactly one layer per press** (invariant 3), in the order
    /// REWRITE-UI.md §8 fixes: search, surface, filter, arm, quit.
    #[test]
    fn esc_peels_one_layer_at_a_time_and_quits_only_at_the_end() {
        let mut m = model();
        update(&mut m, Msg::Char('c'));
        update(&mut m, Msg::OpenDetail);

        assert_eq!(update(&mut m, Msg::Esc), Effect::Redraw);
        assert!(m.query.is_empty(), "first press cleared the search");
        assert!(m.detail, "and nothing else");

        assert_eq!(update(&mut m, Msg::Esc), Effect::Redraw);
        assert!(!m.detail, "second press closed the record");
        assert!(!m.esc_armed, "closing something is not arming");

        assert_eq!(update(&mut m, Msg::Esc), Effect::Redraw);
        assert!(m.esc_armed, "at base state it arms");

        assert_eq!(update(&mut m, Msg::Esc), Effect::Quit);
    }

    /// **An IME dismissal must never quit the app.** Termux sends `Esc` to close
    /// the soft keyboard; any other key in between disarms, so a stray press
    /// cannot compound into an exit.
    #[test]
    fn any_other_key_disarms_the_quit() {
        let mut m = model();
        update(&mut m, Msg::Esc);
        assert!(m.esc_armed);
        update(&mut m, Msg::Move(Motion::Down));
        assert!(!m.esc_armed);
        assert_eq!(update(&mut m, Msg::Esc), Effect::Redraw, "arms again rather than quitting");
    }

    /// **Tap selects; a tap on the selected row opens** (invariant 6) — two
    /// taps, no double-tap timer.
    #[test]
    fn tap_then_tap_opens() {
        let mut m = model();
        m.list = ListGeometry { top: 1, height: 24, row_height: 2 };
        // Row 1 of the list is the second document (two screen lines each).
        assert_eq!(update(&mut m, Msg::Tap { col: 5, row: 3 }), Effect::Redraw);
        assert_eq!(m.cursor, 1);
        assert_eq!(
            update(&mut m, Msg::Tap { col: 5, row: 3 }),
            Effect::Open("Marine/eng1.pdf".into())
        );
    }

    /// A tap on empty space below the last row changes nothing at all — and
    /// costs no repaint.
    #[test]
    fn a_tap_on_nothing_is_idle() {
        let mut m = model();
        m.list = ListGeometry { top: 1, height: 24, row_height: 2 };
        assert_eq!(update(&mut m, Msg::Tap { col: 5, row: 20 }), Effect::Idle);
        assert_eq!(m.cursor, 0);
    }

    /// **The keyboard drop is one tap, not a mode.** The shell reconciles the
    /// terminal against `mouse_on`; the next key press puts it back, so there is
    /// no way to end up with mouse reporting off and no way to notice.
    #[test]
    fn the_ime_affordance_restores_itself_on_the_next_key() {
        let mut m = model();
        update(&mut m, Msg::RaiseKeyboard);
        assert!(!m.mouse_on && m.keyboard_hint);

        update(&mut m, Msg::Char('c'));
        assert!(m.mouse_on && !m.keyboard_hint);
        assert_eq!(m.query, "c", "and the keystroke still counted");
    }

    /// **The hit test reads the geometry the renderer drew.** The header count
    /// is the one verb a thumb cannot otherwise produce while browsing, so it
    /// is checked at both ends: a rounding mistake shows up at a boundary
    /// first, and this boundary is next to nothing else pressable.
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
            assert_eq!(m.filter, Filter::Expiring, "col {col} filters");
            // A toggle, not a jump: the second tap peels it off, exactly as
            // `ctrl+x` does.
            update(&mut m, Msg::Tap { col, row: 0 });
            assert_eq!(m.filter, Filter::All, "col {col} toggles back");
        }

        // A column outside it is not a button.
        let mut m = model();
        crate::find::draw_for_test(&mut m, 45, 28);
        update(&mut m, Msg::Tap { col: zone.col - 1, row: 0 });
        assert_eq!(m.filter, Filter::All);
    }

    /// A keyboard layout has no touch affordances, so its zones are empty and
    /// no tap can find them.
    #[test]
    fn a_wide_terminal_draws_no_touch_affordances() {
        let mut m = model();
        crate::find::draw_for_test(&mut m, 120, 40);
        assert_eq!(m.count_zone.width, 0);
        assert_eq!(m.leader_zone.width, 0);
    }

    /// **`Space` on an empty query is the leader; mid-query it is a space.**
    /// The query is the mode, which is how a modeless surface gets a prefix key.
    #[test]
    fn space_leads_when_the_query_is_empty_and_types_when_it_is_not() {
        let mut m = model();
        update(&mut m, Msg::Char(' '));
        assert!(m.sheet.is_some(), "the sheet opened");
        assert!(m.query.is_empty(), "and nothing was typed");

        update(&mut m, Msg::Esc);
        assert!(m.sheet.is_none(), "esc peels the sheet first");

        update(&mut m, Msg::Char('c'));
        update(&mut m, Msg::Char(' '));
        assert_eq!(m.query, "c ", "mid-query it is just a space");
        assert!(m.sheet.is_none());
    }

    /// A live filter does not make an empty query "typing" — the query decides,
    /// and nothing else.
    #[test]
    fn space_still_leads_with_a_filter_up() {
        let mut m = model();
        update(&mut m, Msg::ToggleExpiring);
        update(&mut m, Msg::Char(' '));
        assert!(m.sheet.is_some());
    }

    /// **A chord is a shortcut for a verb, never a second implementation.**
    /// `SPC f x` goes through the same `update` that `ctrl+x` reaches.
    #[test]
    fn the_sheet_reaches_the_same_verbs_the_keyboard_does() {
        let mut m = model();
        for c in [' ', 'f', 'x'] {
            update(&mut m, Msg::Char(c));
        }
        assert_eq!(m.filter, Filter::Expiring);
        assert!(m.sheet.is_none(), "running an item closes the sheet");
    }

    /// Typing turns the sheet into a picker, and `Enter` runs what is left.
    #[test]
    fn typing_in_the_sheet_finds_a_verb_by_name() {
        let mut m = model();
        for c in [' ', 'f', 'e', 'x', 'p'] {
            update(&mut m, Msg::Char(c));
        }
        let sheet = m.sheet.clone().expect("still open while filtering");
        assert_eq!(sheet.filter, "exp", "the letters became a search, not keys");
        update(&mut m, Msg::Enter);
        assert_eq!(m.filter, Filter::Expiring);
    }

    /// The sheet peels one layer per `Esc`, like everything else on the surface.
    #[test]
    fn esc_peels_the_sheet_one_layer_at_a_time() {
        let mut m = model();
        for c in [' ', 'f', 'e'] {
            update(&mut m, Msg::Char(c));
        }
        update(&mut m, Msg::Esc);
        assert_eq!(m.sheet.as_ref().map(|s| s.filter.clone()), Some(String::new()));
        update(&mut m, Msg::Esc);
        assert_eq!(m.sheet.as_ref().map(|s| s.group), Some(None), "up a level");
        update(&mut m, Msg::Esc);
        assert!(m.sheet.is_none());
        assert!(!m.esc_armed, "and closing the sheet did not arm the quit");
    }

    /// **The chrome goes inert under a pushed record.** Tapping where a filter
    /// used to be would mutate a surface you cannot see.
    #[test]
    fn a_pushed_record_makes_the_chrome_untappable() {
        let mut m = model();
        crate::find::draw_for_test(&mut m, 45, 28);
        let zone = m.count_zone;
        update(&mut m, Msg::OpenDetail);
        crate::find::draw_for_test(&mut m, 45, 28);

        update(&mut m, Msg::Tap { col: zone.col, row: 0 });
        assert_eq!(m.filter, Filter::All, "the count is not a button here");
        assert_eq!(update(&mut m, Msg::Tap { col: 3, row: 26 }), Effect::Idle);
        assert!(m.mouse_on, "and the field did not drop reporting either");
    }

    /// **The record owns `↑`/`↓` while it is open.** They used to move the list
    /// cursor underneath it, so the record silently became a different document
    /// while you were reading it — which at 47 columns is impossible to follow.
    #[test]
    fn arrows_move_the_record_selector_and_not_the_list() {
        let mut m = model();
        let before = m.cursor;
        update(&mut m, Msg::OpenDetail);
        assert_eq!(m.record_cursor, 0, "drilling in starts at the top");

        update(&mut m, Msg::Move(Motion::Down));
        update(&mut m, Msg::Move(Motion::Down));
        assert_eq!(m.record_cursor, 2);
        assert_eq!(m.cursor, before, "the document underneath never moved");

        // And back out, the list has them again.
        update(&mut m, Msg::Esc);
        update(&mut m, Msg::Move(Motion::Down));
        assert_ne!(m.cursor, before);
    }

    /// The selector is clamped to the record it is on, both ends.
    #[test]
    fn the_record_selector_cannot_run_off_either_end() {
        let mut m = model();
        update(&mut m, Msg::OpenDetail);
        update(&mut m, Msg::Move(Motion::Up));
        assert_eq!(m.record_cursor, 0);

        let rows = crate::detail::rows(m.current().unwrap()).len();
        for _ in 0..rows + 5 {
            update(&mut m, Msg::Move(Motion::Down));
        }
        assert_eq!(m.record_cursor, rows - 1);
    }

    /// **A letter is a verb on the record, not search text.** Invariant 1 scopes
    /// find-fast to the browse surface, which is what frees this surface to have
    /// keys at all — and is why editing needs no control key.
    #[test]
    fn letters_are_verbs_on_the_record_not_query_text() {
        let mut m = model();
        update(&mut m, Msg::OpenDetail);
        update(&mut m, Msg::Char('z'));
        assert!(m.query.is_empty(), "nothing reached the query");
        assert!(m.flash.is_some(), "and an unknown verb says so rather than doing nothing");
    }

    /// `e` edits **the row the selector is on** — one verb over every field,
    /// which is the whole reason a per-field control key was the wrong shape.
    #[test]
    fn e_edits_the_selected_row_and_says_so_when_it_cannot() {
        let mut m = model();
        m.write = WriteState::Ready { device: "desk".into() };
        update(&mut m, Msg::OpenDetail);
        let rows = crate::detail::rows(m.current().unwrap());
        let expiry = rows
            .iter()
            .position(|row| matches!(row, crate::detail::Row::Editable(crate::edit::Field::Expiry)))
            .expect("the record has an editable row");

        // A row that is not editable yet.
        m.record_cursor = rows
            .iter()
            .position(|row| matches!(row, crate::detail::Row::Fact(_)))
            .expect("and a row that is not");
        update(&mut m, Msg::Char('e'));
        assert!(m.edit.is_none(), "nothing opened");
        assert!(m.flash.is_some(), "and it explained why");

        m.record_cursor = expiry;
        update(&mut m, Msg::Char('e'));
        assert_eq!(
            m.edit.as_ref().map(|edit| edit.field),
            Some(crate::edit::Field::Expiry),
            "on the editable row it opens the editor"
        );
    }

    /// The same verb through the sheet, because a chord is a shortcut for a verb
    /// and never a second implementation of it.
    #[test]
    fn the_sheet_offers_the_record_verb_too() {
        let mut m = model();
        m.write = WriteState::Ready { device: "desk".into() };
        update(&mut m, Msg::OpenDetail);
        m.record_cursor = crate::detail::rows(m.current().unwrap())
            .iter()
            .position(|row| matches!(row, crate::detail::Row::Editable(crate::edit::Field::Expiry)))
            .unwrap();

        update(&mut m, Msg::Char(' '));
        assert!(m.sheet.is_some(), "space still opens the sheet on the record");
        let listed = crate::sheet::items(None, &m);
        assert!(
            listed.iter().any(|item| item.act == crate::sheet::Act::Edit),
            "and it lists the record's verb: {listed:?}"
        );

        update(&mut m, Msg::Char('e'));
        assert_eq!(m.edit.as_ref().map(|edit| edit.field), Some(crate::edit::Field::Expiry));
        assert!(m.sheet.is_none(), "running an item closes the sheet");
    }

    /// **`Enter` has no button**, and does not need one: a thumb opens the
    /// highlighted row by tapping it a second time, which is the gesture the
    /// button would have duplicated.
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

    /// A tap in the margin at either end is not a button — that is the edge of
    /// the row, and guessing there would be guessing.
    #[test]
    fn the_margins_are_not_buttons() {
        let bar = model().rows_on_screen - 3;
        for col in [0, 1, 44] {
            let mut m = model();
            assert_eq!(update(&mut m, Msg::Tap { col, row: bar }), Effect::Idle, "col {col}");
            assert!(m.flash.is_none());
        }
    }

    /// **Tapping the search bar is the keyboard affordance** (REWRITE-UI.md §5).
    /// It sits there rather than in the action bar because tapping the field you
    /// want to type into is what a thumb does anyway — and because Termux's own
    /// extra-keys row can already carry a keyboard toggle, so a second button
    /// for it wastes a quarter of the only touch chrome there is.
    #[test]
    fn tapping_the_search_bar_drops_mouse_reporting_for_the_ime() {
        let mut m = model();
        // **Both** rows of the touch search bar, right down to the screen edge.
        for row in [m.rows_on_screen - 2, m.rows_on_screen - 1] {
            m.mouse_on = true;
            m.keyboard_hint = false;
            update(&mut m, Msg::Tap { col: 3, row });
            assert!(!m.mouse_on && m.keyboard_hint, "row {row} is part of the target");
        }

        // And the very next keystroke puts reporting back, as before.
        update(&mut m, Msg::Char('c'));
        assert!(m.mouse_on && !m.keyboard_hint);
        assert_eq!(m.query, "c");
    }

    /// **The expiring filter is a filter, not a mode**: it re-orders the same
    /// list, search still narrows it, and `Esc` peels it off.
    #[test]
    fn the_expiring_filter_narrows_and_peels() {
        let mut m = model();
        update(&mut m, Msg::ToggleExpiring);
        let ids: Vec<&str> = m.rows.iter().map(|&i| m.store.docs[i].id.as_str()).collect();
        assert_eq!(ids, ["coc", "eng1", "passport"], "soonest first, untracked gone");

        update(&mut m, Msg::Char('e'));
        update(&mut m, Msg::Char('n'));
        update(&mut m, Msg::Char('g'));
        assert_eq!(m.rows.len(), 1, "search narrows inside the filter");

        update(&mut m, Msg::Esc);
        assert_eq!(m.filter, Filter::Expiring, "the first peel took the search");
        update(&mut m, Msg::Esc);
        assert_eq!(m.filter, Filter::All);
        assert_eq!(m.rows.len(), 4);
    }

    /// The cursor never wraps and never leaves the list, however hard it is
    /// pushed.
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

    /// Searching down to nothing leaves a valid, empty state — and `Enter`
    /// against it says so rather than panicking on an index.
    #[test]
    fn an_empty_result_is_a_valid_state() {
        let mut m = model();
        for c in "zzzz".chars() {
            update(&mut m, Msg::Char(c));
        }
        assert!(m.rows.is_empty());
        assert!(m.current().is_none());
        assert_eq!(update(&mut m, Msg::Enter), Effect::Redraw);
        assert_eq!(m.flash.as_deref(), Some("nothing to open"));

        update(&mut m, Msg::Backspace);
        assert_eq!(m.query, "zzz");
    }

    /// Scrolling drags the cursor with it: nothing scrolls away from the
    /// highlight, because on a phone the highlight is where the next tap goes.
    #[test]
    fn scrolling_keeps_the_cursor_on_screen() {
        let mut m = model();
        m.rows_on_screen = 16; // 12 chrome-free lines → 6 two-line rows
        update(&mut m, Msg::Scroll(2));
        assert_eq!(m.offset, 0, "a four-row list cannot scroll past its end");

        let many: Vec<Doc> =
            (0..50).map(|i| doc(&format!("d{i}"), &format!("Doc {i}"), None, None)).collect();
        m.store = Store { docs: many, ..Store::default() };
        m.filter = Filter::All;
        m.query.clear();
        m.requery();
        update(&mut m, Msg::Scroll(10));
        assert_eq!(m.offset, 10);
        assert!(m.cursor >= m.offset, "the cursor came along");
        update(&mut m, Msg::Scroll(-100));
        assert_eq!(m.offset, 0);
    }

    /// **`ctrl+t` never blocks the render loop** (invariant 7). The first press
    /// asks for a load and shows that it is waiting; the answer arrives as a
    /// message like any other.
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

        for c in "mariner".chars() {
            update(&mut m, Msg::Char(c));
        }
        assert_eq!(m.rows.len(), 1, "found by what the page says, not by its name");
        assert_eq!(m.current().unwrap().id, "coc");

        // Off again, and the word is nowhere in any name.
        update(&mut m, Msg::ToggleScans);
        assert_eq!(m.scan_search, ScanSearch::Off);
        assert!(m.rows.is_empty());
    }

    /// **Scan text widens the result, never replaces it.** A document whose name
    /// matches must not drop out because its transcript does not say the word.
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
        for c in "coc".chars() {
            update(&mut m, Msg::Char(c));
        }
        let ids: Vec<&str> = m.rows.iter().map(|&i| m.store.docs[i].id.as_str()).collect();
        assert_eq!(ids, ["coc", "eng1"], "the name match first, in list order");
    }

    /// A second `ctrl+t` costs nothing: the text is kept even while the toggle
    /// is off, so only the first press ever waits.
    #[test]
    fn the_second_toggle_needs_no_second_load() {
        let mut m = model();
        update(&mut m, Msg::ToggleScans);
        update(&mut m, Msg::ScansLoaded(std::sync::Arc::new(crate::scans::Scans::default())));
        update(&mut m, Msg::ToggleScans);
        assert_eq!(update(&mut m, Msg::ToggleScans), Effect::Redraw, "no second load");
        assert_eq!(m.scan_search, ScanSearch::On);
    }

    /// A load that lands after the user changed their mind is kept, not applied
    /// — and it does not disarm a pending quit, because the user did not touch
    /// anything.
    #[test]
    fn a_late_load_does_not_reopen_the_toggle_or_disarm_the_quit() {
        let mut m = model();
        update(&mut m, Msg::ToggleScans);
        update(&mut m, Msg::ToggleScans); // changed their mind while it loaded
        update(&mut m, Msg::Esc);
        assert!(m.esc_armed);

        update(&mut m, Msg::ScansLoaded(std::sync::Arc::new(crate::scans::Scans::default())));
        assert_eq!(m.scan_search, ScanSearch::Off, "not turned on behind their back");
        assert!(m.scans.is_some(), "but the work is kept");
        assert!(m.esc_armed, "a worker message is not a keystroke");
    }

    /// The header count is the number of documents actually wanting attention —
    /// the number that names `:expiring`.
    #[test]
    fn the_attention_count_is_expired_plus_soon() {
        let m = model();
        assert_eq!(m.attention_count(), 1, "COC is expired; ENG-1 is outside the window");
    }
}
