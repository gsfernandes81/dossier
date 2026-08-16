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

//! The view half of the loop: the Find surface.
//!
//! REWRITE-UI.md §1 — *"the app is a finder that happens to have management
//! surfaces behind it"*. One full-width list, no location headers (U2), the
//! search bar docked at the **bottom** where a thumb reaches it, and the counts
//! that matter in the header. The approved mockups in `docs/dev/mockups/` are
//! the reference this is measured against, down to the column counts.
//!
//! Two properties are load-bearing:
//!
//! 1. **The list is virtualized by hand.** Only rows that fit on screen are
//!    built. Handing a widget 948 pre-built rows and letting it slice would make
//!    frame time scale with the store rather than the viewport — precisely the
//!    property the rewrite exists to avoid.
//! 2. **Every column is measured in cells, never characters** ([`crate::layout`]).
//!
//! The renderer decides nothing: it reads [`Model`] and writes back only the
//! geometry it drew, so taps hit-test against the layout that is really on
//! screen.

use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;

use crate::app::{Filter, ListGeometry, Model, ScanSearch};
use crate::layout::{fit, pad_left, short_date, truncate, width};
use crate::theme::{Theme, Tone};
use crate::{Doc, Status};

/// The status cell is a fixed seven columns — `! 09-26` — so the marker lands on
/// the same screen column in every row, which is what makes a list of dates
/// scannable at a glance.
const STATUS_COLS: usize = 7;

/// Above this width a single-line row has room for a tags column as well.
const TAGS_COLS: u16 = 90;

/// Draw one frame of the Find surface.
///
/// rust: `&mut Model` for the one write-back described in the module header —
/// the row rectangle. Everything else here only reads.
pub fn draw(frame: &mut Frame, model: &mut Model, theme: Theme) {
    let area = frame.area();
    model.cols = area.width;
    model.rows_on_screen = area.height;

    if crate::layout::too_small(area.width, area.height) {
        model.list = ListGeometry::default();
        draw_too_small(frame, area, theme);
        return;
    }

    // Touch and keyboard get the same four rows of chrome, spent differently.
    // With the action bar showing this surface's verbs as buttons, a separate
    // hint line would only repeat them — so that row goes to the search bar
    // instead, which doubles the size of the one target a thumb must hit.
    let touch = crate::layout::touch_bar(area.width);
    let constraints = if touch {
        vec![
            Constraint::Length(1),
            Constraint::Min(1),
            Constraint::Length(1),
            Constraint::Length(2),
        ]
    } else {
        vec![
            Constraint::Length(1),
            Constraint::Min(1),
            Constraint::Length(1),
            Constraint::Length(1),
        ]
    };
    let chunks =
        Layout::default().direction(Direction::Vertical).constraints(constraints).split(area);

    draw_header(frame, chunks[0], model, theme);
    draw_body(frame, chunks[1], model, theme);
    if touch {
        draw_action_bar(frame, chunks[2], model, theme);
        draw_search(frame, chunks[3], model, theme);
    } else {
        draw_search(frame, chunks[2], model, theme);
        draw_footer(frame, chunks[3], model, theme);
    }
}

/// Below the floor, say so. A layout that renders half a row and clips the rest
/// looks like a crash; this looks like an instruction.
fn draw_too_small(frame: &mut Frame, area: Rect, theme: Theme) {
    let (cols, rows) = crate::layout::FLOOR;
    let notice = Paragraph::new(vec![
        Line::styled("terminal too small", theme.style(Tone::Title)),
        Line::styled(
            format!("need ≥ {cols}×{rows}, have {}×{}", area.width, area.height),
            theme.style(Tone::Muted),
        ),
    ])
    .wrap(Wrap { trim: true });
    frame.render_widget(notice, area);
}

