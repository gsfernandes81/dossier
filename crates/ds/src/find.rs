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
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;

use crate::app::{Filter, ListGeometry, Model, ScanSearch, Zone};
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

    // **Three rows of chrome, either way.** Touch spends them on the header and
    // a two-row search bar; a keyboard on the header, a one-row bar and a hint
    // line. There is no action bar: every verb it carried is a key the thumb
    // already has on Termux's extra-keys row, and the two it did not are in the
    // leader sheet, where a toggle can show its off state as well as its on one.
    let touch = crate::layout::touch_layout(area.width);
    let constraints = if touch {
        vec![Constraint::Min(1), Constraint::Length(2)]
    } else {
        vec![Constraint::Min(1), Constraint::Length(1), Constraint::Length(1)]
    };
    // **The entry line is last, on both layouts.** Emacs's minibuffer is the
    // frame's final line, Vim's `:` is the final line below the status line, and
    // fzf's default layout is results, info, prompt. The row above it carries
    // the band, so the lit rule divides the list from the thing you type into.
    let split = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Min(1)])
        .split(area);
    let chunks =
        Layout::default().direction(Direction::Vertical).constraints(constraints).split(split[1]);

    draw_header(frame, split[0], model, theme);
    draw_body(frame, chunks[0], model, theme);
    if touch {
        draw_search(frame, chunks[1], model, theme);
    } else {
        draw_footer(frame, chunks[1], model, theme);
        draw_search(frame, chunks[2], model, theme);
    }
    // The sheet is drawn last and **covers** the list rather than shrinking it:
    // cheaper, and it matches every editor that does this. Nothing under it can
    // be tapped while it is up.
    draw_sheet(frame, chunks[0], model, theme);
}

