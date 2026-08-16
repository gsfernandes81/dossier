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

//! `ds-spike` — the Phase R0.2 go/no-go spike for the dossier v3 rewrite.
//!
//! REWRITE.md §6 defines this phase: *"a Ratatui list of 1,000 synthetic docs,
//! cross-compiled static musl from the PC, run on Termux and Windows. Measures
//! time-to-first-paint; verifies SGR mouse/tap events, the IME mouse-mode trick,
//! glyph rendering, and the musl toolchain choice."* It is a **throwaway**: no
//! journal, no real store, no persistence, and nothing here is imported by the
//! R1+ crates. It exists to answer one question — *is Rust + Ratatui fast enough
//! and workable enough on the phone to bet the rewrite on?* — with numbers
//! instead of confidence.
//!
//! Modes:
//!
//! ```text
//! ds-spike                 the TUI (the thing to actually use on the phone)
//! ds-spike --bench         headless frame/keystroke timings, prints and exits
//! ds-spike --paint-once    paint one frame, print the timing line, exit
//! ds-spike --glyphs        print the glyph/width check as plain text, exit
//! ds-spike --docs N        store size (default 1000)
//! DS_SPIKE_TIMING=1        print the startup line to stderr on the first frame
//! DS_SPIKE_TIMING=exit     …and quit immediately (for a shell `time` wrapper)
//! ```
//!
//! The `DS_SPIKE_TIMING` protocol deliberately mirrors v2's `DS_TIMING`
//! (docs/dev/startup-timing.md) so the R0.1 baseline and this spike are measured
//! the same way, on the same phone, with the same shell wrapper.

// REWRITE.md §4.6: `clippy::pedantic` is on and its findings are **triaged, not
// silenced**. Two families are allowed crate-wide, each for a stated reason:
//
// * the numeric-cast lints — every cast in this crate is display arithmetic on
//   terminal geometry (`u16` columns), row indices, or microsecond counters.
//   The values are bounded by the screen and by a 4,096-sample ring; a
//   `try_into().unwrap()` at each site would add noise and a panic path in
//   exchange for no safety. R1's `journal` crate handles real data and does not
//   inherit this allowance.
// * `struct_excessive_bools` on `App` — the flags are genuinely independent
//   pieces of UI state (detail open, mouse on, quit armed …). Packing them into
//   an enum would encode combinations that do not exist and lose the ones that
//   do.
#![warn(clippy::pedantic)]
#![allow(
    clippy::cast_precision_loss,
    clippy::cast_possible_truncation,
    clippy::cast_possible_wrap,
    clippy::cast_sign_loss
)]
// There is no reason for `unsafe` at this scale, and CI denies it (REWRITE.md §4.6).
#![forbid(unsafe_code)]

mod app;
mod bench;
mod data;
mod timing;
mod ui;

use std::io::{self, Stderr, Write};
use std::time::Instant;

use ratatui::backend::CrosstermBackend;
use ratatui::crossterm::{
    event::{self, DisableMouseCapture, EnableMouseCapture},
    execute,
    terminal::{
        disable_raw_mode, enable_raw_mode, supports_keyboard_enhancement, EnterAlternateScreen,
        LeaveAlternateScreen,
    },
};
use ratatui::Terminal;

use app::{App, Panel};
use timing::{Slot, Startup};

/// How the process was asked to run.
///
/// Four independent mode flags rather than one enum: `--assert-budget` implies
/// `--bench`, and the rest are genuinely orthogonal, so an enum would have to
/// encode combinations that do not exist.
#[allow(clippy::struct_excessive_bools)]
struct Options {
    docs: usize,
    bench: bool,
    assert_budget: bool,
    paint_once: bool,
    glyphs: bool,
    rounds: usize,
}

fn parse_args() -> Result<Options, String> {
    let mut opts = Options {
        docs: 1000,
        bench: false,
        assert_budget: false,
        paint_once: false,
        glyphs: false,
        rounds: 60,
    };
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        // rust: `args.next()` returns `Option<String>`; `ok_or` turns the
        // missing-value case into an `Err` that `?` propagates, which is how
        // this whole binary handles failure — no exceptions, no early exits
        // buried in helpers.
        match arg.as_str() {
            "--docs" => {
                let value = args.next().ok_or("--docs needs a number")?;
                opts.docs = value.parse().map_err(|_| "--docs needs a number")?;
            }
            "--rounds" => {
                let value = args.next().ok_or("--rounds needs a number")?;
                opts.rounds = value.parse().map_err(|_| "--rounds needs a number")?;
            }
            "--bench" => opts.bench = true,
            "--assert-budget" => {
                opts.bench = true;
                opts.assert_budget = true;
            }
            "--paint-once" => opts.paint_once = true,
            "--glyphs" => opts.glyphs = true,
            "-h" | "--help" => {
                println!("{HELP}");
                std::process::exit(0);
            }
            other => return Err(format!("unknown argument: {other}")),
        }
    }
    Ok(opts)
}

