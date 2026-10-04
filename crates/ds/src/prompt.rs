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

//! Asking `ds init`'s questions: plainly, one line each, when stdin is a pipe
//! or a test; on a terminal, with a line editor that lists folders as a path is
//! typed and hides a secret.

use std::io::{BufRead, Write};
use std::path::PathBuf;

use ratatui::crossterm::event::{Event, KeyCode, KeyEvent, KeyModifiers};

use crate::app::Msg;
use crate::complete::{follow, Completion, Entry};

/// Rows the live list shows under the line.
const SHOWN: usize = 8;

/// What kind of answer a question takes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// Free text.
    Text,
    /// A folder, typed with a live list of folders.
    Folder,
    /// A secret, never shown as typed.
    Secret,
    /// Yes or no.
    YesNo,
}

/// One question.
#[derive(Debug, Clone)]
pub struct Question<'a> {
    /// What is asked.
    pub prompt: &'a str,
    /// The answer an empty reply keeps.
    pub default: Option<&'a str>,
    /// What kind of answer it takes.
    pub kind: Kind,
    /// Whether an empty reply with no default is asked again. When not, it
    /// skips the question.
    pub required: bool,
    /// The flag that answers it without asking.
    pub flag: &'static str,
}

/// Why a question went unanswered.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// A value was missing and there was no terminal to ask at.
    #[error("no terminal to ask on — pass {flag}")]
    NotATerminal {
        /// The flag that would have supplied it.
        flag: &'static str,
    },
    /// The person left with `Esc` or `ctrl+c`.
    #[error("cancelled — nothing was written")]
    Cancelled,
    /// Reading or writing the terminal failed.
    #[error("cannot talk to the terminal: {0}")]
    Io(#[from] std::io::Error),
}

/// Something that can ask questions and say things.
pub trait Prompt {
    /// The reply, or the default when it is empty; `None` when an optional
    /// question is skipped. A reply that settles nothing is asked again.
    ///
    /// # Errors
    /// [`Error`] when there is nobody to ask, they cancel, or the terminal fails.
    fn ask(&mut self, question: &Question) -> Result<Option<String>, Error> {
        if !self.interactive() {
            return match (question.default, question.required) {
                (Some(default), _) => Ok(Some(default.to_string())),
                (None, true) => Err(Error::NotATerminal { flag: question.flag }),
                (None, false) => Ok(None),
            };
        }
        loop {
            self.say(&format!("{}{}", question.prompt, hint(question)))?;
            let reply = self.read_line(question)?;
            match settle(question, &reply) {
                Ok(answer) => return Ok(answer),
                Err(()) if question.kind == Kind::YesNo => self.say("  (y or n)")?,
                Err(()) => self.say("  (that one has no sensible default — please answer)")?,
            }
        }
    }

    /// One line typed in answer to `question`.
    ///
    /// # Errors
    /// [`Error`] when the input ends, they cancel, or the terminal fails.
    fn read_line(&mut self, question: &Question) -> Result<String, Error>;

    /// Writes a line of output.
    ///
    /// # Errors
    /// When the output cannot be written.
    fn say(&mut self, line: &str) -> std::io::Result<()>;

    /// Whether there is a person to ask.
    fn interactive(&self) -> bool;
}

/// A secret as a prompt may show it: its ends, never its middle.
#[must_use]
pub fn masked(secret: &str) -> String {
    let chars: Vec<char> = secret.chars().collect();
    if chars.len() <= 8 {
        return "•".repeat(chars.len());
    }
    let head: String = chars[..4].iter().collect();
    let tail: String = chars[chars.len() - 4..].iter().collect();
    format!("{head}…{tail}")
}

/// How the question's default reads in its prompt.
fn hint(question: &Question) -> String {
    match (question.kind, question.default) {
        (Kind::YesNo, Some("yes")) => " [Y/n]".into(),
        (Kind::YesNo, _) => " [y/N]".into(),
        (Kind::Secret, Some(default)) => format!(" (now {} — Enter keeps it)", masked(default)),
        (_, Some(default)) => format!(" (now {default} — Enter keeps it)"),
        (_, None) if !question.required => " (Enter skips)".into(),
        (_, None) => String::new(),
    }
}

/// What a reply means once the default and the kind have had their say:
/// `Err(())` asks again.
fn settle(question: &Question, reply: &str) -> Result<Option<String>, ()> {
    let reply = reply.trim();
    if question.kind == Kind::YesNo {
        return match reply.to_lowercase().as_str() {
            "" => Ok(question.default.map(str::to_string)),
            "y" | "yes" => Ok(Some("yes".into())),
            "n" | "no" => Ok(Some("no".into())),
            _ => Err(()),
        };
    }
    match (reply.is_empty(), question.default) {
        (false, _) => Ok(Some(reply.to_string())),
        (true, Some(default)) => Ok(Some(default.to_string())),
        (true, None) if question.required => Err(()),
        (true, None) => Ok(None),
    }
}

