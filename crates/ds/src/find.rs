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

//! The view half of the loop: the Find surface, its panels and bottom rows.
//!
//! Only the rows that fit are built, so frame time follows the viewport and
//! not the store, and every column is measured in cells. The renderer decides
//! nothing: it reads [`Model`] and writes back only the geometry it drew, so
//! taps hit-test against what is really on screen.

use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;

use crate::app::{Armed, ListGeometry, Model, ScanSearch, Zone};
use crate::layout::{cursor_cell, fit, pad_left, short_date, spread, truncate, width, wrap};
use crate::theme::{Theme, Tone};
use crate::{Doc, Status};

/// The status cell is a fixed seven columns — `! 09-26` — so the marker lands on
/// the same screen column in every row, which is what makes a list of dates
/// scannable at a glance.
const STATUS_COLS: usize = 7;

/// Above this width a single-line row has room for a tags column as well.
const TAGS_COLS: u16 = 90;

/// Draws one frame, writing back only the geometry it drew.
pub fn draw(frame: &mut Frame, model: &mut Model, theme: Theme) {
    let area = frame.area();
    model.cols = area.width;
    model.rows_on_screen = area.height;
    model.clear_geometry();

    if crate::layout::too_small(area.width, area.height) {
        draw_too_small(frame, area, theme);
        return;
    }

    // Three rows of chrome either way: touch spends them on the header and a
    // two-row search bar, a keyboard on the header, a one-row bar and a hint.
    let touch = crate::layout::touch_layout(area.width);
    let status = status_rows(model, area.width);
    let constraints = if touch {
        vec![Constraint::Min(1), Constraint::Length(1 + status)]
    } else {
        vec![Constraint::Min(1), Constraint::Length(status), Constraint::Length(1)]
    };
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

/// Title on the left, attention counts on the right; as the terminal narrows
/// the counts are kept and the title gives way.
fn draw_header(frame: &mut Frame, area: Rect, model: &mut Model, theme: Theme) {
    let wide = area.width >= 72;
    let attention = model.due().len();
    let touch = crate::layout::touch_layout(area.width);
    let left = " dossier";
    // On a touch layout the count is pressed to filter by it, so it is drawn
    // in reverse video, which here means "you can press this".
    let count =
        if wide { format!(" ! {attention} expiring ") } else { format!(" ! {attention} exp ") };
    let conflicts = model.store.conflicts();
    let conflict = if conflicts == 0 { String::new() } else { format!(" ! {conflicts} conflict ") };
    let tail = crate::layout::GUTTER as usize;
    let room = (area.width as usize).saturating_sub(width(left) + tail);
    let count = truncate(&count, room);
    let conflict = truncate(&conflict, room.saturating_sub(width(&count)));
    let gap = room.saturating_sub(width(&count) + width(&conflict));

    let start = width(left) + gap + width(&conflict);
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
            Span::styled(conflict, theme.status(crate::Status::Expired)),
            Span::styled(
                count,
                if model.filter.expiring {
                    theme.lit()
                } else if touch {
                    theme.pressable()
                } else {
                    theme.style(Tone::Accent)
                },
            ),
            Span::raw(" ".repeat(tail)),
        ])),
        area,
    );
}

/// The list — the Find view's, or the Bundles view's in its place — and the
/// view in front beside it or instead of it.
fn draw_body(frame: &mut Frame, area: Rect, model: &mut Model, theme: Theme) {
    use crate::app::View;
    let (list_area, detail_area) = match (model.pane(), crate::layout::splits(area.width)) {
        (true, true) => {
            let split = Layout::default()
                .direction(Direction::Horizontal)
                .constraints([Constraint::Percentage(55), Constraint::Percentage(45)])
                .split(area);
            (Some(split[0]), Some(split[1]))
        }
        (true, false) => (None, Some(area)),
        (false, _) => (Some(area), None),
    };

    let bundles = model.views.iter().find_map(|view| match view {
        View::Bundles { selected, .. } => Some(selected.clone()),
        _ => None,
    });
    match (list_area, bundles) {
        (Some(list_area), Some(selected)) => {
            model.bundle_list =
                crate::bundles::draw_list(frame, list_area, model, &selected, theme);
        }
        (Some(list_area), None) => draw_list(frame, list_area, model, theme),
        (None, _) => {}
    }
    if let Some(detail_area) = detail_area {
        model.record = match model.views.last() {
            Some(View::Versions { doc }) => {
                crate::versions::draw(frame, detail_area, model, doc, theme)
            }
            Some(View::Bundle { id, selected }) => {
                crate::bundles::draw_bundle(frame, detail_area, model, id, selected, theme)
            }
            _ => crate::detail::draw(frame, detail_area, model, theme),
        };
    }
}

