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
use crate::layout::{fit, short_date, truncate, width};
use crate::theme::{Theme, Tone};
use crate::{Bundle, Store};

/// One row of the Bundles view.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Entry {
    /// `+ new`, pinned above the matches.
    New,
    /// A bundle, by index into [`Store::bundles`].
    Bundle(usize),
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
    entries.extend(
        store
            .bundles
            .iter()
            .enumerate()
            .filter(|(_, bundle)| {
                !typed || crate::search::matches(&crate::search::fold(&bundle.name), query, false)
            })
            .map(|(i, _)| Entry::Bundle(i)),
    );
    entries
}

/// Where the cursor starts: on the first match, or on `+ new` when nothing
/// matches.
#[must_use]
pub fn first(entries: &[Entry]) -> usize {
    usize::from(entries.len() > 1 && entries[0] == Entry::New)
}

/// One row of a bundle's Details view.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Row {
    /// What the bundle is called.
    Name,
    /// The date it is for.
    Date,
    /// Free text.
    Notes,
    /// A document version in it, by index into [`Store::members`].
    Member(usize),
}

/// Every row of bundle `id`, in the order it is drawn.
#[must_use]
pub fn rows(store: &Store, id: &str) -> Vec<Row> {
    let mut rows = vec![Row::Name, Row::Date, Row::Notes];
    rows.extend((0..store.members(id).len()).map(Row::Member));
    rows
}

/// How many document versions a bundle holds, said briefly.
fn holds(store: &Store, bundle: &Bundle) -> String {
    let n = store.members(&bundle.id).len();
    format!("{n} doc{}", if n == 1 { "" } else { "s" })
}

/// Draws the Bundles view's list, and returns where each entry landed.
pub fn draw_list(
    frame: &mut Frame,
    area: Rect,
    model: &Model,
    cursor: usize,
    theme: Theme,
) -> RowGeometry {
    let cols = area.width as usize;
    let gutter = crate::layout::GUTTER as usize;
    let entries = entries(&model.store, &model.query);
    let height = area.height as usize;
    let skip = (cursor + 1).saturating_sub(height);
    let mut lines = Vec::new();
    for (index, entry) in entries.iter().enumerate().skip(skip).take(height) {
        let lead = if index == cursor { "▸ " } else { "  " };
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
            Entry::Bundle(i) => {
                let bundle = &model.store.bundles[*i];
                let right = format!(
                    "{}  {}",
                    holds(&model.store, bundle),
                    bundle.date.as_deref().map_or_else(|| "     ".into(), short_date)
                );
                let room = cols.saturating_sub(width(lead) + width(&right) + 2 + gutter);
                let name = truncate(&bundle.name, room);
                let gap = cols.saturating_sub(width(lead) + width(&name) + width(&right) + gutter);
                Line::from(vec![
                    Span::raw(lead),
                    Span::raw(name),
                    Span::raw(" ".repeat(gap)),
                    Span::styled(right, theme.style(Tone::Muted)),
                ])
            }
        };
        lines.push(if index == cursor { line.style(theme.selected()) } else { line });
    }
    if entries.iter().all(|entry| *entry == Entry::New) && !model.query.trim().is_empty() {
        lines.push(Line::styled("  nothing matches", theme.style(Tone::Muted)));
    }
    frame.render_widget(Paragraph::new(lines), area);
    RowGeometry {
        top: area.y,
        left: area.x,
        width: area.width,
        items: (skip..entries.len()).take(height).collect(),
    }
}

/// Draws bundle `id`'s Details view with row `cursor` selected, and returns
/// where each row landed.
pub fn draw_bundle(
    frame: &mut Frame,
    area: Rect,
    model: &Model,
    id: &str,
    cursor: usize,
    theme: Theme,
) -> RowGeometry {
    let cols = area.width as usize;
    let gutter = crate::layout::GUTTER as usize;
    let Some(bundle) = model.store.bundle(id) else { return RowGeometry::default() };
    let members = model.store.members(id);
    let editing = |field: crate::edit::Field| {
        model.edit.as_ref().is_some_and(|edit| edit.doc == id && edit.field == field)
    };
    let label = |text: &str, lit: bool| {
        let style = if lit { theme.band() } else { theme.style(Tone::Muted) };
        Span::styled(format!(" {text:<9}"), style)
    };
    let mut lines = Vec::new();
    let mut owners = Vec::new();
    for (index, row) in rows(&model.store, id).into_iter().enumerate() {
        let drawn = match row {
            Row::Name => {
                let mut style = theme.style(Tone::Title);
                if editing(crate::edit::Field::BundleName) {
                    style = style.add_modifier(ratatui::style::Modifier::REVERSED);
                }
                vec![
                    Line::styled(
                        format!(" {}", truncate(&bundle.name, cols.saturating_sub(2))),
                        style,
                    ),
                    Line::styled(" bundle", theme.style(Tone::Muted)),
                ]
            }
            Row::Date => vec![Line::from(vec![
                label("date", editing(crate::edit::Field::BundleDate)),
                Span::raw(bundle.date.clone().unwrap_or_else(|| "—".into())),
            ])],
            Row::Notes => {
                let notes = if bundle.notes.is_empty() { "—" } else { bundle.notes.as_str() };
                vec![Line::from(vec![
                    label("notes", editing(crate::edit::Field::BundleNotes)),
                    Span::raw(truncate(notes, cols.saturating_sub(11 + gutter))),
                ])]
            }
            Row::Member(k) => {
                let doc = &model.store.docs[members[k].doc];
                let right = if doc.superseded { "newer exists" } else { "" };
                let name = crate::detail::version_name(doc);
                let room = cols.saturating_sub(3 + width(right) + 1 + gutter);
                let name = truncate(&name, room);
                let gap = cols.saturating_sub(3 + width(&name) + width(right) + gutter);
                let mut drawn = Vec::new();
                if k == 0 {
                    drawn.push(Line::raw(""));
                    drawn.push(Line::styled(" documents", theme.style(Tone::Muted)));
                }
                drawn.push(Line::from(vec![
                    Span::raw("   "),
                    Span::raw(name),
                    Span::raw(" ".repeat(gap)),
                    Span::styled(right, theme.status(crate::Status::Soon)),
                ]));
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
    let end = owners.iter().rposition(|&owner| owner == cursor).map_or(0, |at| at + 1);
    let skip = end.saturating_sub(height);
    let lines: Vec<Line> = lines.into_iter().skip(skip).collect();
    frame.render_widget(Paragraph::new(lines), area);
    RowGeometry {
        top: area.y,
        left: area.x,
        width: area.width,
        items: owners.into_iter().skip(skip).take(height).collect(),
    }
}
