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

//! Semantic colour tokens, and the promise that colour never carries meaning
//! alone.
//!
//! REWRITE-UI.md §6: the renderer names *what a thing is* ([`Tone::Expired`]),
//! never what colour it should be, and the mapping to terminal colours happens
//! once, here. Two things fall out of that:
//!
//! * **The user's terminal theme carries the palette.** Everything maps to the
//!   sixteen ANSI colours rather than to RGB, so a Termux colour scheme, a
//!   Solarized tmux and a Windows Terminal profile each render this app in their
//!   own colours instead of fighting it.
//! * **`NO_COLOR` is a supported way to run**, not a degraded one. With colour
//!   off every tone still differs by weight or reverse video, and the glyph
//!   markers (`!`, `~`, `·`) carry the status signal on their own — which is
//!   also what makes the app legible on a phone in sunlight.

use ratatui::style::{Color, Modifier, Style};

/// The ANSI colour a signalling tone maps to, so a terminal theme carries it.
fn hue(tone: Tone) -> Color {
    match tone {
        Tone::Expired => Color::Red,
        Tone::Soon | Tone::Armed => Color::Yellow,
        _ => Color::Cyan,
    }
}

/// The same signal without colour. `Armed` reverses as well as bolds because it
/// is the one state where the *next* keypress does something irreversible.
fn emphasis(tone: Tone) -> Modifier {
    match tone {
        Tone::Armed => Modifier::BOLD | Modifier::REVERSED,
        _ => Modifier::BOLD,
    }
}

/// What a piece of text *means*. The renderer picks one of these; nothing in the
/// renderer picks a colour.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tone {
    /// Surface titles and the header's app name.
    Title,
    /// Secondary text: locations, tags, counts, hints.
    Muted,
    /// The one thing on screen the eye should land on — a prompt, a link, a
    /// count that names a command.
    Accent,
    /// Past its expiry date and still in use.
    Expired,
    /// Inside the warn window.
    Soon,
    /// Tracked and healthy.
    Ok,
    /// Not in the expiry watch at all.
    Untracked,
    /// A transient message: what `Enter` just opened, why it could not.
    Flash,
    /// A state the next keypress will act on — the armed quit.
    Armed,
}

/// The palette, resolved once at startup.
///
/// rust: a plain `Copy` struct rather than a trait object. There is exactly one
/// implementation and there will be exactly one; a trait here would buy
/// indirection and nothing else.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Theme {
    /// Whether colour may be emitted at all.
    pub color: bool,
}

impl Default for Theme {
    fn default() -> Self {
        Self { color: true }
    }
}

impl Theme {
    /// Honour `NO_COLOR` (any non-empty value), per <https://no-color.org>.
    #[must_use]
    pub fn from_env() -> Self {
        let disabled = std::env::var_os("NO_COLOR").is_some_and(|value| !value.is_empty());
        Self { color: !disabled }
    }

    /// The style for a tone.
    ///
    /// The monochrome branch is not a fallback bolted on afterwards — it is
    /// written first and checked by test, because it is what the ASCII markers
    /// have to survive alongside.
    #[must_use]
    pub fn style(self, tone: Tone) -> Style {
        let plain = Style::default();
        match tone {
            Tone::Title => plain.add_modifier(Modifier::BOLD),
            Tone::Muted | Tone::Untracked => plain.add_modifier(Modifier::DIM),
            Tone::Ok => plain,
            // The tones that carry a signal. Each has a colour *and* a
            // monochrome equivalent, written as a pair so neither can be added
            // later without the other.
            Tone::Accent | Tone::Expired | Tone::Soon | Tone::Flash | Tone::Armed => {
                if self.color {
                    plain.fg(hue(tone))
                } else {
                    plain.add_modifier(emphasis(tone))
                }
            }
        }
    }

    /// The band behind the query row: **the field is a lit strip, not a line
    /// under one.**
    ///
    /// A terminal draws `SGR 4` wherever the font's underline metric says, and
    /// on the phone that is through the descenders rather than below them —
    /// nothing here can move it. Emacs marks an editable field the same way this
    /// does: `widget-field` is a *background* face, not a rule.
    ///
    /// **Both ends are pinned.** Setting only a background would be a coin flip
    /// on polarity — ANSI 8 is a light grey on a dark theme and a near-black on
    /// a light one, so a background-only band is unreadable on half of them.
    /// Naming the pair keeps §6's promise intact: the slots are ANSI, so the
    /// user's own theme still chooses the two hues.
    ///
    /// Under `NO_COLOR` there is no band. That is the honest cost of this
    /// texture and the reason the prompt has to name the question on its own —
    /// a marking is decoration over a prompt that already works, never the only
    /// thing saying what the row is for.
    #[must_use]
    pub fn field(self) -> Style {
        if self.color {
            Style::default().bg(Color::DarkGray).fg(Color::White)
        } else {
            Style::default()
        }
    }

    /// The selection style: reverse video, never an indent shift.
    ///
    /// v2's rule, kept: shifting the row by a column as the cursor moves makes
    /// the whole list twitch, and on a phone that reads as the app being slow
    /// even when it is not.
    #[must_use]
    pub fn selected(self) -> Style {
        Style::default().add_modifier(Modifier::REVERSED)
    }

    /// The style for a document's expiry standing.
    #[must_use]
    pub fn status(self, status: crate::Status) -> Style {
        self.style(match status {
            crate::Status::Expired => Tone::Expired,
            crate::Status::Soon => Tone::Soon,
            crate::Status::Ok => Tone::Ok,
            crate::Status::Untracked => Tone::Untracked,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// **Colour is never the only signal.** With colour off, every tone that
    /// means something still differs from plain text by weight or reverse video
    /// — the property that keeps a `NO_COLOR` or monochrome terminal usable.
    #[test]
    fn every_meaningful_tone_survives_monochrome() {
        let mono = Theme { color: false };
        for tone in [Tone::Expired, Tone::Soon, Tone::Accent, Tone::Flash, Tone::Armed, Tone::Title]
        {
            let style = mono.style(tone);
            assert_eq!(style.fg, None, "{tone:?} must not emit colour under NO_COLOR");
            assert!(!style.add_modifier.is_empty(), "{tone:?} must still stand out");
        }
    }

    /// In colour, the three attention states are three different colours — and
    /// each is still paired with its glyph by the renderer.
    #[test]
    fn attention_states_differ_in_colour() {
        let theme = Theme { color: true };
        let expired = theme.status(crate::Status::Expired).fg;
        let soon = theme.status(crate::Status::Soon).fg;
        assert!(expired.is_some() && soon.is_some());
        assert_ne!(expired, soon);
        assert_eq!(theme.status(crate::Status::Ok).fg, None, "healthy is not coloured at all");
    }
}