fn draw_list(frame: &mut Frame, area: Rect, model: &mut Model, theme: Theme) {
    let mut area = area;
    if model.offers_new() && area.height > 1 {
        let line = crate::layout::new_row(
            &model.query,
            "document",
            model.on_new,
            area.width as usize,
            theme,
        );
        frame.render_widget(Paragraph::new(line), Rect { height: 1, ..area });
        model.new_row = Some(area.y);
        area = Rect { y: area.y + 1, height: area.height - 1, ..area };
    }
    let row_height = crate::layout::row_height(model.cols);
    let visible = (area.height / row_height).max(1) as usize;
    model.scroll_into_view(visible);
    model.list = ListGeometry { top: area.y, height: area.height, row_height };

    if model.rows.is_empty() {
        let fresh = model.query.trim().is_empty() && model.offers_new();
        let lines = match (&model.missing_journal, fresh) {
            // Because the journal is born on the first save, say where that
            // save will put it, so a wrong root is caught before anything is
            // written there.
            (Some(path), true) => vec![
                "  no documents yet".to_string(),
                "  the first one creates the journal at".to_string(),
                format!("  {path}"),
            ],
            (None, true) => vec!["  no documents yet".to_string()],
            (_, false) => vec!["  nothing matches".to_string()],
        };
        let lines: Vec<Line> =
            lines.into_iter().map(|text| Line::styled(text, theme.style(Tone::Muted))).collect();
        frame.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), area);
        return;
    }

    let shown: Vec<(usize, &Doc, String)> = (model.offset..model.rows.len())
        .take(visible)
        .map(|at| {
            let doc = &model.store.docs[model.rows[at]];
            (at, doc, model.store.place(doc))
        })
        .collect();
    let columns =
        Columns::fit(area.width, shown.iter().map(|(_, doc, place)| (*doc, place.as_str())));
    let mut lines: Vec<Line> = Vec::with_capacity(visible * row_height as usize);
    for (at, doc, place) in &shown {
        let selected = *at == model.cursor && !model.on_new;
        let status = model.status(doc);
        if row_height == 1 {
            lines.push(single_line_row(doc, place, status, &columns, selected, theme));
        } else {
            let (first, second) = two_line_row(doc, place, status, area.width, selected, theme);
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
/// The wide layout's column widths, decided once per screen from the rows on
/// it, so the columns line up and spare width goes to what needs it.
#[derive(Clone, Copy)]
struct Columns {
    name: usize,
    tags: usize,
    place: usize,
}

impl Columns {
    /// The gap before each column after the name, the status's included.
    const GAP: usize = 2;

    /// Widths for `rows` across `cols`. Each column asks for its widest entry;
    /// when they do not all fit, tags give way first, then the location down to
    /// a dozen columns, then the name down to eight.
    fn fit<'a>(cols: u16, rows: impl Iterator<Item = (&'a Doc, &'a str)>) -> Self {
        let mut want = Self { name: 0, tags: 0, place: 0 };
        for (doc, place) in rows {
            want.name = want.name.max(width(&doc.name));
            want.tags = want.tags.max(width(&doc.tags.join(" ")));
            want.place = want.place.max(width(place));
        }
        if cols < TAGS_COLS {
            want.tags = 0;
        }
        let gaps = |columns: &Self| Self::GAP * (2 + usize::from(columns.tags > 0));
        let room = (cols as usize).saturating_sub(2 + STATUS_COLS);
        let over = |columns: &Self| {
            (columns.name + columns.tags + columns.place + gaps(columns)).saturating_sub(room)
        };
        let mut columns = want;
        columns.tags = columns.tags.saturating_sub(over(&columns));
        columns.place = columns.place.saturating_sub(over(&columns)).max(want.place.min(12));
        columns.name = columns.name.saturating_sub(over(&columns)).max(8);
        columns.place = columns.place.saturating_sub(over(&columns));
        columns.name +=
            room.saturating_sub(columns.name + columns.tags + columns.place + gaps(&columns));
        columns
    }
}

/// Wide layout: name, tags, location and status, each in its own column.
fn single_line_row(
    doc: &Doc,
    place: &str,
    status: Status,
    columns: &Columns,
    selected: bool,
    theme: Theme,
) -> Line<'static> {
    let gap = " ".repeat(Columns::GAP);
    let mut spans = vec![Span::raw(cursor_cell(selected)), Span::raw(fit(&doc.name, columns.name))];
    if columns.tags > 0 {
        spans.push(Span::raw(gap.clone()));
        spans.push(Span::styled(fit(&doc.tags.join(" "), columns.tags), theme.style(Tone::Muted)));
    }
    spans.push(Span::raw(gap));
    spans.push(Span::styled(
        pad_left(&crate::layout::truncate_left(place, columns.place), columns.place),
        theme.style(Tone::Muted),
    ));
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
fn two_line_row(
    doc: &Doc,
    place: &str,
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

    let tags = doc.tags.join(" ");
    let room = total.saturating_sub(4);
    let place = if tags.is_empty() {
        crate::layout::truncate_left(place, room)
    } else {
        crate::layout::truncate_left(place, room.saturating_sub(width(&tags) + 3).max(room / 2))
    };
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

/// The panels drawn over the view: the location picker, a picker, a
/// checklist, and the Space sheet, which can sit over the location picker.
///
/// They cover rows rather than displacing them, so closing one never reflows
/// the list underneath.
fn draw_sheet(frame: &mut Frame, area: Rect, model: &mut Model, theme: Theme) {
    if let Some(picker) = &model.locpick {
        let tree = draw_locpick(frame, area, model, picker, theme);
        model.tree = tree;
        if !model.sheet {
            return;
        }
    }
    let matches = model.edit.as_ref().map(crate::edit::Edit::matches).unwrap_or_default();
    if let (Some(edit), false) = (&model.edit, matches.is_empty()) {
        let (head, tail) = crate::complete::split(&edit.buffer);
        let rows = matches.iter().map(|entry| ("  ".to_string(), entry.label(), "")).collect();
        let place = if head.is_empty() { "the Syncthing folder" } else { head };
        let panel = Panel {
            crumb: format!("in {place}"),
            filter: Some(tail),
            cursor: edit.list.as_ref().and_then(|list| list.chosen),
            subject: None,
        };
        model.panel = draw_panel(frame, area, &panel, rows, theme);
        return;
    }
    if let Some(picker) = &model.picker {
        let rows = picker
            .matching(model)
            .into_iter()
            .map(|entry| {
                // A checklist reserves the box on every row, so toggling one
                // never changes its width.
                let lead = match (entry.on, picker.checklist()) {
                    (Some(on), _) => format!(" [{}] ", if on { "x" } else { " " }),
                    (None, true) => "     ".to_string(),
                    (None, false) => "   ".to_string(),
                };
                (lead, entry.label, "")
            })
            .collect();
        let panel = Panel {
            crumb: format!("{}{}", picker.crumb(&model.store), typed(&picker.filter)),
            filter: Some(&picker.filter),
            cursor: Some(picker.cursor),
            subject: picker.subject(&model.store),
        };
        model.panel = draw_panel(frame, area, &panel, rows, theme);
        return;
    }
    if !model.sheet {
        return;
    }
    let items = crate::sheet::items(model);
    let lead = |item: &crate::sheet::Item| {
        if item.key == crate::sheet::NO_KEY {
            "   ".to_string()
        } else {
            format!(" {} ", item.key)
        }
    };
    let subject = model.locpick.as_ref().and_then(|picker| picker.chosen()).and_then(|id| {
        let location = model.store.locations.get(id)?;
        Some((location.name.clone(), "physical location", model.store.locations.path(id)))
    });
    let heading = if subject.is_some() { 4 } else { 2 };
    let panel = Panel { crumb: "SPC".into(), filter: None, cursor: None, subject };
    if items.len() + heading <= area.height as usize {
        let rows =
            items.iter().map(|item| (lead(item), item.label.to_string(), item.accel)).collect();
        model.panel = draw_panel(frame, area, &panel, rows, theme);
        return;
    }
    // Too short for one column: the verbs flow into two, without their
    // accelerators, so none is cut off.
    let half = items.len().div_ceil(2);
    let column = (area.width as usize).saturating_sub(crate::layout::GUTTER as usize * 2) / 2;
    let rows = (0..half)
        .map(|row| {
            let left = &items[row];
            let mut label = fit(left.label, column.saturating_sub(width(&lead(left))));
            if let Some(right) = items.get(row + half) {
                label.push_str(&lead(right));
                label.push_str(&truncate(right.label, column.saturating_sub(width(&lead(right)))));
            }
            (lead(left), label, "")
        })
        .collect();
    let mut geometry = draw_panel(frame, area, &panel, rows, theme);
    geometry.right = (half..items.len()).collect();
    geometry.split = area.x + u16::try_from(column).unwrap_or(u16::MAX);
    model.panel = geometry;
}

/// The location picker: a three-row heading, then the tree.
///
/// Full screen in the single-pane layout; over the bottom of the view when the
/// terminal splits.
fn draw_locpick(
    frame: &mut Frame,
    area: Rect,
    model: &Model,
    picker: &crate::locpick::LocationPicker,
    theme: Theme,
) -> crate::app::RowGeometry {
    let store = &model.store;
    let cols = area.width as usize;
    let renaming = model.edit.as_ref().and_then(|edit| match &edit.target {
        crate::edit::Target::Location(id) => Some(id.as_str()),
        _ => None,
    });
    let (mut head, current) = locpick_heading(store, picker, renaming, cols, theme);
    let rows = picker.rows(store);
    let cursor_row =
        rows.iter().position(|row| row.target().as_ref() == Some(&picker.cursor)).unwrap_or(0);
    let full = !crate::layout::splits(area.width);
    let room = (area.height as usize).saturating_sub(head.len());
    let offset = cursor_row.saturating_sub(room.saturating_sub(1));
    let heading = head.len();
    let mut items = Vec::new();
    for (index, row) in rows.iter().enumerate().skip(offset).take(room) {
        items.push(index);
        let mut line = tree_line(store, picker, row, current.as_deref(), cols, theme);
        if index == cursor_row {
            line = line.style(theme.selected());
        }
        head.push(line);
    }
    let height = if full {
        area.height
    } else {
        u16::try_from(head.len()).unwrap_or(u16::MAX).min(area.height)
    };
    let rect = Rect {
        x: area.x,
        y: area.y + area.height.saturating_sub(height),
        width: area.width,
        height,
    };
    frame.render_widget(ratatui::widgets::Clear, rect);
    frame.render_widget(Paragraph::new(head), rect);
    crate::app::RowGeometry::rows(rect, rect.y + u16::try_from(heading).unwrap_or(u16::MAX), items)
}

/// The location picker's heading, closed by a rule, and the location the
/// subject is in now.
fn locpick_heading(
    store: &crate::Store,
    picker: &crate::locpick::LocationPicker,
    renaming: Option<&str>,
    cols: usize,
    theme: Theme,
) -> (Vec<Line<'static>>, Option<String>) {
    use crate::locpick::Mode;
    let current = match &picker.mode {
        Mode::File(doc) => store.filed_at(doc).map(str::to_string),
        Mode::Move(moving) => store.locations.parent(moving).map(str::to_string),
    };
    let (crumb, subject, kind, now) = match (&picker.mode, renaming) {
        (_, Some(id)) => (
            "SPC r  rename",
            store.locations.get(id).map_or("", |l| l.name.as_str()),
            "physical location",
            store.locations.path(id),
        ),
        (Mode::File(doc), None) => {
            let doc = store.get(doc);
            let now = doc.map_or_else(|| "unfiled".into(), |doc| store.hard_copy_text(doc));
            ("SPC l  location", doc.map_or("", |doc| doc.name.as_str()), "document", now)
        }
        (Mode::Move(moving), None) => {
            let name = store.locations.get(moving).map_or("", |l| l.name.as_str());
            let now = current
                .as_deref()
                .map_or_else(|| "top level".into(), |id| store.locations.path(id));
            ("SPC m  move…", name, "physical location", now)
        }
    };
    let panel = Panel {
        crumb: format!("{crumb}{}", typed(&picker.filter)),
        filter: Some(&picker.filter),
        cursor: None,
        subject: Some((subject.to_string(), kind, now)),
    };
    let matches = picker
        .rows(store)
        .iter()
        .filter(|row| matches!(row, crate::locpick::Row::Match(_)))
        .count();
    let mut head = panel_heading(&panel, matches, cols, theme);
    head.push(rule(cols, theme));
    (head, current)
}

/// What has been typed into a panel's search, as its crumb shows it.
fn typed(filter: &str) -> String {
    if filter.is_empty() {
        String::new()
    } else {
        format!("  {filter}█")
    }
}

/// A muted rule across the panel, inside the gutters.
fn rule(cols: usize, theme: Theme) -> Line<'static> {
    let gutter = crate::layout::GUTTER as usize;
    Line::styled(
        format!(" {}", "─".repeat(cols.saturating_sub(gutter * 2))),
        theme.style(Tone::Muted),
    )
}

/// A panel's heading: a rule, the crumb with how many rows match, and what
/// it acts on with where that is now.
fn panel_heading(panel: &Panel, matches: usize, cols: usize, theme: Theme) -> Vec<Line<'static>> {
    let gutter = crate::layout::GUTTER as usize;
    let note = match panel.filter {
        None => String::new(),
        Some(filter) if filter.trim().is_empty() => "type to search".to_string(),
        Some(_) => crate::layout::plural(matches, "match", "matches"),
    };
    let crumb =
        truncate(&format!(" {}", panel.crumb), cols.saturating_sub(width(&note) + gutter + 1));
    let mut lines = vec![
        rule(cols, theme),
        spread(
            vec![Span::styled(crumb, theme.style(Tone::Accent))],
            Span::styled(note, theme.style(Tone::Muted)),
            cols,
        ),
    ];
    if let Some((name, kind, now)) = &panel.subject {
        let name = truncate(name, cols.saturating_sub(width(kind) + gutter + 2));
        lines.push(spread(
            vec![Span::styled(format!(" {name}"), theme.style(Tone::Title))],
            Span::styled(kind.to_string(), theme.style(Tone::Muted)),
            cols,
        ));
        lines.push(Line::styled(
            format!(" now: {}", crate::layout::truncate_left(now, cols.saturating_sub(7))),
            theme.style(Tone::Muted),
        ));
    }
    lines
}

