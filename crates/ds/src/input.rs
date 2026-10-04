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

//! Terminal events in, [`Msg`]s out — the only place that knows crossterm, so
//! every terminal quirk has one home. Key releases are dropped (the kitty
//! protocol sends both), drags change nothing (Termux sends a drag as moves
//! and wheel events), and nothing is mapped to a function key, which Termux
//! does not have.

use ratatui::crossterm::event::{
    Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};

use crate::app::{Motion, Msg};

/// Translate one terminal event. `None` means "nothing happened" — the event
/// loop does not even repaint.
#[must_use]
pub fn to_msg(event: &Event) -> Option<Msg> {
    match event {
        Event::Key(key) if key.kind == KeyEventKind::Press => key_msg(*key),
        Event::Mouse(mouse) => mouse_msg(*mouse),
        Event::Resize(cols, rows) => Some(Msg::Resize { cols: *cols, rows: *rows }),
        _ => None,
    }
}

fn key_msg(key: KeyEvent) -> Option<Msg> {
    let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
    // Termux's extra-keys ALT is sticky, and crossterm delivers `alt+f` as
    // `Char('f')` with ALT set, so a modified letter must not reach the search.
    let alt = key.modifiers.contains(KeyModifiers::ALT);
    match key.code {
        // Never bound over: `ctrl+c` must always quit cleanly.
        KeyCode::Char('c' | 'q') if ctrl => Some(Msg::Quit),
        KeyCode::Char('z') if ctrl => Some(Msg::Undo),
        KeyCode::Char('y') if ctrl => Some(Msg::Redo),
        KeyCode::Esc => Some(Msg::Esc),
        KeyCode::Enter => Some(Msg::Enter),
        KeyCode::Tab => Some(Msg::Tab),
        KeyCode::Right => Some(Msg::Right),
        KeyCode::Left => Some(Msg::Left),
        KeyCode::Up => Some(Msg::Move(Motion::Up)),
        KeyCode::Down => Some(Msg::Move(Motion::Down)),
        KeyCode::PageUp => Some(Msg::Move(Motion::PageUp)),
        KeyCode::PageDown => Some(Msg::Move(Motion::PageDown)),
        KeyCode::Home => Some(Msg::Move(Motion::Home)),
        KeyCode::End => Some(Msg::Move(Motion::End)),
        KeyCode::Backspace => Some(Msg::Backspace),
        // Find-fast: every bare printable is search text. The modifier check
        // keeps a `ctrl` key from typing its letter.
        KeyCode::Char(c) if !ctrl && !alt => Some(Msg::Char(c)),
        _ => None,
    }
}

fn mouse_msg(mouse: MouseEvent) -> Option<Msg> {
    match mouse.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            Some(Msg::Tap { col: mouse.column, row: mouse.row })
        }
        // Termux's mouse mode blocks the terminal's own scrollback, so the app
        // scrolls: three rows a notch.
        MouseEventKind::ScrollDown => Some(Msg::Scroll(3)),
        MouseEventKind::ScrollUp => Some(Msg::Scroll(-3)),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn press(code: KeyCode, modifiers: KeyModifiers) -> Event {
        Event::Key(KeyEvent::new(code, modifiers))
    }

    /// Every bare printable becomes search text.
    #[test]
    fn every_bare_letter_is_search_text() {
        for c in ['s', 'b', 'u', 'f', 'q', 'x', ':', '?', '1'] {
            assert_eq!(
                to_msg(&press(KeyCode::Char(c), KeyModifiers::NONE)),
                Some(Msg::Char(c)),
                "{c} must reach the query"
            );
        }
    }

    /// A modified letter is never search text.
    #[test]
    fn a_modified_letter_never_reaches_the_query() {
        for modifiers in [KeyModifiers::ALT, KeyModifiers::CONTROL | KeyModifiers::ALT] {
            for c in ['f', 's', 'b', 'g'] {
                assert_eq!(
                    to_msg(&press(KeyCode::Char(c), modifiers)),
                    None,
                    "{c} with {modifiers:?} is not search text"
                );
            }
        }
    }

    /// `ctrl+alt+z` is `ctrl+z`: a terminal may add a modifier nobody asked for.
    #[test]
    fn a_bound_control_letter_ignores_extra_modifiers() {
        let both = KeyModifiers::CONTROL | KeyModifiers::ALT;
        assert_eq!(to_msg(&press(KeyCode::Char('z'), both)), Some(Msg::Undo));
        assert_eq!(to_msg(&press(KeyCode::Char('q'), both)), Some(Msg::Quit));
    }

    /// Editing is a bare letter, never a control key: Termux delivers ctrl
    /// combinations as one finished key, so the Space sheet cannot teach them.
    #[test]
    fn editing_is_not_behind_a_control_key() {
        assert_eq!(
            to_msg(&press(KeyCode::Char('e'), KeyModifiers::CONTROL)),
            None,
            "ctrl+e is nothing; the app decides what `e` means by surface"
        );
        assert_eq!(to_msg(&press(KeyCode::Char('e'), KeyModifiers::NONE)), Some(Msg::Char('e')));
    }

    /// Control combinations are verbs, and `ctrl+c` is always the exit.
    #[test]
    fn control_combinations_are_the_only_letter_verbs() {
        assert_eq!(to_msg(&press(KeyCode::Char('c'), KeyModifiers::CONTROL)), Some(Msg::Quit));
        assert_eq!(to_msg(&press(KeyCode::Char('q'), KeyModifiers::CONTROL)), Some(Msg::Quit));
        assert_eq!(
            to_msg(&press(KeyCode::Char('t'), KeyModifiers::CONTROL)),
            None,
            "filters live in SPC f"
        );
        assert_eq!(to_msg(&press(KeyCode::Char('x'), KeyModifiers::CONTROL)), None);
        assert_eq!(to_msg(&press(KeyCode::Char('z'), KeyModifiers::CONTROL)), Some(Msg::Undo));
        assert_eq!(to_msg(&press(KeyCode::Char('y'), KeyModifiers::CONTROL)), Some(Msg::Redo));
    }

    /// A key release is not a key press.
    #[test]
    fn releases_and_repeats_are_dropped() {
        let mut event = KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE);
        event.kind = KeyEventKind::Release;
        assert_eq!(to_msg(&Event::Key(event)), None);
    }

    /// Taps and wheel events are the touch story; drags are deliberately not.
    #[test]
    fn taps_and_scrolls_arrive_but_drags_do_not() {
        let tap = MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: 4,
            row: 9,
            modifiers: KeyModifiers::NONE,
        };
        assert_eq!(to_msg(&Event::Mouse(tap)), Some(Msg::Tap { col: 4, row: 9 }));

        let drag = MouseEvent {
            kind: MouseEventKind::Drag(MouseButton::Left),
            column: 4,
            row: 9,
            modifiers: KeyModifiers::NONE,
        };
        assert_eq!(to_msg(&Event::Mouse(drag)), None);

        let wheel = MouseEvent {
            kind: MouseEventKind::ScrollUp,
            column: 0,
            row: 0,
            modifiers: KeyModifiers::NONE,
        };
        assert_eq!(to_msg(&Event::Mouse(wheel)), Some(Msg::Scroll(-3)));
    }

    /// Nothing sits behind a function key; Termux has none.
    #[test]
    fn function_keys_are_bound_to_nothing() {
        for n in 1..=12 {
            assert_eq!(to_msg(&press(KeyCode::F(n), KeyModifiers::NONE)), None);
        }
    }
}
