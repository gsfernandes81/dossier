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

//! Semantic colour tokens: the renderer names what a thing is, and the
//! mapping to the sixteen ANSI colours happens once, here, so the terminal's
//! own theme carries the palette. Under `NO_COLOR` every tone still differs by
//! weight or reverse video, and the glyph markers carry status on their own.

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

    /// The lit status line between the list and the entry line.
    ///
    /// Both ends are pinned, ANSI 7 behind and ANSI 0 in front (`Gray` and
    /// `Black`; `White` is the bright slot), because a background alone is a
    /// coin flip on polarity. Not ANSI 15: reverse video on a black terminal is
    /// 15 on 0, so the band would look like the selected row. Tuned to dark
    /// terminals; under `NO_COLOR` there is no band.
    #[must_use]
    pub fn band(self) -> Style {
        if self.color {
            Style::default().bg(Color::Gray).fg(Color::Black)
        } else {
            Style::default()
        }
    }

    /// A tone as it should be drawn on the band, a light row where `DIM` may
    /// be ignored and yellow barely shows: grey for quiet, red for a message.
    #[must_use]
    pub fn on_band(self, tone: Tone) -> Style {
        if !self.color {
            return self.style(tone);
        }
        match tone {
            Tone::Muted | Tone::Untracked => Style::default().fg(Color::DarkGray),
            Tone::Flash | Tone::Armed | Tone::Expired => {
                Style::default().fg(Color::Red).add_modifier(Modifier::BOLD)
            }
            _ => Style::default(),
        }
    }

    /// The selection style: an ANSI 8 background, or reverse video without colour.
    ///
    /// A background rather than reverse video so every span keeps its own
    /// colour on the selected row; reverse turns a cyan word into a cyan block.
    /// Never an indent shift: a row that moves as the cursor does makes the list
    /// twitch.
    #[must_use]
    pub fn selected(self) -> Style {
        if self.color {
            Style::default().bg(Color::DarkGray)
        } else {
            Style::default().add_modifier(Modifier::REVERSED)
        }
    }

    /// The style of something a tap presses: reverse video.
    #[must_use]
    pub fn pressable(self) -> Style {
        Style::default().add_modifier(Modifier::REVERSED)
    }

    /// Something pressable that is on: the sheet's chip while the sheet is up,
    /// the expiring count while its filter is. Marked more than
    /// [`Theme::pressable`] in either mode, so on never reads as off.
    #[must_use]
    pub fn lit(self) -> Style {
        if self.color {
            self.style(Tone::Armed).add_modifier(Modifier::REVERSED)
        } else {
            Style::default().add_modifier(Modifier::REVERSED | Modifier::BOLD)
        }
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

    #[test]
    fn the_band_is_not_the_selection() {
        let theme = Theme { color: true };
        let band = theme.band();
        assert_eq!(band.bg, Some(Color::Gray), "ANSI 7 behind");
        assert_eq!(band.fg, Some(Color::Black), "ANSI 0 in front");
        assert!(band.add_modifier.is_empty(), "a colour pair, never reverse video");
        assert_eq!(theme.selected().bg, Some(Color::DarkGray), "the selection is ANSI 8");
        assert!(
            Theme { color: false }.selected().add_modifier.contains(Modifier::REVERSED),
            "and reverse video without colour, where there is no band"
        );
    }

    #[test]
    fn the_band_restyles_the_tones_that_would_vanish_on_it() {
        let theme = Theme { color: true };
        let quiet = theme.on_band(Tone::Muted);
        assert_eq!(quiet.fg, Some(Color::DarkGray), "a named grey, not a dimmed black");
        assert!(!quiet.add_modifier.contains(Modifier::DIM), "nothing rests on SGR 2 here");

        let armed = theme.on_band(Tone::Armed);
        assert_eq!(armed.fg, Some(Color::Red), "red reads on light; yellow does not");
        assert_ne!(armed.fg, theme.style(Tone::Armed).fg, "deliberately not the off-band hue");

        // With colour off there is no band, so there is nothing to restyle for.
        let mono = Theme { color: false };
        assert_eq!(mono.on_band(Tone::Armed), mono.style(Tone::Armed));
    }

    #[test]
    fn attention_states_differ_in_colour() {
        let theme = Theme { color: true };
        let expired = theme.status(crate::Status::Expired).fg;
        let soon = theme.status(crate::Status::Soon).fg;
        assert!(expired.is_some() && soon.is_some());
        assert_ne!(expired, soon);
        assert_eq!(theme.status(crate::Status::Ok).fg, None, "healthy is not coloured at all");
    }

    /// A lit toggle must never read as an unlit one.
    #[test]
    fn lit_is_more_than_pressable() {
        let mono = Theme { color: false };
        let off = mono.pressable().add_modifier;
        let on = mono.lit().add_modifier;
        assert!(on.contains(off) && on != off, "{on:?} over {off:?}");
        let colour = Theme { color: true };
        assert!(colour.lit().fg.is_some(), "lit carries a colour as well as reverse video");
        assert!(colour.lit().add_modifier.contains(Modifier::REVERSED));
    }
}
