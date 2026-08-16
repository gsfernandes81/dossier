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

//! The record: everything about one document on one screen.
//!
//! REWRITE-UI.md §2 and the approved phone mockup — location, expiry with its
//! standing spelled out, issue date, tags, bundles, the file list with the
//! primary marked, what it renews, and notes. The user's review call was
//! *"detail looks good for now"*, so this renders that layout and nothing more.
//!
//! It is **read-only in R3**. Detail becomes the one editing surface in R4
//! (v2's Phase 4 conclusion stands: editing lives in one place, not scattered
//! across pickers), and the letter verbs `s`/`b`/`u` arrive with it. Showing
//! them now would break the rule that a hint is only ever shown for something
//! that works.

use ratatui::layout::Rect;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;

use crate::app::Model;
use crate::layout::truncate;
use crate::theme::{Theme, Tone};
use crate::Status;

/// Label column, so values line up under each other.
const LABEL_COLS: usize = 10;

/// Draw the record for the highlighted row.
pub fn draw(frame: &mut Frame, area: Rect, model: &Model, theme: Theme) {
    let inner = area.width.saturating_sub(2) as usize;
    let Some(doc) = model.current() else {
        frame.render_widget(
            Paragraph::new(Line::styled(" nothing selected", theme.style(Tone::Muted))),
            area,
        );
        return;
    };
    let status = model.status(doc);

    let mut lines = vec![
        Line::styled(format!(" {}", truncate(&doc.name, inner)), theme.style(Tone::Title)),
        Line::raw(""),
        field("location", &nonempty(doc.place()), theme),
    ];

    // Expiry carries its standing in words next to the date: `2026-09-28` alone
    // makes the reader do the arithmetic, and the whole point of this app is
    // that nobody should have to.
    lines.push(Line::from(vec![
        label("expiry", theme),
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
    ]));

    lines.push(field("issued", &nonempty(doc.issue_date.clone().unwrap_or_default()), theme));
    lines.push(field("tags", &nonempty(doc.tags.join(" ")), theme));
    lines.push(field("bundles", &nonempty(doc.bundles.join(" · ")), theme));

    // Files: the count, then one line each with the primary marked — the file
    // `Enter` opens is the one with the arrow, and seeing which that is matters
    // more than any other field on this screen.
    if doc.files.is_empty() {
        lines.push(field("files", "none", theme));
    } else {
        let primary = doc.primary_file().map(|f| f.path.clone());
        for (i, file) in doc.files.iter().enumerate() {
            let is_primary = Some(&file.path) == primary.as_ref();
            let head =
                if i == 0 { label("files", theme) } else { Span::raw(" ".repeat(LABEL_COLS + 1)) };
            let mut spans = vec![head];
            if i == 0 {
                spans.push(Span::styled(format!("{} ", doc.files.len()), theme.style(Tone::Muted)));
            }
            spans.push(Span::styled(
                if is_primary { "▸ " } else { "  " }.to_string(),
                theme.style(Tone::Accent),
            ));
            spans.push(Span::raw(truncate(&file.path, inner.saturating_sub(LABEL_COLS + 5))));
            lines.push(Line::from(spans));
        }
    }

    if let Some(older) = &doc.supersedes {
        let title = model
            .store
            .docs
            .iter()
            .find(|d| &d.id == older)
            .map_or_else(|| older.clone(), |d| d.name.clone());
        lines.push(Line::from(vec![
            label("renews", theme),
            Span::styled(truncate(&title, inner - LABEL_COLS), theme.style(Tone::Accent)),
        ]));
    }

    if !doc.notes.is_empty() {
        lines.push(field("notes", &doc.notes, theme));
    }

    lines.push(Line::raw(""));
    lines.push(Line::styled(
        " read-only until R4 makes this the editing surface",
        theme.style(Tone::Muted),
    ));

    frame.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), area);
}

/// An em dash beats a blank: an empty value and a missing field look identical
/// on screen otherwise, and only one of them is worth fixing.
fn nonempty(value: String) -> String {
    if value.is_empty() {
        "—".into()
    } else {
        value
    }
}

fn label(text: &str, theme: Theme) -> Span<'static> {
    Span::styled(format!(" {text:<LABEL_COLS$}"), theme.style(Tone::Muted))
}

fn field(name: &str, value: &str, theme: Theme) -> Line<'static> {
    Line::from(vec![label(name, theme), Span::raw(value.to_string())])
}
