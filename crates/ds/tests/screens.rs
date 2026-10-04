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
//! finished frame rather than the intent behind it, at the column counts of the
//! mockups in `docs/dev/mockups/` (45×28 phone, 100×26 desktop). Every surface
//! must be fully operable at both, and a column that runs off the edge of a phone
//! is exactly the failure a unit test cannot see.

mod common;

use common::{clear_buffer, render, type_str, writable};
use ds::app::{update, Filter, Model, Msg};
use ds::theme::Theme;
use ds::{Doc, FileRef, Status, Store};

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

/// The store the mockups are drawn from: marine certificates, motorcycle
/// papers, identity documents.
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
            expiry_date: expiry.map(str::to_string),
            location: Some(if *slot == 0 {
                ds::place::DIGITAL_ONLY.into()
            } else {
                format!("{location}-{slot}")
            }),
            files: if file.is_empty() {
                Vec::new()
            } else {
                vec![FileRef {
                    label: "complete".into(),
                    path: format!("Marine/{file}"),
                    primary: true,
                }]
            },
            ..Doc::default()
        })
        .collect();
    let mut locations = Vec::new();
    for (_, _, location, slot, ..) in rows.iter().filter(|row| row.3 > 0) {
        if !locations.iter().any(|l: &ds::Location| l.id == *location) {
            locations.push(ds::Location {
                id: (*location).into(),
                name: location.replace('-', " "),
                parent: None,
            });
        }
        locations.push(ds::Location {
            id: format!("{location}-{slot}"),
            name: format!("slot {slot}"),
            parent: Some((*location).into()),
        });
    }
    let mut store = Store { docs, locations: ds::Tree::new(locations), ..Store::default() };
    store.derive();
    store
}

fn model(cols: u16, rows: u16) -> Model {
    model_of(sample_store(), cols, rows)
}

fn model_of(store: Store, cols: u16, rows: u16) -> Model {
    Model::new(store, "2026-10-20".into(), "2027-01-18".into(), cols, rows)
}

/// Which cells of one screen row carry a modifier — the way to check that a
/// *texture* landed where it was meant to, since text alone cannot show it.
fn modifier_columns(
    model: &mut Model,
    cols: u16,
    rows: u16,
    row: u16,
    modifier: ratatui::style::Modifier,
) -> Vec<u16> {
    let buffer = render(model, cols, rows, Theme { color: true });
    (0..cols).filter(|x| buffer[(*x, row)].style().add_modifier.contains(modifier)).collect()
}

/// The columns of one row drawn as the selection.
fn selected_columns(model: &mut Model, cols: u16, rows: u16, row: u16) -> Vec<u16> {
    let buffer = render(model, cols, rows, Theme { color: true });
    let selected = Theme { color: true }.selected().bg;
    (0..cols).filter(|x| buffer[(*x, row)].style().bg == selected).collect()
}

/// The columns of one row whose background is not the terminal's own.
fn banded_columns(model: &mut Model, cols: u16, rows: u16, row: u16, theme: Theme) -> Vec<u16> {
    let buffer = render(model, cols, rows, theme);
    (0..cols)
        .filter(|x| {
            !matches!(buffer[(*x, row)].style().bg, None | Some(ratatui::style::Color::Reset))
        })
        .collect()
}

/// Render one frame and read the screen back as lines of text.
fn screen(model: &mut Model, cols: u16, rows: u16) -> Vec<String> {
    render_with(model, cols, rows, Theme { color: true }).0
}

