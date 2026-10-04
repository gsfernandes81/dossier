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

//! The Details view: one document's rows, and the selector its verbs act on.
//!
//! The layout is in REWRITE-UI.md. `e` edits the selected row.
//!
//! [`rows`] is the list the selector walks and the renderer draws, so a
//! highlight can never land on a row the reader is not looking at.

use ratatui::layout::Rect;
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::Frame;

use crate::app::Model;
use crate::layout::{truncate, width, wrap};
use crate::theme::{Theme, Tone};
use crate::Status;

/// Label column, so values line up under each other.
const LABEL_COLS: usize = 10;

/// One row of the record — the unit the selector moves over, and every one
/// of them changes with `e`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Row {
    /// A field typed on the bottom line, and the field it writes.
    Editable(crate::edit::Field),
    /// The bundles this version is in.
    Bundles,
    /// The files row when there are none.
    Files,
    /// One linked file, by index into `doc.files`.
    File(usize),
    /// Where the hard copy is filed.
    Location,
    /// The digital-only checkbox.
    DigitalOnly,
    /// The older version this one replaces.
    Renews,
}

/// Every row of the record, in the order it is drawn.
///
/// The selector and the renderer both read this, which is the same rule the
/// list's geometry follows: a hit test or a highlight that re-derives a layout
/// disagrees with it at the first edge case.
///
/// **An editable field is always a row, even when it is empty.** A row that
/// appeared only once it had a value could never be the row you use to give it
/// one — the empty `—` is the affordance, not clutter. Rows this build cannot
/// change stay conditional, because there is nothing to do with an absent one.
#[must_use]
pub fn rows(doc: &crate::Doc) -> Vec<Row> {
    use crate::edit::Field;
    let mut rows = vec![Row::Editable(Field::Name)];
    if doc.location.as_deref() != Some(crate::place::DIGITAL_ONLY) {
        rows.push(Row::Location);
    }
    rows.push(Row::DigitalOnly);
    rows.extend([
        Row::Editable(Field::Expiry),
        Row::Editable(Field::Issued),
        Row::Editable(Field::Tags),
        Row::Bundles,
    ]);
    if doc.files.is_empty() {
        rows.push(Row::Files);
    } else {
        rows.extend((0..doc.files.len()).map(Row::File));
    }
    rows.push(Row::Renews);
    rows.push(Row::Editable(Field::Notes));
    rows
}

/// Draws the Details view for the highlighted document, and returns where each
/// row landed so a tap can find it.
pub fn draw(frame: &mut Frame, area: Rect, model: &Model, theme: Theme) -> crate::app::RowGeometry {
    let inner = area.width.saturating_sub(2) as usize;
    let Some(doc) = model.current() else {
        frame.render_widget(
            Paragraph::new(Line::styled(" nothing selected", theme.style(Tone::Muted))),
            area,
        );
        return crate::app::RowGeometry::default();
    };

    let rows = rows(doc);
    let selected = model.record_cursor().min(rows.len().saturating_sub(1));
    let mut lines: Vec<Line> = Vec::new();
    let mut owners = Vec::new();
    for (index, row) in rows.iter().enumerate() {
        let mut drawn = render_row(*row, doc, model, inner, theme);
        if index == selected {
            drawn = drawn.into_iter().map(|line| line.style(theme.selected())).collect();
        }
        owners.extend(std::iter::repeat_n(index, drawn.len()));
        lines.extend(drawn);
    }

    // Scroll only when the selection would fall off the bottom. A record fits
    // on the phone in every ordinary case; this is for the document with eight
    // files, where a selector you cannot see is worse than no selector.
    let height = area.height as usize;
    let skip = crate::layout::scroll(&owners, selected, height);
    let lines: Vec<Line> = lines.into_iter().skip(skip).collect();
    let items = owners.into_iter().skip(skip).take(height).collect();

    // No widget-level wrap: everything above is already laid out to this pane's
    // width, and letting the widget wrap as well would put a continuation line
    // at the left margin, where it reads as a new field.
    frame.render_widget(Paragraph::new(lines), area);
    crate::app::RowGeometry::rows(area, area.y, items)
}