/// Questions asked one line at a time over plain streams — a pipe, or a test.
pub struct Lines<'a, R: BufRead, W: Write> {
    /// Where replies come from.
    pub input: &'a mut R,
    /// Where questions go.
    pub output: &'a mut W,
    /// Whether a person is on the other end. When not, an unanswered question
    /// is an error rather than a wait: `ds init` in a pipe fails fast.
    pub interactive: bool,
}

impl<R: BufRead, W: Write> Prompt for Lines<'_, R, W> {
    fn read_line(&mut self, question: &Question) -> Result<String, Error> {
        write!(self.output, "> ")?;
        self.output.flush()?;
        let mut line = String::new();
        if self.input.read_line(&mut line)? == 0 {
            return Err(Error::NotATerminal { flag: question.flag });
        }
        Ok(line)
    }

    fn say(&mut self, line: &str) -> std::io::Result<()> {
        writeln!(self.output, "{line}")
    }

    fn interactive(&self) -> bool {
        self.interactive
    }
}

/// What a key did to the line being edited.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Step {
    /// Keep editing.
    Continue,
    /// The line is finished.
    Done(String),
    /// The person left.
    Cancel,
}

/// One line being typed, with the live list under it for a folder.
#[derive(Debug, Clone)]
pub struct LineEditor {
    /// What has been typed.
    pub buffer: String,
    kind: Kind,
    list: Option<Completion>,
}

impl LineEditor {
    /// Starts an empty line of `kind`; relative folders are read from `base`.
    ///
    /// Empty even when the question has a current answer: the hint names it
    /// and Enter keeps it, while typing replaces it rather than appending.
    #[must_use]
    pub fn new(kind: Kind, base: PathBuf, wsl: Option<crate::wsl::Wsl>) -> Self {
        let list = (kind == Kind::Folder).then(|| Completion::new(base, true, wsl, ""));
        Self { buffer: String::new(), kind, list }
    }

    /// The live list's rows.
    #[must_use]
    pub fn matches(&self) -> Vec<Entry> {
        self.list
            .as_ref()
            .map(|list| list.matches(&self.buffer).into_iter().cloned().collect())
            .unwrap_or_default()
    }

    /// Applies one key: the live list's, then `ctrl+u` clears, `Enter`
    /// finishes, and `Esc` or `ctrl+c` leaves.
    pub fn key(&mut self, key: KeyEvent) -> Step {
        if key.code == KeyCode::Char('u') && key.modifiers.contains(KeyModifiers::CONTROL) {
            self.buffer.clear();
            follow(self.list.as_mut(), &self.buffer);
            return Step::Continue;
        }
        let Some(msg) = crate::input::to_msg(&Event::Key(key)) else { return Step::Continue };
        if let Some(finished) = self.list.as_mut().and_then(|list| list.key(&mut self.buffer, &msg))
        {
            return if finished { Step::Done(self.buffer.clone()) } else { Step::Continue };
        }
        match msg {
            Msg::Esc | Msg::Quit => return Step::Cancel,
            Msg::Enter => return Step::Done(self.buffer.clone()),
            Msg::Char(c) => self.buffer.push(c),
            Msg::Backspace => {
                self.buffer.pop();
            }
            _ => return Step::Continue,
        }
        follow(self.list.as_mut(), &self.buffer);
        Step::Continue
    }

    /// The line as shown: a secret as dots.
    #[must_use]
    pub fn shown(&self) -> String {
        if self.kind == Kind::Secret {
            "•".repeat(self.buffer.chars().count())
        } else {
            self.buffer.clone()
        }
    }

    /// The list rows on screen, with the chosen one marked, scrolled to keep
    /// it in view.
    #[must_use]
    pub fn rows(&self) -> Vec<(bool, String)> {
        let chosen = self.list.as_ref().and_then(|list| list.chosen);
        let skip = chosen.map_or(0, |at| (at + 1).saturating_sub(SHOWN));
        self.matches()
            .iter()
            .enumerate()
            .skip(skip)
            .take(SHOWN)
            .map(|(at, entry)| (chosen == Some(at), entry.label()))
            .collect()
    }
}

/// Questions asked on a real terminal, through [`LineEditor`].
pub struct Terminal {
    /// The WSL this runs under, so a folder typed as Windows writes it lists.
    pub wsl: Option<crate::wsl::Wsl>,
}