/// Title on the left, attention counts on the right.
///
/// Both halves shed detail as the terminal narrows — found the hard way in a
/// 45-column run during R0.2, where a full-width header ran straight through the
/// count and the terminal clipped it mid-word. At phone width the counts are the
/// only thing worth keeping, so the title goes first.
fn draw_header(frame: &mut Frame, area: Rect, model: &Model, theme: Theme) {
    let wide = area.width >= 72;
    let attention = model.attention_count();
    let total = model.store.docs.len();
    let left = " dossier";
    let right = if wide {
        format!("! {attention} expiring · {total} docs ")
    } else {
        format!("! {attention} exp · {total} docs ")
    };
    let right = truncate(&right, (area.width as usize).saturating_sub(width(left) + 1));
    let gap = (area.width as usize).saturating_sub(width(left) + width(&right));
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled(left, theme.style(Tone::Title)),
            Span::raw(" ".repeat(gap)),
            // The count names a command (`:expiring`), so it is an accent, not
            // decoration — REWRITE-UI.md §1.
            Span::styled(right, theme.style(Tone::Accent)),
        ])),
        area,
    );
}

/// The list, and the detail pane beside or instead of it (U3).
fn draw_body(frame: &mut Frame, area: Rect, model: &mut Model, theme: Theme) {
    let (list_area, detail_area) = match (model.detail, crate::layout::splits(area.width)) {
        (true, true) => {
            let split = Layout::default()
                .direction(Direction::Horizontal)
                .constraints([Constraint::Percentage(55), Constraint::Percentage(45)])
                .split(area);
            (Some(split[0]), Some(split[1]))
        }
        // Narrow: the record is a full-screen push, and `Esc` pops back with the
        // cursor where it was.
        (true, false) => (None, Some(area)),
        (false, _) => (Some(area), None),
    };

    match list_area {
        Some(list_area) => draw_list(frame, list_area, model, theme),
        None => model.list = ListGeometry::default(),
    }
    if let Some(detail_area) = detail_area {
        crate::detail::draw(frame, detail_area, model, theme);
    }
}

fn draw_list(frame: &mut Frame, area: Rect, model: &mut Model, theme: Theme) {
    let row_height = crate::layout::row_height(model.cols);
    let visible = (area.height / row_height).max(1) as usize;
    model.scroll_into_view(visible);
    model.list = ListGeometry { top: area.y, height: area.height, row_height };

    if model.rows.is_empty() {
        let message = if model.query.is_empty() {
            "  no documents yet — `ds init` and the filing surface fill this"
        } else {
            "  nothing matches"
        };
        frame.render_widget(Paragraph::new(Line::styled(message, theme.style(Tone::Muted))), area);
        return;
    }

    // *** The virtualization: only the visible window is built. ***
    let mut lines: Vec<Line> = Vec::with_capacity(visible * row_height as usize);
    for slot in 0..visible {
        let Some(&index) = model.rows.get(model.offset + slot) else { break };
        let doc = &model.store.docs[index];
        let selected = model.offset + slot == model.cursor;
        let status = model.status(doc);
        if row_height == 1 {
            lines.push(single_line_row(doc, status, area.width, selected, theme));
        } else {
            let (first, second) = two_line_row(doc, status, area.width, selected, theme);
            lines.push(first);
            lines.push(second);
        }
    }
    frame.render_widget(Paragraph::new(lines), area);
}

/// `! 09-26` — marker and month-year, always seven columns.
fn status_cell(doc: &Doc, status: Status) -> String {
    match (status, doc.expiry_date.as_deref()) {
        (Status::Untracked, _) | (_, None) => "   ·   ".to_string(),
        (_, Some(iso)) => format!("{} {}", status.marker(), fit(&short_date(iso), 5)),
    }
}

/// The cursor column. Selection is reverse video and the marker never shifts the
/// row: an indent shift makes the whole list twitch as the cursor moves.
fn cursor_cell(selected: bool) -> &'static str {
    if selected {
        "▸ "
    } else {
        "  "
    }
}

