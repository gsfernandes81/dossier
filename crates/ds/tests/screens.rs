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

//! What the screen actually says, at the two sizes that matter.
//!
//! These render into a `TestBackend` and read the cells back, so they check the
//! finished frame rather than the intent behind it — the same thing the approved
//! mockups in `docs/dev/mockups/` show, at the same column counts (45×28 phone,
//! 100×26 desktop). REWRITE-UI.md §8 requires every surface to be *fully
//! operable* at both, and a column that runs off the edge of a phone is exactly
//! the failure a unit test cannot see.

use ratatui::backend::TestBackend;
use ratatui::Terminal;

use ds::app::{update, Filter, Model, Msg};
use ds::theme::Theme;
use ds::{find, Doc, FileRef, Status, Store};

/// The store the mockups are drawn from: marine certificates, motorcycle
/// papers, identity documents — declared in the shelf order [`Store::build`]
/// would put them in (that sort has its own test in `doc.rs`; these tests are
/// about what the screen does with the list, not how it was ordered).
/// One fixture row: id, name, location, slot, tag, expiry, file.
type Row = (
    &'static str,
    &'static str,
    &'static str,
    u32,
    &'static str,
    Option<&'static str>,
    &'static str,
);

fn sample_store() -> Store {
    let rows: &[Row] = &[
        (
            "insurance",
            "Motorcycle Insurance",
            "blue-folder",
            1,
            "motorcycle",
            Some("2026-07-31"),
            "",
        ),
        ("rc", "RC Book — Himalayan 450", "blue-folder", 2, "motorcycle", None, ""),
        ("dl", "Driving Licence", "blue-folder", 3, "identity", Some("2033-08-31"), ""),
        ("eng1", "ENG-1 Medical", "cert-file", 3, "marine", Some("2027-01-13"), "eng1.pdf"),
        ("stcw", "STCW Basic Safety Training", "cert-file", 4, "marine", Some("2031-05-31"), ""),
        ("aff", "Advanced Fire Fighting", "cert-file", 5, "marine", Some("2029-11-30"), ""),
        ("ssa", "Ship Security Awareness", "cert-file", 6, "marine", Some("2030-02-28"), ""),
        (
            "coc",
            "COC Certificate (Master)",
            "cert-file",
            8,
            "marine",
            Some("2026-09-28"),
            "coc.pdf",
        ),
        ("pan", "PAN Card", "file-4096", 12, "identity", None, ""),
        ("degree", "Degree Certificate", "file-4096", 14, "education", None, ""),
        ("passport", "Passport (IN)", "passport-pouch", 1, "identity", Some("2031-05-31"), ""),
        ("cdc", "Seaman Book (CDC)", "passport-pouch", 2, "marine", Some("2027-03-31"), ""),
        ("yellow", "Yellow Fever Card", "passport-pouch", 3, "travel", None, ""),
        ("testimonial", "Sea Service Testimonial 2024", "softcopy", 0, "marine", None, "t.pdf"),
    ];
    let docs = rows
        .iter()
        .map(|(id, name, location, slot, tag, expiry, file)| Doc {
            id: (*id).into(),
            name: (*name).into(),
            tags: vec![(*tag).into()],
            bundles: Vec::new(),
            issue_date: None,
            expiry_date: expiry.map(str::to_string),
            ignore_expiry: false,
            supersedes: None,
            location: Some((*location).into()),
            slot: (*slot > 0).then_some(*slot),
            subslot: None,
            files: if file.is_empty() {
                Vec::new()
            } else {
                vec![FileRef {
                    label: "complete".into(),
                    path: format!("Marine/{file}"),
                    primary: true,
                }]
            },
            notes: String::new(),
            superseded: false,
            haystack: ds::search::fold(&format!("{name} {tag}")),
        })
        .collect();
    Store { docs, ..Store::default() }
}

fn model(cols: u16, rows: u16) -> Model {
    Model::new(sample_store(), "2026-10-20".into(), "2027-01-18".into(), cols, rows)
}

/// Render one frame and read the screen back as lines of text.
fn screen(model: &mut Model, cols: u16, rows: u16) -> Vec<String> {
    render_with(model, cols, rows, Theme { color: true }).0
}

/// Render, returning both the text and whether any cell carried a colour.
fn render_with(model: &mut Model, cols: u16, rows: u16, theme: Theme) -> (Vec<String>, bool) {
    let mut terminal = Terminal::new(TestBackend::new(cols, rows)).expect("test backend");
    terminal.draw(|frame| find::draw(frame, model, theme)).expect("draw");
    let buffer = terminal.backend().buffer().clone();
    let mut colored = false;
    let lines = (0..rows)
        .map(|y| {
            (0..cols)
                .map(|x| {
                    let cell = &buffer[(x, y)];
                    // Every cell carries `Color::Reset` — that is the terminal's
                    // own foreground, not a colour this app chose. Only a
                    // named colour counts as emitting one.
                    if !matches!(cell.style().fg, None | Some(ratatui::style::Color::Reset)) {
                        colored = true;
                    }
                    cell.symbol().to_string()
                })
                .collect::<String>()
        })
        .collect();
    (lines, colored)
}

