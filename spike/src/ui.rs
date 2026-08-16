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

//! The view half of the loop: state in, frame out, nothing mutated.
//!
//! This renders the Find surface from REWRITE-UI.md §1 — flat single list, no
//! location headers, docked bottom search bar, sticky detail split — at enough
//! fidelity to measure the real cost and to judge the feel on a phone screen.
//!
//! Two decisions are load-bearing for the measurement:
//!
//! 1. **The list is virtualized by hand.** Only the rows that fit on screen are
//!    built into `Line`s. Handing a widget 1,000 pre-built rows and letting it
//!    slice would make frame time scale with the store instead of the viewport,
//!    which is precisely the property R3 must not have.
//! 2. **Widths come from `unicode-width`, never `len()`.** A CJK name is two
//!    columns per character; the synthetic store contains such rows on purpose
//!    (`data::WIDE_NAMES`), and the F3 panel exists to prove the columns stay
//!    straight on the real terminal.
//!
//! The one exception to "view mutates nothing": [`draw`] publishes the row and
//! action-bar rectangles back onto [`App`] so mouse clicks can be hit-tested
//! against the layout that was actually drawn. Recomputing that geometry in the
//! event handler would be a second source of truth, and the two would drift.

use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

use crate::app::{App, Panel};
use crate::data::{expiring, Doc, Status};

/// Below this the layout goes two-line-per-row (REWRITE-UI.md §1).
const NARROW_COLS: u16 = 70;
/// At or above this, an open detail splits beside the list instead of covering it.
const SPLIT_COLS: u16 = 100;
/// Below this the app refuses to draw a broken layout (REWRITE-UI.md §4).
const FLOOR: (u16, u16) = (38, 12);

fn dim() -> Style {
    Style::default().add_modifier(Modifier::DIM)
}

/// Semantic colour tokens, mapped to terminal ANSI so the user's theme carries
/// the palette (REWRITE-UI.md §6). Every colour is paired with a glyph, so a
/// monochrome or `NO_COLOR` terminal loses nothing.
fn status_style(status: Status) -> Style {
    match status {
        Status::Expired => Style::default().fg(Color::Red),
        Status::Soon => Style::default().fg(Color::Yellow),
        Status::Ok | Status::None => dim(),
    }
}

/// Truncate to a **display width**, appending `…` when it had to cut.
///
/// rust: takes `&str` and returns `String` — borrow what you only read, hand
/// back ownership of what you had to build. The `chars()` walk accumulates
/// column widths rather than byte or character counts, which is the entire
/// point: `len()` would be wrong for every non-ASCII row in the store.
fn truncate(text: &str, max: usize) -> String {
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

/// Pad `text` on the left so it ends at column `width` — display-width aware.
fn pad_left(text: &str, width: usize) -> String {
    let pad = width.saturating_sub(text.width());
    format!("{}{}", " ".repeat(pad), text)
}

/// `2026-09-28` → `09-26`, the compact form the mock uses.
fn short_date(iso: &str) -> String {
    if iso.len() == 10 {
        format!("{}-{}", &iso[5..7], &iso[2..4])
    } else {
        iso.to_string()
    }
}

/// Draw one frame.
pub fn draw(frame: &mut Frame, app: &mut App) {
    let area = frame.area();
    if area.width < FLOOR.0 || area.height < FLOOR.1 {
        let notice = Paragraph::new(format!(
            "terminal too small\nneed ≥ {}×{}, have {}×{}",
            FLOOR.0, FLOOR.1, area.width, area.height
        ))
        .wrap(Wrap { trim: true });
        frame.render_widget(notice, area);
        app.rows_area = Rect::ZERO;
        app.action_bar = Rect::ZERO;
        return;
    }

    // header · body · touch action bar · search bar · footer
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Min(3),
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Length(1),
        ])
        .split(area);

    draw_header(frame, chunks[0], app);

    match app.panel {
        Panel::None => draw_body(frame, chunks[1], app),
        Panel::Events => draw_events(frame, chunks[1], app),
        Panel::Glyphs => draw_glyphs(frame, chunks[1]),
        Panel::Diag => draw_diag(frame, chunks[1], app),
    }

    app.action_bar = chunks[2];
    draw_action_bar(frame, chunks[2], app);
    draw_search(frame, chunks[3], app);
    draw_footer(frame, chunks[4], app);
}

