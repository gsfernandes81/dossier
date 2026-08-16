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

//! Application state and the update half of the Elm-style loop.
//!
//! REWRITE.md §11 names "Ratatui immediate-mode state management sprawl" as the
//! top implementation risk and prescribes the cure: a fixed `msg → update →
//! view` loop. This module is `update`; [`crate::ui`] is `view`. Nothing here
//! draws, and nothing in `ui` mutates — that separation *is* the experiment, and
//! it is why the spike's event handling reads as a flat list of rules instead of
//! callbacks scattered through the renderer.
//!
//! The interaction rules implemented here are the layout-independent invariants
//! from REWRITE.md §4.5 — find-fast, the `Enter`/`→` verb pair, one-layer Esc
//! peeling, tap-then-tap-to-open, and the mouse-mode drop that lets Termux raise
//! its keyboard. They are the things the spike has to *feel* right, not just
//! compile.

use std::collections::VecDeque;
use std::time::Instant;

use ratatui::crossterm::event::{
    Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use ratatui::layout::Rect;

use crate::data::{self, Doc};
use crate::timing::FrameStats;

/// Which diagnostic overlay is showing, if any.
///
/// The overlays are bound to **function keys**, never letters: the Find surface
/// binds no letter keys at all (invariant 1), and a spike that cheated on that
/// rule would not be testing the real thing.
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Panel {
    /// No overlay — the Find list.
    None,
    /// Raw input events as the terminal delivered them (F2).
    Events,
    /// Glyph and column-alignment torture test (F3).
    Glyphs,
    /// Timing, budget and terminal-capability readout (F4).
    Diag,
}

/// One line in the event inspector.
pub struct LoggedEvent {
    /// Seconds since launch, so taps can be correlated with what was on screen.
    pub at: f64,
    /// Short kind tag (`key`, `tap`, `drag`, `scroll`, `resize`).
    pub kind: &'static str,
    /// The event as the terminal actually delivered it.
    pub detail: String,
}

/// Counts by event kind — the compact summary the phone screenshot can show.
#[derive(Default)]
pub struct EventCounts {
    /// Key presses (releases and repeats are filtered out).
    pub keys: usize,
    /// Mouse button presses.
    pub taps: usize,
    /// Drag events (Termux maps a finger drag to these or to scrolls).
    pub drags: usize,
    /// Wheel/scroll events.
    pub scrolls: usize,
    /// Terminal resizes (SIGWINCH / rotation).
    pub resizes: usize,
}

/// Everything the renderer needs and the event loop mutates.
///
/// The bool fields are independent UI facts, not a state machine (see the lint
/// triage in `main.rs`).
#[allow(clippy::struct_excessive_bools)]
pub struct App {
    /// The synthetic store, sorted once at startup.
    pub docs: Vec<Doc>,
    /// Indices of `docs` matching the current query, in list order.
    pub filtered: Vec<usize>,
    /// Cursor position *within `filtered`*, not within `docs`.
    pub selected: usize,
    /// First visible row — the spike scrolls the window itself (see `ui`).
    pub offset: usize,
    /// The search text; a bare printable anywhere on the list lands here.
    pub query: String,
    /// `ctrl+t` — the search-inside-scans chip (state only; no scans here).
    pub scans: bool,
    /// Detail is a sticky toggle (REWRITE-UI.md U3).
    pub detail: bool,
    /// Active overlay.
    pub panel: Panel,
    /// One Esc from quitting (invariant 3).
    pub esc_armed: bool,
    /// SGR mouse reporting is on.
    pub mouse_on: bool,
    /// Mouse reporting was dropped on purpose so the next tap raises the IME.
    pub keyboard_hint: bool,
    /// Transient one-line message (what `Enter` "opened", etc).
    pub flash: Option<String>,
    /// Ring of recent input events for the F2 inspector.
    pub events: VecDeque<LoggedEvent>,
    /// Totals by kind.
    pub counts: EventCounts,
    /// Frame render times.
    pub frames: FrameStats,
    /// Launch instant, for event timestamps.
    pub born: Instant,
    /// Startup breakdown line, shown in the F4 panel.
    pub startup_line: String,
    /// Whether the terminal answered the kitty keyboard-protocol probe (F4).
    pub kbd_enhancement: Option<bool>,
    /// Set when the loop should exit.
    pub quit: bool,
    /// Rect the list rows occupy, published by `ui` for click hit-testing.
    pub rows_area: Rect,
    /// Height of one row (1 wide, 2 narrow), also published by `ui`.
    pub row_h: u16,
    /// Rect of the touch action bar, published by `ui`.
    pub action_bar: Rect,
}

impl App {
    /// Build the initial state from a freshly generated store.
    pub fn new(docs: Vec<Doc>) -> Self {
        let filtered = (0..docs.len()).collect();
        Self {
            docs,
            filtered,
            selected: 0,
            offset: 0,
            query: String::new(),
            scans: false,
            detail: false,
            panel: Panel::None,
            esc_armed: false,
            mouse_on: true,
            keyboard_hint: false,
            flash: None,
            events: VecDeque::new(),
            counts: EventCounts::default(),
            frames: FrameStats::default(),
            born: Instant::now(),
            startup_line: String::new(),
            kbd_enhancement: None,
            quit: false,
            rows_area: Rect::ZERO,
            row_h: 1,
            action_bar: Rect::ZERO,
        }
    }

    /// The highlighted document, if the filter matched anything.
    pub fn current(&self) -> Option<&Doc> {
        self.filtered.get(self.selected).map(|&i| &self.docs[i])
    }

    /// Re-run the filter and clamp the cursor. Called on every query edit.
    fn refilter(&mut self) {
        self.filtered = data::filter(&self.docs, &self.query);
        self.selected = self.selected.min(self.filtered.len().saturating_sub(1));
        self.offset = self.offset.min(self.selected);
    }

    /// Move the cursor by `delta` rows, clamped (never wraps — a wrapping list
    /// on a phone is a way to lose your place with a fat thumb).
    fn move_by(&mut self, delta: isize) {
        if self.filtered.is_empty() {
            return;
        }
        let last = self.filtered.len() - 1;
        let next = (self.selected as isize + delta).clamp(0, last as isize) as usize;
        self.selected = next;
    }

    /// The `Enter` verb: open the file. Never mutates, never dies — with no
    /// file linked it falls through to the record (invariant 2).
    fn activate(&mut self) {
        match self.current() {
            None => self.flash = Some("no match".into()),
            Some(doc) if doc.has_file => {
                self.flash = Some(format!("would open file: {} [{}]", doc.name, doc.place()));
            }
            Some(doc) => {
                // No file — fall through to the record rather than erroring.
                self.flash = Some(format!("no file linked → record: {}", doc.name));
                self.detail = true;
            }
        }
    }

    /// Handle one terminal event. Returns `true` if the frame should be redrawn.
    ///
    /// rust: `&mut self` plus a bool return, instead of returning a list of
    /// commands. At this size the direct mutation is clearer than a message
    /// enum; R3 will need the enum once background workers post messages too.
    pub fn handle(&mut self, event: &Event) -> bool {
        self.log(event);
        // Everything else — key *releases* (which terminals that negotiated the
        // kitty protocol send, and acting on them would make one tap act twice),
        // focus changes, paste — falls through to "no redraw needed".
        match event {
            Event::Key(key) if key.kind == KeyEventKind::Press => self.on_key(*key),
            Event::Mouse(mouse) => self.on_mouse(*mouse),
            Event::Resize(_, _) => true,
            _ => false,
        }
    }

    fn on_key(&mut self, key: KeyEvent) -> bool {
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        // Any key at all disarms the quit and restores mouse reporting: the
        // keyboard-drop is a one-tap affordance, not a mode you can get stuck in.
        let was_armed = self.esc_armed;
        if !matches!(key.code, KeyCode::Esc) {
            self.esc_armed = false;
        }
        self.keyboard_hint = false;
        self.flash = None;

        match key.code {
            KeyCode::Char('c' | 'q') if ctrl => self.quit = true,
            // ctrl+t: search inside scan text. A modifier combination on the
            // browse surface, which is exactly what Termux's keyboard variants
            // (termux-app #1255) are least reliable at delivering — so it is
            // here on purpose, as a probe.
            KeyCode::Char('t') if ctrl => self.scans = !self.scans,
            KeyCode::Esc => return self.peel(was_armed),
            KeyCode::Enter => self.activate(),
            KeyCode::Right => self.detail = true,
            KeyCode::Left => self.detail = false,
            KeyCode::Up => self.move_by(-1),
            KeyCode::Down => self.move_by(1),
            KeyCode::PageUp => self.move_by(-(self.visible_rows() as isize)),
            KeyCode::PageDown => self.move_by(self.visible_rows() as isize),
            KeyCode::Home => self.selected = 0,
            KeyCode::End => self.selected = self.filtered.len().saturating_sub(1),
            KeyCode::Backspace => {
                self.query.pop();
                self.refilter();
            }
            KeyCode::F(2) => self.toggle(Panel::Events),
            KeyCode::F(3) => self.toggle(Panel::Glyphs),
            KeyCode::F(4) => self.toggle(Panel::Diag),
            KeyCode::F(5) => return self.drop_mouse_for_ime(),
            // Find-fast (invariant 1): a bare printable starts the search and
            // the first character is *kept*. This is why the surface binds no
            // letters — every letter belongs to the query.
            KeyCode::Char(c) if !ctrl => {
                self.query.push(c);
                self.refilter();
            }
            _ => return false,
        }
        true
    }

    /// Esc peels exactly one layer per press; at base state it arms, and a
    /// second consecutive Esc quits (invariant 3).
    ///
    /// The order matters on Termux, where Esc is also how the IME gets
    /// dismissed: every press must consume something visible before it can ever
    /// reach "quit", or the app dies on a keyboard dismissal.
    fn peel(&mut self, was_armed: bool) -> bool {
        if self.panel != Panel::None {
            self.panel = Panel::None;
        } else if !self.query.is_empty() {
            self.query.clear();
            self.refilter();
        } else if self.detail {
            self.detail = false;
        } else if was_armed {
            self.quit = true;
        } else {
            self.esc_armed = true;
        }
        true
    }

    fn toggle(&mut self, panel: Panel) {
        self.panel = if self.panel == panel { Panel::None } else { panel };
    }

    /// The Termux IME affordance (invariant 6, DESIGN §14).
    ///
    /// Termux only raises the soft keyboard on a tap when mouse tracking is
    /// **off**, and no escape sequence or API can raise it directly. So the
    /// `⌨` control drops mouse reporting for exactly one tap; the tap raises
    /// the keyboard, and the first key press restores reporting. The caller
    /// performs the actual terminal command — this only flips the state.
    fn drop_mouse_for_ime(&mut self) -> bool {
        self.mouse_on = false;
        self.keyboard_hint = true;
        true
    }

    fn on_mouse(&mut self, mouse: MouseEvent) -> bool {
        self.flash = None;
        match mouse.kind {
            MouseEventKind::Down(MouseButton::Left) => {
                self.esc_armed = false;
                if self.hit_action_bar(mouse.column, mouse.row) {
                    return true;
                }
                let Some(index) = self.row_at(mouse.column, mouse.row) else { return true };
                if index >= self.filtered.len() {
                    return true;
                }
                // Tap selects; tap on the already-selected row opens
                // (invariant 6). Two taps, no double-tap timing — timing-based
                // gestures are miserable on a laggy terminal.
                if index == self.selected {
                    self.activate();
                } else {
                    self.selected = index;
                }
                true
            }
            // The app owns scrolling: Termux's mouse mode blocks the terminal's
            // own scrollback (termux-app #4302), so if the list does not move
            // the finger, nothing does.
            MouseEventKind::ScrollDown => {
                self.scroll(3);
                true
            }
            MouseEventKind::ScrollUp => {
                self.scroll(-3);
                true
            }
            // Drags, moves and button-ups change nothing: Termux delivers a
            // finger drag as a stream of these plus wheel events, and reacting
            // to each one would make the list chase the finger twice.
            _ => false,
        }
    }

    /// Scroll the window and drag the cursor along with it, so the selection is
    /// always on screen (nothing scrolls "away" from the highlight).
    fn scroll(&mut self, delta: isize) {
        let last = self.filtered.len().saturating_sub(1);
        let next_offset = (self.offset as isize + delta).clamp(0, last as isize) as usize;
        self.offset = next_offset;
        let visible = self.visible_rows();
        self.selected = self.selected.clamp(self.offset, (self.offset + visible - 1).min(last));
    }

    /// Which list row a click landed on, if any.
    fn row_at(&self, col: u16, row: u16) -> Option<usize> {
        let area = self.rows_area;
        let inside = col >= area.x
            && col < area.x + area.width
            && row >= area.y
            && row < area.y + area.height;
        inside.then(|| self.offset + ((row - area.y) / self.row_h.max(1)) as usize)
    }

    /// The touch action bar: `Open · Detail · Panels · ⌨` (REWRITE-UI.md §5).
    fn hit_action_bar(&mut self, col: u16, row: u16) -> bool {
        let bar = self.action_bar;
        if bar.height == 0 || row != bar.y {
            return false;
        }
        // Four equal quarters, so the hit test needs no per-label geometry.
        let quarter = (bar.width / 4).max(1);
        match (col.saturating_sub(bar.x) / quarter).min(3) {
            0 => self.activate(),
            1 => self.detail = !self.detail,
            2 => self.toggle(Panel::Diag),
            _ => {
                self.drop_mouse_for_ime();
            }
        }
        true
    }

    /// Rows that fit in the current list area — used by PageUp/PageDown and by
    /// scroll clamping.
    pub fn visible_rows(&self) -> usize {
        (self.rows_area.height / self.row_h.max(1)).max(1) as usize
    }

    fn log(&mut self, event: &Event) {
        let (kind, detail) = match event {
            Event::Key(k) if k.kind == KeyEventKind::Press => {
                self.counts.keys += 1;
                ("key", format!("{:?} mods={:?}", k.code, k.modifiers))
            }
            Event::Key(k) => ("key", format!("{:?} {:?} (ignored)", k.kind, k.code)),
            Event::Mouse(m) => {
                let kind = match m.kind {
                    MouseEventKind::Down(_) | MouseEventKind::Up(_) => {
                        self.counts.taps += 1;
                        "tap"
                    }
                    MouseEventKind::Drag(_) | MouseEventKind::Moved => {
                        self.counts.drags += 1;
                        "drag"
                    }
                    _ => {
                        self.counts.scrolls += 1;
                        "scroll"
                    }
                };
                (kind, format!("{:?} @{},{} mods={:?}", m.kind, m.column, m.row, m.modifiers))
            }
            Event::Resize(w, h) => {
                self.counts.resizes += 1;
                ("resize", format!("{w}x{h}"))
            }
            other => ("other", format!("{other:?}")),
        };
        if self.events.len() >= 200 {
            self.events.pop_front();
        }
        self.events.push_back(LoggedEvent { at: self.born.elapsed().as_secs_f64(), kind, detail });
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::data::synth;

    fn app() -> App {
        let mut app = App::new(synth(200));
        // The renderer normally publishes this; tests set it directly so key
        // handling that depends on the viewport (PageDown) is exercised.
        app.rows_area = Rect::new(0, 1, 60, 20);
        app
    }

    fn press(app: &mut App, code: KeyCode) {
        app.handle(&Event::Key(KeyEvent::new(code, KeyModifiers::NONE)));
    }

    /// Invariant 1 — a bare printable typed on the list starts a search and the
    /// first character is kept, so cold-start → type → Enter is 5 keystrokes.
    #[test]
    fn a_printable_starts_the_search_keeping_the_first_char() {
        let mut app = app();
        press(&mut app, KeyCode::Char('p'));
        press(&mut app, KeyCode::Char('a'));
        assert_eq!(app.query, "pa");
        assert!(app.filtered.len() < app.docs.len());
    }

    /// Invariant 2 — `Enter` opens the file and never mutates; with no file it
    /// falls through to the record instead of dying.
    #[test]
    fn enter_opens_and_falls_through_when_there_is_no_file() {
        let mut app = app();
        let index = app.filtered.iter().position(|&i| !app.docs[i].has_file).unwrap();
        app.selected = index;
        press(&mut app, KeyCode::Enter);
        assert!(app.detail, "no-file rows fall through to the record");
        assert!(app.flash.is_some());
        assert!(!app.quit);
    }

    /// Invariant 3 — Esc peels exactly one layer per press: panel, then query,
    /// then detail, then arm, then quit. Termux's IME-dismiss Esc must never
    /// reach "quit" while anything is still open.
    #[test]
    fn esc_peels_one_layer_per_press_then_arms_then_quits() {
        let mut app = app();
        app.panel = Panel::Diag;
        app.query = "pass".into();
        app.detail = true;
        press(&mut app, KeyCode::Esc);
        assert_eq!(app.panel as u8, Panel::None as u8);
        press(&mut app, KeyCode::Esc);
        assert!(app.query.is_empty());
        press(&mut app, KeyCode::Esc);
        assert!(!app.detail);
        press(&mut app, KeyCode::Esc);
        assert!(app.esc_armed && !app.quit, "base state arms, it does not quit");
        press(&mut app, KeyCode::Esc);
        assert!(app.quit, "a second consecutive Esc quits");
    }

    /// Any other key disarms the quit, so an armed Esc left over from an IME
    /// dismissal cannot kill the app minutes later.
    #[test]
    fn any_other_key_disarms_the_quit() {
        let mut app = app();
        press(&mut app, KeyCode::Esc);
        assert!(app.esc_armed);
        press(&mut app, KeyCode::Down);
        assert!(!app.esc_armed);
        press(&mut app, KeyCode::Esc);
        assert!(!app.quit);
    }

    /// Invariant 6 — first tap selects, a tap on the already-selected row opens.
    #[test]
    fn tap_selects_then_tap_on_selected_opens() {
        let mut app = app();
        let tap = |app: &mut App, row: u16| {
            app.handle(&Event::Mouse(MouseEvent {
                kind: MouseEventKind::Down(MouseButton::Left),
                column: 3,
                row,
                modifiers: KeyModifiers::NONE,
            }));
        };
        tap(&mut app, 6);
        assert_eq!(app.selected, 5);
        assert!(app.flash.is_none(), "the first tap only selects");
        tap(&mut app, 6);
        assert!(app.flash.is_some(), "the second tap on the same row opens");
    }

    /// The F5 / `⌨` affordance drops mouse reporting so Termux will raise the
    /// keyboard on the next tap, and the next key press restores it.
    #[test]
    fn keyboard_affordance_drops_mouse_mode_and_the_next_key_restores_it() {
        let mut app = app();
        press(&mut app, KeyCode::F(5));
        assert!(!app.mouse_on && app.keyboard_hint);
        press(&mut app, KeyCode::Char('x'));
        assert!(!app.keyboard_hint, "the hint clears as soon as typing starts");
    }

    /// Key *releases* (kitty protocol) must not act twice.
    #[test]
    fn key_releases_are_ignored() {
        let mut app = app();
        let mut release = KeyEvent::new(KeyCode::Char('z'), KeyModifiers::NONE);
        release.kind = KeyEventKind::Release;
        app.handle(&Event::Key(release));
        assert!(app.query.is_empty());
    }

    /// Scrolling keeps the cursor inside the viewport — the list owns scrolling
    /// because Termux's mouse mode blocks terminal scrollback (#4302).
    #[test]
    fn scrolling_keeps_the_cursor_on_screen() {
        let mut app = app();
        for _ in 0..10 {
            app.handle(&Event::Mouse(MouseEvent {
                kind: MouseEventKind::ScrollDown,
                column: 1,
                row: 5,
                modifiers: KeyModifiers::NONE,
            }));
        }
        assert!(app.selected >= app.offset);
        assert!(app.selected < app.offset + app.visible_rows());
    }
}
