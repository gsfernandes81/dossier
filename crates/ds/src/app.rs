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
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Msg {
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
    /// Leave, restoring the terminal.
    Quit,
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

/// The rectangle the renderer last drew document rows into.
///
/// Published by the view so taps can be hit-tested against what is actually on
/// screen. Zeroed when the list is not drawn at all (too-small notice, or detail
/// covering it on a narrow terminal), which makes a tap in that state a no-op
/// rather than a guess.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
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
    /// `ctrl+t`: whether scan text is part of the haystack. The enrich namespace
    /// that holds that text is loaded lazily, so this is the chip and the
    /// intent; the load arrives with the enrich slice.
    pub scans: bool,
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
            scans: false,
            filter: Filter::All,
            rows: Vec::new(),
            cursor: 0,
            offset: 0,
            detail: false,
            esc_armed: false,
            mouse_on: true,
            keyboard_hint: false,
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
        self.rows = match base {
            None => self.store.search(&self.query),
            Some(expiring) if self.query.is_empty() => expiring,
            Some(expiring) => {
                // The filter decides the set *and* the order; the search then
                // narrows it. Running the search first would re-sort the list
                // back into shelf order and lose "soonest first".
                let matched = self.store.search(&self.query);
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

    /// The touch action bar: `⏎ Open · → Detail · : Cmds · ⌨ Keys`, in quarters.
    ///
    /// Quarters rather than measured labels, so the hit test needs no geometry
    /// from the renderer beyond the row it sits on — and so a fat thumb always
    /// lands on something.
    fn tap_action_bar(&mut self, col: u16) -> Effect {
        let quarter = (self.cols / 4).max(1);
        match (col / quarter).min(3) {
            0 => self.activate(),
            1 => {
                self.detail = !self.detail;
                Effect::Redraw
            }
            // The third quarter carries whatever this surface's third verb is.
            // Today that is the expiring filter; when command mode lands it
            // becomes `: Cmds`. What it never becomes is a button for something
            // that does not work yet — v2's `check_action` lesson.
            2 => update(self, Msg::ToggleExpiring),
            _ => self.raise_keyboard(),
        }
    }

    /// The Termux IME affordance (invariant 6, DESIGN §14).
    ///
    /// Termux raises the soft keyboard on a tap only while mouse tracking is
    /// **off**, and no escape sequence can raise it directly. So the `⌨` control
    /// drops reporting for exactly one tap: the tap raises the keyboard, and the
    /// first key press turns reporting back on. Confirmed working on the phone
    /// in R0.2.
    ///
    /// This flips state only. Reporting is a *terminal* command, so the shell
    /// performs it by reconciling itself against [`Model::mouse_on`] after every
    /// update — one source of truth, no second mechanism to drift from it.
    fn raise_keyboard(&mut self) -> Effect {
        self.mouse_on = false;
        self.keyboard_hint = true;
        Effect::Redraw
    }
}

/// Apply one message. The only entry point to state change.
#[allow(clippy::too_many_lines)] // One flat table of rules reads better than five helpers.
pub fn update(model: &mut Model, msg: Msg) -> Effect {
    // A key press means the user is at the keyboard, so the IME affordance has
    // done its job: restore mouse reporting. Doing it here, once, is why the
    // drop can never become a mode you get stuck in.
    if model.keyboard_hint && is_key(msg) {
        model.mouse_on = true;
        model.keyboard_hint = false;
    }

    // Esc arms only on a *consecutive* Esc; any other key disarms it.
    let was_armed = model.esc_armed;
    if !matches!(msg, Msg::Esc) {
        model.esc_armed = false;
    }
    if is_key(msg) {
        model.flash = None;
    }

    match msg {
        Msg::Quit => Effect::Quit,
        Msg::Esc => model.peel(was_armed),
        Msg::Enter => model.activate(),
        Msg::OpenDetail => {
            model.detail = true;
            Effect::Redraw
        }
        Msg::CloseDetail => {
            model.detail = false;
            Effect::Redraw
        }
        Msg::Move(motion) => {
            model.move_cursor(motion);
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
        Msg::Char(c) => {
            model.query.push(c);
            model.requery();
            Effect::Redraw
        }
        Msg::ToggleScans => {
            model.scans = !model.scans;
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
            if crate::layout::touch_bar(model.cols) && row == action_bar_row(model) {
                model.tap_action_bar(col)
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

/// Whether this message came from the keyboard.
fn is_key(msg: Msg) -> bool {
    !matches!(msg, Msg::Tap { .. } | Msg::Scroll(_) | Msg::Resize { .. })
}

/// The terminal row the touch action bar sits on: directly above the search bar
/// and the hint line.
fn action_bar_row(model: &Model) -> u16 {
    model.rows_on_screen.saturating_sub(3)
}

#[cfg(test)]
mod tests {
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

    fn model() -> Model {
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

    /// The action bar's quarters are hit-tested without the renderer measuring
    /// labels: `⏎ Open` on the left, `⌨ Keys` on the right.
    #[test]
    fn the_action_bar_is_four_quarters() {
        let mut m = model();
        let bar = m.rows_on_screen - 3;
        assert_eq!(
            update(&mut m, Msg::Tap { col: 2, row: bar }),
            Effect::Open("Marine/coc.pdf".into())
        );
        update(&mut m, Msg::Tap { col: 44, row: bar });
        assert!(!m.mouse_on, "the right-hand quarter is the keyboard affordance");
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

    /// The header count is the number of documents actually wanting attention —
    /// the number that names `:expiring`.
    #[test]
    fn the_attention_count_is_expired_plus_soon() {
        let m = model();
        assert_eq!(m.attention_count(), 1, "COC is expired; ENG-1 is outside the window");
    }
}