/// One row of the location tree.
fn tree_line(
    store: &crate::Store,
    picker: &crate::locpick::LocationPicker,
    row: &crate::locpick::Row,
    current: Option<&str>,
    cols: usize,
    theme: Theme,
) -> Line<'static> {
    use crate::locpick::Row;
    let gutter = crate::layout::GUTTER as usize;
    match row {
        Row::Root => {
            let label = picker
                .root
                .as_deref()
                .map_or_else(|| "locations".into(), |id| store.locations.path(id));
            Line::styled(
                format!(" {}", crate::layout::truncate_left(&label, cols.saturating_sub(2))),
                theme.style(Tone::Title),
            )
        }
        Row::Location { id, lead, open, .. } => {
            let name = store.locations.get(id).map_or("", |l| l.name.as_str());
            let chevron = match open {
                Some(true) => "▾ ",
                Some(false) => "▸ ",
                None => "  ",
            };
            let right = if current == Some(id.as_str()) {
                "now".to_string()
            } else if *open == Some(true) {
                String::new()
            } else {
                let used = 1 + width(lead) + 2 + width(name) + 1 + gutter;
                count(store, picker, id, cols.saturating_sub(used))
            };
            let room = cols.saturating_sub(1 + width(lead) + 2 + width(&right) + 1 + gutter);
            spread(
                vec![
                    Span::styled(format!(" {lead}"), theme.style(Tone::Muted)),
                    Span::styled(chevron, theme.style(Tone::Accent)),
                    Span::raw(truncate(name, room)),
                ],
                Span::styled(right, theme.style(Tone::Muted)),
                cols,
            )
        }
        Row::Doc { index, lead } => Line::from(vec![
            Span::styled(format!(" {lead}"), theme.style(Tone::Muted)),
            Span::styled(
                truncate(&store.docs[*index].name, cols.saturating_sub(1 + width(lead) + gutter)),
                theme.style(Tone::Muted),
            ),
        ]),
        Row::More { lead, hidden, .. } => {
            Line::styled(format!(" {lead}{hidden} more"), theme.style(Tone::Muted))
        }
        Row::New => {
            let place = store.locations.place(picker.anchor.as_deref());
            let text = format!("+ new \"{}\" in {place}", picker.new_name());
            Line::styled(
                format!(" {}", truncate(&text, cols.saturating_sub(1 + gutter))),
                theme.style(Tone::Accent),
            )
        }
        Row::Match(id) => {
            let right = if current == Some(id.as_str()) {
                "now".to_string()
            } else {
                count(store, picker, id, cols / 3)
            };
            let room = cols.saturating_sub(1 + width(&right) + 1 + gutter);
            let path = crate::layout::truncate_left(&store.locations.path(id), room);
            spread(
                vec![Span::raw(format!(" {path}"))],
                Span::styled(right, theme.style(Tone::Muted)),
                cols,
            )
        }
    }
}

