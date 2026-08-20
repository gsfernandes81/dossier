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

//! Terminal events in, [`Msg`]s out — the only place that knows crossterm.
//!
//! Keeping the translation here is what lets [`crate::app`] be tested as a state
//! machine: a test writes `Msg::Char('c')` instead of assembling a `KeyEvent`,
//! and the rules stay readable as rules. It also means every terminal quirk has
//! exactly one home, and the quirks are real:
//!
//! * **Key releases must be dropped.** A terminal that negotiated the kitty
//!   keyboard protocol sends press *and* release; acting on both makes every
//!   tap fire twice.
//! * **Drags and button-ups change nothing.** Termux delivers a finger drag as a
//!   stream of moves plus wheel events, and reacting to each would make the list
//!   chase the finger twice over.
//! * **Termux has no function keys** — the binding R0.2 finding. Nothing
//!   user-facing may sit behind one, so nothing here maps one.

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
    // **ALT is not a typing modifier.** Termux's extra-keys `ALT` is a sticky
    // modifier that composes with soft-keyboard letters, and crossterm delivers
    // `alt+f` as `Char('f')` with `ALT` set — so without this guard, latching
    // ALT and typing quietly pollutes the search. Measured on the built binary
    // before it was fixed, not inferred.
    //
    // Nothing is bound to the alt tier yet. It is where R4's in-edit verbs go:
    // on a surface that is not search-as-you-type, bare letters are the primary
    // namespace, and `alt+s` mirrors `s` while a field is being typed into.
    let alt = key.modifiers.contains(KeyModifiers::ALT);
    match key.code {
        // `ctrl+c` always quits cleanly and is never bound over (REWRITE-UI.md §3).
        KeyCode::Char('c' | 'q') if ctrl => Some(Msg::Quit),
        KeyCode::Char('t') if ctrl => Some(Msg::ToggleScans),
        KeyCode::Char('x') if ctrl => Some(Msg::ToggleExpiring),
        KeyCode::Esc => Some(Msg::Esc),
        KeyCode::Enter => Some(Msg::Enter),
        KeyCode::Right => Some(Msg::OpenDetail),
        KeyCode::Left => Some(Msg::CloseDetail),
        KeyCode::Up => Some(Msg::Move(Motion::Up)),
        KeyCode::Down => Some(Msg::Move(Motion::Down)),
        KeyCode::PageUp => Some(Msg::Move(Motion::PageUp)),
        KeyCode::PageDown => Some(Msg::Move(Motion::PageDown)),
        KeyCode::Home => Some(Msg::Move(Motion::Home)),
        KeyCode::End => Some(Msg::Move(Motion::End)),
        KeyCode::Backspace => Some(Msg::Backspace),
        // Find-fast: every bare printable is search text. The modifier check is
        // what keeps `ctrl+t` from typing a `t` — and nothing else on this
        // surface may claim a letter.
        KeyCode::Char(c) if !ctrl && !alt => Some(Msg::Char(c)),
        _ => None,
    }
}

fn mouse_msg(mouse: MouseEvent) -> Option<Msg> {
    match mouse.kind {
        MouseEventKind::Down(MouseButton::Left) => {
            Some(Msg::Tap { col: mouse.column, row: mouse.row })
        }
        // The app owns scrolling: Termux's mouse mode blocks the terminal's own
        // scrollback (termux-app #4302), so if the list does not move the
        // finger, nothing does. Three rows a notch, matching v2's feel.
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

    /// **The surface binds no letter keys.** Every bare printable — including
    /// the ones that would be tempting hotkeys — becomes search text.
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

    /// **A modified letter is never search text.** `ctrl` was always guarded;
    /// `alt` was not, so latching Termux's `ALT` key and typing put the letters
    /// straight into the query. The alt tier is silent until something is bound
    /// to it, which is the honest default: a key that does nothing is better
    /// than a key that does the wrong thing.
    #[test]
    fn a_modified_letter_never_reaches_the_query() {
        for modifiers in [KeyModifiers::ALT, KeyModifiers::CONTROL | KeyModifiers::ALT] {
            for c in ['f', 's', 'b', 'z'] {
                assert_eq!(
                    to_msg(&press(KeyCode::Char(c), modifiers)),
                    None,
                    "{c} with {modifiers:?} is not search text"
                );
            }
        }
    }

    /// `ctrl+alt+x` is deliberately the same verb as `ctrl+x`.
    ///
    /// The guard asks whether CONTROL is present, not whether it is the *only*
    /// modifier — and it stays that way on purpose. Exact-modifier matching is
    /// how a binding breaks on a terminal that decorates keys with a modifier
    /// nobody asked for, and the two combinations have no reason to differ.
    #[test]
    fn a_bound_control_letter_ignores_extra_modifiers() {
        let both = KeyModifiers::CONTROL | KeyModifiers::ALT;
        assert_eq!(to_msg(&press(KeyCode::Char('x'), both)), Some(Msg::ToggleExpiring));
        assert_eq!(to_msg(&press(KeyCode::Char('q'), both)), Some(Msg::Quit));
    }

    /// Control combinations are verbs, and `ctrl+c` is always the exit.
    #[test]
    fn control_combinations_are_the_only_letter_verbs() {
        assert_eq!(to_msg(&press(KeyCode::Char('c'), KeyModifiers::CONTROL)), Some(Msg::Quit));
        assert_eq!(to_msg(&press(KeyCode::Char('q'), KeyModifiers::CONTROL)), Some(Msg::Quit));
        assert_eq!(
            to_msg(&press(KeyCode::Char('t'), KeyModifiers::CONTROL)),
            Some(Msg::ToggleScans)
        );
        assert_eq!(
            to_msg(&press(KeyCode::Char('x'), KeyModifiers::CONTROL)),
            Some(Msg::ToggleExpiring)
        );
    }

    /// **A key release is not a key press.** Terminals that negotiated the kitty
    /// protocol send both, and acting on both makes every keystroke count twice.
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

    /// **Nothing sits behind a function key** — Termux does not have them
    /// (R0.2's binding finding), so a function key must not be the only way to
    /// reach anything.
    #[test]
    fn function_keys_are_bound_to_nothing() {
        for n in 1..=12 {
            assert_eq!(to_msg(&press(KeyCode::F(n), KeyModifiers::NONE)), None);
        }
    }
}