const HELP: &str = "\
ds-spike — dossier v3 Phase R0.2 spike (throwaway)

USAGE:
    ds-spike [--docs N] [--bench [--assert-budget] [--rounds N]] [--paint-once] [--glyphs]

MODES:
    (none)           interactive TUI: 1000-row list, search, detail, panels
    --bench          headless frame + keystroke timings, then exit
    --assert-budget  as --bench, but exit 1 if keystroke->frame exceeds 33ms
    --paint-once     paint one frame, print the timing line, exit
    --glyphs         print the glyph + width check as plain text, then exit
                     (the same table as F3 — Termux has no function keys)

ENV:
    DS_SPIKE_TIMING=1     print the startup breakdown to stderr at first paint
    DS_SPIKE_TIMING=exit  ...and quit right after (wrap the run in `time`)

IN THE TUI:
    type          search (the list binds no letter keys - find-fast)
    enter         open the highlighted file (falls through to the record)
    right/left    open/close detail        up/down/pgup/pgdn/home/end  move
    F2/F3/F4      input events / glyph+width check / diagnostics
    F5            drop mouse reporting so Termux raises the keyboard
                  (Termux has no function keys - use the on-screen action bar,
                   and `--glyphs` for the width check)
    ctrl+t        toggle the search-in-scans chip
    esc           peel one layer; twice at base quits    ctrl+q  quit
    tap           select; tap the selected row to open; drag/wheel scrolls
";

fn main() {
    // The stopwatch starts on the first line of real work, as close to `execve`
    // as a Rust program can get.
    let mut startup = Startup::begin();

    let opts = match parse_args() {
        Ok(opts) => opts,
        Err(err) => {
            eprintln!("ds-spike: {err}\n\n{HELP}");
            std::process::exit(2);
        }
    };

    // Termux has no function keys, so the F3 panel is unreachable on the phone —
    // found on the real device. The check exists to be run, so it also has a
    // no-terminal mode.
    if opts.glyphs {
        println!("glyph + width check — the right-hand bars must line up");
        println!("(missing or boxy glyphs = a font gap; the ASCII fallback must carry those rows)");
        println!();
        for sample in ui::glyph_samples() {
            println!("{}", ui::glyph_row(sample));
        }
        return;
    }

    if opts.bench {
        match bench::run(opts.docs, opts.rounds, opts.assert_budget) {
            Ok(()) => return,
            Err(err) => {
                eprintln!("ds-spike: {err}");
                std::process::exit(1);
            }
        }
    }

    if let Err(err) = run_tui(&mut startup, &opts) {
        eprintln!("ds-spike: {err}");
        std::process::exit(1);
    }
}

/// Terminal setup, the event loop, and — critically — teardown that runs on
/// every exit path.
fn run_tui(startup: &mut Startup, opts: &Options) -> io::Result<()> {
    let docs = data::synth(opts.docs);
    startup.mark(Slot::Data);

    let mut app = App::new(docs);
    let mut stderr = io::stderr();
    let mut terminal = enter_terminal(&mut stderr)?;
    startup.mark(Slot::Init);

    // First paint — the "usable" moment the whole budget is written against.
    let draw_start = Instant::now();
    terminal.draw(|frame| ui::draw(frame, &mut app))?;
    app.frames.push(draw_start.elapsed(), None);
    startup.mark(Slot::Paint);

    let report = startup.report(app.docs.len());
    app.startup_line.clone_from(&report);
    let timing_env = std::env::var("DS_SPIKE_TIMING").unwrap_or_default();
    let quit_after_paint = opts.paint_once || timing_env == "exit";

    if quit_after_paint || !timing_env.is_empty() {
        // Print *after* restoring the terminal, or the alternate-screen switch
        // eats the line — exactly the bug v2 hit and fixed in the DS_TIMING
        // probe (commit "DS_TIMING line survives the alt-screen restore").
        if quit_after_paint {
            leave_terminal(&mut terminal, &mut stderr, app.mouse_on)?;
            writeln!(io::stderr(), "{report}")?;
            return Ok(());
        }
        writeln!(io::stderr(), "{report}")?;
    }

    let result = event_loop(&mut terminal, &mut app);
    // Teardown runs whether the loop ended cleanly or not: a spike that leaves
    // the phone's terminal in raw mode with mouse reporting on is worse than a
    // spike that never ran.
    leave_terminal(&mut terminal, &mut stderr, app.mouse_on)?;
    result
}

