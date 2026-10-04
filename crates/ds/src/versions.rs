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

//! The Versions view: every version of one document, newest first, two lines
//! each — its dates and standing, then where its hard copy is filed.

use ratatui::layout::Rect;
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::Frame;

use crate::app::{Model, RowGeometry};
use crate::layout::{truncate, truncate_left, width, wrap};
use crate::theme::{Theme, Tone};
use crate::{Doc, HardCopy, Status, Store};

/// What a conflicting latest version is, said where one is listed.
const CONFLICT_NOTE: &str = "conflicting latest: a second document that also claims to replace \
                             this one, not a copy of the same paper";

/// The versions of the document `id` belongs to, newest first: the latest one,
/// then any conflicting latest, then the older ones.
#[must_use]
pub fn rows(store: &Store, id: &str) -> Vec<usize> {
    let mut rows = store.versions(id);
    rows.reverse();
    rows.sort_by_key(|&i| (store.docs[i].superseded, store.docs[i].conflicting));
    rows
}

/// What the right-hand side of a version's row says, and in which style.
fn standing(model: &Model, doc: &Doc) -> (String, Option<Status>) {
    if doc.conflicting {
        return ("conflicting latest".into(), Some(Status::Expired));
    }
    if !doc.superseded {
        return ("latest".into(), None);
    }
    match doc.dated(&model.today, &model.warn_until) {
        Some(status @ Status::Expired) => (format!("{} expired", status.marker()), Some(status)),
        Some(status @ Status::Soon) => (format!("{} soon", status.marker()), Some(status)),
        _ => (String::new(), None),
    }
}

/// The two lines of one version.
fn version_lines(
    model: &Model,
    doc: &Doc,
    selected: bool,
    cols: usize,
    theme: Theme,
) -> [Line<'static>; 2] {
    let date = |value: &Option<String>| value.clone().unwrap_or_else(|| "—".into());
    let dates = format!("{} → {}", date(&doc.issue_date), date(&doc.expiry_date));
    let (right, status) = standing(model, doc);
    let lead = if selected { "▸ " } else { "  " };
    let gutter = crate::layout::GUTTER as usize;
    let room = cols.saturating_sub(width(lead) + width(&right) + 1 + gutter);
    let dates = truncate(&dates, room);
    let gap = cols.saturating_sub(width(lead) + width(&dates) + width(&right) + gutter);
    let right_style = match status {
        Some(status) => theme.status(status),
        None => theme.style(Tone::Accent),
    };
    let first = Line::from(vec![
        Span::raw(lead),
        Span::raw(dates),
        Span::raw(" ".repeat(gap)),
        Span::styled(right, right_style),
    ]);
    let place = match model.store.hard_copy(doc) {
        HardCopy::At(_) => model.store.place(doc),
        HardCopy::Unfiled => "unfiled".into(),
        HardCopy::DigitalOnly => "digital only".into(),
    };
    let second = Line::styled(
        format!("    {}", truncate_left(&place, cols.saturating_sub(4 + gutter))),
        theme.style(Tone::Muted),
    );
    if selected {
        [first.style(theme.selected()), second.style(theme.selected())]
    } else {
        [first, second]
    }
}

/// Draws the versions of the document version `id` belongs to, `id`
/// selected, and returns where each version landed so a tap can find it.
pub fn draw(frame: &mut Frame, area: Rect, model: &Model, id: &str, theme: Theme) -> RowGeometry {
    let cols = area.width as usize;
    let rows = rows(&model.store, id);
    let cursor = rows.iter().position(|&i| model.store.docs[i].id == id).unwrap_or(0);
    let name = model.store.index_of(id).map_or("", |i| model.store.docs[i].name.as_str());
    let mut heading = vec![
        Line::styled(
            format!(" {}", truncate(name, cols.saturating_sub(2))),
            theme.style(Tone::Title),
        ),
        Line::styled(" versions, newest first", theme.style(Tone::Muted)),
    ];
    if rows.iter().any(|&i| model.store.docs[i].conflicting) {
        heading.extend(
            wrap(CONFLICT_NOTE, cols.saturating_sub(2))
                .into_iter()
                .map(|line| Line::styled(format!(" {line}"), theme.style(Tone::Muted))),
        );
    }
    heading.push(Line::raw(""));

    let mut lines = Vec::new();
    let mut owners = Vec::new();
    for (index, &doc) in rows.iter().enumerate() {
        lines.extend(version_lines(model, &model.store.docs[doc], index == cursor, cols, theme));
        owners.extend([index, index]);
    }
    let room = (area.height as usize).saturating_sub(heading.len());
    let skip = (2 * (cursor + 1)).saturating_sub(room);
    let top = area.y + u16::try_from(heading.len()).unwrap_or(u16::MAX);
    heading.extend(lines.into_iter().skip(skip));
    frame.render_widget(Paragraph::new(heading), area);
    RowGeometry {
        top,
        left: area.x,
        width: area.width,
        items: owners.into_iter().skip(skip).take(room).collect(),
        ..Default::default()
    }
}
