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

//! Geometry: the four numbers the whole responsive plan turns on, and the
//! display-width helpers every column depends on.
//!
//! REWRITE-UI.md §4 allows **exactly two layout states** — split-capable and
//! single-pane — plus a floor below which the app says so instead of drawing a
//! broken screen. Keeping those thresholds here, rather than inline in the
//! renderer, is what lets the update half of the loop reason about the same
//! viewport the view half drew (`PageDown` has to move by the number of rows
//! that actually fit).
//!
//! The width helpers exist because **terminal columns are not characters**: a
//! CJK name is two cells per character and an emoji can be two as well. Every
//! alignment decision in the plan says "never `len()`", and this is where that
//! promise is kept — R0.2 confirmed the phone's terminal agrees with the
//! `unicode-width` tables.

use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

/// Below this list width, rows go two-line (name + status, then location and
/// tags underneath). The user confirmed the two-line phone row in the R-UI
/// mockup review and the trade against density with it.
///
/// The phone reports **47×45** with the keyboard down and **47×24** with it up
/// (measured on the device; Termux resizes the terminal rather than covering
/// it). That is twenty-one two-line documents while browsing and ten while
/// typing — the review's "twelve at 45×28" was a mockup size, not a
/// measurement, and is superseded.
pub const NARROW_COLS: u16 = 70;

/// At or above this terminal width, an open detail splits beside the list
/// instead of covering it (U3).
pub const SPLIT_COLS: u16 = 100;

/// Below this the app refuses to draw (REWRITE-UI.md §4).
pub const FLOOR: (u16, u16) = (38, 12);

/// Whether the terminal is too small to render anything honest.
#[must_use]
pub fn too_small(cols: u16, rows: u16) -> bool {
    cols < FLOOR.0 || rows < FLOOR.1
}

/// Whether an open detail splits beside the list at this width.
#[must_use]
pub fn splits(cols: u16) -> bool {
    cols >= SPLIT_COLS
}

/// Screen lines one document row occupies.
///
/// Measured against the **terminal** width, not the list pane's. When detail
/// splits, the list pane is ~55 columns wide but the rows stay single-line —
/// that is what the approved split mockup shows, and it is right: the two-line
/// row exists for the phone, where there is no second pane to trade against.
#[must_use]
pub fn row_height(cols: u16) -> u16 {
    if cols < NARROW_COLS {
        2
    } else {
        1
    }
}

/// Whether this width gets the touch layout.
///
/// The single-pane layout — the phone, and also a narrow tmux split. A wide
/// desktop terminal has a keyboard and needs none of the touch affordances.
///
/// It used to decide whether an action bar was drawn. There is no action bar any
/// more: every verb it held is a key the thumb already has on Termux's own
/// extra-keys row, and the two it did not are reachable as `CTRL` then a letter.
/// What the touch layout still decides is the *shape* of the search bar — two
/// rows, so a thumb can hit it — and whether the touch affordances are drawn:
/// the pressable header count, and the `SPC` chip that opens the leader sheet.
#[must_use]
pub fn touch_layout(cols: u16) -> bool {
    !splits(cols)
}

/// Rows of chrome above and below the list: the header, and the search bar —
/// two rows of it on a touch layout, or one plus a hint line on a keyboard one.
///
/// **Three, either way.** They are spent differently but they cost the same,
/// which is why deleting the action bar bought a document row rather than a
/// different arrangement of the same ones.
#[must_use]
pub const fn chrome_rows(_cols: u16) -> u16 {
    3
}

/// How many document rows fit, given the whole terminal.
///
/// The update half calls this for `PageUp`/`PageDown` and for scroll clamping;
/// the view half lays out the same numbers with `Layout`. One function, so they
/// cannot disagree.
#[must_use]
pub fn visible_rows(cols: u16, rows: u16) -> usize {
    if too_small(cols, rows) {
        return 0;
    }
    let list_rows = rows.saturating_sub(chrome_rows(cols));
    (list_rows / row_height(cols)).max(1) as usize
}

/// The blank column at each end of the chrome, and between two cells.
///
/// One column, everywhere: the search field, the count row and the leader sheet
/// all start and end on it, which is what gives the block a left and a right
/// edge instead of one of each per row.
pub const GUTTER: u16 = 1;

/// Where a tiled row's cells begin and how wide they are: `(start, width)`.
///
/// The row is tiled as `n` equal cells with a one-column separator between them,
/// and the whole block is **centred**, so any remainder becomes margin split
/// evenly between the two ends rather than a stray column hanging off one side.
/// At 45 columns and three cells that is a 13-wide cell with two columns of
/// margin; at four it was 10 wide with one.
///
/// **The renderer and the hit test both call this.** They drew and read the same
/// row, so they must agree about where it is; the alternative is a tap near a
/// boundary landing on the neighbour.
#[must_use]
pub fn cells(cols: u16, n: u16) -> Vec<(u16, u16)> {
    if n == 0 {
        return Vec::new();
    }
    let inner = cols.saturating_sub(2 * GUTTER);
    let cell = (inner.saturating_sub((n - 1) * GUTTER) / n).max(1);
    let block = n * cell + (n - 1) * GUTTER;
    let left = (cols.saturating_sub(block)) / 2;
    (0..n).map(|i| (left + i * (cell + GUTTER), cell)).collect()
}

