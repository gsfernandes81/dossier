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

//! `ds` — the binary: terminal lifecycle, the event loop, and nothing else.
//!
//! Everything that decides anything lives in the library. This file is the
//! *shell*: it reads the journal, sets the terminal up, pumps events through
//! [`ds::app::update`], performs the effects that need the outside world, and —
//! critically — **restores the terminal on every exit path**. A TUI that leaves
//! a phone in raw mode with mouse reporting on is worse than one that never
//! started.
//!
//! ```text
//! ds                      browse the store
//! ds --root <DIR>         the Syncthing root (default: $DS_ROOT)
//! ds --journal <DIR>      a journal directory directly, for a copy or a test
//! DS_TIMING=1             print the startup breakdown to stderr at first paint
//! DS_TIMING=exit          ...and quit right after (wrap the run in `time`)
//! ```
//!
//! `DS_TIMING` deliberately mirrors v2's probe and the R0.2 spike's, so the
//! three are measured the same way on the same phone: 1053 ms for the Python
//! app, 6.2 ms for the spike, and this binary is the one that has to keep the
//! second number with a real store behind it.

#![warn(clippy::pedantic)]
#![forbid(unsafe_code)]

use std::io::{self, Stderr, Write};
use std::path::PathBuf;
use std::time::Instant;

use clap::Parser;
use jiff::{civil::Date, ToSpan, Zoned};
use ratatui::backend::CrosstermBackend;
use ratatui::crossterm::{
    event::{self, DisableMouseCapture, EnableMouseCapture},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::Terminal;

use ds::app::{update, Effect, Model};
use ds::{find, input, open, Store};
use journal::{Journal, Namespace};

/// Browse, search and open your documents.
#[derive(Parser, Debug)]
#[command(name = "ds", version, about, long_about = None)]
struct Args {
    /// The Syncthing root — the folder the journal and the documents live in.
    #[arg(long, value_name = "DIR")]
    root: Option<PathBuf>,

    /// A journal directory to read directly, instead of `<root>/.dossier/journal`.
    ///
    /// Reading an exported copy is how R3 is daily-driven before cutover, so
    /// this is a first-class flag rather than a debugging one.
    #[arg(long, value_name = "DIR")]
    journal: Option<PathBuf>,
}

fn main() {
    // The stopwatch starts on the first line of real work, as close to `execve`
    // as a Rust program gets.
    let start = Instant::now();
    let args = Args::parse();

    if let Err(error) = run(&args, start) {
        eprintln!("ds: {error}");
        std::process::exit(1);
    }
}

/// Everything, in order, with the terminal restored whatever happens.
fn run(args: &Args, start: Instant) -> io::Result<()> {
    let root = args
        .root
        .clone()
        .or_else(|| std::env::var_os("DS_ROOT").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("."));
    let journal = args.journal.clone().map_or_else(|| Journal::under_root(&root), Journal::new);

    // Read + parse. The `meta` namespace only: transcripts and scan readings
    // live in `enrich` and are not on the startup path (§3.1), which is half the
    // reason the fold is fast enough to do on every launch.
    let load = journal.load(Namespace::Meta).map_err(io::Error::other)?;
    let read_at = start.elapsed();
    let folded = journal::fold(&load.lines);
    let fold_at = start.elapsed();
    let store = Store::build(&folded);
    let build_at = start.elapsed();

    let (today, warn_until) = window(store.warn_days());
    let mut model = Model::new(store, today, warn_until, 80, 24);
    let theme = ds::theme::Theme::from_env();

    let mut stderr = io::stderr();
    let mut terminal = enter_terminal(&mut stderr)?;
    let init_at = start.elapsed();
    terminal.draw(|frame| find::draw(frame, &mut model, theme))?;
    let paint_at = start.elapsed();

    let timing = std::env::var("DS_TIMING").unwrap_or_default();
    if !timing.is_empty() {
        let line = format!(
            "DS_TIMING read={:.1}ms fold={:.1}ms build={:.1}ms term={:.1}ms usable={:.1}ms \
             ops={} docs={}",
            ms(read_at),
            ms(fold_at) - ms(read_at),
            ms(build_at) - ms(fold_at),
            ms(init_at) - ms(build_at),
            ms(paint_at),
            load.lines.len(),
            model.store.docs.len(),
        );
        if timing == "exit" {
            // Print *after* restoring the terminal, or the alternate-screen
            // switch eats the line — the exact bug v2's probe hit and fixed.
            leave_terminal(&mut terminal, &mut stderr, model.mouse_on)?;
            writeln!(io::stderr(), "{line}")?;
            return Ok(());
        }
        writeln!(io::stderr(), "{line}")?;
    }

    let result = event_loop(&mut terminal, &mut model, theme, &root);
    leave_terminal(&mut terminal, &mut stderr, model.mouse_on)?;
    result
}

fn ms(duration: std::time::Duration) -> f64 {
    duration.as_secs_f64() * 1000.0
}

/// Today and the far edge of the warn window, both ISO.
///
/// Resolved once at startup and carried in the model: every expiry comparison is
/// then a string comparison against these two, which is why nothing else in the
/// crate needs a date library.
fn window(warn_days: i64) -> (String, String) {
    let today: Date = Zoned::now().date();
    let warn_until = today.checked_add(warn_days.days()).unwrap_or(today);
    (today.to_string(), warn_until.to_string())
}

type Tui = Terminal<CrosstermBackend<Stderr>>;

/// Raw mode, alternate screen, SGR mouse reporting.
///
/// The TUI paints to **stderr** so stdout stays free for piping — `ds` will grow
/// commands whose output is meant to be read by something other than a person.
fn enter_terminal(stderr: &mut Stderr) -> io::Result<Tui> {
    enable_raw_mode()?;
    // `EnableMouseCapture` turns on SGR (1006) reporting, which is what Termux
    // needs for taps to arrive as clicks at all (DESIGN §14, confirmed in R0.2).
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

fn event_loop(
    terminal: &mut Tui,
    model: &mut Model,
    theme: ds::theme::Theme,
    root: &std::path::Path,
) -> io::Result<()> {
    let mut stderr = io::stderr();
    let mut mouse_applied = model.mouse_on;
    loop {
        // A blocking read: the loop wakes only on input, so an idle `ds` costs
        // no CPU at all. On a phone, idle CPU is battery.
        let event = event::read()?;
        let Some(msg) = input::to_msg(&event) else { continue };
        let effect = update(model, msg);

        // The terminal is reconciled against the model, never commanded
        // separately — one source of truth for whether reporting is on.
        if model.mouse_on != mouse_applied {
            if model.mouse_on {
                execute!(stderr, EnableMouseCapture)?;
            } else {
                execute!(stderr, DisableMouseCapture)?;
            }
            mouse_applied = model.mouse_on;
        }

        match effect {
            Effect::Idle => continue,
            Effect::Quit => return Ok(()),
            Effect::Redraw => {}
            Effect::Open(stored) => {
                let path = open::resolve(root, &stored);
                // The opener's failure is the user's news, not the program's:
                // it lands in the footer and the app carries on.
                if let Err(error) = open::open_file(&path) {
                    model.flash = Some(error.to_string());
                }
            }
        }
        terminal.draw(|frame| find::draw(frame, model, theme))?;
    }
}