/// The lines one row occupies.
fn render_row(
    row: Row,
    doc: &crate::Doc,
    model: &Model,
    inner: usize,
    theme: Theme,
) -> Vec<Line<'static>> {
    match row {
        Row::Editable(what) => render_editable(what, doc, model, inner, theme),
        Row::Location => {
            let label = "hard copy location";
            let value = model.store.hard_copy_text(doc);
            let room = inner.saturating_sub(width(label) + 2);
            vec![Line::from(vec![
                Span::styled(format!(" {label} "), theme.style(Tone::Muted)),
                Span::raw(crate::layout::truncate_left(&value, room)),
            ])]
        }
        Row::DigitalOnly => {
            let on = doc.location.as_deref() == Some(crate::place::DIGITAL_ONLY);
            vec![Line::from(vec![
                Span::styled(
                    format!(" [{}] ", if on { "x" } else { " " }),
                    theme.style(Tone::Accent),
                ),
                Span::raw("digital only (no hard copy)"),
            ])]
        }
        Row::Bundles => {
            let names: Vec<&str> =
                model.store.bundles_of(doc).map(|bundle| bundle.name.as_str()).collect();
            vec![field("bundles", &nonempty(names.join(" · ")), inner, theme)]
        }
        Row::Files => vec![field("files", "none", inner, theme)],
        // The arrow marks the primary file, the one `Enter` opens.
        Row::File(i) => {
            let primary = doc.primary_file().map(|f| f.path.clone());
            let file = &doc.files[i];
            let mut spans = vec![if i == 0 {
                label("files", theme)
            } else {
                Span::raw(" ".repeat(LABEL_COLS + 1))
            }];
            if i == 0 {
                spans.push(Span::styled(format!("{} ", doc.files.len()), theme.style(Tone::Muted)));
            }
            spans.push(Span::styled(
                if Some(&file.path) == primary.as_ref() { "▸ " } else { "  " }.to_string(),
                theme.style(Tone::Accent),
            ));
            spans.push(Span::raw(truncate(&file.path, inner.saturating_sub(LABEL_COLS + 5))));
            vec![Line::from(spans)]
        }
        Row::Renews => {
            let title = renews(&model.store, doc);
            vec![field("renews", &nonempty(title), inner, theme)]
        }
    }
}

/// The older version a document replaces, by name and issue date, or empty.
#[must_use]
pub fn renews(store: &crate::Store, doc: &crate::Doc) -> String {
    let Some(older) = doc.supersedes.as_deref() else { return String::new() };
    match store.get(older) {
        Some(older) => version_name(older),
        None => older.to_string(),
    }
}

/// A version's name, with its issue date when it has one.
#[must_use]
pub fn version_name(doc: &crate::Doc) -> String {
    match &doc.issue_date {
        Some(issued) => format!("{} · issued {issued}", doc.name),
        None => doc.name.clone(),
    }
}

/// The lines an editable field occupies.
///
/// Split out because these rows share a rule the others do not: **while one is
/// being edited its label is lit.** The value on the row is the *stored* one and
/// the one being typed is down on the entry line, so the mark is what says those
/// two rows are about each other.
fn render_editable(
    what: crate::edit::Field,
    doc: &crate::Doc,
    model: &Model,
    inner: usize,
    theme: Theme,
) -> Vec<Line<'static>> {
    use crate::edit::Field;
    let lit = model.edit.as_ref().is_some_and(|edit| {
        edit.target == crate::edit::Target::Doc(doc.id.clone()) && edit.field == what
    });
    match what {
        // Typed from a picker; never a row of its own.
        Field::Attach => Vec::new(),
        Field::Name => vec![title(&doc.name, lit, inner, theme), Line::raw("")],
        // The standing in words, so nobody does date arithmetic.
        Field::Expiry => {
            let status = model.status(doc);
            vec![Line::from(vec![
                head("expiry", lit, theme),
                Span::raw(doc.expiry_date.clone().unwrap_or_else(|| "—".into())),
                Span::raw("  "),
                Span::styled(
                    match status {
                        Status::Expired => "! expired",
                        Status::Soon => "~ expiring soon",
                        Status::Ok => "  tracked",
                        Status::Untracked if doc.superseded => "· superseded",
                        Status::Untracked if doc.ignore_expiry => "· watch ignored",
                        Status::Untracked => "· no expiry",
                    }
                    .to_string(),
                    theme.status(status),
                ),
            ])]
        }
        Field::Issued => vec![labelled(
            "issued",
            &nonempty(doc.issue_date.clone().unwrap_or_default()),
            inner,
            theme,
            lit,
        )],
        Field::Tags => {
            vec![labelled("tags", &nonempty(doc.tags.join(" ")), inner, theme, lit)]
        }
        Field::Notes => wrapped_field("notes", &nonempty(doc.notes.clone()), inner, theme, lit),
    }
}