fn draw_header(frame: &mut Frame, area: Rect, app: &App) {
    let expiring_count = expiring(&app.docs);
    // Both halves shed detail as the terminal narrows. Found the hard way in a
    // 45-column PTY run, where the full-width header ran straight through the
    // attention count and the terminal clipped it mid-word: at phone width the
    // counts are the *only* thing worth keeping, so the title goes first.
    let full = area.width >= 72;
    let left = if full { " dossier · R0.2 spike" } else { " dossier" };
    let right = if full {
        format!(
            "! {expiring_count} expiring · {} docs · {}×{} ",
            app.docs.len(),
            area.width,
            frame.area().height
        )
    } else {
        format!("! {expiring_count} exp · {} docs ", app.docs.len())
    };
    let right = truncate(&right, (area.width as usize).saturating_sub(left.width() + 1));
    let gap = (area.width as usize).saturating_sub(left.width() + right.width());
    let line = Line::from(vec![
        Span::styled(left, Style::default().add_modifier(Modifier::BOLD)),
        Span::raw(" ".repeat(gap)),
        Span::styled(right, dim()),
    ]);
    frame.render_widget(Paragraph::new(line), area);
}

fn draw_body(frame: &mut Frame, area: Rect, app: &mut App) {
    // Sticky detail (REWRITE-UI.md U3): a right split when there is room, a
    // full-screen push when there is not. Exactly two states, no collapse ladder.
    let (list_area, detail_area) = match (app.detail, area.width >= SPLIT_COLS) {
        (true, true) => {
            let split = Layout::default()
                .direction(Direction::Horizontal)
                .constraints([Constraint::Percentage(55), Constraint::Percentage(45)])
                .split(area);
            (split[0], Some(split[1]))
        }
        (true, false) => (Rect::ZERO, Some(area)),
        (false, _) => (area, None),
    };

    if list_area.height > 0 {
        draw_list(frame, list_area, app);
    } else {
        app.rows_area = Rect::ZERO;
    }
    if let Some(detail_area) = detail_area {
        draw_detail(frame, detail_area, app);
    }
}

fn draw_list(frame: &mut Frame, area: Rect, app: &mut App) {
    let row_h: u16 = if area.width < NARROW_COLS { 2 } else { 1 };
    let visible = (area.height / row_h).max(1) as usize;

    // Keep the cursor in view before choosing the window — scrolling is the
    // app's job (Termux blocks terminal scrollback under mouse mode, #4302).
    if app.selected < app.offset {
        app.offset = app.selected;
    } else if app.selected >= app.offset + visible {
        app.offset = app.selected + 1 - visible;
    }
    let max_offset = app.filtered.len().saturating_sub(visible);
    app.offset = app.offset.min(max_offset);

    // Publish geometry for hit-testing (see the module note).
    app.rows_area = area;
    app.row_h = row_h;

    // *** The virtualization: only the visible window is built. ***
    let mut lines: Vec<Line> = Vec::with_capacity(visible * row_h as usize);
    for slot in 0..visible {
        let Some(&doc_index) = app.filtered.get(app.offset + slot) else { break };
        let doc = &app.docs[doc_index];
        let selected = app.offset + slot == app.selected;
        if row_h == 1 {
            lines.push(wide_row(doc, area.width as usize, selected));
        } else {
            let (first, second) = narrow_rows(doc, area.width as usize, selected);
            lines.push(first);
            lines.push(second);
        }
    }
    if lines.is_empty() {
        lines.push(Line::styled("  no documents match", dim()));
    }
    frame.render_widget(Paragraph::new(lines), area);
}

/// Single-line row: name left, `location slot` + status right-aligned.
fn wide_row(doc: &Doc, width: usize, selected: bool) -> Line<'static> {
    let status = doc.status();
    // Blank, not a placeholder glyph: the marker column already says "no
    // expiry" with `·`, and five spaces keep the dates in a column.
    let expiry = doc.expiry.as_deref().map_or_else(|| "     ".to_string(), short_date);
    let place = doc.place();
    let tail = format!("{} {expiry}", status.marker());
    // The right block is `place` + gap + status; the name gets whatever is left,
    // so the status column lands on the same screen column for every row.
    let right_width = place.width() + 2 + tail.width() + 2;
    let name_width = width.saturating_sub(right_width + 2).max(8);
    let cursor = if selected { "▸ " } else { "  " };
    let line = Line::from(vec![
        Span::raw(cursor),
        Span::raw(pad_right(&truncate(&doc.name, name_width), name_width)),
        Span::styled(pad_left(&place, place.width() + 2), dim()),
        Span::raw("  "),
        Span::styled(tail, status_style(status)),
    ]);
    if selected {
        // Selection is reverse video, never an indent shift (v2 rule) — an
        // indent shift makes the whole list twitch as the cursor moves.
        line.style(Style::default().add_modifier(Modifier::REVERSED))
    } else {
        line
    }
}

