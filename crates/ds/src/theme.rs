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

    /// The lit rule between the list and the entry line.
    ///
    /// **This is a status line, not a field marking.** It carries the count,
    /// the live filters and the hints, and it sits directly above the row the
    /// user types into — which is Vim's arrangement exactly: `StatusLine`
    /// highlighted, `:` on the plain final line beneath it. The entry row keeps
    /// the terminal's own background, so nothing is drawn behind the user's own
    /// text and nothing has to fight it for contrast.
    ///
    /// It began as a marking *on* the field, which was worse in three ways at
    /// once: dim placeholder text over a lit row is the least legible thing on
    /// the screen, the band had to pin a foreground and so replaced the
    /// terminal's own, and the list still ran into the chrome with no boundary.
    /// Dividing is the job a band can do without fighting anything.
    ///
    /// **Both ends are pinned** — ANSI 7 behind, ANSI 0 in front (ratatui's
    /// `Gray` and `Black` are SGR 47 and 30; note that `White` is SGR 97, the
    /// *bright* slot, which the names do not say). Setting only a background
    /// would be a coin flip on polarity, so the pair is named — and §6's promise
    /// survives it, because the slots are ANSI and the user's own theme still
    /// chooses the two hues.
    ///
    /// It was ANSI 8 on 15 first, a mid grey carrying dim white text, and that
    /// was two greys too close together. ANSI 0 was tried on the device and is
    /// indistinguishable from the terminal background there. ANSI 15 has the
    /// most contrast of all and is unusable for a subtler reason: reverse video
    /// on a black terminal *is* ANSI 15 on ANSI 0, so the bar would be
    /// indistinguishable from the selected row.
    ///
    /// **This tunes the band to a dark terminal.** A light theme puts a
    /// near-white bar on a near-white background, which is the mirror of what
    /// ANSI 0 does on a dark one. There is no pair that works equally well both
    /// ways; the band is tuned to one polarity and survives the other.
    ///
    /// Under `NO_COLOR` there is no band, and that costs only a divider rather
    /// than the one thing marking the field.
    #[must_use]
    pub fn band(self) -> Style {
        if self.color {
            Style::default().bg(Color::Gray).fg(Color::Black)
        } else {
            Style::default()
        }
    }

    /// A tone as it should be drawn **on the band**.
    ///
    /// The band is a light surface inside a dark one, so the tones that work
    /// against the terminal's own background do not work against it: `Muted` is
    /// `DIM`, whose rendering is a terminal's own business and which some
    /// ignore outright, and `Armed` is yellow, which on a near-white row is
    /// barely there.
    ///
    /// So the quiet parts of the band are a named grey rather than a dimmed
    /// black, and a message that matters is red — the colour this app already
    /// uses for *expired*, and one of the few with real contrast on light.
    /// §6 still holds: the words say it, and the colour agrees with them.
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

    /// **The band must not look like the selection.** Reverse video on a black
    /// terminal *is* ANSI 15 on ANSI 0, so a bright-white band would be
    /// indistinguishable from the selected row — the reason the brightest pair
    /// was rejected rather than the reason it was never tried.
    #[test]
    fn the_band_is_not_the_selection() {
        let theme = Theme { color: true };
        let band = theme.band();
        assert_eq!(band.bg, Some(Color::Gray), "ANSI 7 behind");
        assert_eq!(band.fg, Some(Color::Black), "ANSI 0 in front");
        assert!(band.add_modifier.is_empty(), "a colour pair, never reverse video");
        assert!(
            theme.selected().add_modifier.contains(Modifier::REVERSED),
            "which is what the selection is, and they must not converge"
        );
    }

    /// **Tones on the band are not the tones off it.** The band is a light
    /// surface inside a dark one: `DIM` is a terminal's own business and some
    /// ignore it outright, and yellow on near-white is barely there.
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