/// What a closed location's row says on the right: what it holds, in short
/// words only when `room` is too narrow for the long ones.
fn count(
    store: &crate::Store,
    picker: &crate::locpick::LocationPicker,
    id: &str,
    room: usize,
) -> String {
    let held = if picker.doc().is_some() { store.held(id) } else { 0 };
    let inside = store
        .locations
        .subtree(id)
        .iter()
        .filter(|at| !picker.left_out(store, at))
        .count()
        .saturating_sub(1);
    let (n, long, short) = match (held, inside) {
        (0, 0) => return "empty".into(),
        (0, n) => (n, "location", "loc"),
        (n, _) => (n, "document", "doc"),
    };
    let words = crate::layout::plural(n, long, &format!("{long}s"));
    if width(&words) <= room {
        words
    } else {
        crate::layout::plural(n, short, &format!("{short}s"))
    }
}

/// The heading and selection of a panel drawn over the list.
struct Panel<'a> {
    crumb: String,
    /// The typed search, or `None` for a panel nothing searches.
    filter: Option<&'a str>,
    cursor: Option<usize>,
    /// What it acts on, its kind, and where it is now.
    subject: Option<(String, &'static str, String)>,
}

/// Draws a list panel at the bottom of `area`: a rule, the heading, then one
/// row per `(lead, label, right)`.
fn draw_panel(
    frame: &mut Frame,
    area: Rect,
    panel: &Panel,
    rows: Vec<(String, String, &str)>,
    theme: Theme,
) -> crate::app::RowGeometry {
    let cols = area.width as usize;
    let extra = if panel.subject.is_some() { 2 } else { 0 };
    let height = u16::try_from(rows.len() + 2 + extra).unwrap_or(u16::MAX).min(area.height);
    let rect = Rect {
        x: area.x,
        y: area.y + area.height.saturating_sub(height),
        width: area.width,
        height,
    };
    let mut lines = panel_heading(panel, rows.len(), cols, theme);
    let count = rows.len();
    let room = (height as usize).saturating_sub(lines.len());
    let skip = panel.cursor.map_or(0, |cursor| (cursor + 1).saturating_sub(room));
    for (index, (lead, label, right)) in rows.into_iter().enumerate().skip(skip) {
        let mut line = spread(
            vec![Span::styled(lead, theme.style(Tone::Accent)), Span::raw(label)],
            Span::styled(right.to_string(), theme.style(Tone::Muted)),
            cols,
        );
        if panel.cursor == Some(index) {
            line = line.style(theme.selected());
        }
        lines.push(line);
    }
    let heading = lines.len().saturating_sub(count);
    frame.render_widget(ratatui::widgets::Clear, rect);
    frame.render_widget(Paragraph::new(lines), rect);
    let top = rect.y + u16::try_from(heading).unwrap_or(u16::MAX);
    crate::app::RowGeometry::rows(rect, top, (skip..count).collect())
}

