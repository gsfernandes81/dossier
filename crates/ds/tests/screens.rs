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

/// A model allowed to write, which is not the default: a `Model` nobody has told
/// about a device has no writer id and must not offer an edit.
fn writable(cols: u16, rows: u16) -> Model {
    let mut model = model(cols, rows);
    model.write = ds::app::WriteState::Ready;
    model
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
    let mut terminal = Terminal::new(TestBackend::new(cols, rows)).expect("test backend");
    terminal.draw(|frame| find::draw(frame, model, Theme { color: true })).expect("draw");
    let buffer = terminal.backend().buffer().clone();
    (0..cols).filter(|x| buffer[(*x, row)].style().add_modifier.contains(modifier)).collect()
}

/// The columns of one row whose background is not the terminal's own.
fn banded_columns(model: &mut Model, cols: u16, rows: u16, row: u16, theme: Theme) -> Vec<u16> {
    let mut terminal = Terminal::new(TestBackend::new(cols, rows)).expect("test backend");
    terminal.draw(|frame| find::draw(frame, model, theme)).expect("draw");
    let buffer = terminal.backend().buffer().clone();
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

/// **The phone screen the user approved**: header, two-line documents, the touch
/// action bar, the docked search bar, one hint line.
///
/// Drawn here at 45×28, which is the mockup size rather than the device's — the
/// phone reports 47×45 browsing and 47×24 typing. The assertions are about what
/// the rows contain, so the pane size only has to be a plausible narrow one.
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

    // No action bar: row 25 is the twelfth document's second line, not chrome.
    // Every verb the bar carried is a key the thumb already has on Termux's own
    // extra-keys row, and the two it did not are in the leader sheet.
    assert!(!lines[25].contains("Detail") && !lines[25].contains("Expiry"));
    // The search bar is docked at the bottom and is **two rows** on touch: the
    // query, then the count and hints. Both rows are the keyboard target.
    // Status line above, entry line last — Vim's arrangement, and fzf's.
    assert!(lines[26].trim_start().starts_with("14/14"), "matched/total: {:?}", lines[26]);
    assert!(lines[26].contains("⏎ open"), "the hint line teaches the verbs");
    assert!(lines[26].contains("^x expiry") && lines[26].contains("^t scans"));
    assert!(lines[27].starts_with(" > █"), "the query row is last: {:?}", lines[27]);
    assert!(lines[27].contains("SPC"), "and carries the leader chip: {:?}", lines[27]);
    assert!(!lines[27].contains('⌨'), "which replaced the keyboard chip: {:?}", lines[27]);
    assert!(lines[27].contains("Type to search"), "the empty field says so: {:?}", lines[27]);
    assert!(lines[27].contains("For more, hit"), "and what the chip is for");
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
    // On touch it is the *buttons* that change with the surface — the hint line
    // carries only what they do not (`esc`, `^q`), which is why it can be short
    // enough to share a row with the count.
    assert!(lines[26].contains("◀ back"), "the hints changed with the surface: {:?}", lines[26]);
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

/// **The header count is the touch layout's one filter button** — reverse
/// video, because in this design reverse means "you can press this". It is
/// checked against the columns the renderer actually filled, which is the same
/// place the hit test reads.
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

/// **The leader sheet covers the list rather than shrinking it**, and its
/// toggles draw their off state — which is the whole reason the filters live
/// there instead of as pressable status chips.
#[test]
fn the_leader_sheet_opens_over_the_list() {
    let mut m = model(45, 28);
    let before = screen(&mut m, 45, 28);

    update(&mut m, Msg::Char(' '));
    let open = screen(&mut m, 45, 28);
    assert_eq!(open.len(), before.len(), "the pane did not change size");
    assert!(open.iter().any(|l| l.contains("SPC")), "the breadcrumb is up");
    assert!(open.iter().any(|l| l.contains("filter")), "and the groups: {open:?}");
    assert_eq!(open[27], before[27], "the entry line is untouched underneath");
    assert_eq!(open[26], before[26], "and so is the status line");

    update(&mut m, Msg::Char('f'));
    let group = screen(&mut m, 45, 28);
    let boxes: Vec<&String> = group.iter().filter(|l| l.contains('[')).collect();
    assert_eq!(boxes.len(), 2, "two toggles, both drawn: {group:?}");
    assert!(boxes.iter().all(|l| l.contains("[ ]")), "and both showing off: {boxes:?}");

    update(&mut m, Msg::Char('x'));
    update(&mut m, Msg::Char(' '));
    update(&mut m, Msg::Char('f'));
    let on = screen(&mut m, 45, 28);
    assert!(on.iter().any(|l| l.contains("[✓]")), "and on, in the same place: {on:?}");
}

/// **The status line is a lit rule between the list and the entry line**, and
/// the entry line itself is plain.
///
/// This is Vim's arrangement — `StatusLine` highlighted, `:` on the plain final
/// row beneath it — and it is why the band works: it divides rather than sitting
/// behind the user's own text, where it had to pin a foreground and put dim
/// placeholder text over a lit row.
#[test]
fn the_status_line_is_a_band_and_the_entry_line_is_not() {
    let mut m = model(45, 28);
    let band = banded_columns(&mut m, 45, 28, 26, Theme { color: true });
    assert_eq!(band.len(), 45, "every column, gutters included: {band:?}");
    assert!(
        banded_columns(&mut m, 45, 28, 27, Theme { color: true }).is_empty(),
        "the row being typed into keeps the terminal's own background"
    );

    // Nothing is underlined either — the rule that landed through the
    // descenders is gone and has not come back as anything else.
    for row in [26u16, 27] {
        let underlined =
            modifier_columns(&mut m, 45, 28, row, ratatui::style::Modifier::UNDERLINED);
        assert!(underlined.is_empty(), "row {row} carries no rule: {underlined:?}");
    }

    // Typing changes the count on the band, never the band.
    for c in "coc".chars() {
        update(&mut m, Msg::Char(c));
    }
    assert_eq!(banded_columns(&mut m, 45, 28, 26, Theme { color: true }), band);

    // The leader chip closes the entry line, reversed against the plain
    // background rather than against the band.
    let reversed = modifier_columns(&mut m, 45, 28, 27, ratatui::style::Modifier::REVERSED);
    assert_eq!(reversed, [39, 40, 41, 42, 43], "SPC is reverse, with a gutter after it");
}

/// **`NO_COLOR` has no band**, and that is the honest cost of this texture:
/// it is the first thing on the surface that a monochrome run loses.
///
/// What survives is the prompt and the words in the field, which is why the
/// marking may never be the only thing saying what the row is for.
#[test]
fn a_monochrome_run_loses_the_band_but_not_the_row() {
    let mut m = model(45, 28);
    assert!(banded_columns(&mut m, 45, 28, 26, Theme { color: false }).is_empty());

    let (lines, _) = render_with(&mut m, 45, 28, Theme { color: false });
    assert!(lines[27].contains("Type to search"), "the words still say it: {:?}", lines[27]);
    assert!(lines[27].contains("SPC"), "and the button is still there");
    assert!(lines[26].contains("⏎ open"), "and the status line still reads: {:?}", lines[26]);
}

/// **The touch layout has one button, and it says what it is for.**
///
/// The `⌨` chip is gone: Termux has its own keyboard key, and tapping the field
/// already raises the IME, so a second button for it was a button for a key the
/// thumb already holds. `SPC` took the corner — and because a bare reversed
/// `SPC` announces only that it is pressable, the empty field's second phrase
/// runs into it and finishes the sentence.
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
    // and no signpost — but it does get the underline its query row was missing.
    let mut wide = model(100, 26);
    let lines = screen(&mut wide, 100, 26);
    assert!(!lines[25].contains("SPC"), "{:?}", lines[25]);
    assert!(!lines[25].contains("For more"), "nothing to point at: {:?}", lines[25]);
    assert!(lines[25].starts_with(" > "), "the entry line is last here too");
    assert!(lines[24].contains("space menu"), "the hint names the key: {:?}", lines[24]);
    assert!(lines[24].contains("^t scans"), "and teaches every verb it has");
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
    assert!(lines[27].contains("coc█"), "the query is shown with a cursor: {:?}", lines[27]);
    assert!(lines[26].trim_start().starts_with("1/14"), "matched/total: {:?}", lines[26]);
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
    assert!(lines[26].trim_start().starts_with("0/0"));
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

/// **An edit takes the entry line over rather than adding a row.** Three rows of
/// chrome is the budget on both layouts (REWRITE-UI.md §5a), and the last row is
/// exactly what a field you are typing into is for — so the field's own prompt
/// replaces `>` and the count and the `SPC` chip stand down.
#[test]
fn an_edit_takes_over_the_entry_line() {
    let mut m = writable(47, 24);
    update(&mut m, Msg::EditField(ds::edit::Field::Expiry));
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

/// **The record marks the field being edited.** The value on the record is the
/// stored one and the value being typed is on the entry line; without the mark
/// the two rows are talking about each other with nothing to connect them.
#[test]
fn the_record_marks_the_field_being_edited() {
    let mut m = writable(47, 24);
    update(&mut m, Msg::EditField(ds::edit::Field::Expiry));
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

/// **A session that cannot write is not told how to.** The `^e` hint appears
/// when the verb works and not before — the rule that killed the action bar.
#[test]
fn the_edit_hint_appears_only_when_this_session_can_write() {
    let mut readonly = model(100, 26);
    update(&mut readonly, Msg::OpenDetail);
    let hints = screen(&mut readonly, 100, 26).join("\n");
    assert!(!hints.contains("^e"), "a read-only session is not offered an edit");

    let mut writing = writable(100, 26);
    update(&mut writing, Msg::OpenDetail);
    let hints = screen(&mut writing, 100, 26).join("\n");
    assert!(hints.contains("^e"), "and a writing one is: {hints}");
}

/// A dirty edit says what the second `Esc` will do, in the armed tone the quit
/// already uses — one texture for "the next press acts".
#[test]
fn a_dirty_edit_warns_before_it_discards() {
    let mut m = writable(47, 24);
    update(&mut m, Msg::EditField(ds::edit::Field::Expiry));
    update(&mut m, Msg::Char('9'));
    update(&mut m, Msg::Esc);
    let rows = screen(&mut m, 47, 24);
    assert!(rows[rows.len() - 2].contains("esc again to discard"), "{rows:?}");
}

/// **`NO_COLOR` loses the band and nothing else**, the editor included: the
/// prompt, the value and the cursor are all text.
#[test]
fn the_editor_survives_no_color() {
    let mut m = writable(47, 24);
    update(&mut m, Msg::EditField(ds::edit::Field::Expiry));
    let (rows, coloured) = render_with(&mut m, 47, 24, Theme { color: false });
    assert!(!coloured, "no colour was emitted");
    let entry = rows.last().expect("an entry line");
    assert!(entry.contains("expiry:") && entry.contains("2026-07-31"), "{entry:?}");
}
