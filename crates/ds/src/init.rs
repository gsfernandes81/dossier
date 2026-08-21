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

//! `ds init` — naming this device, and nothing else.
//!
//! REWRITE.md §4.1 calls it "conversational; sets device id, root, termux
//! checks, syncthing API key". This is the half that the write path needs: the
//! **device name**, which is the first half of the writer id every op this
//! device ever emits carries (`phone` → `phone-core`, §3.1). Until it is set,
//! `ds` can browse but cannot write, because there is no id to write under.
//!
//! # What it deliberately does not do
//!
//! **It does not create `<root>/.dossier/journal/`.** REWRITE.md §7 is explicit
//! that the journal directory first exists inside the synced tree *at cutover,
//! never before* — anything created inside a Syncthing folder syncs by default,
//! and a journal appearing on the other device before its store has been
//! exported is the one thing the cutover plan cannot tolerate. The directory is
//! born on the first real edit, when it has content to justify itself, and
//! [`journal::Writer::open`] creates it then. Init says so rather than doing it.
//!
//! It also does not yet ask for the Syncthing address and API key, or run the
//! Termux checks. Those are the rest of the conversation and they are orthogonal
//! to the write path; the seam is [`Conversation::ask`] — each future question is
//! one more call to it.
//!
//! # Why the conversation is a function over streams
//!
//! rust: `ask` takes `&mut impl BufRead` and `&mut impl Write` rather than
//! reaching for `stdin()`/`stdout()` itself. That is what makes the questions
//! testable — a test drives them with a `Cursor` over canned answers and reads
//! the transcript back — and CI has no terminal to offer either way.

use std::io::{BufRead, IsTerminal, Write};
use std::path::{Path, PathBuf};

use crate::config::Config;

/// Why an init could not finish.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// A config already exists and `--force` was not given.
    #[error(
        "{path} already exists — this device is `{device}`.\n\
         Re-run with --force to replace it (changing the device name strands \
         every edit made under the old one)."
    )]
    Exists {
        /// The config file in the way.
        path: PathBuf,
        /// What it currently calls this device.
        device: String,
    },
    /// The device name is not a usable writer id.
    #[error(
        "`{device}` cannot be a device name: the writer id it forms (`{device}-core`) \
         must be lowercase letters, digits and hyphens, starting with a letter or digit."
    )]
    BadDevice {
        /// The name that was refused.
        device: String,
    },
    /// A value was missing and there was no terminal to ask at.
    #[error("no terminal to ask on — pass {flag}")]
    NotATerminal {
        /// The flag that would have supplied it.
        flag: &'static str,
    },
    /// Reading an answer failed.
    #[error("cannot read a reply: {0}")]
    Io(#[from] std::io::Error),
    /// The config could not be read or written.
    #[error(transparent)]
    Config(#[from] crate::config::Error),
}

/// The component half of this device's writer id.
///
/// The core is `ds` itself; the Python satellite writes as `<device>-lab`
/// (§3.1). Named here because init is where a user first sees the id it forms.
pub const COMPONENT: &str = "core";

/// The writer id a device name forms.
#[must_use]
pub fn writer_id(device: &str) -> String {
    format!("{device}-{COMPONENT}")
}

/// What the caller already knows, from flags.
///
/// rust: a struct of `Option`s rather than four arguments, because the whole
/// point is that any subset may be absent — and the compiler then names each
/// one at the call site instead of relying on argument order.
#[derive(Debug, Default, Clone)]
pub struct Answers {
    /// `--device`.
    pub device: Option<String>,
    /// `--root`, or `$DS_ROOT`.
    pub root: Option<PathBuf>,
    /// Replace an existing config.
    pub force: bool,
}

/// The question-and-answer half, split from the filesystem half so it can be
/// driven by a `Cursor` in a test.
struct Conversation<'a, R: BufRead, W: Write> {
    input: &'a mut R,
    output: &'a mut W,
    /// Whether there is a person on the other end. When there is not, an
    /// unanswered question is an error rather than a wait — `ds init` in a pipe
    /// must fail fast, never hang.
    interactive: bool,
}

impl<R: BufRead, W: Write> Conversation<'_, R, W> {
    /// Ask one question, unless a flag already answered it.
    fn ask(
        &mut self,
        known: Option<String>,
        prompt: &str,
        flag: &'static str,
    ) -> Result<String, Error> {
        if let Some(value) = known {
            return Ok(value);
        }
        if !self.interactive {
            return Err(Error::NotATerminal { flag });
        }
        loop {
            write!(self.output, "{prompt}\n> ")?;
            self.output.flush()?;
            let mut line = String::new();
            if self.input.read_line(&mut line)? == 0 {
                return Err(Error::NotATerminal { flag });
            }
            let answer = line.trim().to_string();
            if !answer.is_empty() {
                return Ok(answer);
            }
            writeln!(self.output, "  (that one has no sensible default — please answer)")?;
        }
    }
}