/// The filter chips: what is narrowing the list beyond the query itself.
fn chips(model: &Model) -> String {
    let mut chips = String::new();
    if model.filter.expiring {
        chips.push_str("  [expiring]");
    }
    if model.filter.old_versions {
        chips.push_str("  [old versions]");
    }
    match model.scan_search {
        // It admits when the scan text is still loading: a search that quietly
        // ignores the toggle for two seconds reads as the toggle not working.
        ScanSearch::On => chips.push_str("  [scans]"),
        ScanSearch::Loading => chips.push_str("  [scans…]"),
        ScanSearch::Off => {}
    }
    chips
}

/// The count beside the search: what the view in front holds.
fn count_text(model: &Model) -> String {
    use crate::app::View;
    match model.views.last() {
        Some(View::Versions { doc, .. }) => {
            format!("{} versions", crate::versions::rows(&model.store, doc).len())
        }
        Some(View::Bundles { .. }) => {
            let entries = crate::bundles::entries(&model.store, &model.query);
            let shown = entries.iter().filter(|e| **e != crate::bundles::Entry::New).count();
            format!("{shown}/{} bundles", model.store.bundles.len())
        }
        Some(View::Bundle { id, .. }) => {
            let n = model.store.members(id).len();
            crate::layout::plural(n, "document", "documents")
        }
        _ => format!("{}/{}", model.rows.len(), model.total()),
    }
}

