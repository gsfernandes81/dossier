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

//! Path completion: the live list under a path being typed. The folder the
//! typed text is in is read once, and the part after the last separator
//! narrows it.

use std::path::{Path, PathBuf};

/// More entries than this in one folder are not read: a list that long is
/// searched by typing, and reading it all would stall a keystroke.
const READ_LIMIT: usize = 5000;

/// One entry of a folder.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    /// Its name within the folder.
    pub name: String,
    /// Whether it is a folder.
    pub dir: bool,
    /// The name folded as search folds it, once.
    folded: String,
}

impl Entry {
    /// An entry named `name`.
    #[must_use]
    pub fn new(name: String, dir: bool) -> Self {
        let folded = crate::search::fold(&name);
        Self { name, dir, folded }
    }

    /// The name as the list shows it: a folder ends in `/`.
    #[must_use]
    pub fn label(&self) -> String {
        if self.dir {
            format!("{}/", self.name)
        } else {
            self.name.clone()
        }
    }
}

/// The folder a typed path is in, read once.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Folder {
    /// The typed text up to and including its last separator.
    pub head: String,
    /// Everything in the folder, folders first, each part in natural order.
    pub entries: Vec<Entry>,
}

/// Splits typed text at its last separator, `/` or `\`, keeping the separator
/// with the head.
#[must_use]
pub fn split(typed: &str) -> (&str, &str) {
    match typed.rfind(['/', '\\']) {
        Some(at) => typed.split_at(at + 1),
        None => ("", typed),
    }
}

/// Where the head of a typed path points: relative to `base`, from home for
/// `~`, through WSL for a Windows path.
fn resolve(base: &Path, head: &str, wsl: Option<&crate::wsl::Wsl>) -> PathBuf {
    if let Some(native) = wsl.and_then(|wsl| wsl.to_linux(head)) {
        return native;
    }
    if head == "~/" || head == "~\\" {
        return dirs::home_dir().unwrap_or_default();
    }
    if let Some(rest) = head.strip_prefix("~/").or_else(|| head.strip_prefix("~\\")) {
        return dirs::home_dir().unwrap_or_default().join(rest);
    }
    if head.is_empty() {
        return base.to_path_buf();
    }
    base.join(head)
}

/// Reads the folder `typed` is in, relative to `base`; `dirs_only` leaves out
/// files. A folder that cannot be read lists nothing.
#[must_use]
pub fn read(base: &Path, typed: &str, dirs_only: bool, wsl: Option<&crate::wsl::Wsl>) -> Folder {
    let head = split(typed).0.to_string();
    let mut entries: Vec<Entry> = std::fs::read_dir(resolve(base, &head, wsl))
        .map(|listing| {
            listing
                .take(READ_LIMIT)
                .filter_map(Result::ok)
                .filter_map(|entry| {
                    let name = entry.file_name().into_string().ok()?;
                    // The listing says what an entry is; only a link needs a stat.
                    let kind = entry.file_type().ok()?;
                    let dir = if kind.is_symlink() { entry.path().is_dir() } else { kind.is_dir() };
                    (dir || !dirs_only).then(|| Entry::new(name, dir))
                })
                .collect()
        })
        .unwrap_or_default();
    entries.sort_by(|a, b| {
        b.dir.cmp(&a.dir).then_with(|| crate::place::natural_cmp(&a.folded, &b.folded))
    });
    Folder { head, entries }
}

impl Folder {
    /// Whether this folder is still the one `typed` is in.
    #[must_use]
    pub fn holds(&self, typed: &str) -> bool {
        split(typed).0 == self.head
    }

    /// The entries whose names start with what is typed after the last
    /// separator, ignoring case. Hidden entries show only once a `.` is typed.
    #[must_use]
    pub fn matching(&self, typed: &str) -> Vec<&Entry> {
        let tail = crate::search::fold(split(typed).1);
        self.entries
            .iter()
            .filter(|entry| tail.starts_with('.') || !entry.name.starts_with('.'))
            .filter(|entry| entry.folded.starts_with(&tail))
            .collect()
    }

    /// The typed text with `entry` in place of its last part; a folder keeps
    /// the separator the head uses, so the next list is that folder's.
    #[must_use]
    pub fn fill(&self, entry: &Entry) -> String {
        let separator = if self.head.ends_with('\\') { '\\' } else { '/' };
        if entry.dir {
            format!("{}{}{separator}", self.head, entry.name)
        } else {
            format!("{}{}", self.head, entry.name)
        }
    }
}

/// The live list under a line being typed: the folder it is in and the row
/// the arrows chose. The line itself belongs to the caller.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Completion {
    base: PathBuf,
    dirs_only: bool,
    wsl: Option<crate::wsl::Wsl>,
    folder: Folder,
    /// The row `↑`/`↓` chose; `None` is the line itself.
    pub chosen: Option<usize>,
}

impl Completion {
    /// A list for `line`, read relative to `base`; `dirs_only` leaves out files.
    #[must_use]
    pub fn new(base: PathBuf, dirs_only: bool, wsl: Option<crate::wsl::Wsl>, line: &str) -> Self {
        let folder = read(&base, line, dirs_only, wsl.as_ref());
        Self { base, dirs_only, wsl, folder, chosen: None }
    }