impl Terminal {
    fn edit(&self, question: &Question) -> Result<String, Error> {
        use ratatui::crossterm::{cursor, event, queue, style, terminal};
        let base = std::env::current_dir().unwrap_or_default();
        let mut editor = LineEditor::new(question.kind, base, self.wsl.clone());
        let mut out = std::io::stdout();
        terminal::enable_raw_mode()?;
        let result = (|| -> Result<String, Error> {
            loop {
                let cols = terminal::size()
                    .ok()
                    .filter(|(cols, _)| *cols > 0)
                    .map_or(80, |(cols, _)| cols as usize);
                let line = format!("> {}", editor.shown());
                let line = crate::layout::truncate_left(&line, cols.saturating_sub(1));
                queue!(
                    out,
                    cursor::MoveToColumn(0),
                    terminal::Clear(terminal::ClearType::FromCursorDown)
                )?;
                queue!(out, style::Print(&line))?;
                let rows = editor.rows();
                for (chosen, label) in &rows {
                    let label =
                        crate::layout::truncate(&format!("  {label}"), cols.saturating_sub(1));
                    queue!(out, style::Print("\r\n"))?;
                    if *chosen {
                        queue!(out, style::PrintStyledContent(style::Stylize::reverse(label)))?;
                    } else {
                        queue!(out, style::Print(label))?;
                    }
                }
                if !rows.is_empty() {
                    let up = u16::try_from(rows.len()).unwrap_or(u16::MAX);
                    let at = u16::try_from(crate::layout::width(&line)).unwrap_or(u16::MAX);
                    queue!(out, cursor::MoveUp(up), cursor::MoveToColumn(at))?;
                }
                out.flush()?;
                if let event::Event::Key(key) = event::read()? {
                    match editor.key(key) {
                        Step::Continue => {}
                        Step::Done(text) => return Ok(text),
                        Step::Cancel => return Err(Error::Cancelled),
                    }
                }
            }
        })();
        let _ =
            queue!(out, terminal::Clear(terminal::ClearType::FromCursorDown), style::Print("\r\n"));
        let _ = out.flush();
        terminal::disable_raw_mode()?;
        result
    }
}

impl Prompt for Terminal {
    fn read_line(&mut self, question: &Question) -> Result<String, Error> {
        self.edit(question)
    }

    fn say(&mut self, line: &str) -> std::io::Result<()> {
        writeln!(std::io::stdout(), "{line}")
    }

    fn interactive(&self) -> bool {
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn press(editor: &mut LineEditor, code: KeyCode) -> Step {
        editor.key(KeyEvent::new(code, KeyModifiers::NONE))
    }

    fn typed(editor: &mut LineEditor, text: &str) {
        for c in text.chars() {
            press(editor, KeyCode::Char(c));
        }
    }

    fn sandbox() -> tempfile::TempDir {
        let dir = tempfile::tempdir().expect("tempdir");
        for folder in ["Sync/Documents", "Sync/Music", "Other"] {
            std::fs::create_dir_all(dir.path().join(folder)).expect("mkdir");
        }
        std::fs::write(dir.path().join("Sync/notes.txt"), "").expect("write");
        dir
    }

    /// A folder question lists only folders; `Tab` fills the top one, and a
    /// chosen row opens on `Enter` rather than finishing the line.
    #[test]
    fn a_folder_line_lists_folders_and_opens_the_chosen_one() {
        let base = sandbox();
        let mut editor = LineEditor::new(Kind::Folder, base.path().to_path_buf(), None);
        typed(&mut editor, "Sy");
        press(&mut editor, KeyCode::Tab);
        assert_eq!(editor.buffer, "Sync/");
        let labels: Vec<String> = editor.matches().iter().map(Entry::label).collect();
        assert_eq!(labels, ["Documents/", "Music/"], "no files");
        press(&mut editor, KeyCode::Down);
        assert_eq!(press(&mut editor, KeyCode::Enter), Step::Continue);
        assert_eq!(editor.buffer, "Sync/Documents/");
        assert_eq!(press(&mut editor, KeyCode::Enter), Step::Done("Sync/Documents/".into()));
    }

    /// `Esc` leaves, and a secret is shown as dots.
    #[test]
    fn esc_cancels_and_a_secret_stays_hidden() {
        let mut editor = LineEditor::new(Kind::Secret, PathBuf::new(), None);
        typed(&mut editor, "abc");
        assert_eq!(editor.shown(), "•••");
        assert_eq!(press(&mut editor, KeyCode::Esc), Step::Cancel);
    }

    /// An empty reply keeps the default, a yes-or-no takes either word, and an
    /// optional question can be skipped.
    #[test]
    fn a_reply_settles_against_the_default() {
        let question = |kind, default, required| Question {
            prompt: "?",
            default,
            kind,
            required,
            flag: "--x",
        };
        assert_eq!(
            settle(&question(Kind::Text, Some("phone"), true), ""),
            Ok(Some("phone".into()))
        );
        assert_eq!(settle(&question(Kind::Text, None, true), " "), Err(()));
        assert_eq!(settle(&question(Kind::Secret, None, false), ""), Ok(None));
        assert_eq!(settle(&question(Kind::YesNo, Some("yes"), true), ""), Ok(Some("yes".into())));
        assert_eq!(settle(&question(Kind::YesNo, Some("yes"), true), "N"), Ok(Some("no".into())));
        assert_eq!(settle(&question(Kind::YesNo, None, true), "maybe"), Err(()));
    }

    /// A prompt never prints a secret, only its ends.
    #[test]
    fn a_secret_default_is_masked() {
        assert_eq!(masked("abcdefghijkl"), "abcd…ijkl");
        assert_eq!(masked("short"), "•••••");
    }
}