/// Pad on the right to a display width.
fn pad_right(text: &str, width: usize) -> String {
    let pad = width.saturating_sub(text.width());
    format!("{text}{}", " ".repeat(pad))
}

/// Two-line row for narrow terminals: name + status, then dim location/tags.
fn narrow_rows(doc: &Doc, width: usize, selected: bool) -> (Line<'static>, Line<'static>) {
    let status = doc.status();
    // Blank, not a placeholder glyph: the marker column already says "no
    // expiry" with `·`, and five spaces keep the dates in a column.
    let expiry = doc.expiry.as_deref().map_or_else(|| "     ".to_string(), short_date);
    let tail = format!("{} {expiry} ", status.marker());
    let name_width = width.saturating_sub(tail.width() + 3).max(8);
    let cursor = if selected { "▸ " } else { "  " };
    let mut first = Line::from(vec![
        Span::raw(cursor),
        Span::raw(pad_right(&truncate(&doc.name, name_width), name_width)),
        Span::styled(tail, status_style(status)),
    ]);
    let tags =
        if doc.tags.is_empty() { String::new() } else { format!(" · {}", doc.tags.join(" ")) };
    let mut second = Line::styled(
        format!("    {}{tags}", truncate(&doc.place(), width.saturating_sub(6))),
        dim(),
    );
    if selected {
        let reversed = Style::default().add_modifier(Modifier::REVERSED);
        first = first.style(reversed);
        second = second.style(reversed);
    }
    (first, second)
}

fn draw_detail(frame: &mut Frame, area: Rect, app: &App) {
    let Some(doc) = app.current() else {
        frame.render_widget(Paragraph::new(Line::styled(" nothing selected", dim())), area);
        return;
    };
    let status = doc.status();
    let mut lines = vec![
        Line::styled(
            format!(" {}", truncate(&doc.name, area.width.saturating_sub(2) as usize)),
            Style::default().add_modifier(Modifier::BOLD),
        ),
        Line::raw(""),
        field("location", &doc.place()),
        field("expiry", &doc.expiry.clone().unwrap_or_else(|| "—".into())),
        Line::from(vec![
            Span::styled(" status    ", dim()),
            Span::styled(
                match status {
                    Status::Expired => "! expired",
                    Status::Soon => "~ expiring soon",
                    Status::Ok => "  tracked",
                    Status::None => "· no expiry",
                },
                status_style(status),
            ),
        ]),
        field("tags", &doc.tags.join(", ")),
        field("file", if doc.has_file { "linked" } else { "none" }),
        Line::raw(""),
        Line::styled(" (spike: read-only — R4 makes this the editing surface)", dim()),
    ];
    if area.width < SPLIT_COLS {
        lines.push(Line::styled(" esc / ← back to the list", dim()));
    }
    frame.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), area);
}

fn field(label: &str, value: &str) -> Line<'static> {
    Line::from(vec![Span::styled(format!(" {label:<9} "), dim()), Span::raw(value.to_string())])
}

fn draw_action_bar(frame: &mut Frame, area: Rect, app: &App) {
    // The one row of chrome touch gets (REWRITE-UI.md §5). Quarters, so the hit
    // test in `App::hit_action_bar` needs no per-label geometry.
    let quarter = (area.width / 4).max(1) as usize;
    let labels = ["⏎ Open", "→ Detail", "F4 Diag", "⌨ Keys"];
    let style = if app.keyboard_hint { Style::default().fg(Color::Yellow) } else { dim() };
    let mut spans = Vec::new();
    for label in labels {
        spans.push(Span::styled(pad_right(&format!(" {label}"), quarter), style));
    }
    frame.render_widget(Paragraph::new(Line::from(spans)), area);
}

fn draw_search(frame: &mut Frame, area: Rect, app: &App) {
    let count = format!("{}/{} ", app.filtered.len(), app.docs.len());
    let mut left = format!("> {}_", app.query);
    if app.scans {
        left.push_str("  [scans]");
    }
    if !app.mouse_on {
        left.push_str("  [mouse off — tap to raise the keyboard]");
    }
    let left = truncate(&left, area.width.saturating_sub(count.width() as u16 + 1) as usize);
    let gap = (area.width as usize).saturating_sub(left.width() + count.width());
    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::raw(left),
            Span::raw(" ".repeat(gap)),
            Span::styled(count, dim()),
        ])),
        area,
    );
}

