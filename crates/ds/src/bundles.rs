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

//! The Bundles view, which lists bundles in the list's place, and a bundle's
//! Details view: its own rows, then one row per document version in it.

use ratatui::layout::Rect;
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::Frame;

use crate::app::{Model, RowGeometry};
use crate::detail;
use crate::edit::Field;
use crate::layout::{cursor_cell, fit, scroll, short_date, spread, truncate, width};
use crate::theme::{Theme, Tone};
use crate::{Bundle, Store};

/// One row of the Bundles view.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Entry {
    /// `+ new`, pinned above the matches.
    New,
    /// A bundle, by id.
    Bundle(String),
}

/// The Bundles view's rows for `query`: `+ new` once something is typed, or
/// always when there are no bundles, then the bundles whose names match.
#[must_use]
pub fn entries(store: &Store, query: &str) -> Vec<Entry> {
    let typed = !query.trim().is_empty();
    let mut entries = Vec::new();
    if typed || store.bundles.is_empty() {
        entries.push(Entry::New);
    }
    let query = crate::search::Query::new(query);
    entries.extend(
        store
            .bundles
            .iter()
            .filter(|bundle| query.matches(&crate::search::fold(&bundle.name), false))
            .map(|bundle| Entry::Bundle(bundle.id.clone())),
    );
    entries
}

/// Where the selection starts: on the first match, or on `+ new` when nothing
/// matches.
#[must_use]
pub fn first(entries: &[Entry]) -> Entry {
    let at = usize::from(entries.len() > 1 && entries[0] == Entry::New);
    entries.get(at).cloned().unwrap_or(Entry::New)
}

/// One row of a bundle's Details view.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Row {
    /// What the bundle is called.
    Name,
    /// The date it is for.
    Date,
    /// Free text.
    Notes,
    /// A document version in it, by the version's id.
    Member(String),
}

/// Every row of bundle `id`, in the order it is drawn.
#[must_use]
pub fn rows(store: &Store, id: &str) -> Vec<Row> {
    let mut rows = vec![Row::Name, Row::Date, Row::Notes];
    rows.extend(
        store.members(id).into_iter().map(|member| Row::Member(store.docs[member.doc].id.clone())),
    );
    rows
}

/// How many document versions a bundle holds, said briefly.
fn holds(store: &Store, bundle: &Bundle) -> String {
    let n = store.members(&bundle.id).len();
    crate::layout::plural(n, "doc", "docs")
}

/// Draws the Bundles view's list, and returns where each entry landed.
pub fn draw_list(
    frame: &mut Frame,
    area: Rect,
    model: &Model,
    selected: &Entry,
    theme: Theme,
) -> RowGeometry {
    let cols = area.width as usize;
    let gutter = crate::layout::GUTTER as usize;
    let entries = entries(&model.store, &model.query);
    let cursor = crate::app::position(&entries, selected);
    let height = area.height as usize;
    let skip = (cursor + 1).saturating_sub(height);
    let mut lines = Vec::new();
    for (index, entry) in entries.iter().enumerate().skip(skip).take(height) {
        let lead = cursor_cell(index == cursor);
        let line = match entry {
            Entry::New => {
                let typed = model.query.trim();
                let text = if typed.is_empty() {
                    "+ new bundle".to_string()
                } else {
                    format!("+ new \"{typed}\"")
                };
                Line::from(vec![
                    Span::raw(lead),
                    Span::styled(fit(&text, cols.saturating_sub(2)), theme.style(Tone::Accent)),
                ])
            }
            Entry::Bundle(id) => {
                let Some(bundle) = model.store.bundle(id) else { continue };
                let right = format!(
                    "{}  {}",
                    holds(&model.store, bundle),
                    bundle.date.as_deref().map_or_else(|| "     ".into(), short_date)
                );
                let room = cols.saturating_sub(width(lead) + width(&right) + 2 + gutter);
                spread(
                    vec![Span::raw(lead), Span::raw(truncate(&bundle.name, room))],
                    Span::styled(right, theme.style(Tone::Muted)),
                    cols,
                )
            }
        };
        lines.push(if index == cursor { line.style(theme.selected()) } else { line });
    }
    if entries.iter().all(|entry| *entry == Entry::New) && !model.query.trim().is_empty() {
        lines.push(Line::styled("  nothing matches", theme.style(Tone::Muted)));
    }
    frame.render_widget(Paragraph::new(lines), area);
    RowGeometry::rows(area, area.y, (skip..entries.len()).take(height).collect())
}

/// Draws bundle `id`'s Details view with row `selected` selected, and returns
/// where each row landed.
pub fn draw_bundle(
    frame: &mut Frame,
    area: Rect,
    model: &Model,
    id: &str,
    selected: &Row,
    theme: Theme,
) -> RowGeometry {
    let cols = area.width as usize;
    let gutter = crate::layout::GUTTER as usize;
    let Some(bundle) = model.store.bundle(id) else { return RowGeometry::default() };
    let members = model.store.members(id);
    let inner = cols.saturating_sub(2);
    let editing = |field: Field| {
        model.edit.as_ref().is_some_and(|edit| {
            edit.target == crate::edit::Target::Bundle(id.to_string()) && edit.field == field
        })
    };
    let rows = rows(&model.store, id);
    let cursor = crate::app::position(&rows, selected);
    let mut lines = Vec::new();
    let mut owners = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let drawn = match row {
            Row::Name => vec![
                detail::title(&bundle.name, editing(Field::Name), inner, theme),
                Line::styled(" bundle", theme.style(Tone::Muted)),
            ],
            Row::Date => vec![detail::labelled(
                "date",
                &detail::nonempty(bundle.date.clone().unwrap_or_default()),
                inner,
                theme,
                editing(Field::Expiry),
            )],
            Row::Notes => vec![detail::labelled(
                "notes",
                &detail::nonempty(bundle.notes.clone()),
                inner,
                theme,
                editing(Field::Notes),
            )],
            Row::Member(doc) => {
                let Some(doc) = model.store.get(doc) else {
                    continue;
                };
                let right = if doc.superseded { "newer exists" } else { "" };
                let name = crate::detail::version_name(doc);
                let room = cols.saturating_sub(3 + width(right) + 1 + gutter);
                let mut drawn = Vec::new();
                if rows[index - 1] == Row::Notes {
                    drawn.push(Line::raw(""));
                    drawn.push(Line::styled(" documents", theme.style(Tone::Muted)));
                }
                drawn.push(spread(
                    vec![Span::raw("   "), Span::raw(truncate(&name, room))],
                    Span::styled(right, theme.status(crate::Status::Soon)),
                    cols,
                ));
                drawn
            }
        };
        let selected = index == cursor;
        let count = drawn.len();
        for (n, line) in drawn.into_iter().enumerate() {
            let body = n + 1 == count || !matches!(row, Row::Member(_));
            lines.push(if selected && body { line.style(theme.selected()) } else { line });
            owners.push(if body { index } else { usize::MAX });
        }
    }
    if members.is_empty() {
        lines.push(Line::raw(""));
        lines.push(Line::styled(" no documents yet", theme.style(Tone::Muted)));
    }
    let height = area.height as usize;
    let skip = scroll(&owners, cursor, height);
    let lines: Vec<Line> = lines.into_iter().skip(skip).collect();
    frame.render_widget(Paragraph::new(lines), area);
    RowGeometry::rows(area, area.y, owners.into_iter().skip(skip).take(height).collect())
}