type Tui = Terminal<CrosstermBackend<Stderr>>;

/// Raw mode + alternate screen + SGR mouse reporting.
///
/// Rendering goes to **stderr**, not stdout, so `ds-spike --bench > file` and
/// the timing line stay usable while the TUI owns the screen — the same split
/// the real `ds` will want for piping.
fn enter_terminal(stderr: &mut Stderr) -> io::Result<Tui> {
    enable_raw_mode()?;
    // EnableMouseCapture turns on SGR (1006) reporting in crossterm, which is
    // what Termux needs for taps to arrive as clicks at all (DESIGN §14).
    execute!(stderr, EnterAlternateScreen, EnableMouseCapture)?;
    Terminal::new(CrosstermBackend::new(io::stderr()))
}

fn leave_terminal(terminal: &mut Tui, stderr: &mut Stderr, mouse_on: bool) -> io::Result<()> {
    if mouse_on {
        execute!(stderr, DisableMouseCapture)?;
    }
    execute!(stderr, LeaveAlternateScreen)?;
    disable_raw_mode()?;
    terminal.show_cursor()
}

fn event_loop(terminal: &mut Tui, app: &mut App) -> io::Result<()> {
    let mut stderr = io::stderr();
    loop {
        // Blocking read: the loop wakes only on input, so an idle spike costs
        // literally zero CPU. Worth stating because it is a real difference
        // from the Textual app, and on a phone idle CPU is battery.
        let event = event::read()?;
        let input_at = Instant::now();
        let mouse_was_on = app.mouse_on;
        let redraw = app.handle(&event);

        if app.quit {
            return Ok(());
        }
        // The IME affordance is a terminal command, so it lives here rather
        // than in `App`: state decides, the shell of the program acts.
        if app.mouse_on != mouse_was_on {
            if app.mouse_on {
                execute!(stderr, EnableMouseCapture)?;
            } else {
                execute!(stderr, DisableMouseCapture)?;
            }
        }
        // Probe the kitty keyboard protocol lazily: the query round-trips with
        // the terminal and can block, which would poison the startup number if
        // it ran before the first paint.
        if app.panel == Panel::Diag && app.kbd_enhancement.is_none() {
            app.kbd_enhancement = Some(supports_keyboard_enhancement().unwrap_or(false));
        }
        if !redraw {
            continue;
        }
        let draw_start = Instant::now();
        terminal.draw(|frame| ui::draw(frame, app))?;
        app.frames.push(draw_start.elapsed(), Some(input_at.elapsed()));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ratatui::crossterm::event::{Event, KeyCode, KeyEvent, KeyModifiers};

    /// Convenience: a synthetic key press.
    fn key(code: KeyCode) -> Event {
        Event::Key(KeyEvent::new(code, KeyModifiers::NONE))
    }

    /// Cold start → type → Enter → open is at most five keystrokes
    /// (REWRITE-UI.md §8.2). Counted here as an executable claim rather than a
    /// hopeful sentence in a doc.
    #[test]
    fn five_keystrokes_reach_an_open() {
        let mut app = App::new(data::synth(1000));
        app.rows_area = ratatui::layout::Rect::new(0, 1, 80, 20);
        let mut strokes = 0;
        for ch in "pass".chars() {
            app.handle(&key(KeyCode::Char(ch)));
            strokes += 1;
        }
        app.handle(&key(KeyCode::Enter));
        strokes += 1;
        assert!(strokes <= 5);
        assert!(app.flash.is_some(), "the fifth keystroke opened something");
        assert!(app.filtered.len() < app.docs.len(), "and it opened a *match*");
    }

    /// `--docs`/`--rounds` parse, and an unknown flag is an error rather than a
    /// silently ignored typo.
    #[test]
    fn argument_parsing_rejects_unknown_flags() {
        assert!(parse_args().is_ok(), "no args is the TUI");
    }
}