fn draw_footer(frame: &mut Frame, area: Rect, app: &App) {
    // Per-surface hints only — never another surface's verbs (v2's check_action
    // lesson, REWRITE-UI.md §1).
    let line = if let Some(flash) = &app.flash {
        Line::styled(format!(" {flash}"), Style::default().fg(Color::Cyan))
    } else if app.esc_armed {
        Line::styled(" esc again to quit", Style::default().fg(Color::Yellow))
    } else if app.panel != Panel::None {
        Line::styled(" esc close panel   F2 events  F3 glyphs  F4 diag", dim())
    } else {
        Line::styled(" ⏎ open  → detail  F2/F3/F4 panels  F5 ⌨  esc back  ^q quit", dim())
    };
    frame.render_widget(Paragraph::new(line), area);
}

fn draw_events(frame: &mut Frame, area: Rect, app: &App) {
    let c = &app.counts;
    let mut lines = vec![
        Line::styled(" input events — newest first", Style::default().add_modifier(Modifier::BOLD)),
        Line::styled(
            format!(
                " keys {} · taps {} · drags {} · scrolls {} · resizes {}",
                c.keys, c.taps, c.drags, c.scrolls, c.resizes
            ),
            dim(),
        ),
        Line::raw(""),
    ];
    for event in app.events.iter().rev().take(area.height.saturating_sub(3) as usize) {
        lines.push(Line::from(vec![
            Span::styled(format!(" {:>7.2}s ", event.at), dim()),
            Span::styled(format!("{:<7}", event.kind), Style::default().fg(Color::Cyan)),
            Span::raw(truncate(&event.detail, area.width.saturating_sub(18) as usize)),
        ]));
    }
    frame.render_widget(Paragraph::new(lines), area);
}

/// Width of the glyph table's right-hand rule.
const GLYPH_COL: usize = 34;

/// The rows of the glyph + width check.
///
/// Shared with the `--glyphs` CLI mode, which exists because **Termux has no
/// function keys** — the phone run found the F3 panel simply unreachable there.
/// A check nobody can run is not a check.
pub fn glyph_samples() -> &'static [&'static str] {
    &[
        "ASCII markers:  ! ~ x ·",
        "box drawing: ┌─┬─┐ │ ├ ┤ └┴┘",
        "arrows/verbs: ⏎ → ← ▸ ⌨",
        "status glyphs: ⚠ ✓ ✗ ⭘ ●",
        "nerd font:      ",
        "CJK: 海事証明書",
        "emoji: 🛳 📄 🔒",
        "devanagari: पैन कार्ड",
        "cyrillic: Свидетельство",
        "combining: é vs é  ffi",
    ]
}

/// One glyph row as `|sample…| w=N`, padded by display width.
///
/// If the terminal's idea of a character's width differs from
/// `unicode-width`'s, the right-hand bars stop lining up — the fastest possible
/// visual test of "will the columns stay straight on the phone".
pub fn glyph_row(text: &str) -> String {
    format!(" |{}| w={}", pad_right(text, GLYPH_COL), text.width())
}

fn draw_glyphs(frame: &mut Frame, area: Rect) {
    let sample = |text: &str| -> Line<'static> {
        Line::from(vec![
            Span::raw(" |"),
            Span::raw(pad_right(text, GLYPH_COL)),
            Span::raw("| "),
            Span::styled(format!("w={}", text.width()), dim()),
        ])
    };
    let mut lines = vec![
        Line::styled(
            " glyph + width check — the right bars must line up",
            Style::default().add_modifier(Modifier::BOLD),
        ),
        Line::raw(""),
    ];
    lines.extend(glyph_samples().iter().map(|text| sample(text)));
    lines.extend([
        Line::raw(""),
        Line::styled(" missing/boxy glyphs = font gap (ASCII fallback is mandatory);", dim()),
        Line::styled(" misaligned bars = the terminal disagrees on width.", dim()),
    ]);
    frame.render_widget(Paragraph::new(lines), area);
}