/// An em dash beats a blank: an empty value and a missing field look identical
/// on screen otherwise, and only one of them is worth fixing.
pub(crate) fn nonempty(value: String) -> String {
    if value.is_empty() {
        "—".into()
    } else {
        value
    }
}

fn label(text: &str, theme: Theme) -> Span<'static> {
    Span::styled(format!(" {text:<LABEL_COLS$}"), theme.style(Tone::Muted))
}

/// The label of the field currently being edited.
///
/// Reverse video needs no colour; underline lands in the descenders on the
/// phone's font and dim may be ignored.
fn marked_label(text: &str, theme: Theme) -> Span<'static> {
    Span::styled(
        format!(" {text:<LABEL_COLS$}"),
        theme.style(Tone::Accent).add_modifier(ratatui::style::Modifier::REVERSED),
    )
}

/// A record's title. It carries no label, so being edited is marked on the name.
pub(crate) fn title(name: &str, lit: bool, inner: usize, theme: Theme) -> Line<'static> {
    let mut style = theme.style(Tone::Title);
    if lit {
        style = style.add_modifier(ratatui::style::Modifier::REVERSED);
    }
    Line::styled(format!(" {}", truncate(name, inner)), style)
}

/// A field's label, lit when that field is the one being edited.
pub(crate) fn head(name: &str, lit: bool, theme: Theme) -> Span<'static> {
    if lit {
        marked_label(name, theme)
    } else {
        label(name, theme)
    }
}

/// A one-line field: label, then the value cut to whatever the pane leaves.
fn field(name: &str, value: &str, inner: usize, theme: Theme) -> Line<'static> {
    labelled(name, value, inner, theme, false)
}

/// [`field`], for a row that can be the one under edit.
pub(crate) fn labelled(
    name: &str,
    value: &str,
    inner: usize,
    theme: Theme,
    lit: bool,
) -> Line<'static> {
    let value_cols = inner.saturating_sub(LABEL_COLS).max(8);
    Line::from(vec![head(name, lit, theme), Span::raw(truncate(value, value_cols))])
}

/// A field whose value is free text: wrapped, with continuations **hanging under
/// the value column** so the field still reads as one thing.
fn wrapped_field(
    name: &str,
    value: &str,
    inner: usize,
    theme: Theme,
    lit: bool,
) -> Vec<Line<'static>> {
    let value_cols = inner.saturating_sub(LABEL_COLS).max(8);
    let mut lines = Vec::new();
    for (i, chunk) in wrap(value, value_cols).into_iter().enumerate() {
        let first =
            if i == 0 { head(name, lit, theme) } else { Span::raw(" ".repeat(LABEL_COLS + 1)) };
        lines.push(Line::from(vec![first, Span::raw(chunk)]));
    }
    lines
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::edit::Field;

    /// **An empty editable field is still a row.** A row that appeared only once
    /// it had a value could never be the row you use to give it one — this is
    /// the whole affordance for adding notes or an issue date, so it has to hold
    /// for a document with none of them.
    #[test]
    fn every_editable_field_has_a_row_even_when_it_is_empty() {
        let model = crate::app::tests::model();
        let doc = model.current().expect("a document");
        assert!(doc.notes.is_empty() && doc.tags.is_empty() && doc.issue_date.is_none());

        let rows = rows(doc);
        for field in [Field::Name, Field::Expiry, Field::Issued, Field::Tags, Field::Notes] {
            assert!(rows.contains(&Row::Editable(field)), "{field:?} has no row: {rows:?}");
        }
    }
}