/// Run `ds init`: ask what is missing, then write the config at `path`.
///
/// rust: the config path is a parameter rather than read from
/// [`crate::config::path`] inside. That keeps the one piece of process-global
/// state — the environment — out of the unit tests, which would otherwise have
/// to serialize against each other to use it.
///
/// # Errors
/// [`Error`] for a config already in the way, a device name outside the frozen
/// writer grammar, a question with no terminal to ask it at, or a write that
/// failed.
pub fn run<R: BufRead, W: Write>(
    path: &Path,
    answers: &Answers,
    input: &mut R,
    output: &mut W,
    interactive: bool,
) -> Result<(), Error> {
    // Refuse before asking anything: making someone answer three questions and
    // *then* telling them it was pointless is the rudest possible ordering.
    //
    // A config that exists but will not parse is only fatal *without* `--force`.
    // With it, "replace whatever is there" is exactly what was asked for, and
    // refusing to repair a broken file would leave the one case init is most
    // needed for unreachable.
    let existing = match (path.is_file(), answers.force) {
        (false, _) => None,
        (true, false) => Some(Config::read(path)?),
        (true, true) => Config::read(path).ok(),
    };
    if let Some(current) = &existing {
        if !answers.force {
            return Err(Error::Exists {
                path: path.to_path_buf(),
                device: current.device.clone().unwrap_or_else(|| "unnamed".into()),
            });
        }
    }

    let mut talk = Conversation { input, output, interactive };
    let device = talk.ask(
        answers.device.clone(),
        "What is this device called? (lowercase letters, digits and hyphens — e.g. `phone`, `desk`)",
        "--device",
    )?;
    if !journal::names::is_valid_writer_id(&writer_id(&device)) {
        return Err(Error::BadDevice { device });
    }
    let root = match answers.root.clone() {
        Some(root) => root,
        None => PathBuf::from(talk.ask(
            None,
            "Where is the Syncthing folder? (the root your documents live under)",
            "--root",
        )?),
    };

    // Everything not asked about is carried through, so `--force` re-names a
    // device without silently dropping its Syncthing credentials.
    let config = Config {
        syncthing_root: Some(root.clone()),
        device: Some(device.clone()),
        syncthing: existing.map(|c| c.syncthing).unwrap_or_default(),
    };
    config.save(path)?;
    report(&mut talk, path, &device, &root)
}

/// What init says when it has finished.
///
/// The journal line is the load-bearing one: it names a directory that does not
/// exist and explains that this is correct, which is otherwise the first thing a
/// new user would try to "fix" by creating it.
fn report<R: BufRead, W: Write>(
    talk: &mut Conversation<'_, R, W>,
    path: &Path,
    device: &str,
    root: &Path,
) -> Result<(), Error> {
    let journal = root.join(".dossier").join("journal");
    writeln!(talk.output, "wrote {}", path.display())?;
    writeln!(
        talk.output,
        "  device         {device} — this device writes as `{}`",
        writer_id(device)
    )?;
    writeln!(talk.output, "  syncthing_root {}", root.display())?;
    if journal.is_dir() {
        writeln!(talk.output, "  journal        {}", journal.display())?;
    } else {
        writeln!(
            talk.output,
            "  journal        {} — not there yet; your first edit creates it",
            journal.display()
        )?;
    }
    if !root.is_dir() {
        writeln!(
            talk.output,
            "\nnote: {} does not exist yet. That is fine if Syncthing has not set it up on this\n\
             device — `ds status` will say so once it does.",
            root.display()
        )?;
    }
    Ok(())
}