/// **The phone screen the user approved**: header, twelve two-line documents,
/// the touch action bar, the docked search bar, one hint line — at 45×28.
#[test]
fn the_phone_screen_matches_the_approved_mockup() {
    let mut m = model(45, 28);
    let lines = screen(&mut m, 45, 28);

    assert_eq!(lines.len(), 28);
    assert!(lines[0].starts_with(" dossier"), "header: {:?}", lines[0]);
    assert!(lines[0].contains("exp"), "the attention count survives phone width");
    assert!(lines[0].contains("14 docs"));

    // Twelve documents, two lines each, starting on row 1.
    assert!(lines[1].starts_with("▸ Motorcycle Insurance"), "shelf order: {:?}", lines[1]);
    assert!(lines[2].contains("blue-folder 1 · motorcycle"), "under-line: {:?}", lines[2]);
    assert!(lines[3].starts_with("  RC Book"), "second row is not selected: {:?}", lines[3]);
    assert_eq!(ds::layout::visible_rows(45, 28), 12);

    let bar = &lines[25];
    assert!(bar.contains("⏎ Open") && bar.contains("→ Detail") && bar.contains("⌨ Keys"));
    assert!(lines[26].starts_with(" > _"), "the search bar is docked: {:?}", lines[26]);
    assert!(lines[26].trim_end().ends_with("14/14"), "matched/total: {:?}", lines[26]);
    assert!(lines[27].contains("⏎ open"), "hints for this surface: {:?}", lines[27]);
}

/// **Every line is exactly the terminal's width, and the status column lands on
/// the same column in every row.** A ragged status column is the thing that
/// makes a list of dates unreadable at a glance.
#[test]
fn the_status_column_is_straight() {
    for (cols, rows) in [(45u16, 28u16), (80, 24), (100, 26)] {
        let mut m = model(cols, rows);
        let lines = screen(&mut m, cols, rows);
        for line in &lines {
            assert_eq!(
                line.chars().count(),
                cols as usize,
                "every rendered line fills the terminal at {cols}x{rows}"
            );
        }
        let marker_columns: Vec<usize> = lines
            .iter()
            .filter(|line| line.contains("09-26") || line.contains("01-27"))
            // A **character** position, not a byte offset: the cursor glyph is
            // three bytes, so `find` would report the selected row two columns
            // to the right and this test would pass a crooked column.
            .map(|line| line.chars().position(|c| c == '!' || c == '~').expect("a marker"))
            .collect();
        assert!(marker_columns.len() >= 2, "at least two dated rows at {cols}x{rows}");
        assert!(
            marker_columns.windows(2).all(|pair| pair[0] == pair[1]),
            "markers must share a column at {cols}x{rows}: {marker_columns:?}"
        );
    }
}

/// The desktop layout is single-line rows with a tags column — and the same
/// list, in the same order.
#[test]
fn the_desktop_screen_is_single_line_rows() {
    let mut m = model(100, 26);
    let lines = screen(&mut m, 100, 26);
    assert!(lines[1].starts_with("▸ Motorcycle Insurance"));
    assert!(lines[1].contains("motorcycle"), "tags column: {:?}", lines[1]);
    assert!(lines[1].contains("blue-folder 1"));
    assert!(lines[2].starts_with("  RC Book"), "no under-line at this width: {:?}", lines[2]);
    assert!(!lines[23].contains("⏎ Open"), "no touch action bar on the desktop");
}

/// **Detail splits beside the list only when there is room** (U3): a right pane
/// at 100 columns, a full-screen push at 45.
#[test]
fn detail_splits_wide_and_pushes_narrow() {
    let mut wide = model(100, 26);
    update(&mut wide, Msg::OpenDetail);
    let lines = screen(&mut wide, 100, 26);
    assert!(lines[1].starts_with("▸ Motorcycle Insurance"), "the list is still there");
    assert!(
        lines[1].contains("Motorcycle Insurance") && lines[1].len() > 60,
        "and the record is beside it: {:?}",
        lines[1]
    );
    assert!(lines.iter().any(|l| l.contains("expiry")), "the record's fields");

    let mut narrow = model(45, 28);
    update(&mut narrow, Msg::OpenDetail);
    let lines = screen(&mut narrow, 45, 28);
    assert!(lines[1].contains("Motorcycle Insurance"), "title: {:?}", lines[1]);
    assert!(!lines[3].contains("RC Book"), "the list is covered, not squeezed");
    assert!(lines.iter().any(|l| l.contains("! expired")), "standing in words, not just a date");
    assert!(lines[27].contains("← close"), "the hints changed with the surface");
}