/// Which cell a column falls in, for a tap.
///
/// A tap in the separator between two cells belongs to the one on its left: a
/// thumb landing in a one-column gap meant a button, and the one it was leaving
/// is a better answer than none. A tap in the **margin** at either end is not a
/// button at all — that is the edge of the row, and guessing there would be
/// guessing.
#[must_use]
pub fn cell_at(cols: u16, n: u16, col: u16) -> Option<usize> {
    let cells = cells(cols, n);
    let (first, cell) = *cells.first()?;
    let (last, _) = *cells.last()?;
    if col < first || col >= last + cell {
        return None;
    }
    Some(((col - first) / (cell + GUTTER)) as usize)
}

/// Display width in terminal cells.
#[must_use]
pub fn width(text: &str) -> usize {
    text.width()
}

/// Truncate to a display width, appending `…` when it had to cut.
///
/// rust: takes `&str` and returns `String` — borrow what is only read, hand back
/// ownership of what had to be built. The walk accumulates *column* widths, not
/// bytes and not characters, which is the entire point.
#[must_use]
pub fn truncate(text: &str, max: usize) -> String {
    if text.width() <= max {
        return text.to_string();
    }
    if max <= 1 {
        return "…".into();
    }
    let mut out = String::new();
    let mut used = 0usize;
    for ch in text.chars() {
        let w = ch.width().unwrap_or(0);
        if used + w > max - 1 {
            break;
        }
        out.push(ch);
        used += w;
    }
    out.push('…');
    out
}

/// Truncate, then pad on the right, so the result is exactly `cols` wide.
#[must_use]
pub fn fit(text: &str, cols: usize) -> String {
    let cut = truncate(text, cols);
    let pad = cols.saturating_sub(cut.width());
    format!("{cut}{}", " ".repeat(pad))
}

/// Centre in `cols`, with the odd column going to the right — so a label one
/// column short of its cell leans left, the direction text is read from.
#[must_use]
pub fn centre(text: &str, cols: usize) -> String {
    let cut = truncate(text, cols);
    let slack = cols.saturating_sub(cut.width());
    format!("{}{cut}{}", " ".repeat(slack / 2), " ".repeat(slack - slack / 2))
}

/// Pad on the left so the text ends at column `cols`.
#[must_use]
pub fn pad_left(text: &str, cols: usize) -> String {
    let cut = truncate(text, cols);
    let pad = cols.saturating_sub(cut.width());
    format!("{}{cut}", " ".repeat(pad))
}

/// Break `text` into lines of at most `cols` display columns, at word
/// boundaries where there is one.
///
/// The renderer wraps free text itself rather than handing it to a widget,
/// because a wrapped value has to line up **under its own column** — a
/// continuation line that starts at the left margin reads as a new field. A word
/// longer than the whole width is cut rather than allowed to overflow.
#[must_use]
pub fn wrap(text: &str, cols: usize) -> Vec<String> {
    if cols == 0 {
        return Vec::new();
    }
    let mut lines = Vec::new();
    let mut current = String::new();
    for word in text.split_whitespace() {
        let candidate =
            if current.is_empty() { word.width() } else { current.width() + 1 + word.width() };
        if candidate <= cols {
            if !current.is_empty() {
                current.push(' ');
            }
            current.push_str(word);
            continue;
        }
        if !current.is_empty() {
            lines.push(std::mem::take(&mut current));
        }
        if word.width() <= cols {
            current.push_str(word);
        } else {
            lines.push(truncate(word, cols));
        }
    }
    if !current.is_empty() {
        lines.push(current);
    }
    lines
}