/// The docked search bar.
///
/// **One row on a keyboard layout, two on a touch one.** The second row is not
/// decoration: the whole block is the keyboard target, and one terminal row is
/// too small a thing to ask a thumb to hit against the screen edge.
fn draw_search(frame: &mut Frame, area: Rect, model: &mut Model, theme: Theme) {
    let cols = area.width as usize;
    if area.height <= 1 {
        frame.render_widget(Paragraph::new(keyboard_row(model, cols, theme)), area);
        return;
    }

    // **Status line above, entry line below**, and only the status line is lit.
    // Two widgets rather than one, because a `Paragraph`'s style paints its
    // whole area rather than only the cells its text reaches — which is what
    // makes the band edge to edge, and what keeps it off the row underneath.
    let status = area.height.saturating_sub(1);
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(status), Constraint::Length(1)])
        .split(area);
    let info = match message(model) {
        Some((message, tone)) => caution_lines(&message, cols, tone, theme),
        None => vec![info_row(model, cols, theme)],
    };
    frame.render_widget(Paragraph::new(info).style(theme.band()), rows[0]);

    // An edit takes the entry line over, keeping the chrome at three rows; the
    // `SPC` chip goes with it, as the sheet is unreachable from an edit.
    let entry = if let Some(edit) = &model.edit {
        edit_row(edit, cols)
    } else {
        let key = " SPC ";
        let gutter = crate::layout::GUTTER as usize;
        model.leader_zone = Zone {
            row: rows[1].y,
            col: area.x + u16::try_from(cols.saturating_sub(width(key) + gutter)).unwrap_or(0),
            width: u16::try_from(width(key)).unwrap_or(0),
        };
        entry_row(model, key, cols, theme)
    };
    frame.render_widget(Paragraph::new(entry), rows[1]);
}

/// The keyboard layout's one-row bar: the edit, the query, or where the keys
/// go, with the count on the right.
fn keyboard_row(model: &Model, cols: usize, theme: Theme) -> Line<'static> {
    if let Some(edit) = &model.edit {
        return edit_row(edit, cols);
    }
    let tail = format!("{} ", count_text(model));
    let quiet = theme.style(Tone::Muted);
    if !model.typing_into_query() {
        let room = cols.saturating_sub(width(&tail) + 1);
        return Line::from(vec![
            Span::styled(format!(" {}", fit(&mode_line(model), room)), quiet),
            Span::styled(tail, quiet),
        ]);
    }
    let prompt = " > ";
    let span = cols.saturating_sub(width(prompt) + width(&tail));
    let chips = chips(model);
    let mut line = vec![Span::styled(prompt, theme.style(Tone::Accent))];
    let used = push_query(&mut line, model, span.saturating_sub(width(&chips)), Style::default());
    line.push(Span::raw(fit(&chips, span.saturating_sub(used))));
    line.push(Span::styled(tail, quiet));
    Line::from(line)
}

/// The touch layout's last row: the query as a field, or where the keys go
/// when they do not go into it, closed by the `SPC` chip that opens the sheet.
fn entry_row(model: &Model, key: &'static str, cols: usize, theme: Theme) -> Line<'static> {
    let gutter = crate::layout::GUTTER as usize;
    let prompt = " >";
    let span = cols.saturating_sub(width(prompt) + width(key) + gutter);
    let under = Style::default();
    let quiet = theme.style(Tone::Muted);
    let mut field: Vec<Span> = vec![Span::styled(prompt, theme.style(Tone::Accent))];
    if !model.typing_into_query() {
        // Nothing on the row may look typeable: no prompt and no cursor.
        let room = cols.saturating_sub(width(key) + gutter + 1);
        field = vec![Span::styled(format!(" {}", fit(&mode_line(model), room)), quiet)];
    } else if model.query.is_empty() {
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
            field.push(Span::styled(" ".repeat(span.saturating_sub(3 + width(invite))), under));
        }
    } else {
        field.push(Span::styled(" ", under));
        let used = 1 + push_query(&mut field, model, span.saturating_sub(1), under);
        field.push(Span::styled(" ".repeat(span.saturating_sub(used)), under));
    }
    field.push(Span::styled(key, if model.sheet { theme.lit() } else { theme.pressable() }));
    field.push(Span::raw(" ".repeat(gutter)));
    Line::from(field)
}

