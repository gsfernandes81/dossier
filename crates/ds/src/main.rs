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

//! `ds` — the binary: terminal lifecycle, the event loop, and the commands that
//! need no terminal at all.
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
//! ds status [--quiet]     what the store is, and what is wrong with it
//! ds open <query>         open a document's file without the TUI
//! --root <DIR>            the Syncthing root (default: $DS_ROOT, then config)
//! --journal <DIR>         a journal directory directly, for a copy or a test
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
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Instant;

use clap::{Parser, Subcommand};
use ratatui::backend::CrosstermBackend;
use ratatui::crossterm::{
    event::{self, DisableMouseCapture, EnableMouseCapture},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::Terminal;

use ds::app::{update, Effect, Model};
use ds::status::Report;
use ds::{find, input, load, open, Theme};

/// Browse, search and open your documents.
#[derive(Parser, Debug)]
#[command(name = "ds", version, about, long_about = None)]
struct Args {
    /// The Syncthing root — the folder the journal and the documents live in.
    #[arg(long, value_name = "DIR", global = true)]
    root: Option<PathBuf>,

    /// A journal directory to read directly, instead of `<root>/.dossier/journal`.
    ///
    /// Reading an exported copy is how R3 is daily-driven before cutover, so
    /// this is a first-class flag rather than a debugging one.
    #[arg(long, value_name = "DIR", global = true)]
    journal: Option<PathBuf>,

    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// Report what the store contains and anything wrong with it.
    Status {
        /// Print only problems, and exit non-zero if there are any.
        ///
        /// The mode a cron job uses: silent for months, believable when it
        /// finally speaks. Read-only by design (REWRITE.md §3.1).
        #[arg(long)]
        quiet: bool,

        /// Skip the Syncthing check.
        ///
        /// The only part of `status` that touches the network. Worth having a
        /// switch for: on a metered or captive connection the two-second
        /// timeout is the slowest thing this command does.
        #[arg(long)]
        no_sync: bool,
    },
    /// Open a document's file without starting the TUI.
    Open {
        /// A document id, or search terms — the same matching the TUI does.
        #[arg(required = true, num_args = 1..)]
        query: Vec<String>,
    },
}

/// Exit codes, so a script can tell the cases apart.
mod code {
    /// Something the user asked for could not be done.
    pub const FAILED: u8 = 1;
    /// A query matched nothing, or matched too much to act on.
    pub const NO_MATCH: u8 = 2;
    /// `--quiet` found something wrong with the store.
    pub const UNHEALTHY: u8 = 3;
}

fn main() -> ExitCode {
    // The stopwatch starts on the first line of real work, as close to `execve`
    // as a Rust program gets.
    let start = Instant::now();
    let args = Args::parse();

    match run(&args, start) {
        Ok(code) => ExitCode::from(code),
        Err(error) => {
            eprintln!("ds: {error}");
            ExitCode::from(code::FAILED)
        }
    }
}

fn run(args: &Args, start: Instant) -> io::Result<u8> {
    // A config that exists but is broken is fatal; one that is simply absent is
    // not. A fresh device has none until `ds init`, and `--root` covers it.
    let config = ds::config::Config::load().map_err(io::Error::other)?;
    let journal =
        load::locate(args.journal.clone(), args.root.clone(), config.syncthing_root.clone());
    let root = load::root_for(args.root.clone(), config.syncthing_root.clone(), &journal);
    let loaded = load::load(&journal).map_err(io::Error::other)?;

    match &args.command {
        Some(Command::Status { quiet, no_sync }) => {
            Ok(status(&loaded, &config, &root, *quiet, *no_sync))
        }
        Some(Command::Open { query }) => Ok(open_one(&loaded, &root, &query.join(" "))),
        None => browse(loaded, &root, start).map(|()| 0),
    }
}

/// `ds status`.
fn status(
    loaded: &load::Loaded,
    config: &ds::config::Config,
    root: &Path,
    quiet: bool,
    no_sync: bool,
) -> u8 {
    let mut report = Report::new(
        loaded.path.display().to_string(),
        &loaded.load,
        &loaded.stats,
        &loaded.store,
        &loaded.today,
        &loaded.warn_until,
    );
    // The one network call in the whole binary, and the only one that can be
    // slow — so it is last, after everything local has already been decided.
    if !no_sync {
        report.sync = ds::syncthing::Settings::from_config(&config.syncthing)
            .map(|settings| ds::syncthing::query(&settings, root));
    }
    if quiet {
        if report.healthy() {
            return 0;
        }
        print!("{}", report.problems());
        return code::UNHEALTHY;
    }
    print!("{}", report.render());
    0
}

/// `ds open <query>` — the TUI's `Enter` verb, without the TUI.
///
/// An id is tried first, so a script that knows exactly what it wants is never
/// at the mercy of a search. Several matches are **listed, not guessed**: opening
/// the wrong document silently is worse than opening none.
fn open_one(loaded: &load::Loaded, root: &Path, query: &str) -> u8 {
    let docs = &loaded.store.docs;
    let matched: Vec<usize> = docs
        .iter()
        .position(|doc| doc.id == query)
        .map_or_else(|| loaded.store.search(query), |exact| vec![exact]);
    let [only] = matched[..] else {
        if matched.is_empty() {
            eprintln!("ds: nothing matches {query:?}");
        } else {
            eprintln!("ds: {} documents match {query:?}:", matched.len());
            for &i in matched.iter().take(10) {
                eprintln!("  {}  {}", docs[i].id, docs[i].name);
            }
            if matched.len() > 10 {
                eprintln!("  … and {} more", matched.len() - 10);
            }
        }
        return code::NO_MATCH;
    };

    let doc = &docs[only];
    let Some(file) = doc.primary_file() else {
        eprintln!("ds: {} has no file linked", doc.name);
        return code::NO_MATCH;
    };
    let path = open::resolve(root, &file.path);
    match open::open_file(&path) {
        Ok(()) => {
            println!("{}", path.display());
            0
        }
        Err(error) => {
            eprintln!("ds: {error}");
            code::FAILED
        }
    }
}

/// The TUI: everything, in order, with the terminal restored whatever happens.
fn browse(loaded: load::Loaded, root: &Path, start: Instant) -> io::Result<()> {
    let ops = loaded.load.lines.len();
    let build_at = start.elapsed();
    let mut model = Model::new(loaded.store, loaded.today, loaded.warn_until, 80, 24);
    let theme = Theme::from_env();

    let mut stderr = io::stderr();
    let mut terminal = enter_terminal(&mut stderr)?;
    let init_at = start.elapsed();
    terminal.draw(|frame| find::draw(frame, &mut model, theme))?;
    let paint_at = start.elapsed();

    let timing = std::env::var("DS_TIMING").unwrap_or_default();
    if !timing.is_empty() {
        let line = format!(
            "DS_TIMING store={:.1}ms term={:.1}ms usable={:.1}ms ops={ops} docs={}",
            ms(build_at),
            ms(init_at) - ms(build_at),
            ms(paint_at),
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

    let result = event_loop(&mut terminal, &mut model, theme, root);
    leave_terminal(&mut terminal, &mut stderr, model.mouse_on)?;
    result
}

fn ms(duration: std::time::Duration) -> f64 {
    duration.as_secs_f64() * 1000.0
}

type Tui = Terminal<CrosstermBackend<Stderr>>;

/// Raw mode, alternate screen, SGR mouse reporting.
///
/// The TUI paints to **stderr** so stdout stays free for piping — `ds open`
/// prints the path it opened, and a future command's output is meant to be read
/// by something other than a person.
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

fn event_loop(terminal: &mut Tui, model: &mut Model, theme: Theme, root: &Path) -> io::Result<()> {
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