/// Wide layout: name, tags, location, status — each in a fixed column.
fn single_line_row(
    doc: &Doc,
    status: Status,
    cols: u16,
    selected: bool,
    theme: Theme,
) -> Line<'static> {
    let total = cols as usize;
    let (tags_cols, place_cols) =
        if cols >= TAGS_COLS { (20usize, 18usize) } else { (0usize, 12usize) };
    let name_cols = total.saturating_sub(2 + tags_cols + place_cols + 3 + STATUS_COLS).max(8);

    let mut spans = vec![Span::raw(cursor_cell(selected)), Span::raw(fit(&doc.name, name_cols))];
    if tags_cols > 0 {
        spans.push(Span::styled(fit(&doc.tags.join(" "), tags_cols), theme.style(Tone::Muted)));
    }
    spans.push(Span::styled(pad_left(&doc.place(), place_cols), theme.style(Tone::Muted)));
    spans.push(Span::raw("  "));
    spans.push(Span::styled(status_cell(doc, status), theme.status(status)));

    let line = Line::from(spans);
    if selected {
        line.style(theme.selected())
    } else {
        line
    }
}

/// Narrow layout: name and status, then location and tags underneath.
///
/// The user confirmed this in the mockup review — twelve documents visible at
/// 45×28, the trade against density accepted.
fn two_line_row(
    doc: &Doc,
    status: Status,
    cols: u16,
    selected: bool,
    theme: Theme,
) -> (Line<'static>, Line<'static>) {
    let total = cols as usize;
    let name_cols = total.saturating_sub(2 + STATUS_COLS + 1).max(8);
    let mut first = Line::from(vec![
        Span::raw(cursor_cell(selected)),
        Span::raw(fit(&doc.name, name_cols)),
        Span::styled(status_cell(doc, status), theme.status(status)),
    ]);

    let place = doc.place();
    let tags = doc.tags.join(" ");
    let under = match (place.is_empty(), tags.is_empty()) {
        (true, true) => String::new(),
        (false, true) => place,
        (true, false) => tags,
        (false, false) => format!("{place} · {tags}"),
    };
    let mut second = Line::styled(
        format!("    {}", fit(&under, total.saturating_sub(4))),
        theme.style(Tone::Muted),
    );

    if selected {
        first = first.style(theme.selected());
        second = second.style(theme.selected());
    }
    (first, second)
}

/// The one row of chrome touch gets (REWRITE-UI.md §5), in four equal quarters
/// so the hit test needs no per-label geometry.
fn draw_action_bar(frame: &mut Frame, area: Rect, model: &Model, theme: Theme) {
    let quarter = (area.width as usize / 4).max(1);
    let detail = if model.detail { "← Back" } else { "→ Detail" };
    let scan_label = if model.scan_search == ScanSearch::Off { "^t Scans" } else { "^t Scans•" };
    // Short enough that a quarter of a 45-column phone screen holds the whole
    // label — a truncated button is a button nobody trusts. The last two are the
    // verbs whose keys are modifier combinations, which is what a thumb most
    // needs a button for; the keyboard affordance moved to the search bar.
    let labels = ["⏎ Open", detail, "^x Expiry", scan_label];
    let tone = Tone::Muted;
    let spans: Vec<Span> = labels
        .iter()
        .map(|label| Span::styled(fit(&format!(" {label}"), quarter), theme.style(tone)))
        .collect();
    frame.render_widget(Paragraph::new(Line::from(spans)), area);
}

/// The filter chips: what is narrowing the list beyond the query itself.
fn chips(model: &Model) -> String {
    let mut chips = String::new();
    if model.filter == Filter::Expiring {
        chips.push_str("  [expiring]");
    }
    match model.scan_search {
        // The chip says which of the two searches is running, and admits when it
        // is still waiting — a query that quietly ignores `ctrl+t` for two
        // seconds reads as the toggle not working.
        ScanSearch::On => chips.push_str("  [scans]"),
        ScanSearch::Loading => chips.push_str("  [scans…]"),
        ScanSearch::Off => {}
    }
    chips
}