/// Render, returning both the text and whether any cell carried a colour.
fn render_with(model: &mut Model, cols: u16, rows: u16, theme: Theme) -> (Vec<String>, bool) {
    let buffer = render(model, cols, rows, theme);
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

/// Drawn at 45×28, which is the mockup size rather than the device's — the
/// phone reports 47×45 browsing and 47×24 typing. The assertions are about what
/// the rows contain, so the pane size only has to be a plausible narrow one.
#[test]
fn the_phone_screen_matches_the_mockup() {
    let mut m = model(45, 28);
    let lines = screen(&mut m, 45, 28);

    assert_eq!(lines.len(), 28);
    assert!(lines[0].starts_with(" dossier"), "header: {:?}", lines[0]);
    assert!(lines[0].contains("exp"), "the attention count survives phone width");
    assert!(!lines[0].contains("docs"), "no document total: {:?}", lines[0]);

    // Twelve documents, two lines each, starting on row 1.
    assert!(lines[1].starts_with("▸ Motorcycle Insurance"), "shelf order: {:?}", lines[1]);
    assert!(lines[2].contains("blue folder › slot 1 · motorcycle"), "under-line: {:?}", lines[2]);
    assert!(lines[3].starts_with("  RC Book"), "second row is not selected: {:?}", lines[3]);
    assert_eq!(ds::layout::visible_rows(45, 28), 12);

    // Row 25 is the twelfth document's second line, not a row of verb buttons.
    assert!(!lines[25].contains("Detail") && !lines[25].contains("Expiry"));
    // The search bar is docked at the bottom and is **two rows** on touch: the
    // count and hints, then the query. Both rows are the keyboard target.
    assert!(lines[26].trim_start().starts_with("14/14"), "matched/total: {:?}", lines[26]);
    assert!(lines[26].contains("⏎ record"), "the hint line teaches the verbs");
    assert!(lines[26].contains("space menu"), "{:?}", lines[26]);
    assert!(lines[27].starts_with(" > █"), "the query row is last: {:?}", lines[27]);
    assert!(lines[27].contains("SPC"), "and carries the leader chip: {:?}", lines[27]);
    assert!(!lines[27].contains('⌨'), "and no keyboard chip: {:?}", lines[27]);
    assert!(lines[27].contains("Type to search"), "the empty field says so: {:?}", lines[27]);
    assert!(lines[27].contains("For more, hit"), "and what the chip is for");
}

/// A ragged status column makes a list of dates unreadable at a glance.
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

#[test]
fn the_desktop_screen_is_single_line_rows() {
    let mut m = model(100, 26);
    let lines = screen(&mut m, 100, 26);
    assert!(lines[1].starts_with("▸ Motorcycle Insurance"));
    assert!(lines[1].contains("motorcycle"), "tags column: {:?}", lines[1]);
    assert!(lines[1].contains("  blue folder › slot 1"), "the whole path fits: {:?}", lines[1]);
    assert!(lines[2].starts_with("  RC Book"), "no under-line at this width: {:?}", lines[2]);
    assert!(!lines[23].contains("⏎ Open"), "no touch buttons on the desktop");
}

#[test]
fn tags_give_way_before_the_location() {
    let mut m = model(92, 26);
    let i = m.store.index_of("insurance").unwrap();
    m.store.docs[i].tags =
        vec!["motorcycle".into(), "longtagone".into(), "longtagtwo".into(), "longtagthree".into()];
    let lines = screen(&mut m, 92, 26);
    assert!(lines[1].contains("motorcycle longtagone lon…  "), "tags cut: {:?}", lines[1]);
    assert!(lines[1].contains("  blue folder › slot 1"), "location whole: {:?}", lines[1]);
}

#[test]
fn detail_splits_wide_and_pushes_narrow() {
    let mut wide = model(100, 26);
    update(&mut wide, Msg::Enter);
    let lines = screen(&mut wide, 100, 26);
    assert!(lines[1].starts_with("▸ Motorcycle Insurance"), "the list is still there");
    assert!(
        lines[1].contains("Motorcycle Insurance") && lines[1].len() > 60,
        "and the record is beside it: {:?}",
        lines[1]
    );
    assert!(lines.iter().any(|l| l.contains("expiry")), "the record's fields");

    let mut narrow = model(45, 28);
    update(&mut narrow, Msg::Enter);
    let lines = screen(&mut narrow, 45, 28);
    assert!(lines[1].contains("Motorcycle Insurance"), "title: {:?}", lines[1]);
    assert!(!lines[3].contains("RC Book"), "the list is covered, not squeezed");
    assert!(lines.iter().any(|l| l.contains("! expired")), "standing in words, not just a date");
    // On touch it is the *buttons* that change with the surface — the hint line
    // carries only what they do not (`esc`, `^q`), which is why it can be short
    // enough to share a row with the count.
    assert!(lines[26].contains("esc back"), "the hints changed with the surface: {:?}", lines[26]);
}

#[test]
fn a_tiny_terminal_gets_a_notice() {
    let mut m = model(30, 10);
    let lines = screen(&mut m, 30, 10);
    assert!(lines[0].contains("too small"));
    assert!(lines[1].contains("38×12"), "and says what it needs: {:?}", lines[1]);
    assert_eq!(m.list.height, 0, "no rows drawn means a tap cannot hit one");
}

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

/// Reverse video means pressable, checked against the columns the renderer
/// filled, which are the ones the hit test reads.
#[test]
fn the_header_count_is_a_filled_cell_on_a_touch_layout() {
    let mut m = model(45, 28);
    let reversed = modifier_columns(&mut m, 45, 28, 0, ratatui::style::Modifier::REVERSED);
    assert!(!reversed.is_empty(), "the count is pressable");

    let lines = screen(&mut m, 45, 28);
    let cell: String = lines[0].chars().skip(reversed[0] as usize).take(reversed.len()).collect();
    assert!(cell.contains("exp"), "and it is the expiring count: {cell:?}");
    assert!(cell.starts_with(' ') && cell.ends_with(' '), "padded, not butted: {cell:?}");

    // A wide terminal has a keyboard and needs no button at all.
    let mut wide = model(120, 40);
    let none = modifier_columns(&mut wide, 120, 40, 0, ratatui::style::Modifier::REVERSED);
    assert!(none.is_empty(), "no touch affordance where there is a keyboard");
}

#[test]
fn the_leader_sheet_opens_over_the_list() {
    let mut m = model(45, 28);
    let before = screen(&mut m, 45, 28);

    update(&mut m, Msg::Char(' '));
    let open = screen(&mut m, 45, 28);
    assert_eq!(open.len(), before.len(), "the pane did not change size");
    assert!(open.iter().any(|l| l.trim() == "SPC"), "the heading is SPC alone: {open:?}");
    assert!(open.iter().any(|l| l.contains("f filter")), "and the verbs: {open:?}");
    assert!(!open.iter().any(|l| l.contains("type to search")), "nothing searches it: {open:?}");
    assert!(open[27].starts_with(" menu · letters run verbs"), "{:?}", open[27]);
    assert!(before[27].starts_with(" > "), "the search came back after: {:?}", before[27]);

    update(&mut m, Msg::Char('f'));
    let list = screen(&mut m, 45, 28);
    let boxes: Vec<&String> = list.iter().filter(|l| l.contains("[ ]")).collect();
    assert_eq!(boxes.len(), 3, "three filters, all off: {list:?}");
    assert!(list.iter().any(|l| l.contains("clear all")), "{list:?}");
    assert!(list.iter().any(|l| l.contains("SPC f  filter")), "{list:?}");

    update(&mut m, Msg::Char(' '));
    let on = screen(&mut m, 45, 28);
    assert!(on.iter().any(|l| l.contains("[x] expiring only")), "ticked in place: {on:?}");
    assert!(on[26].contains("[expiring]"), "and its chip is up: {:?}", on[26]);
}

/// The band divides rather than sitting behind the user's own text, where it
/// would need a pinned foreground and dim placeholder text over a lit row.
#[test]
fn the_status_line_is_a_band_and_the_entry_line_is_not() {
    let mut m = model(45, 28);
    let band = banded_columns(&mut m, 45, 28, 26, Theme { color: true });
    assert_eq!(band.len(), 45, "every column, gutters included: {band:?}");
    assert!(
        banded_columns(&mut m, 45, 28, 27, Theme { color: true }).is_empty(),
        "the row being typed into keeps the terminal's own background"
    );

    // Nothing is underlined either: an underline lands through the descenders.
    for row in [26u16, 27] {
        let underlined =
            modifier_columns(&mut m, 45, 28, row, ratatui::style::Modifier::UNDERLINED);
        assert!(underlined.is_empty(), "row {row} carries no rule: {underlined:?}");
    }

    // Typing changes the count on the band, never the band.
    type_str(&mut m, "coc");
    assert_eq!(banded_columns(&mut m, 45, 28, 26, Theme { color: true }), band);

    // The leader chip closes the entry line, reversed against the plain
    // background rather than against the band.
    let reversed = modifier_columns(&mut m, 45, 28, 27, ratatui::style::Modifier::REVERSED);
    assert_eq!(reversed, [39, 40, 41, 42, 43], "SPC is reverse, with a gutter after it");
}

/// The band is the one texture a monochrome run loses, so it may never be the
/// only thing saying what the row is for.
#[test]
fn a_monochrome_run_loses_the_band_but_not_the_row() {
    let mut m = model(45, 28);
    let band = banded_columns(&mut m, 45, 28, 26, Theme { color: false });
    assert!(band.is_empty(), "{band:?}");

    let (lines, _) = render_with(&mut m, 45, 28, Theme { color: false });
    assert!(lines[27].contains("Type to search"), "the words still say it: {:?}", lines[27]);
    assert!(lines[27].contains("SPC"), "and the button is still there");
    assert!(lines[26].contains("⏎ record"), "and the status line still reads: {:?}", lines[26]);
}

/// No keyboard button: Termux has its own keyboard key, and tapping the field
/// raises the IME. A bare reversed `SPC` says only that it is pressable, so the
/// empty field's second phrase runs into it and finishes the sentence.
#[test]
fn the_touch_layout_has_one_button_and_it_explains_itself() {
    let mut m = model(45, 28);
    let lines = screen(&mut m, 45, 28);
    assert!(!lines[27].contains('⌨'), "no keyboard chip: {:?}", lines[27]);
    // Columns, not bytes: the row carries `█`, so a byte offset is not a column.
    let row: Vec<char> = lines[27].chars().collect();
    let at = |needle: &str| {
        let needle: Vec<char> = needle.chars().collect();
        (0..row.len()).find(|i| row[*i..].starts_with(&needle))
    };
    let hit = at("For more, hit").expect("the signpost");
    let chip = at("SPC").expect("the chip");
    assert!(hit < chip, "the sentence runs into the button: {:?}", lines[27]);
    let between: String = row[hit + 13..chip].iter().collect();
    assert_eq!(between, "  ", "one plain column, then the chip's own padding");

    // Typing takes both phrases away together, and puts them back on the way out.
    update(&mut m, Msg::Char('c'));
    let typed = screen(&mut m, 45, 28);
    assert!(!typed[27].contains("Type to search"), "{:?}", typed[27]);
    assert!(!typed[27].contains("For more"), "both halves go together");
    update(&mut m, Msg::Backspace);
    assert!(screen(&mut m, 45, 28)[27].contains("Type to search"));

    // **Both** rows raise the keyboard, and they sit against the bottom edge —
    // one row is too small a thing to ask a thumb to hit.
    for row in [26u16, 27] {
        m.mouse_on = true;
        m.keyboard_hint = false;
        update(&mut m, Msg::Tap { col: 3, row });
        assert!(!m.mouse_on, "row {row} is part of the target");
    }

    // The desktop has a keyboard and a space bar, so it gets one row, no chip,
    // and no signpost.
    let mut wide = model(100, 26);
    let lines = screen(&mut wide, 100, 26);
    assert!(!lines[25].contains("SPC"), "{:?}", lines[25]);
    assert!(!lines[25].contains("For more"), "nothing to point at: {:?}", lines[25]);
    assert!(lines[25].starts_with(" > "), "the entry line is last here too");
    assert!(lines[24].contains("space menu"), "the hint names the key: {:?}", lines[24]);
    assert!(!lines[24].contains("^t"), "no ctrl key for a filter: {:?}", lines[24]);
}

#[test]
fn typing_narrows_the_list_and_the_count() {
    let mut m = model(45, 28);
    type_str(&mut m, "coc");
    let lines = screen(&mut m, 45, 28);
    assert!(lines[1].contains("+ new \"coc\""), "pinned above the matches: {:?}", lines[1]);
    assert!(lines[2].starts_with("▸ COC Certificate"), "the cursor on the match: {:?}", lines[2]);
    assert!(lines[27].contains("coc█"), "the query is shown with a cursor: {:?}", lines[27]);
    assert!(lines[26].trim_start().starts_with("1/14"), "matched/total: {:?}", lines[26]);
}

/// Moving the cursor into the query never shifts the text: the cursor is a
/// reversed cell over a character, and `█` only past the end.
#[test]
fn a_mid_query_cursor_leaves_the_text_in_place() {
    let mut m = model(45, 28);
    type_str(&mut m, "coc");
    update(&mut m, Msg::Left);
    let lines = screen(&mut m, 45, 28);
    assert!(lines[27].contains(" coc "), "the text is unbroken: {:?}", lines[27]);
    assert!(!lines[27].contains('█'), "and no block is drawn inside it: {:?}", lines[27]);
}

#[test]
fn a_file_row_picker_draws_in_the_panel() {
    let mut m = writable(model(45, 28));
    let doc = m.store.docs.iter().position(|d| !d.files.is_empty()).expect("a doc with a file");
    m.cursor = m.rows.iter().position(|&i| i == doc).expect("listed");
    update(&mut m, Msg::Enter);
    let rows = ds::detail::rows(m.current().unwrap());
    m.set_record_cursor(rows.iter().position(|r| matches!(r, ds::detail::Row::File(0))).unwrap());
    update(&mut m, Msg::Char('e'));
    let text = screen(&mut m, 45, 28).join("\n");
    assert!(text.contains("file "), "the heading names the file: {text}");
    assert!(text.contains("detach") && text.contains("attach another file"), "{text}");
    assert!(text.contains("⏎ choose"), "and the hints change with it: {text}");
}

/// The expiring filter shows its chip, so a filtered list can never be mistaken
/// for the whole store.
#[test]
fn the_expiring_filter_is_visible_in_the_bar() {
    let mut m = model(45, 28);
    update(&mut m, Msg::ToggleExpiring);
    assert_eq!(m.filter, Filter::EXPIRING);
    let lines = screen(&mut m, 45, 28);
    assert!(lines[26].contains("[expiring]"), "the chip: {:?}", lines[26]);
    assert!(lines[1].contains("Motorcycle Insurance"), "soonest first: {:?}", lines[1]);
}

#[test]
fn a_conflict_is_counted_in_the_header() {
    let mut store = sample_store();
    for id in ["pan-desk", "pan-phone"] {
        let mut version = store.get("pan").unwrap().clone();
        version.id = id.into();
        version.supersedes = Some("pan".into());
        store.docs.push(version);
    }
    store.derive();
    let mut m = model_of(store, 45, 28);
    let lines = screen(&mut m, 45, 28);
    assert!(lines[0].contains("! 1 conflict"), "{:?}", lines[0]);
    assert!(lines[0].contains(" exp "), "the expiring count stays: {:?}", lines[0]);
}

#[test]
fn the_versions_view_draws_two_lines_a_version() {
    let mut store = sample_store();
    let old = store.index_of("eng1").unwrap();
    let mut new = store.docs[old].clone();
    new.id = "eng1-2".into();
    new.issue_date = Some("2026-09-01".into());
    new.expiry_date = Some("2028-09-01".into());
    new.supersedes = Some("eng1".into());
    store.docs[old].issue_date = Some("2024-01-14".into());
    store.docs[old].expiry_date = Some("2026-01-13".into());
    store.docs.push(new);
    store.derive();
    let mut m = model_of(store, 47, 24);
    m.views.push(ds::View::Versions { doc: "eng1-2".into() });
    let lines = screen(&mut m, 47, 24);
    let text = lines.join("\n");
    assert!(lines[1].contains("ENG-1 Medical"), "{text}");
    assert!(lines[2].contains("versions, newest first"), "{text}");
    assert!(lines[4].starts_with("▸ 2026-09-01 → 2028-09-01"), "{text}");
    assert!(lines[4].trim_end().ends_with("latest"), "{text}");
    assert!(lines[5].contains("cert"), "where its paper is: {text}");
    assert!(lines[6].contains("2024-01-14 → 2026-01-13"), "{text}");
    assert!(lines[6].trim_end().ends_with("! expired"), "{text}");
    assert!(text.contains("2 versions"), "{text}");
    assert!(text.contains("⏎ open"), "{text}");
}

#[test]
fn the_renews_picker_has_three_heading_rows() {
    let mut m = writable(model(47, 24));
    m.cursor = m.rows.iter().position(|&i| m.store.docs[i].id == "eng1").unwrap();
    update(&mut m, Msg::Enter);
    let rows = ds::detail::rows(m.current().unwrap());
    m.set_record_cursor(rows.iter().position(|r| *r == ds::detail::Row::Renews).unwrap());
    update(&mut m, Msg::Char('e'));
    let lines = screen(&mut m, 47, 24);
    let text = lines.join("\n");
    let heading = lines.iter().position(|line| line.contains("e  edit")).expect(&text);
    assert!(lines[heading + 1].contains("ENG-1 Medical"), "{text}");
    assert!(lines[heading + 1].trim_end().ends_with("document"), "{text}");
    assert!(lines[heading + 2].contains("now: renews nothing"), "{text}");
    assert!(text.contains("Driving Licence"), "{text}");
    type_str(&mut m, "dri");
    let text = screen(&mut m, 47, 24).join("\n");
    assert!(text.contains("e  edit  dri█"), "the heading shows the typing: {text}");
}

/// A model whose store holds two bundles; the dated one holds two documents,
/// one of them an old version.
fn with_bundles(cols: u16, rows: u16) -> Model {
    let mut store = sample_store();
    let bundle = |id: &str, name: &str, date: Option<&str>| ds::Bundle {
        id: id.into(),
        name: name.into(),
        date: date.map(Into::into),
        ..ds::Bundle::default()
    };
    store.bundles = vec![
        bundle("visa", "US visa application", Some("2027-03-27")),
        bundle("ideas", "Ideas", None),
    ];
    let mut newer = store.get("eng1").unwrap().clone();
    newer.id = "eng1-2".into();
    newer.supersedes = Some("eng1".into());
    store.docs.push(newer);
    for id in ["eng1", "dl"] {
        let i = store.index_of(id).unwrap();
        store.docs[i].bundles.push(ds::Membership { bundle: "visa".into(), file: None });
    }
    store.derive();
    model_of(store, cols, rows)
}

#[test]
fn the_bundles_view_lists_bundles_in_the_lists_place() {
    let mut m = with_bundles(47, 24);
    update(&mut m, Msg::Char(' '));
    update(&mut m, Msg::Char('b'));
    let lines = screen(&mut m, 47, 24);
    let text = lines.join("\n");
    assert!(lines[1].starts_with("▸ US visa application"), "{text}");
    assert!(lines[1].trim_end().ends_with("2 docs  03-27"), "{text}");
    assert!(lines[2].starts_with("  Ideas"), "{text}");
    assert!(lines[2].contains("0 docs"), "{text}");
    assert!(text.contains("2/2 bundles"), "{text}");
}

#[test]
fn a_bundle_lists_its_documents() {
    let mut m = with_bundles(47, 24);
    update(&mut m, Msg::Char(' '));
    update(&mut m, Msg::Char('b'));
    update(&mut m, Msg::Enter);
    let lines = screen(&mut m, 47, 24);
    let text = lines.join("\n");
    assert!(lines[1].contains("US visa application"), "{text}");
    assert!(text.contains(" date      2027-03-27"), "{text}");
    let eng = lines.iter().find(|line| line.contains("ENG-1 Medical")).expect(&text);
    assert!(eng.trim_end().ends_with("newer exists"), "{text}");
    assert!(text.contains("Driving Licence"), "{text}");
    assert!(text.contains("2 documents"), "{text}");
}

#[test]
fn the_bundle_record_marks_the_field_being_edited() {
    let mut m = with_bundles(47, 24);
    m.write = ds::app::WriteState::Ready { device: "desk".into() };
    update(&mut m, Msg::Char(' '));
    update(&mut m, Msg::Char('b'));
    update(&mut m, Msg::Enter);
    update(&mut m, Msg::Move(ds::app::Motion::Down));
    update(&mut m, Msg::Char('e'));
    let lit = modifier_columns(&mut m, 47, 24, 3, ratatui::style::Modifier::REVERSED);
    assert_eq!(lit, (0..11).collect::<Vec<u16>>(), "the date label, and only it");
}

#[test]
fn the_attach_line_has_a_live_list() {
    let dir = tempfile::tempdir().expect("tempdir");
    let root = dir.path().to_path_buf();
    std::fs::create_dir_all(root.join("Identity")).expect("mkdir");
    std::fs::write(root.join("Identity/passport.pdf"), "").expect("write");
    std::fs::write(root.join("Identity/pan.pdf"), "").expect("write");
    let mut m = writable(model(47, 24));
    m.root = Some(root);
    m.cursor = m.rows.iter().position(|&i| m.store.docs[i].files.is_empty()).expect("unfiled");
    update(&mut m, Msg::Enter);
    let rows = ds::detail::rows(m.current().unwrap());
    m.set_record_cursor(rows.iter().position(|r| *r == ds::detail::Row::Files).unwrap());
    update(&mut m, Msg::Char('e'));
    type_str(&mut m, "Identity/p");
    let lines = screen(&mut m, 47, 24);
    let text = lines.join("\n");
    let heading = lines.iter().position(|line| line.contains("in Identity/")).expect(&text);
    assert!(lines[heading].contains("2 matches"), "{text}");
    assert!(lines[heading + 1].contains("pan.pdf"), "{text}");
    assert!(lines[heading + 2].contains("passport.pdf"), "{text}");
    assert!(lines[23].contains("attach: Identity/p"), "{text}");
}

/// While a pane is in front letters are verbs, so the last row names the pane
/// instead of offering a field.
#[test]
fn a_pane_names_its_mode_on_the_last_row() {
    let mut m = writable(model(47, 24));
    update(&mut m, Msg::Enter);
    let lines = screen(&mut m, 47, 24);
    assert!(lines[23].starts_with(" details · letters run verbs"), "{:?}", lines[23]);
    assert!(!lines[23].contains('>') && !lines[23].contains('█'), "{:?}", lines[23]);
    assert!(lines[23].trim_end().ends_with("SPC"), "the chip stays: {:?}", lines[23]);

    update(&mut m, Msg::Char('d'));
    let lines = screen(&mut m, 47, 24);
    let name = m.current().unwrap().name.clone();
    assert!(lines[23].contains("d · d again to delete"), "{:?}", lines[23]);
    assert!(lines[23].contains(&name[..10]), "it names the document: {:?}", lines[23]);
}

#[test]
fn the_spc_chip_opens_the_sheet_where_it_is_drawn() {
    let mut m = model(47, 24);
    let lines = screen(&mut m, 47, 24);
    let col = lines[23].find("SPC").expect("the chip");
    let col = u16::try_from(lines[23][..col].chars().count()).unwrap();
    update(&mut m, Msg::Tap { col, row: 23 });
    assert!(m.sheet, "a tap on the chip opens the sheet");
}

/// With a query typed, Space types a space, so the hints stop offering the
/// menu.
#[test]
fn the_menu_hint_goes_once_something_is_typed() {
    let mut m = model(47, 24);
    let lines = screen(&mut m, 47, 24);
    assert!(lines[22].contains("space menu"), "{:?}", lines[22]);
    update(&mut m, Msg::Char('p'));
    let lines = screen(&mut m, 47, 24);
    assert!(!lines[22].contains("space menu"), "{:?}", lines[22]);
    assert!(lines[22].contains("esc clear"), "{:?}", lines[22]);
}

#[test]
fn a_tap_on_a_sheet_row_runs_it() {
    let mut m = model(47, 45);
    update(&mut m, Msg::Char(' '));
    let lines = screen(&mut m, 47, 45);
    let row = lines.iter().position(|line| line.contains("f filter")).expect("the filter row");
    update(&mut m, Msg::Tap { col: 5, row: u16::try_from(row).unwrap() });
    assert!(!m.sheet, "the sheet gave way");
    assert!(m.picker.is_some(), "to the filter list");
}

#[test]
fn the_sheet_fits_the_floor_in_two_columns() {
    let mut m = writable(model(38, 12));
    update(&mut m, Msg::Enter);
    update(&mut m, Msg::Char(' '));
    let lines = screen(&mut m, 38, 12);
    let text = lines.join("\n");
    for verb in ["e edit", "n new version", "d delete", "q quit"] {
        assert!(text.contains(verb), "{verb} is on screen: {text}");
    }
    let row = lines.iter().position(|line| line.contains("q quit")).expect(&text);
    let col = lines[row].find(" q quit").expect(&text) + 1;
    let col = u16::try_from(lines[row][..col].chars().count()).unwrap();
    assert_eq!(
        update(&mut m, Msg::Tap { col, row: u16::try_from(row).unwrap() }),
        ds::Effect::Quit
    );
}

#[test]
fn an_empty_store_explains_itself() {
    let mut m = Model::new(Store::default(), "2026-10-20".into(), "2027-01-18".into(), 45, 28);
    let lines = screen(&mut m, 45, 28);
    assert!(lines[1].starts_with("▸ + new document"), "{:?}", lines[1]);
    assert!(lines[2].contains("no documents yet"), "{:?}", lines[2]);
    assert!(lines[26].trim_start().starts_with("0/0"));
}

/// Saying where the first save creates the journal catches a wrong root
/// before anything is written there.
#[test]
fn a_fresh_store_says_how_to_start_and_where() {
    let mut m = Model::new(Store::default(), "2026-10-20".into(), "2027-01-18".into(), 45, 28);
    m.missing_journal = Some("/mnt/c/Docs/.dossier/journal".into());
    let lines = screen(&mut m, 45, 28);
    let text = lines.join("\n");
    assert!(lines[1].contains("+ new document"), "{text}");
    assert!(lines[3].contains("the first one creates the journal at"), "{text}");
    assert_eq!(lines[4].trim_end(), "  /mnt/c/Docs/.dossier/journal", "{text}");
    assert!(!text.contains("ds init"), "init creates no documents: {text}");
}

/// A long name is cut with an ellipsis at a **cell** boundary, so a wide-glyph
/// name cannot push the status column sideways.
#[test]
fn wide_glyphs_do_not_break_the_columns() {
    let mut store = sample_store();
    store.docs[0].name = "護照護照護照護照護照護照護照護照護照護照護照護照".into();
    store.derive();
    let mut m = model_of(store, 45, 28);
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

#[test]
fn a_long_note_hangs_under_its_column() {
    let mut store = sample_store();
    store.docs[0].notes =
        "Revalidation booked at MMD, slot 14 Oct. Bring originals and two photographs.".into();
    store.derive();
    let mut m = model_of(store, 45, 28);
    update(&mut m, Msg::Enter);
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

/// Three rows of chrome is the budget on both layouts, so the field's prompt
/// replaces `>` rather than adding a row.
#[test]
fn an_edit_takes_over_the_entry_line() {
    let mut m = writable(model(47, 24));
    m.open_edit(ds::edit::Field::Expiry);
    let before = screen(&mut m, 47, 24).len();

    let rows = screen(&mut m, 47, 24);
    assert_eq!(rows.len(), before, "no row was added for the editor");
    let entry = rows.last().expect("an entry line");
    assert!(entry.contains("expiry:"), "the prompt names the field: {entry:?}");
    assert!(entry.contains("2026-07-31"), "seeded with the stored value: {entry:?}");
    assert!(entry.contains('█'), "and the cursor is where typing goes: {entry:?}");
    assert!(!entry.contains("SPC"), "the leader is not reachable from inside an edit");

    let band = &rows[rows.len() - 2];
    assert!(band.contains("save"), "the band teaches the verb: {band:?}");
    assert!(band.contains("discard"), "{band:?}");
}

/// The record shows the stored value and the entry line the typed one; the mark
/// is what connects the two rows.
#[test]
fn the_record_marks_the_field_being_edited() {
    let mut m = writable(model(47, 24));
    m.open_edit(ds::edit::Field::Expiry);
    let rows = screen(&mut m, 47, 24);
    let expiry_row = rows
        .iter()
        .position(|row| row.trim_start().starts_with("expiry"))
        .expect("the record shows an expiry row");
    let lit = modifier_columns(
        &mut m,
        47,
        24,
        u16::try_from(expiry_row).expect("row fits"),
        ratatui::style::Modifier::REVERSED,
    );
    assert!(!lit.is_empty(), "the label is marked while it is being edited");
    assert!(lit.iter().all(|&x| x <= 11), "and only the label, not the value: {lit:?}");
}

#[test]
fn the_edit_hint_appears_only_when_this_session_can_write() {
    let mut readonly = model(100, 26);
    update(&mut readonly, Msg::Enter);
    let hints = screen(&mut readonly, 100, 26).join("\n");
    assert!(!hints.contains("e edit"), "a read-only session is not offered an edit");

    let mut writing = writable(model(100, 26));
    update(&mut writing, Msg::Enter);
    let hints = screen(&mut writing, 100, 26).join("\n");
    assert!(hints.contains("e edit"), "and a writing one is: {hints}");
}

#[test]
fn a_dirty_edit_warns_before_it_discards() {
    let mut m = writable(model(47, 24));
    m.open_edit(ds::edit::Field::Expiry);
    clear_buffer(&mut m);
    update(&mut m, Msg::Esc);
    let rows = screen(&mut m, 47, 24);
    assert!(rows[rows.len() - 2].contains("esc again to discard"), "{rows:?}");
}

#[test]
fn the_editor_survives_no_color() {
    let mut m = writable(model(47, 24));
    m.open_edit(ds::edit::Field::Expiry);
    let (rows, coloured) = render_with(&mut m, 47, 24, Theme { color: false });
    assert!(!coloured, "no colour was emitted");
    let entry = rows.last().expect("an entry line");
    assert!(entry.contains("expiry:") && entry.contains("2026-07-31"), "{entry:?}");
}

/// The location is cut from the left so the innermost place survives the
/// phone's width.
#[test]
fn the_details_view_shows_the_hard_copy_location() {
    let mut m = model(47, 24);
    update(&mut m, Msg::Enter);
    let lines = screen(&mut m, 47, 24);
    assert!(
        lines.iter().any(|l| l.contains("hard copy location blue folder › slot 1")),
        "{lines:?}"
    );
    assert!(lines.iter().any(|l| l.contains("[ ] digital only (no hard copy)")), "{lines:?}");
}

#[test]
fn the_location_picker_is_a_tree_on_the_phone() {
    let mut m = writable(model(47, 24));
    update(&mut m, Msg::Enter);
    update(&mut m, Msg::Leader);
    update(&mut m, Msg::Char('l'));
    let lines = screen(&mut m, 47, 24);
    assert!(lines[2].starts_with(" SPC l  location"), "{lines:?}");
    assert!(
        lines[3].starts_with(" Motorcycle Insurance") && lines[3].ends_with("document "),
        "{lines:?}"
    );
    assert!(lines[4].starts_with(" now: blue folder › slot 1"), "{lines:?}");
    assert!(lines[6].starts_with(" blue folder"), "the root row: {lines:?}");
    assert!(lines[7].starts_with(" ├ ▾ slot 1") && lines[7].ends_with("now "), "{lines:?}");
    assert!(lines[8].starts_with(" │ └ Motorcycle Insurance"), "{lines:?}");
    assert!(lines[9].starts_with(" ├ ▸ slot 2") && lines[9].ends_with("1 document "), "{lines:?}");
}

#[test]
fn the_location_picker_searches_by_path() {
    let mut m = writable(model(47, 24));
    update(&mut m, Msg::Enter);
    update(&mut m, Msg::Leader);
    update(&mut m, Msg::Char('l'));
    type_str(&mut m, "slot 2");
    let lines = screen(&mut m, 47, 24);
    assert!(
        lines[2].starts_with(" SPC l  location  slot 2█") && lines[2].ends_with("2 matches "),
        "{lines:?}"
    );
    assert!(lines[6].starts_with(r#" + new "slot 2" in blue folder › slot 1"#), "{lines:?}");
    assert!(
        lines[7].starts_with(" blue folder › slot 2") && lines[7].ends_with("1 document "),
        "{lines:?}"
    );
}

#[test]
fn the_location_sheet_and_its_caution_fit_the_phone() {
    let mut m = writable(model(47, 24));
    update(&mut m, Msg::Enter);
    update(&mut m, Msg::Leader);
    update(&mut m, Msg::Char('l'));
    update(&mut m, Msg::Move(ds::app::Motion::Up));
    update(&mut m, Msg::Char(' '));
    let lines = screen(&mut m, 47, 24);
    assert!(
        lines.iter().any(|l| l.starts_with(" blue folder") && l.ends_with("physical location ")),
        "{lines:?}"
    );
    assert!(lines.iter().any(|l| l.starts_with(" r rename")), "{lines:?}");
    assert!(lines.iter().any(|l| l.starts_with("   redo") && l.contains("^y")), "{lines:?}");

    update(&mut m, Msg::Char('d'));
    let lines = screen(&mut m, 47, 24);
    assert_eq!(lines[20].trim(), "Caution: blue folder holds 3 locations and 3", "{lines:?}");
    assert_eq!(lines[22].trim(), "remove their location attributes", "{lines:?}");
    assert!(lines[23].starts_with(" locations · "), "the last row stays last: {lines:?}");
}

#[test]
fn the_tree_answers_taps() {
    let mut m = writable(model(47, 24));
    update(&mut m, Msg::Enter);
    update(&mut m, Msg::Leader);
    update(&mut m, Msg::Char('l'));
    let lines = screen(&mut m, 47, 24);
    let row = |lines: &[String], text: &str| {
        u16::try_from(lines.iter().position(|l| l.contains(text)).expect(text)).unwrap()
    };

    let slot2 = row(&lines, "slot 2");
    update(&mut m, Msg::Tap { col: 3, row: slot2 });
    let lines = screen(&mut m, 47, 24);
    assert!(lines[usize::from(slot2)].contains("▾ slot 2"), "the chevron opened it: {lines:?}");
    update(&mut m, Msg::Tap { col: 3, row: slot2 });
    let lines = screen(&mut m, 47, 24);
    assert!(lines[usize::from(slot2)].contains("▸ slot 2"), "and closed it: {lines:?}");

    let slot3 = row(&lines, "slot 3");
    assert_eq!(update(&mut m, Msg::Tap { col: 10, row: slot3 }), ds::Effect::Redraw);
    screen(&mut m, 47, 24);
    let ds::Effect::Append(drafts) = update(&mut m, Msg::Tap { col: 10, row: slot3 }) else {
        panic!("a tap on the selected row files the hard copy");
    };
    assert_eq!(drafts.len(), 1, "{drafts:?}");
}

#[test]
fn the_details_rows_answer_taps() {
    let mut m = writable(model(47, 24));
    update(&mut m, Msg::Enter);
    let lines = screen(&mut m, 47, 24);
    let at = |text: &str| {
        u16::try_from(lines.iter().position(|l| l.contains(text)).expect(text)).unwrap()
    };
    let tags = at("tags");
    assert_eq!(update(&mut m, Msg::Tap { col: 5, row: tags }), ds::Effect::Redraw);
    let rows = ds::detail::rows(m.current().unwrap());
    assert_eq!(rows[m.record_cursor()], ds::detail::Row::Editable(ds::edit::Field::Tags));

    let ds::Effect::Append(drafts) = update(&mut m, Msg::Tap { col: 5, row: at("digital only") })
    else {
        panic!("the checkbox toggles on a tap");
    };
    assert_eq!(
        drafts,
        vec![journal::Draft::set("doc", "insurance", "location", serde_json::Value::from("none"))]
    );
}

/// The highlight is drawn from the same `detail::rows` the selector walks, so
/// it can never land on a row the reader is not on.
#[test]
fn the_record_selector_is_drawn_where_it_is() {
    let mut m = model(47, 24);
    update(&mut m, Msg::Enter);

    // It opens on the name, the first row and an editable one.
    let first = selected_columns(&mut m, 47, 24, 1);
    assert!(!first.is_empty(), "the top row of the record is highlighted");

    update(&mut m, Msg::Move(ds::app::Motion::Down));
    assert!(selected_columns(&mut m, 47, 24, 1).is_empty(), "and it left the row above");
    let second = selected_columns(&mut m, 47, 24, 3);
    assert!(!second.is_empty(), "for the next one down");

    let lines = screen(&mut m, 47, 24);
    assert!(lines[3].contains("location"), "which is the row below the name: {:?}", lines[3]);

    // And the blank line under the name is never highlighted — a reversed empty
    // row would read as a second selection.
    update(&mut m, Msg::Move(ds::app::Motion::Up));
    assert!(selected_columns(&mut m, 47, 24, 2).is_empty(), "the blank under the name stays blank");
}
