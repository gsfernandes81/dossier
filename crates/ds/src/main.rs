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

#![warn(clippy::pedantic)]
#![forbid(unsafe_code)]

use std::io::{self, Stderr, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::sync::mpsc;
use std::sync::Arc;
use std::time::Instant;

use clap::{Parser, Subcommand};
use ratatui::backend::CrosstermBackend;
use ratatui::crossterm::{
    cursor::Show,
    event::{self, DisableMouseCapture, EnableMouseCapture},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::Terminal;

use ds::app::{update, Effect, Model, Msg};
use ds::follow::{Follower, Owner};
use ds::scans::Scans;
use ds::status::Report;
use ds::{find, input, load, open, Theme};
use journal::Journal;

/// Browse, search and open your documents.
#[derive(Parser, Debug)]
#[command(name = "ds", version, about, long_about = None)]
struct Args {
    /// The Syncthing root — the folder the journal and the documents live in.
    #[arg(long, value_name = "DIR", global = true, env = "DS_ROOT")]
    root: Option<PathBuf>,

    /// A journal directory to read directly, instead of `<root>/.dossier/journal`.
    #[arg(long, value_name = "DIR", global = true)]
    journal: Option<PathBuf>,

    #[command(subcommand)]
    command: Option<Command>,
}

#[derive(Subcommand, Debug)]
enum Command {
    /// Report what the store contains and anything wrong with it.
    Status {
        /// Print only what is wrong, and exit 3 if anything is: for cron.
        #[arg(long)]
        quiet: bool,

        /// Skip the Syncthing check, the only part that touches the network.
        #[arg(long)]
        no_sync: bool,
    },
    /// Open a document's file without starting the TUI.
    Open {
        /// A document id, or search terms — the same matching the TUI does.
        #[arg(required = true, num_args = 1..)]
        query: Vec<String>,
    },
    /// Set this device up: its name, the Syncthing folder and API, and on
    /// Termux what the phone still needs. Re-run it to change any of them.
    Init {
        /// This device's name — the first half of its writer id (`phone` →
        /// `phone-core`). Asked for interactively when omitted.
        #[arg(long, value_name = "NAME")]
        device: Option<String>,

        /// Rename an already named device without asking.
        #[arg(long)]
        force: bool,
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
    // Init comes first, before anything reads a config or a journal: it is the
    // verb for a device that has neither, and loading a store to answer "what
    // is this device called" would be backwards.
    if let Some(Command::Init { device, force }) = &args.command {
        return Ok(init(args, device.clone(), *force));
    }

    // A config that exists but is broken is fatal; one that is simply absent is
    // not. A fresh device has none until `ds init`, and `--root` covers it.
    let config = ds::config::Config::load().map_err(io::Error::other)?;
    let Some((journal, root)) =
        load::locate(args.journal.clone(), args.root.clone(), config.syncthing_root.clone())
    else {
        eprintln!("ds: this device is not set up — run `ds init`, or pass --root");
        return Ok(code::FAILED);
    };
    let loaded = load::load(&journal).map_err(io::Error::other)?;

    match &args.command {
        Some(Command::Status { quiet, no_sync }) => {
            Ok(status(&loaded, &config, &root, *quiet, *no_sync))
        }
        Some(Command::Open { query }) => Ok(open_one(&loaded, &root, &query.join(" "))),
        // Handled above, before any store was read.
        Some(Command::Init { .. }) => Ok(0),
        None => browse(loaded, &config, &journal, &root, start).map(|()| 0),
    }
}

/// `ds init`, wired to the real streams: a line editor on a terminal, plain
/// lines otherwise.
fn init(args: &Args, device: Option<String>, force: bool) -> u8 {
    let Some(path) = ds::config::path() else {
        eprintln!(
            "ds: cannot work out where this platform keeps config — set {}",
            ds::config::DIR_ENV
        );
        return code::FAILED;
    };
    let answers = ds::init::Answers { device, root: args.root.clone(), force };
    let machine = ds::init::Machine::current();
    let result = if ds::init::stdin_is_interactive() {
        let mut prompt = ds::prompt::Terminal { wsl: machine.wsl.clone() };
        ds::init::run(&path, &answers, &mut prompt, &machine)
    } else {
        let stdin = io::stdin();
        let mut input = stdin.lock();
        let mut output = io::stdout();
        let mut prompt =
            ds::prompt::Lines { input: &mut input, output: &mut output, interactive: false };
        ds::init::run(&path, &answers, &mut prompt, &machine)
    };
    match result {
        Ok(_) => 0,
        Err(error) => {
            eprintln!("ds: {error}");
            code::FAILED
        }
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
        root,
    );
    // The one network call in the binary, and the only slow one, so it is last.
    if !no_sync {
        report.sync = Some(
            ds::syncthing::Settings::from_config(&config.syncthing)
                .map(|settings| ds::syncthing::query(&settings, root, ds::wsl::Wsl::current()))
                .unwrap_or_default(),
        );
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

/// `ds open <query>`: an id first, then a search; several matches are listed,
/// never guessed.
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
fn browse(
    loaded: load::Loaded,
    config: &ds::config::Config,
    journal: &Journal,
    root: &Path,
    start: Instant,
) -> io::Result<()> {
    let ops = loaded.load.lines.len();
    let build_at = start.elapsed();
    let missing_journal = (!loaded.load.present).then(|| loaded.path.display().to_string());
    let mut model = Model::new(loaded.store, loaded.today, loaded.warn_until, 80, 24);
    model.missing_journal = missing_journal;
    model.root = Some(root.to_path_buf());
    let theme = Theme::from_env();

    // One queue, made here rather than in the loop, because the journal thread
    // needs its sending half before the loop that owns the receiving half has
    // started. Terminal input, scan loads, saves and reloads all arrive on it.
    let (tx, rx) = mpsc::channel::<Msg>();

    // Whether this session can write at all is decided before the first paint,
    // because the record's hints must not offer an edit that cannot happen. The
    // *journal* is not touched here — see `Follower::save`.
    let owner = Owner::for_config(config, root);
    model.write = match &owner {
        Ok(owner) => ds::app::WriteState::Ready { device: owner.device.clone() },
        Err(reason) => ds::app::WriteState::Off(reason.clone()),
    };
    let follower = Follower::new(journal.clone(), owner.ok(), loaded.stamp, loaded.stats.max_ts());
    let session = Session::start(follower, tx.clone());

    let mut tui = Tui::enter()?;
    let terminal = &mut tui.0;
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
            // After restoring the terminal, or leaving the alternate screen
            // erases the line.
            drop(tui);
            writeln!(io::stderr(), "{line}")?;
            return Ok(());
        }
        writeln!(io::stderr(), "{line}")?;
    }

    let result = event_loop(terminal, &mut model, theme, root, journal, &tx, &rx, &session);
    // Restored before the writer is waited for, so a save still in its fsync
    // finishes behind the shell rather than a frozen screen.
    drop(tui);
    session.finish();
    result
}

/// The journal thread, reachable only through its channel.
///
/// The `Writer` is owned by the thread, so there is no handle for a second
/// part of the program to append through: one process, one writer.
struct Session {
    /// Ops to append. Dropping this ends the thread.
    commands: mpsc::Sender<Vec<journal::Draft>>,
    worker: std::thread::JoinHandle<()>,
}

impl Session {
    /// Starts the thread that saves for this session and follows the journal.
    fn start(mut follower: Follower, results: mpsc::Sender<Msg>) -> Self {
        let (commands, orders) = mpsc::channel::<Vec<journal::Draft>>();
        let worker = std::thread::spawn(move || loop {
            let message = match orders.recv_timeout(ds::follow::POLL) {
                Ok(drafts) => follower.save(drafts),
                Err(mpsc::RecvTimeoutError::Timeout) => match follower.poll() {
                    Some(message) => message,
                    None => continue,
                },
                Err(mpsc::RecvTimeoutError::Disconnected) => return,
            };
            if results.send(message).is_err() {
                return;
            }
        });
        Self { commands, worker }
    }

    /// Closes the channel and waits for the thread to finish what it holds.
    fn finish(self) {
        drop(self.commands);
        let _ = self.worker.join();
    }
}

fn ms(duration: std::time::Duration) -> f64 {
    duration.as_secs_f64() * 1000.0
}

/// The terminal in raw mode on the alternate screen, painted on stderr so
/// stdout stays free for piping. Dropping it restores the terminal.
struct Tui(Terminal<CrosstermBackend<Stderr>>);

impl Tui {
    fn enter() -> io::Result<Self> {
        enable_raw_mode()?;
        // SGR mouse reporting is what makes Termux taps arrive as clicks.
        execute!(io::stderr(), EnterAlternateScreen, EnableMouseCapture)?;
        // Restored before the message prints, or the alternate screen eats
        // the only diagnostic a stripped release build gives.
        let report = std::panic::take_hook();
        std::panic::set_hook(Box::new(move |info| {
            restore_terminal();
            report(info);
        }));
        Ok(Self(Terminal::new(CrosstermBackend::new(io::stderr()))?))
    }
}

impl Drop for Tui {
    fn drop(&mut self) {
        restore_terminal();
    }
}

/// Leaves raw mode, the alternate screen and mouse reporting; harmless when
/// they are already off.
fn restore_terminal() {
    let _ = execute!(io::stderr(), DisableMouseCapture, LeaveAlternateScreen, Show);
    let _ = disable_raw_mode();
}

/// The loop: messages in from **one** channel, frames out.
///
/// Terminal input arrives on its own thread and lands in the same queue as
/// results from workers, so the loop blocks on a single `recv()` and wakes only
/// for a message: a worker wakes the UI without the UI asking whether it is
/// done.
#[allow(clippy::too_many_arguments)] // The shell's whole state, and it is flat.
fn event_loop(
    terminal: &mut Terminal<CrosstermBackend<Stderr>>,
    model: &mut Model,
    theme: Theme,
    root: &Path,
    journal: &Journal,
    tx: &mpsc::Sender<Msg>,
    rx: &mpsc::Receiver<Msg>,
    session: &Session,
) -> io::Result<()> {
    let mut stderr = io::stderr();
    let mut mouse_applied = model.mouse_on;

    // The input thread. `event::read()` blocks it, never the loop below.
    let input_tx = tx.clone();
    std::thread::spawn(move || {
        while let Ok(event) = event::read() {
            let Some(msg) = input::to_msg(&event) else { continue };
            if input_tx.send(msg).is_err() {
                // The loop is gone, so the app is quitting.
                return;
            }
        }
    });

    loop {
        let Ok(msg) = rx.recv() else { return Ok(()) };
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
                let tx = tx.clone();
                std::thread::spawn(move || {
                    if let Err(error) = open::open_file(&path) {
                        let _ = tx.send(Msg::OpenFailed(error.to_string()));
                    }
                });
            }
            Effect::Append(drafts) => {
                // The result comes back as `Msg::Saved` or `Msg::SaveFailed`.
                if session.commands.send(drafts).is_err() {
                    let reason = "the writer is gone — this edit was not saved".into();
                    update(model, Msg::SaveFailed { reason, permanent: true });
                }
            }
            Effect::LoadScans => {
                // Off the render loop: the `enrich` namespace is the bulky half
                // of the store, and the frame that turned the chip on has
                // already been drawn by the time this thread finishes.
                let journal = journal.clone();
                let tx = tx.clone();
                std::thread::spawn(move || {
                    let scans = Scans::load(&journal).unwrap_or_default();
                    let _ = tx.send(Msg::ScansLoaded(Arc::new(scans)));
                });
            }
        }
        terminal.draw(|frame| find::draw(frame, model, theme))?;
    }
}