/// Whether there is a person at the other end of stdin.
///
/// Split out so `main` reads as the policy and this reads as the mechanism —
/// and so a test can pass `false` without a pty.
#[must_use]
pub fn stdin_is_interactive() -> bool {
    std::io::stdin().is_terminal()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    /// A directory of this test's own, holding the config it writes. No
    /// environment variable is involved, so these run in parallel like every
    /// other test in the crate.
    fn sandbox(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ds-init-{name}"));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("mkdir");
        dir
    }

    fn talk(
        path: &Path,
        answers: &Answers,
        replies: &str,
        interactive: bool,
    ) -> (Result<(), Error>, String) {
        let mut input = Cursor::new(replies.as_bytes().to_vec());
        let mut output = Vec::new();
        let result = run(path, answers, &mut input, &mut output, interactive);
        (result, String::from_utf8(output).expect("utf-8"))
    }

    /// **Init asks only for what the flags did not supply.** The conversation is
    /// the fallback, never the requirement — which is what lets CI drive it.
    #[test]
    fn it_asks_only_for_what_the_flags_left_out() {
        let dir = sandbox("asks");
        let path = dir.join("config.toml");
        let answers = Answers { root: Some(dir.join("Sync")), ..Answers::default() };
        let (result, transcript) = talk(&path, &answers, "phone\n", true);
        result.expect("init");
        assert!(transcript.contains("What is this device called?"), "{transcript}");
        assert!(!transcript.contains("Where is the Syncthing folder?"), "--root answered it");
        assert!(transcript.contains("writes as `phone-core`"), "{transcript}");
    }

    /// **A blank answer is re-asked, not accepted.** An empty device name would
    /// form the writer id `-core`, which the grammar rejects anyway — better to
    /// ask again than to fail at the end of the conversation.
    #[test]
    fn a_blank_reply_is_asked_again() {
        let dir = sandbox("blank");
        let path = dir.join("config.toml");
        let answers = Answers { root: Some(dir.join("Sync")), ..Answers::default() };
        let (result, transcript) = talk(&path, &answers, "\n\ndesk\n", true);
        result.expect("init");
        assert_eq!(transcript.matches("What is this device called?").count(), 3);
        assert_eq!(Config::read(&path).expect("read").device.as_deref(), Some("desk"));
    }

    /// **The device name is checked against the frozen writer grammar**, by the
    /// same function the writer itself calls — so init and `Writer::open` can
    /// never disagree about what a legal id is.
    #[test]
    fn a_device_name_outside_the_grammar_is_refused() {
        let dir = sandbox("grammar");
        let path = dir.join("config.toml");
        for bad in ["Phone", "my_device", "phone!", ""] {
            let answers = Answers {
                device: Some(bad.into()),
                root: Some(dir.join("Sync")),
                ..Answers::default()
            };
            let (result, _) = talk(&path, &answers, "", false);
            assert!(
                matches!(result, Err(Error::BadDevice { .. })),
                "{bad:?} must be refused, got {result:?}"
            );
        }
        assert!(!path.exists(), "nothing was written");
    }

    /// **`ds init` never creates the journal directory** (REWRITE.md §7): it
    /// first exists inside the synced tree at cutover and not one launch before,
    /// because anything inside a Syncthing folder syncs by default.
    #[test]
    fn it_does_not_create_the_journal() {
        let dir = sandbox("nojournal");
        let path = dir.join("config.toml");
        let root = dir.join("Sync");
        std::fs::create_dir_all(&root).expect("mkdir");
        let answers = Answers {
            device: Some("phone".into()),
            root: Some(root.clone()),
            ..Answers::default()
        };
        let (result, transcript) = talk(&path, &answers, "", false);
        result.expect("init");
        assert!(!root.join(".dossier").exists(), "the journal must not exist yet");
        assert!(transcript.contains("not there yet"), "and it says so: {transcript}");
    }

    /// **An existing config is never silently replaced.** `device` is this
    /// device's identity; changing it strands every op written under the old id
    /// in a file nothing appends to again.
    #[test]
    fn an_existing_config_is_refused_until_forced() {
        let dir = sandbox("exists");
        let path = dir.join("config.toml");
        let answers = Answers {
            device: Some("phone".into()),
            root: Some(dir.join("Sync")),
            ..Answers::default()
        };
        talk(&path, &answers, "", false).0.expect("first init");

        let (result, _) = talk(&path, &answers, "", false);
        assert!(matches!(result, Err(Error::Exists { .. })), "got {result:?}");

        let forced = Answers { device: Some("desk".into()), force: true, ..answers };
        talk(&path, &forced, "", false).0.expect("forced init");
        assert_eq!(Config::read(&path).expect("read").device.as_deref(), Some("desk"));
    }

    /// **`--force` re-names the device without dropping the rest of the file.**
    /// The Syncthing credentials were never part of the question, so they must
    /// not be part of the answer.
    #[test]
    fn forcing_carries_the_syncthing_settings_through() {
        let dir = sandbox("carry");
        let path = dir.join("config.toml");
        std::fs::write(
            &path,
            "device = \"phone\"\n[syncthing]\naddress = \"https://127.0.0.1:8384\"\napikey = \"secret\"\n",
        )
        .expect("write");

        let answers =
            Answers { device: Some("phone".into()), root: Some(dir.join("Sync")), force: true };
        talk(&path, &answers, "", false).0.expect("init");
        let config = Config::read(&path).expect("read");
        assert_eq!(config.syncthing.apikey.as_deref(), Some("secret"));
        assert_eq!(config.syncthing.address.as_deref(), Some("https://127.0.0.1:8384"));
    }

    /// **With no terminal, a missing answer is an error and not a wait.** `ds
    /// init` in a pipe or a CI job must fail fast, naming the flag that would
    /// have answered it.
    #[test]
    fn without_a_terminal_a_missing_answer_names_its_flag() {
        let dir = sandbox("notty");
        let (result, _) = talk(&dir.join("config.toml"), &Answers::default(), "", false);
        match result {
            Err(Error::NotATerminal { flag }) => assert_eq!(flag, "--device"),
            other => panic!("expected a fail-fast, got {other:?}"),
        }
    }
}