/// The last row when typing does not go into the search: where the keys go
/// instead, or the chord half typed.
fn mode_line(model: &Model) -> String {
    use crate::app::View;
    let top = model.views.last();
    if model.armed == Some(Armed::Delete) && model.locpick.is_none() {
        return match top {
            Some(View::Bundle { id, .. }) => {
                let name = model.store.bundle(id).map_or("", |bundle| bundle.name.as_str());
                format!("d · d again to delete {name}, not its documents")
            }
            _ => format!("d · d again to delete {}", model.current().map_or("", |doc| &doc.name)),
        };
    }
    let (what, keys) = if model.sheet {
        ("menu", "letters run verbs")
    } else if model.locpick.is_some() {
        ("locations", "typing searches them")
    } else if let Some(picker) = &model.picker {
        match picker.purpose {
            crate::pick::Purpose::Filter => ("filters", "typing searches them"),
            crate::pick::Purpose::Bundles(_) => ("bundles", "typing searches them"),
            _ => ("choices", "typing narrows them"),
        }
    } else {
        let name = match top {
            Some(View::Versions { .. }) => "versions",
            Some(View::Bundle { .. }) => "bundle",
            _ => "details",
        };
        (name, "letters run verbs")
    };
    format!("{what} · {keys}")
}

/// The touch bar's status row when there is no message: the count, the filter
/// chips, and as many hints as fit.
fn info_row(model: &Model, cols: usize, theme: Theme) -> Line<'static> {
    let gutter = crate::layout::GUTTER as usize;
    let left = format!(" {}{}", count_text(model), chips(model));
    let room = cols.saturating_sub(width(&left) + gutter);
    let hint = shed(&hints(model), room);
    spread(vec![Span::raw(left)], Span::styled(hint, theme.on_band(Tone::Muted)), cols)
}

/// How many rows the status line needs: one, or as many as an armed location
/// delete's caution wraps to.
fn status_rows(model: &Model, cols: u16) -> u16 {
    match (model.armed == Some(Armed::Delete) && model.locpick.is_some(), &model.flash) {
        (true, Some(flash)) => {
            u16::try_from(wrap(flash, cols.saturating_sub(2) as usize).len().clamp(1, 4))
                .unwrap_or(1)
        }
        _ => 1,
    }
}

/// A status message wrapped onto the band, one line per row.
fn caution_lines(message: &str, cols: usize, tone: Tone, theme: Theme) -> Vec<Line<'static>> {
    wrap(message, cols.saturating_sub(2))
        .into_iter()
        .map(|line| Line::styled(format!(" {line}"), theme.on_band(tone)))
        .collect()
}

/// The entry line while a field is being edited: the field's own prompt, what
/// has been typed, and the block cursor.
///
/// The prompt is the field's name rather than `>`, so the row says which
/// question it is asking. Unlike the query, the buffer's cursor is always at
/// the end.
fn edit_row(edit: &crate::edit::Edit, cols: usize) -> Line<'static> {
    let prompt = format!(" {}: ", edit.prompt());
    let room = cols.saturating_sub(width(&prompt) + 1);
    // The tail is kept: the end is where the next character lands.
    let shown = crate::layout::truncate_left(&edit.buffer, room);
    Line::from(vec![
        Span::styled(prompt, Style::default().add_modifier(Modifier::REVERSED)),
        Span::raw(shown),
        Span::raw("█"),
    ])
}

/// Pushes the query with its cursor, cut to `room` columns around the cursor,
/// and returns the columns used.
///
/// The cursor is a reversed cell over the character it sits on, or a `█` past
/// the end, so moving it never shifts the text.
fn push_query(spans: &mut Vec<Span<'static>>, model: &Model, room: usize, style: Style) -> usize {
    let chars: Vec<char> = model.query.chars().collect();
    let at = model.query_cursor.min(chars.len());
    let mut before: Vec<char> = chars[..at].to_vec();
    let under = chars.get(at).copied();
    let mut after: Vec<char> = chars.get(at + 1..).map(<[char]>::to_vec).unwrap_or_default();
    let text = |part: &[char]| part.iter().collect::<String>();
    let cell = under.map_or_else(|| "█".to_string(), String::from);
    let total =
        |before: &[char], after: &[char]| width(&text(before)) + width(&cell) + width(&text(after));
    while total(&before, &after) > room && !after.is_empty() {
        after.pop();
    }
    while total(&before, &after) > room && !before.is_empty() {
        before.remove(0);
    }
    let used = total(&before, &after);
    spans.push(Span::styled(text(&before), style));
    spans.push(if under.is_some() {
        Span::styled(cell, style.add_modifier(Modifier::REVERSED))
    } else {
        Span::styled(cell, style)
    });
    spans.push(Span::styled(text(&after), style));
    used
}