/// Below the floor the app says so instead of drawing something broken.
#[test]
fn a_tiny_terminal_gets_a_notice() {
    let mut m = model(30, 10);
    let lines = screen(&mut m, 30, 10);
    assert!(lines[0].contains("too small"));
    assert!(lines[1].contains("38×12"), "and says what it needs: {:?}", lines[1]);
    assert_eq!(m.list.height, 0, "no rows drawn means a tap cannot hit one");
}

/// **`NO_COLOR` is a supported way to run.** With colour off the screen still
/// carries every signal — the markers are text, and the layout is unchanged.
#[test]
fn no_color_loses_nothing_but_colour() {
    let mut colored = model(45, 28);
    let (with_color, any_color) = render_with(&mut colored, 45, 28, Theme { color: true });
    let mut mono = model(45, 28);
    let (without_color, no_color) = render_with(&mut mono, 45, 28, Theme { color: false });

    assert!(any_color, "the colour run really did emit colour");
    assert!(!no_color, "NO_COLOR emits none");
    assert_eq!(with_color, without_color, "the text is identical either way");
    assert!(without_color.iter().any(|l| l.contains('!')), "the expired marker is text");
}

/// Typing narrows the list and the count says so — the fzf-style feedback the
/// plan asks for.
#[test]
fn typing_narrows_the_list_and_the_count() {
    let mut m = model(45, 28);
    for c in "coc".chars() {
        update(&mut m, Msg::Char(c));
    }
    let lines = screen(&mut m, 45, 28);
    assert!(lines[1].contains("COC Certificate"));
    assert!(lines[26].contains("coc_"), "the query is shown with a cursor: {:?}", lines[26]);
    assert!(lines[26].trim_end().ends_with("1/14"), "matched/total: {:?}", lines[26]);
}

/// The expiring filter shows its chip, so a filtered list can never be mistaken
/// for the whole store.
#[test]
fn the_expiring_filter_is_visible_in_the_bar() {
    let mut m = model(45, 28);
    update(&mut m, Msg::ToggleExpiring);
    assert_eq!(m.filter, Filter::Expiring);
    let lines = screen(&mut m, 45, 28);
    assert!(lines[26].contains("[expiring]"), "the chip: {:?}", lines[26]);
    assert!(lines[1].contains("Motorcycle Insurance"), "soonest first: {:?}", lines[1]);
}

/// An empty store renders as a sentence, not as a blank rectangle.
#[test]
fn an_empty_store_explains_itself() {
    let mut m = Model::new(Store::default(), "2026-10-20".into(), "2027-01-18".into(), 45, 28);
    let lines = screen(&mut m, 45, 28);
    assert!(lines[1].contains("no documents yet"), "{:?}", lines[1]);
    assert!(lines[26].trim_end().ends_with("0/0"));
}

/// A long name is cut with an ellipsis at a **cell** boundary, so a wide-glyph
/// name cannot push the status column sideways.
#[test]
fn wide_glyphs_do_not_break_the_columns() {
    let mut store = sample_store();
    store.docs[0].name = "護照護照護照護照護照護照護照護照護照護照護照護照".into();
    let mut m = Model::new(store, "2026-10-20".into(), "2027-01-18".into(), 45, 28);
    let lines = screen(&mut m, 45, 28);
    let row = &lines[1];
    assert!(row.contains('…'), "the name was cut: {:?}", row);
    assert_eq!(
        m.status(&m.store.docs[m.rows[0]]),
        Status::Expired,
        "the row under test is still the expired one"
    );
    assert!(row.contains('!'), "and its marker survived the cut: {:?}", row);
}

/// **A wrapped value stays in its own column.** A continuation line that starts
/// at the left margin reads as a new field, which is why the renderer wraps free
/// text itself instead of handing it to a widget.
#[test]
fn a_long_note_hangs_under_its_column() {
    let mut store = sample_store();
    store.docs[0].notes =
        "Revalidation booked at MMD, slot 14 Oct. Bring originals and two photographs.".into();
    let mut m = Model::new(store, "2026-10-20".into(), "2027-01-18".into(), 45, 28);
    update(&mut m, Msg::OpenDetail);
    let lines = screen(&mut m, 45, 28);

    let first = lines.iter().position(|l| l.contains("notes")).expect("the notes field");
    let value_column = lines[first].find("Revalidation").expect("the value");
    let continuation = &lines[first + 1];
    assert!(continuation.trim_start().starts_with("14 Oct."), "wrapped: {continuation:?}");
    assert_eq!(
        continuation.find("14 Oct."),
        Some(value_column),
        "and it hangs under the value, not at the margin: {continuation:?}"
    );
}