    /// The rows `line` leaves.
    #[must_use]
    pub fn matches(&self, line: &str) -> Vec<&Entry> {
        if self.folder.holds(line) {
            self.folder.matching(line)
        } else {
            Vec::new()
        }
    }

    /// Follows `line` after it was typed into: no row is chosen, and the
    /// folder is read again once the line has left it.
    pub fn typed(&mut self, line: &str) {
        self.chosen = None;
        if !self.folder.holds(line) {
            self.folder = read(&self.base, line, self.dirs_only, self.wsl.as_ref());
        }
    }

    /// Moves the choice; `↑` from the first row goes back to the line.
    pub fn step(&mut self, line: &str, down: bool) {
        self.chosen = step(self.chosen, self.matches(line).len(), down);
    }

    /// Fills `line` with the chosen row, or the top one.
    pub fn tab(&mut self, line: &mut String) {
        let at = self.chosen.unwrap_or(0);
        self.pick(line, at);
    }

    /// Fills `line` with row `at`: true when it is a file, which finishes
    /// the line; a folder opens instead.
    pub fn pick(&mut self, line: &mut String, at: usize) -> bool {
        let Some(entry) = self.matches(line).get(at).map(|entry| (*entry).clone()) else {
            return false;
        };
        *line = self.folder.fill(&entry);
        self.typed(line);
        !entry.dir
    }

    /// `Enter`: true when the line is finished — nothing was chosen, or a
    /// file was. A chosen folder opens instead.
    pub fn enter(&mut self, line: &mut String) -> bool {
        match self.chosen {
            Some(at) => self.pick(line, at),
            None => true,
        }
    }
}

/// A selection in a live list: `None` until an arrow chooses a row.
///
/// `↓` from nothing selects the first row and `↑` from it goes back to
/// nothing, so the line under the list is always one key away.
#[must_use]
pub fn step(chosen: Option<usize>, count: usize, down: bool) -> Option<usize> {
    if count == 0 {
        return None;
    }
    match (chosen, down) {
        (None, true) => Some(0),
        (None, false) => Some(count - 1),
        (Some(0), false) => None,
        (Some(at), false) => Some(at - 1),
        (Some(at), true) => Some((at + 1).min(count - 1)),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sandbox(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ds-complete-{name}"));
        let _ = std::fs::remove_dir_all(&dir);
        for folder in ["Scans/2024", "Scans/2025", "Marine", ".hidden"] {
            std::fs::create_dir_all(dir.join(folder)).expect("mkdir");
        }
        for file in ["Scans/passport.pdf", "Scans/pan.pdf", "notes.txt", ".dotfile"] {
            std::fs::write(dir.join(file), "").expect("write");
        }
        dir
    }

    fn labels(folder: &Folder, typed: &str) -> Vec<String> {
        folder.matching(typed).into_iter().map(Entry::label).collect()
    }

    /// Folders come first, then files; hidden ones only once a `.` is typed.
    #[test]
    fn a_folder_lists_folders_first_and_hides_dotfiles() {
        let base = sandbox("order");
        let folder = read(&base, "", false, None);
        assert_eq!(labels(&folder, ""), ["Marine/", "Scans/", "notes.txt"]);
        assert_eq!(labels(&folder, "."), [".hidden/", ".dotfile"]);
    }

    /// The part after the last separator narrows the list, ignoring case, and
    /// a folder can be kept to folders.
    #[test]
    fn typing_narrows_the_folder_the_head_names() {
        let base = sandbox("narrow");
        let folder = read(&base, "Scans/p", false, None);
        assert_eq!(folder.head, "Scans/");
        assert_eq!(labels(&folder, "Scans/P"), ["pan.pdf", "passport.pdf"]);
        assert!(folder.holds("Scans/pass") && !folder.holds("Scans/2024/"));
        let dirs = read(&base, "Scans/", true, None);
        assert_eq!(labels(&dirs, "Scans/"), ["2024/", "2025/"]);
    }

    /// Filling a folder ends in its separator, a backslash where one is typed.
    #[test]
    fn filling_a_folder_ends_in_its_separator() {
        let base = sandbox("fill");
        let folder = read(&base, "Sc", false, None);
        let scans = folder.matching("Sc")[0].clone();
        assert_eq!(folder.fill(&scans), "Scans/");
        let windows = Folder { head: "C:\\Users\\".into(), entries: vec![] };
        assert_eq!(windows.fill(&Entry::new("g".into(), true)), "C:\\Users\\g\\");
    }

    /// A folder that is not there lists nothing rather than failing.
    #[test]
    fn a_missing_folder_lists_nothing() {
        let folder = read(Path::new("/no/such/place"), "x/", false, None);
        assert!(folder.entries.is_empty(), "{:?}", folder.entries);
    }

    /// The selection starts on nothing and `↑` from the top returns to it.
    #[test]
    fn the_selection_steps_back_to_the_line() {
        assert_eq!(step(None, 3, true), Some(0));
        assert_eq!(step(Some(0), 3, false), None);
        assert_eq!(step(Some(2), 3, true), Some(2));
        assert_eq!(step(None, 0, true), None);
    }
}