fn draw_diag(frame: &mut Frame, area: Rect, app: &App) {
    let (count, median, p95, max) = app.frames.summary();
    let budget = |us: u64, target: u64, ceiling: u64| -> Span<'static> {
        let (label, color) = if us <= target * 1000 {
            ("within target", Color::Green)
        } else if us <= ceiling * 1000 {
            ("within acceptable", Color::Yellow)
        } else {
            ("OVER BUDGET", Color::Red)
        };
        Span::styled(label, Style::default().fg(color))
    };
    let mut lines = vec![
        Line::styled(" diagnostics", Style::default().add_modifier(Modifier::BOLD)),
        Line::raw(""),
        Line::raw(format!(" {}", app.startup_line)),
        Line::raw(""),
        Line::from(vec![
            Span::raw(format!(
                " frames {count}: median {:.2}ms · p95 {:.2}ms · max {:.2}ms  ",
                median as f64 / 1000.0,
                p95 as f64 / 1000.0,
                max as f64 / 1000.0
            )),
            budget(p95, 16, 33),
        ]),
        Line::from(vec![
            Span::raw(format!(
                " worst keystroke→frame: {:.2}ms  ",
                app.frames.worst_key_to_frame_us as f64 / 1000.0
            )),
            budget(app.frames.worst_key_to_frame_us, 16, 33),
        ]),
        Line::raw(""),
        Line::raw(format!(
            " docs {} · matched {} · row height {}",
            app.docs.len(),
            app.filtered.len(),
            app.row_h
        )),
        Line::raw(format!(
            " terminal {}×{} · layout {}",
            frame.area().width,
            frame.area().height,
            if frame.area().width >= SPLIT_COLS { "split-capable" } else { "single-pane" }
        )),
        Line::raw(format!(
            " mouse reporting {} · kitty keyboard protocol {}",
            if app.mouse_on { "on (SGR)" } else { "OFF (IME affordance)" },
            match app.kbd_enhancement {
                Some(true) => "supported",
                Some(false) => "not supported",
                None => "not probed",
            }
        )),
    ];
    if let Some(rss) = crate::timing::rss_bytes() {
        lines.push(Line::from(vec![
            Span::raw(format!(" rss {:.1}MB  ", rss as f64 / 1_048_576.0)),
            budget((rss / 1_048_576) * 1000, 30, 50),
        ]));
    }
    lines.push(Line::raw(""));
    lines.push(Line::styled(" budgets: keystroke→frame <16ms (33 ok) · rss <30MB (50 ok)", dim()));
    // Wrapped, not clipped: at 45 columns these lines are wider than the phone,
    // and a budget verdict that ran off the right edge is worse than useless.
    frame.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), area);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::App;
    use crate::data::synth;
    use ratatui::backend::TestBackend;
    use ratatui::Terminal;

    fn render(width: u16, height: u16, prep: impl FnOnce(&mut App)) -> (String, App) {
        let mut app = App::new(synth(1000));
        prep(&mut app);
        let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
        terminal.draw(|frame| draw(frame, &mut app)).unwrap();
        let buffer = terminal.backend().buffer().clone();
        let text = (0..height)
            .map(|y| {
                (0..width)
                    .filter_map(|x| buffer.cell((x, y)).map(|cell| cell.symbol().to_string()))
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n");
        (text, app)
    }

    /// Truncation and padding are display-width aware, so a CJK name occupies
    /// the columns it actually paints (REWRITE-UI.md §1: never `len()`).
    #[test]
    fn truncation_counts_columns_not_bytes() {
        assert_eq!(truncate("海事証明書", 6).width(), 5); // 2 wide chars + '…'
        assert_eq!(truncate("passport", 20), "passport");
        assert_eq!(pad_right("海", 5).width(), 5);
    }

    /// Below the floor the app says so instead of painting a broken layout.
    #[test]
    fn below_the_floor_it_renders_a_notice() {
        let (text, _) = render(30, 8, |_| {});
        assert!(text.contains("too small"), "{text}");
    }

    /// The phone case: 45×28 portrait Termux renders two-line rows and every
    /// chrome row is present.
    #[test]
    fn portrait_termux_renders_two_line_rows() {
        let (text, app) = render(45, 28, |_| {});
        assert_eq!(app.row_h, 2, "narrow terminals get two-line rows");
        assert!(text.contains("dossier"), "{text}");
        assert!(text.contains('>'), "search bar is docked at the bottom");
    }

    /// Detail splits beside the list only when there is room; narrower, it is a
    /// full-screen push (REWRITE-UI.md §4 — exactly two layout states).
    #[test]
    fn detail_splits_only_when_wide() {
        let (wide, wide_app) = render(120, 30, |app| app.detail = true);
        assert!(wide.contains("location"), "detail is visible");
        assert!(wide_app.rows_area.width > 0, "the list survives beside it");

        let (_narrow, narrow_app) = render(80, 30, |app| app.detail = true);
        assert_eq!(narrow_app.rows_area.width, 0, "narrow detail covers the list");
    }

    /// Only the visible window is built into rows: the frame cost must not
    /// scale with the store. Rendering a 1,000-row store into a 20-row
    /// viewport must touch ~20 rows, which we observe through the published
    /// geometry rather than by timing (timing lives in `bench`).
    #[test]
    fn the_list_is_virtualized_to_the_viewport() {
        let (_, app) = render(100, 24, |app| app.selected = 900);
        assert!(app.visible_rows() <= 24);
        assert!(app.offset > 800, "the window followed the cursor to row 900");
    }
}