/// `2026-09-28` → `09-26`: the compact expiry the row shows.
///
/// Month and year, because the day is never what makes someone act — the month
/// is. Anything that is not an ISO date passes through untouched rather than
/// being mangled into one.
#[must_use]
pub fn short_date(iso: &str) -> String {
    let bytes = iso.as_bytes();
    if bytes.len() == 10 && bytes[4] == b'-' && bytes[7] == b'-' {
        format!("{}-{}", &iso[5..7], &iso[2..4])
    } else {
        iso.to_string()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// **Two states, no ladder.** The phone and a 60-column tmux split get the
    /// same single-pane layout; the split appears only when there is really room
    /// for two panes.
    #[test]
    fn there_are_exactly_two_layout_states() {
        assert!(!splits(45) && !splits(80) && !splits(99));
        assert!(splits(100) && splits(180));
        assert_eq!(row_height(45), 2, "the phone gets two-line rows");
        assert_eq!(row_height(80), 1);
    }

    /// The floor is a refusal, not a glitch.
    #[test]
    fn the_floor_is_checked_on_both_axes() {
        assert!(too_small(37, 40));
        assert!(too_small(80, 11));
        assert!(!too_small(38, 12), "the floor itself is allowed");
        assert_eq!(visible_rows(20, 8), 0, "nothing is drawn below the floor");
    }

    /// Both halves of the loop count rows the same way, chrome included.
    #[test]
    fn visible_rows_accounts_for_chrome() {
        // The measured phone, keyboard down: 47×45 − 3 chrome = 42, two lines a
        // row = 21 docs. With the retired action bar it was 41 rows and 20.
        assert_eq!(visible_rows(47, 45), 21, "browsing, as the device reports it");
        // The same phone with the keyboard up — Termux resizes rather than
        // covering, so this is a shorter layout and not a hidden one.
        assert_eq!(visible_rows(47, 24), 10, "typing, as the device reports it");
        // 45×28, the mockup size the older R-UI pages are drawn at.
        assert_eq!(visible_rows(45, 28), 12);
        // 100×26 desktop: 26 − 3 chrome = 23 single-line rows.
        assert_eq!(visible_rows(100, 26), 23);
    }

    /// **Widths are cells, never characters.** A CJK name is two cells per
    /// character, and a column that used `len()` would run straight through its
    /// neighbour.
    #[test]
    fn truncation_counts_cells_not_characters() {
        assert_eq!(width("護照"), 4, "two characters, four cells");
        assert_eq!(width(&truncate("護照護照", 5)), 5, "cut lands on a cell boundary");
        assert_eq!(truncate("Passport", 20), "Passport", "short enough is untouched");
        assert_eq!(truncate("Passport", 5), "Pass…");
        assert_eq!(width(&fit("護照", 10)), 10);
        assert_eq!(width(&pad_left("8", 4)), 4);
        assert_eq!(centre("ab", 6), "  ab  ");
        assert_eq!(centre("abc", 6), " abc  ", "the odd column goes right");
        assert_eq!(width(&centre("護照", 7)), 7);
    }

    /// **The tiling has one owner.** The renderer and the hit test read the same
    /// cells, so a tap can never land a button away from what was drawn.
    #[test]
    fn the_action_bar_tiles_evenly_and_centres_the_remainder() {
        // Three cells on the phone: 13 wide, two columns of margin each side.
        let cells = cells(45, 3);
        assert_eq!(cells, [(2, 13), (16, 13), (30, 13)]);
        let (last_start, last_width) = *cells.last().unwrap();
        assert_eq!(45 - (last_start + last_width), 2, "margins match at both ends");

        // Every cell start reads back as its own cell, and so does its last
        // column — the two places a rounding mistake shows up first.
        for (i, (start, w)) in cells.iter().enumerate() {
            assert_eq!(cell_at(45, 3, *start), Some(i), "start of cell {i}");
            assert_eq!(cell_at(45, 3, start + w - 1), Some(i), "end of cell {i}");
        }
        assert_eq!(cell_at(45, 3, 15), Some(0), "a separator goes to the cell it left");
        assert_eq!(cell_at(45, 3, 1), None, "the margin is not a button");
        assert_eq!(cell_at(45, 3, 44), None, "and neither is the far edge");
    }

    /// The same arithmetic at the floor and at the widest touch layout: cells
    /// stay equal, and the row never runs off the end.
    #[test]
    fn the_tiling_holds_at_every_touch_width() {
        for cols in FLOOR.0..SPLIT_COLS {
            let cells = cells(cols, 3);
            assert_eq!(cells.len(), 3);
            let widths: Vec<u16> = cells.iter().map(|(_, w)| *w).collect();
            assert!(widths.windows(2).all(|p| p[0] == p[1]), "equal cells at {cols}");
            let (first, _) = *cells.first().unwrap();
            let (start, w) = *cells.last().unwrap();
            assert!(start + w <= cols, "the row fits at {cols}: {start}+{w}");
            assert!(
                first.abs_diff(cols - (start + w)) <= 1,
                "margins match within a column at {cols}"
            );
        }
    }

    /// **Wrapping happens here, not in a widget**, so a continuation line can be
    /// indented under its own column instead of starting at the left margin —
    /// where it would read as a new field.
    #[test]
    fn wrapping_breaks_on_words_and_respects_cells() {
        let lines = wrap("Revalidation booked at MMD, slot 14 Oct. Bring originals", 20);
        assert!(lines.iter().all(|line| width(line) <= 20), "{lines:?}");
        assert_eq!(lines[0], "Revalidation booked");
        assert!(lines.join(" ").contains("Bring originals"), "nothing is lost: {lines:?}");

        let long = wrap("supercalifragilistic", 8);
        assert_eq!(long, ["superca…"], "a word wider than the pane is cut, never overflowed");
        assert!(wrap("", 10).is_empty());
    }

    /// The compact date is month-year, and a non-date is left alone.
    #[test]
    fn short_dates_keep_the_month_and_year() {
        assert_eq!(short_date("2026-09-28"), "09-26");
        assert_eq!(short_date("unknown"), "unknown");
        assert_eq!(short_date("2026-09"), "2026-09");
    }
}