/// The location picker's hints, which follow what the cursor is on.
fn locpick_hints(picker: &crate::locpick::LocationPicker) -> Vec<&'static str> {
    use crate::locpick::{Mode, Target};
    match (&picker.cursor, &picker.mode) {
        (Target::More(_), _) => vec!["⏎ show all", "esc back"],
        (Target::New, _) => vec!["⏎ create and file here", "esc clear"],
        (Target::Match(_), Mode::File(_)) => vec!["⏎ file here", "↑ new", "esc clear"],
        (_, Mode::Move(_)) => vec!["⏎ move here", "→ open", "esc cancel"],
        (Target::Root, _) if picker.root.is_none() => vec!["↓ pick a location", "esc back"],
        (Target::Root, _) => vec!["⏎ file here", "← up", "esc back"],
        _ => vec!["⏎ file here", "→ open", "← close", "esc back"],
    }
}

/// This surface's hints, most sheddable first. Per surface only, never
/// another surface's verbs, and a verb appears when it works, not before.
fn hints(model: &Model) -> Vec<&'static str> {
    use crate::app::View;
    if model.edit.is_some() {
        return if model.edit.as_ref().is_some_and(|edit| edit.matches().is_empty()) {
            vec!["⏎ save", "esc discard"]
        } else {
            vec!["↑↓ choose", "tab fill", "⏎ save", "esc discard"]
        };
    }
    if model.sheet {
        return vec!["letter runs it", "esc back"];
    }
    if model.picker.as_ref().is_some_and(crate::pick::Picker::checklist) {
        return vec!["type to search", "⏎ toggle", "esc back"];
    }
    if let Some(picker) = &model.locpick {
        return locpick_hints(picker);
    }
    if model.picker.is_some() {
        return vec!["type to narrow", "⏎ choose", "esc back"];
    }
    if model.armed == Some(Armed::Delete) {
        return vec!["any other key cancels"];
    }
    let ready = model.write.ready();
    let mut hints = match model.views.last() {
        None if model.query.is_empty() => vec!["⏎ record"],
        None => vec!["esc clear", "⏎ record"],
        Some(View::Details { .. }) if ready => vec!["esc back", "⏎ open file", "e edit"],
        Some(View::Details { .. }) => vec!["esc back", "⏎ open file"],
        Some(View::Bundles { selected: crate::bundles::Entry::New, .. }) => {
            vec!["esc back", "⏎ create"]
        }
        Some(View::Versions { .. } | View::Bundles { .. }) => vec!["esc back", "⏎ open"],
        Some(View::Bundle { selected: crate::bundles::Row::Member(_), .. }) => {
            vec!["esc back", "⏎ open", "e edit"]
        }
        Some(View::Bundle { .. }) => vec!["esc back", "e edit"],
    };
    // Offered only once there is something to take back, and likewise for the
    // way forward: a hint on a session that has written nothing teaches a key
    // that answers with an apology.
    if model.pane() && ready && !model.undo.is_empty() {
        hints.push("u undo");
    }
    if model.pane() && ready && !model.redo.is_empty() {
        hints.push("r redo");
    }
    // Space types a space once something is typed, so the menu is offered only
    // where Space opens it.
    if model.pane() || model.query.is_empty() {
        hints.push("space menu");
    }
    hints
}

/// Fits as many hints as the room allows, dropping them one at a time from
/// the left.
fn shed(hints: &[&str], room: usize) -> String {
    for start in 0..hints.len() {
        let line = hints[start..].join("  ");
        if width(&line) <= room {
            return line;
        }
    }
    String::new()
}

/// What the status line says instead of its hints, when there is something
/// to say.
fn message(model: &Model) -> Option<(String, Tone)> {
    if let Some(flash) = &model.flash {
        return Some((flash.clone(), Tone::Flash));
    }
    if model.armed == Some(Armed::Esc) {
        return Some(("esc again to quit".into(), Tone::Armed));
    }
    let edit = model.edit.as_ref()?;
    if edit.armed_discard {
        Some(("esc again to discard".into(), Tone::Armed))
    } else if edit.saving {
        Some(("saving…".into(), Tone::Muted))
    } else {
        None
    }
}

/// The keyboard layout's hint line.
fn draw_footer(frame: &mut Frame, area: Rect, model: &Model, theme: Theme) {
    let (message, tone) = message(model)
        .unwrap_or_else(|| (format!("{}  ^q quit", hints(model).join("  ")), Tone::Muted));
    // Lit, like the touch layout's — a keyboard layout has the same two rows in
    // the same order, and the same rule dividing the list from the entry line.
    let lines = if area.height > 1 {
        caution_lines(&message, area.width as usize, tone, theme)
    } else {
        vec![Line::styled(
            format!(" {}", truncate(&message, area.width as usize - 1)),
            theme.on_band(tone),
        )]
    };
    frame.render_widget(Paragraph::new(lines).style(theme.band()), area);
}