/// The docked search bar.
///
/// **One row on a keyboard layout, two on a touch one.** The second row is not
/// decoration: the whole block is the keyboard target, and one terminal row is
/// too small a thing to ask a thumb to hit between the action bar above it and
/// the screen edge below. Doubling it costs nothing, because the row it takes is
/// the hint line the action bar had already made redundant.
fn draw_search(frame: &mut Frame, area: Rect, model: &Model, theme: Theme) {
    let count = format!("{}/{}", model.rows.len(), model.store.docs.len());
    let cols = area.width as usize;
    let touch = area.height > 1;

    if !touch {
        let body = truncate(
            &format!("{}_{}", model.query, chips(model)),
            cols.saturating_sub(width(&count) + 4),
        );
        let gap = cols.saturating_sub(3 + width(&body) + width(&count) + 1);
        frame.render_widget(
            Paragraph::new(Line::from(vec![
                Span::styled(" > ", theme.style(Tone::Accent)),
                Span::raw(body),
                Span::raw(" ".repeat(gap)),
                Span::styled(format!("{count} "), theme.style(Tone::Muted)),
            ])),
            area,
        );
        return;
    }

    // Row one: the query, with the keyboard glyph at the far end so the block
    // reads as tappable. It lights up while reporting is dropped, so the state
    // is visible rather than mysterious.
    let key_tone = if model.keyboard_hint { Tone::Armed } else { Tone::Muted };
    let typed = truncate(&format!("{}_", model.query), cols.saturating_sub(6));
    let gap = cols.saturating_sub(3 + width(&typed) + 3);
    let query_row = Line::from(vec![
        Span::styled(" > ", theme.style(Tone::Accent)),
        Span::raw(typed),
        Span::raw(" ".repeat(gap)),
        Span::styled("⌨ ", theme.style(key_tone)),
    ]);

    // Row two: what the search found, and what is filtering it — or, when there
    // is something to say, the message instead. The count is worth losing for a
    // moment; a message nobody reads is worth nothing.
    let (message, tone) = status_text(model, true);
    let info_row = if model.flash.is_some() || model.esc_armed {
        Line::styled(format!(" {}", fit(&message, cols.saturating_sub(1))), theme.style(tone))
    } else {
        let left = format!(" {count}{}", chips(model));
        let room = cols.saturating_sub(width(&left) + 1);
        let hint = if width(&message) <= room { message } else { String::new() };
        let gap = cols.saturating_sub(width(&left) + width(&hint) + 1);
        Line::from(vec![
            Span::styled(left, theme.style(Tone::Muted)),
            Span::raw(" ".repeat(gap)),
            Span::styled(format!("{hint} "), theme.style(Tone::Muted)),
        ])
    };

    frame.render_widget(Paragraph::new(vec![query_row, info_row]), area);
}

/// What the bottom line says: a message if there is one, else this surface's
/// hints. Per-surface only — never another surface's verbs (v2's `check_action`
/// lesson), and a verb appears here when it works, not before.
///
/// On a touch layout the action bar is already showing the verbs as buttons, so
/// the hints shrink to the two things it does not carry.
fn status_text(model: &Model, touch: bool) -> (String, Tone) {
    if let Some(flash) = &model.flash {
        return (flash.clone(), Tone::Flash);
    }
    if model.esc_armed {
        return ("esc again to quit".into(), Tone::Armed);
    }
    let hints = match (touch, model.detail) {
        (true, _) => "esc back  ^q quit",
        (false, true) => "⏎ open  ← close  esc back  ^q quit",
        (false, false) => "⏎ open  → detail  ^x expiring  ^q quit",
    };
    (hints.into(), Tone::Muted)
}

/// The keyboard layout's hint line.
fn draw_footer(frame: &mut Frame, area: Rect, model: &Model, theme: Theme) {
    let (message, tone) = status_text(model, false);
    let line = Line::styled(
        format!(" {}", truncate(&message, area.width as usize - 1)),
        theme.style(tone),
    );
    frame.render_widget(Paragraph::new(line), area);
}