/// Render one frame into a scratch backend so a unit test can read back the
/// geometry the view published.
///
/// The hit test must be checked against numbers the renderer really produced,
/// never against re-derived ones — dividing a width twice and getting two
/// answers is the exact bug the write-back exists to prevent.
///
/// # Panics
///
/// If the scratch terminal cannot be built or drawn — which in a test means the
/// renderer is broken, and failing loudly is the point.
#[cfg(test)]
pub fn draw_for_test(model: &mut Model, cols: u16, rows: u16) {
    let backend = ratatui::backend::TestBackend::new(cols, rows);
    let mut terminal = ratatui::Terminal::new(backend).expect("test backend");
    terminal.draw(|frame| draw(frame, model, Theme { color: true })).expect("draw");
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
fn draw_header(frame: &mut Frame, area: Rect, model: &mut Model, theme: Theme) {
    let wide = area.width >= 72;
    let attention = model.attention_count();
    let total = model.store.docs.len();
    let touch = crate::layout::touch_layout(area.width);
    let left = " dossier";
    let docs = format!("{total} docs  ");
    // On a touch layout the expiring count is the one verb a thumb cannot
    // otherwise produce while browsing — with the keyboard down there is no
    // letter for `CTRL` to land on. **You tap the number that told you there
    // were three**, which is the affordance REWRITE-UI §1 already specified and
    // the most discoverable one available. Reverse video, because in this design
    // reverse means *you can press this* and nothing else does.
    let count =
        if wide { format!(" ! {attention} expiring ") } else { format!(" ! {attention} exp ") };
    let tail = crate::layout::GUTTER as usize;
    let room = (area.width as usize).saturating_sub(width(left) + tail);
    let (docs, count) = if width(&docs) + width(&count) <= room {
        (docs, count)
    } else {
        (String::new(), truncate(&count, room))
    };
    let gap = room.saturating_sub(width(&docs) + width(&count));

    let start = width(left) + gap + width(&docs);
    model.count_zone = if touch {
        Zone {
            row: area.y,
            col: area.x + u16::try_from(start).unwrap_or(u16::MAX),
            width: u16::try_from(width(&count)).unwrap_or(0),
        }
    } else {
        Zone::default()
    };

    frame.render_widget(
        Paragraph::new(Line::from(vec![
            Span::styled(left, theme.style(Tone::Title)),
            Span::raw(" ".repeat(gap)),
            Span::styled(docs, theme.style(Tone::Muted)),
            Span::styled(count, if touch { theme.selected() } else { theme.style(Tone::Accent) }),
            Span::raw(" ".repeat(tail)),
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
/// The user confirmed this in the mockup review, density trade accepted. On the
/// measured phone (47×45 keyboard down, 47×24 up) it is twenty-one documents
/// while browsing and ten while typing.
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

/// The leader sheet, drawn over the bottom of the list.
///
/// A which-key panel: a rule, a breadcrumb, then what the next key can be. It
/// **covers** rows rather than displacing them, so opening it never reflows the
/// list underneath — the thing you were looking at is still where you left it
/// when the sheet closes.
///
/// Toggles are drawn as checkboxes, not keys. That is the point of putting them
/// here: a filter is state, and a checkbox is the only shape that shows **off**
/// as well as on. The status chips below could never do it, because a chip is
/// not rendered until its filter is already on — so it could turn one off and
/// never on.
fn draw_sheet(frame: &mut Frame, area: Rect, model: &Model, theme: Theme) {
    let Some(sheet) = &model.sheet else { return };
    let items = crate::sheet::items(sheet.group, model);
    let hits = crate::sheet::matching(&items, &sheet.filter);
    let cols = area.width as usize;
    let gutter = crate::layout::GUTTER as usize;

    let height = u16::try_from(hits.len() + 2).unwrap_or(u16::MAX).min(area.height);
    let panel = Rect {
        x: area.x,
        y: area.y + area.height.saturating_sub(height),
        width: area.width,
        height,
    };

    let crumb = crate::sheet::crumb(sheet.group, &sheet.filter);
    let note = if sheet.filter.is_empty() {
        "type to search".to_string()
    } else {
        format!("{} match{}", hits.len(), if hits.len() == 1 { "" } else { "es" })
    };
    let head_gap = cols.saturating_sub(width(&crumb) + width(&note) + gutter * 2);
    let mut lines = vec![
        Line::styled(
            format!(" {}", "─".repeat(cols.saturating_sub(gutter * 2))),
            theme.style(Tone::Muted),
        ),
        Line::from(vec![
            Span::styled(format!(" {crumb}"), theme.style(Tone::Accent)),
            Span::raw(" ".repeat(head_gap)),
            Span::styled(note, theme.style(Tone::Muted)),
            Span::raw(" ".repeat(gutter)),
        ]),
    ];

    for (index, item) in hits.iter().enumerate() {
        let lead = match item.on {
            // The box is reserved whether or not it is ticked, so an item never
            // changes width when it is toggled — a row that reflows on a press
            // is a row whose next press lands somewhere else.
            Some(on) => format!(" [{}] ", if on { "✓" } else { " " }),
            None => format!(" {} ", item.key),
        };
        let body = format!("{lead}{}", item.label);
        let gap = cols.saturating_sub(width(&body) + width(item.accel) + gutter);
        let mut line = Line::from(vec![
            Span::styled(lead, theme.style(Tone::Accent)),
            Span::raw(item.label.to_string()),
            Span::raw(" ".repeat(gap)),
            Span::styled(item.accel.to_string(), theme.style(Tone::Muted)),
            Span::raw(" ".repeat(gutter)),
        ]);
        // Only the picker has a cursor: with nothing typed, the keys are the
        // selection and a highlight would be a second, competing one.
        if !sheet.filter.is_empty() && index == sheet.cursor {
            line = line.style(theme.selected());
        }
        lines.push(line);
    }

    frame.render_widget(ratatui::widgets::Clear, panel);
    frame.render_widget(Paragraph::new(lines), panel);
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
/// too small a thing to ask a thumb to hit against the screen edge.
fn draw_search(frame: &mut Frame, area: Rect, model: &mut Model, theme: Theme) {
    let count = format!("{}/{}", model.rows.len(), model.store.docs.len());
    let cols = area.width as usize;
    let gutter = crate::layout::GUTTER as usize;
    let touch = area.height > 1;

    if !touch {
        model.leader_zone = Zone::default();
        if let Some(edit) = &model.edit {
            frame.render_widget(Paragraph::new(edit_row(edit, cols)), area);
            return;
        }
        let tail = format!("{count} ");
        let prompt = " > ";
        let span = cols.saturating_sub(width(prompt) + width(&tail));
        frame.render_widget(
            Paragraph::new(Line::from(vec![
                Span::styled(prompt, theme.style(Tone::Accent)),
                Span::raw(fit(&format!("{}{}█", model.query, chips(model)), span)),
                Span::styled(tail, theme.style(Tone::Muted)),
            ])),
            area,
        );
        return;
    }

    // Row one: the query as a field. **The whole row is a lit band** — Emacs
    // marks an editable field with a background face rather than a rule under
    // it, and a rule is exactly what a terminal cannot place: `SGR 4` lands
    // where the font's metric says, which on the phone is through the
    // descenders. The band runs edge to edge, so the field's shape is identical
    // empty, half-typed and full, and no glyph can sit on top of the marking.
    //
    // The `SPC` chip closes the right-hand end. It opens the leader sheet, which
    // `Space` opens from a keyboard — and with the phone keyboard down there is
    // no Space to press. It replaced the `⌨` affordance rather than joining it:
    // Termux has its own keyboard key, and tapping the field already raises the
    // IME, so a second button for it was a button for a key you already hold.
    // An edit takes the entry line over rather than adding a row: three rows of
    // chrome is the budget on both layouts (REWRITE-UI.md §5a), and a field you
    // are typing into is exactly what the last row is for. The `SPC` chip goes
    // with it — the leader sheet is not reachable from inside an edit, so a
    // button for it would be a button that does nothing.
    if let Some(edit) = &model.edit {
        model.leader_zone = Zone::default();
        let rows = Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Length(1), Constraint::Length(1)])
            .split(area);
        let (message, tone) = status_text(model, true);
        let band = Line::styled(
            format!(" {}", fit(&message, cols.saturating_sub(gutter))),
            theme.on_band(tone),
        );
        frame.render_widget(Paragraph::new(band).style(theme.band()), rows[0]);
        frame.render_widget(Paragraph::new(edit_row(edit, cols)), rows[1]);
        return;
    }

    let key = " SPC ";
    let prompt = " >";
    let span = cols.saturating_sub(width(prompt) + width(key) + gutter);
    let under = Style::default();
    let quiet = theme.style(Tone::Muted);

    model.leader_zone = Zone {
        row: area.y,
        col: area.x + u16::try_from(cols.saturating_sub(width(key) + gutter)).unwrap_or(0),
        width: u16::try_from(width(key)).unwrap_or(0),
    };

    let mut field: Vec<Span> = vec![Span::styled(prompt, theme.style(Tone::Accent))];
    if model.query.is_empty() {
        // What the empty field says. An invitation on the left, and on the right
        // a sentence that **finishes on the button**: the prose runs out at
        // "hit" and the reversed `SPC` is its object. One plain column separates
        // them, because a reverse block butted against text reads as a rendering
        // fault rather than as something you can press.
        //
        // Both halves are dim and both live inside the underline — dim and
        // underline are independent attributes on the same cell — so they change
        // what is drawn on the line, never how long it is. They go together on
        // the first keystroke.
        let invite = "Type to search";
        let signpost = "For more, hit";
        let gap = span.saturating_sub(3 + width(invite) + width(signpost) + 1);
        field.push(Span::styled(" █ ", under));
        field.push(Span::styled(invite, quiet));
        if gap >= 2 {
            field.push(Span::styled(" ".repeat(gap), under));
            field.push(Span::styled(signpost, quiet));
            field.push(Span::styled(" ", under));
        } else {
            // Too narrow to pair: the invitation outranks the signpost, and the
            // chip is still there saying `SPC` for itself.
            field.push(Span::styled(" ".repeat(span.saturating_sub(3 + width(invite))), under));
        }
    } else {
        field.push(Span::styled(fit(&format!(" {}█", model.query), span), under));
    }
    field.push(Span::styled(
        key,
        if model.sheet.is_some() {
            // Lit while the sheet is up, so it reads as the thing that opened it.
            theme.style(Tone::Armed).add_modifier(Modifier::REVERSED)
        } else {
            theme.selected()
        },
    ));
    field.push(Span::raw(" ".repeat(gutter)));
    let query_row = Line::from(field);

    // Row two: what the search found, and what is filtering it — or, when there
    // is something to say, the message instead. The count is worth losing for a
    // moment; a message nobody reads is worth nothing.
    // Tones on the band are not the tones on the terminal's own background —
    // see [`Theme::on_band`]. A light row needs a named grey where the rest of
    // the screen uses `DIM`, and red where it uses yellow.
    let (message, tone) = status_text(model, true);
    let info_row = if model.flash.is_some() || model.esc_armed {
        Line::styled(
            format!(" {}", fit(&message, cols.saturating_sub(gutter))),
            theme.on_band(tone),
        )
    } else {
        let left = format!(" {count}{}", chips(model));
        let room = cols.saturating_sub(width(&left) + gutter);
        let hint = shed(&touch_hints(model), room);
        let gap = cols.saturating_sub(width(&left) + width(&hint) + gutter);
        Line::from(vec![
            Span::raw(left),
            Span::raw(" ".repeat(gap)),
            Span::styled(hint, theme.on_band(Tone::Muted)),
            Span::raw(" ".repeat(gutter)),
        ])
    };

    // **Status line above, entry line below**, and only the status line is lit.
    // Two widgets rather than one, because a `Paragraph`'s style paints its
    // whole area rather than only the cells its text reaches — which is what
    // makes the band edge to edge, and what keeps it off the row underneath.
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(1), Constraint::Length(1)])
        .split(area);
    frame.render_widget(Paragraph::new(info_row).style(theme.band()), rows[0]);
    frame.render_widget(Paragraph::new(query_row), rows[1]);
}

/// The entry line while a field is being edited: the field's own prompt, what
/// has been typed, and the block cursor.
///
/// The prompt is the field's name rather than `>`, which is the minibuffer's
/// whole trick — one row that says which question it is asking. The cursor is
/// the same `█` the query uses, drawn at the end because that is where typing
/// goes; a cursor that can be moved through the text arrives with REWRITE-UI.md
/// §5b's query cursor, and both get it from the same mechanism when it does.
fn edit_row(edit: &crate::edit::Edit, cols: usize) -> Line<'static> {
    let prompt = format!(" {}: ", edit.prompt());
    let room = cols.saturating_sub(width(&prompt) + 1);
    // The *tail* of an over-long value is what matters while typing: the end is
    // where the next character lands.
    let shown: String = {
        let mut chars: Vec<char> = edit.buffer.chars().collect();
        while width(&chars.iter().collect::<String>()) > room && !chars.is_empty() {
            chars.remove(0);
        }
        chars.into_iter().collect()
    };
    Line::from(vec![
        Span::styled(prompt, Style::default().add_modifier(Modifier::REVERSED)),
        Span::raw(shown),
        Span::raw("█"),
    ])
}

/// The hints a touch layout shows, most sheddable first.
fn touch_hints(model: &Model) -> Vec<&'static str> {
    if model.edit.is_some() {
        vec!["⏎ save", "esc discard"]
    } else if model.detail {
        // The record's hints **follow the selector**: the verb is shown when the
        // row under it has one and this session can actually write. A hint for a
        // key that does nothing on *this* row is worse than no hint, and it is
        // what a per-field control key forced — one key advertised everywhere,
        // working in one place.
        let verb = model.write.ready().then(|| selected_row(model)).flatten();
        let mut hints = vec!["◀ back", "⏎ open file"];
        hints.extend(verb.and_then(crate::detail::Row::verb));
        // Offered only once there is something to take back, and likewise for
        // the way forward. A hint on a session that has written nothing teaches
        // a key that answers with an apology — the same rule the row verbs
        // follow. They are separate hints because they are separate verbs.
        if model.write.ready() && !model.undo.is_empty() {
            hints.push("u undo");
        }
        if model.write.ready() && !model.redo.is_empty() {
            hints.push("r redo");
        }
        // While a delete is armed the hint line stops teaching and starts
        // asking: the one moment where the next keystroke is the whole point.
        if model.delete_armed {
            return vec!["d again to delete", "any key cancels"];
        }
        hints
    } else {
        vec!["⏎ open", "^x expiry", "^t scans"]
    }
}

/// The record row the selector is on, if a record is open at all.
fn selected_row(model: &Model) -> Option<crate::detail::Row> {
    let rows = crate::detail::rows(model.current()?);
    rows.get(model.record_cursor.min(rows.len().saturating_sub(1))).copied()
}

/// Fit as many hints as the room allows, **dropping them one at a time from the
/// left** rather than dropping the line whole.
///
/// The old all-or-nothing rule erased every hint the moment two filters were
/// live — which is exactly when the user has the most state and the most reason
/// to want a way back out of it.
fn shed(hints: &[&str], room: usize) -> String {
    for start in 0..hints.len() {
        let line = hints[start..].join("  ");
        if width(&line) <= room {
            return line;
        }
    }
    String::new()
}

/// What the bottom line says: a message if there is one, else this surface's
/// hints. Per-surface only — never another surface's verbs (v2's `check_action`
/// lesson), and a verb appears here when it works, not before.
fn status_text(model: &Model, touch: bool) -> (String, Tone) {
    if let Some(flash) = &model.flash {
        return (flash.clone(), Tone::Flash);
    }
    if model.esc_armed {
        return ("esc again to quit".into(), Tone::Armed);
    }
    if let Some(edit) = &model.edit {
        if edit.armed_discard {
            return ("esc again to discard".into(), Tone::Armed);
        }
        if edit.saving {
            return ("saving…".into(), Tone::Muted);
        }
    }
    if touch {
        return (touch_hints(model).join("  "), Tone::Muted);
    }
    // The keyboard layout teaches every verb it has. `^t scans` used to appear
    // in no desktop hint at all, which made content search reachable only by
    // prior knowledge.
    let hints = if model.edit.is_some() {
        "⏎ save  esc discard"
    } else if model.detail && model.write.ready() {
        "⏎ open  ^e edit expiry  ← close  esc back  ^q quit"
    } else if model.detail {
        "⏎ open  ← close  esc back  ^q quit"
    } else {
        "space menu  ⏎ open  → detail  ^x expiring  ^t scans  ^q quit"
    };
    (hints.into(), Tone::Muted)
}

/// The keyboard layout's hint line.
fn draw_footer(frame: &mut Frame, area: Rect, model: &Model, theme: Theme) {
    let (message, tone) = status_text(model, false);
    // Lit, like the touch layout's — a keyboard layout has the same two rows in
    // the same order, and the same rule dividing the list from the entry line.
    let line = Line::styled(
        format!(" {}", truncate(&message, area.width as usize - 1)),
        theme.on_band(tone),
    );
    frame.render_widget(Paragraph::new(line).style(theme.band()), area);
}
