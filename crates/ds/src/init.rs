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

//! `ds init` — setting this device up: its name, the Syncthing folder, the
//! Syncthing API, and on Termux what the phone still needs.
//!
//! The **device name** is the first half of the writer id every op this device
//! emits carries (`phone` → `phone-core`, §3.1); until it is set, `ds` can
//! browse but not write. Re-running it walks through the same questions with
//! the current answers as defaults, so filling in the Syncthing key later is
//! just `ds init` again.
//!
//! **It does not create `<root>/.dossier/journal/`.** Anything created inside a
//! Syncthing folder syncs, so the directory is born on the first real edit,
//! when [`journal::Writer::open`] creates it. Init says so rather than doing
//! it.

use std::io::IsTerminal;
use std::path::{Path, PathBuf};

use crate::config::Config;
use crate::prompt::{Kind, Prompt, Question};

/// Where the Syncthing API listens unless it says otherwise.
const DEFAULT_ADDRESS: &str = "127.0.0.1:8384";

/// Why an init could not finish.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// The device name is not a usable writer id.
    #[error(
        "`{device}` cannot be a device name: the writer id it forms (`{device}-core`) \
         must be lowercase letters, digits and hyphens, starting with a letter or digit."
    )]
    BadDevice {
        /// The name that was refused.
        device: String,
    },
    /// A rename asked for with no one to confirm it.
    #[error(
        "this device is `{from}`; renaming it to `{to}` strands every edit made as \
         `{from}-core`. Pass --force to do it anyway."
    )]
    Rename {
        /// The current name.
        from: String,
        /// The name asked for.
        to: String,
    },
    /// A question went unanswered.
    #[error(transparent)]
    Prompt(#[from] crate::prompt::Error),
    /// Writing to the terminal failed.
    #[error("cannot write to the terminal: {0}")]
    Io(#[from] std::io::Error),
    /// The config could not be written.
    #[error(transparent)]
    Config(#[from] crate::config::Error),
    /// Under WSL: `ds.exe` on the Windows side of this machine already writes
    /// to the same store under this name.
    #[error("{}", twin_message(device, twin))]
    WindowsTwin {
        /// The name that was refused.
        device: String,
        /// The Windows-side config that claims it.
        twin: crate::wsl::Twin,
    },
}

/// Why a device name shared with `ds.exe` on the same PC is refused, and what
/// to do instead. One wording for `ds init` and for the writer, which refuses
/// the same thing at the first save.
#[must_use]
pub fn twin_message(device: &str, twin: &crate::wsl::Twin) -> String {
    format!(
        "`{device}` is already ds.exe's device name on this PC ({config}) — both \
         would append to `{writer}`, and a lock taken on one side of WSL is \
         invisible from the other. Give this side a name of its own, e.g. \
         `ds init --force --device {device}-wsl`",
        config = twin.config.display(),
        writer = twin.writer,
    )
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
#[derive(Debug, Default, Clone)]
pub struct Answers {
    /// `--device`.
    pub device: Option<String>,
    /// `--root`, or `$DS_ROOT`.
    pub root: Option<PathBuf>,
    /// Rename the device without asking.
    pub force: bool,
}

/// Where init looks for Syncthing's own config, and whether this is Termux.
#[derive(Debug, Default, Clone)]
pub struct Machine {
    /// The WSL this runs under, if any.
    pub wsl: Option<crate::wsl::Wsl>,
    /// Syncthing `config.xml` files to read the API key from, likeliest first.
    pub syncthing_configs: Vec<PathBuf>,
    /// Whether this is Termux, which gets its own checks.
    pub termux: bool,
}

impl Machine {
    /// This machine.
    #[must_use]
    pub fn current() -> Self {
        let wsl = crate::wsl::Wsl::current().cloned();
        Self {
            syncthing_configs: crate::syncthing::config_candidates(wsl.as_ref()),
            wsl,
            termux: crate::open::is_termux(),
        }
    }
}

/// Run `ds init`: ask what the flags left out, then write the config at
/// `path` and return it.
///
/// # Errors
/// [`Error`] for a device name outside the frozen writer grammar, a rename
/// nobody confirmed, a question with nobody to ask or a cancel, a write that
/// failed — or, under WSL, a device name `ds.exe` on the same PC already writes
/// to this store as.
pub fn run(
    path: &Path,
    answers: &Answers,
    prompt: &mut dyn Prompt,
    machine: &Machine,
) -> Result<Config, Error> {
    // A config that will not parse is replaced rather than refused: repairing
    // it is one of the things init is for.
    let existing = if path.is_file() {
        match Config::read(path) {
            Ok(config) => Some(config),
            Err(error) => {
                prompt.say(&format!("{error}\nIt will be replaced.\n"))?;
                None
            }
        }
    } else {
        None
    };
    let was = existing.clone().unwrap_or_default();

    let device = ask_device(answers, prompt, was.device.as_deref())?;
    let root = if let Some(root) = answers.root.clone() {
        root
    } else {
        let default = was.syncthing_root.as_ref().map(|root| root.display().to_string());
        let typed = prompt
            .ask(&Question {
                prompt: "Where is the Syncthing folder? (the root your documents live under)",
                default: default.as_deref(),
                kind: Kind::Folder,
                required: true,
                flag: "--root",
            })?
            .unwrap_or_default();
        PathBuf::from(trim_separator(&typed))
    };
    let wsl = machine.wsl.as_ref();
    let root = absolute(crate::wsl::native_root(wsl, root));
    if let Some(twin) = wsl.and_then(|wsl| crate::wsl::windows_twin(wsl, &device, Some(&root))) {
        return Err(Error::WindowsTwin { device, twin });
    }

    let syncthing = ask_syncthing(prompt, machine, was.syncthing)?;
    let config =
        Config { syncthing_root: Some(root.clone()), device: Some(device.clone()), syncthing };
    config.save(path)?;
    report(prompt, path, &device, &root, machine.termux)?;
    Ok(config)
}

/// A root as stored: `~` expanded and a relative path made absolute, so it
/// does not depend on where `ds` is started.
fn absolute(root: PathBuf) -> PathBuf {
    let root = match root.strip_prefix("~") {
        Ok(rest) => dirs::home_dir().map_or(root.clone(), |home| home.join(rest)),
        Err(_) => root,
    };
    if root.is_relative() {
        std::path::absolute(&root).unwrap_or(root)
    } else {
        root
    }
}

/// A typed folder without the separator the live list leaves on its end.
fn trim_separator(typed: &str) -> &str {
    let trimmed = typed.trim_end_matches(['/', '\\']);
    if trimmed.is_empty() || trimmed.ends_with(':') {
        typed
    } else {
        trimmed
    }
}

/// The device name: the flag's, or asked with the current one as the default.
/// A rename is confirmed, because it strands every edit made under the old
/// name.
fn ask_device(
    answers: &Answers,
    prompt: &mut dyn Prompt,
    was: Option<&str>,
) -> Result<String, Error> {
    loop {
        let device = match answers.device.clone() {
            Some(device) => device,
            None => prompt
                .ask(&Question {
                    prompt: "What is this device called? (lowercase letters, digits and hyphens — e.g. `phone`, `desk`)",
                    default: was,
                    kind: Kind::Text,
                    required: true,
                    flag: "--device",
                })?
                .unwrap_or_default(),
        };
        if !journal::names::is_valid_writer_id(&writer_id(&device)) {
            if answers.device.is_none() && prompt.interactive() {
                prompt.say(&Error::BadDevice { device }.to_string())?;
                continue;
            }
            return Err(Error::BadDevice { device });
        }
        let Some(from) = was.filter(|was| *was != device && !answers.force) else {
            return Ok(device);
        };
        if !prompt.interactive() {
            return Err(Error::Rename { from: from.into(), to: device });
        }
        let confirm = prompt.ask(&Question {
            prompt: &format!(
                "Rename this device from `{from}` to `{device}`? Edits made as `{from}-core` \
                 stay in the journal, but this device stops adding to them."
            ),
            default: Some("no"),
            kind: Kind::YesNo,
            required: true,
            flag: "--force",
        })?;
        if confirm.as_deref() == Some("yes") {
            return Ok(device);
        }
        if answers.device.is_some() {
            return Ok(from.to_string());
        }
    }
}

/// The Syncthing API settings: Syncthing's own, when its config can be read
/// here and the person agrees; otherwise asked for, the current ones kept on
/// Enter. Skippable: only `ds status` needs them.
fn ask_syncthing(
    prompt: &mut dyn Prompt,
    machine: &Machine,
    was: crate::config::Syncthing,
) -> Result<crate::config::Syncthing, Error> {
    let found = crate::syncthing::discover(&machine.syncthing_configs)
        .filter(|found| was.apikey.as_deref() != Some(found.apikey.as_str()));
    if let Some(found) = found {
        let take = prompt.ask(&Question {
            prompt: &format!(
                "Syncthing's settings ({}) have its API key. Use it, so `ds status` can ask \
                 Syncthing how the folder is doing?",
                found.path.display()
            ),
            default: Some("yes"),
            kind: Kind::YesNo,
            required: true,
            flag: "--device",
        })?;
        if take.as_deref() == Some("yes") {
            return Ok(crate::config::Syncthing {
                address: found.address,
                apikey: Some(found.apikey),
                ..was
            });
        }
    }
    if !prompt.interactive() {
        return Ok(was);
    }
    let where_ = if machine.termux {
        "in the Syncthing app's settings"
    } else {
        "in Syncthing's GUI, Actions → Settings → General"
    };
    let apikey = prompt.ask(&Question {
        prompt: &format!(
            "Syncthing API key, so `ds status` can ask Syncthing how the folder is doing \
             ({where_})"
        ),
        default: was.apikey.as_deref(),
        kind: Kind::Secret,
        required: false,
        flag: "--device",
    })?;
    let Some(apikey) = apikey else { return Ok(was) };
    let address = prompt.ask(&Question {
        prompt: "Syncthing's GUI address",
        default: Some(was.address.as_deref().unwrap_or(DEFAULT_ADDRESS)),
        kind: Kind::Text,
        required: true,
        flag: "--device",
    })?;
    Ok(crate::config::Syncthing { address, apikey: Some(apikey), ..was })
}

/// What a Termux install still needs for `ds` to open files and reach shared
/// storage.
#[must_use]
pub fn termux_problems(home: &Path, path_var: Option<&std::ffi::OsStr>) -> Vec<String> {
    let mut problems = Vec::new();
    let on_path = path_var.is_some_and(|paths| {
        std::env::split_paths(paths).any(|dir| dir.join("termux-open").is_file())
    });
    if !on_path {
        problems.push(
            "termux-open is missing, so files cannot be opened — run `pkg install termux-api` \
             and install the Termux:API app"
                .into(),
        );
    }
    if !home.join("storage").is_dir() {
        problems.push(
            "~/storage is missing, so shared storage is out of reach — run \
             `termux-setup-storage` and allow access"
                .into(),
        );
    }
    problems
}

/// What init says when it has finished.
///
/// The journal line is the load-bearing one: it names a directory that does not
/// exist and explains that this is correct, which is otherwise the first thing a
/// new user would try to "fix" by creating it.
fn report(
    prompt: &mut dyn Prompt,
    path: &Path,
    device: &str,
    root: &Path,
    termux: bool,
) -> Result<(), Error> {
    let journal = root.join(".dossier").join("journal");
    prompt.say(&format!("wrote {}", path.display()))?;
    prompt.say(&format!(
        "  device         {device} — this device writes as `{}`",
        writer_id(device)
    ))?;
    prompt.say(&format!("  syncthing_root {}", root.display()))?;
    if journal.is_dir() {
        prompt.say(&format!("  journal        {}", journal.display()))?;
    } else {
        prompt.say(&format!(
            "  journal        {} — not there yet; your first edit creates it",
            journal.display()
        ))?;
    }
    if !root.is_dir() {
        prompt.say(&format!(
            "\nnote: {} does not exist yet. That is fine if Syncthing has not set it up on this\n\
             device yet.",
            root.display()
        ))?;
    }
    if termux {
        let home = dirs::home_dir().unwrap_or_default();
        let problems = termux_problems(&home, std::env::var_os("PATH").as_deref());
        if !problems.is_empty() {
            prompt.say("\nTermux still needs:")?;
            for problem in problems {
                prompt.say(&format!("  - {problem}"))?;
            }
        }
    }
    prompt.say("\n`ds status` checks the store and Syncthing; `ds` opens it.")?;
    Ok(())
}

/// Whether there is a person at the other end of stdin.
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
    ) -> (Result<Config, Error>, String) {
        converse(path, answers, replies, interactive, &Machine::default())
    }

    fn converse(
        path: &Path,
        answers: &Answers,
        replies: &str,
        interactive: bool,
        machine: &Machine,
    ) -> (Result<Config, Error>, String) {
        let mut input = Cursor::new(replies.as_bytes().to_vec());
        let mut output = Vec::new();
        let mut prompt =
            crate::prompt::Lines { input: &mut input, output: &mut output, interactive };
        let result = run(path, answers, &mut prompt, machine);
        (result, String::from_utf8(output).expect("utf-8"))
    }

    /// **Init asks only for what the flags did not supply.** The conversation is
    /// the fallback, never the requirement — which is what lets CI drive it.
    #[test]
    fn it_asks_only_for_what_the_flags_left_out() {
        let dir = sandbox("asks");
        let path = dir.join("config.toml");
        let answers = Answers { root: Some(dir.join("Sync")), ..Answers::default() };
        let (result, transcript) = talk(&path, &answers, "phone\n\n", true);
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
        let (result, transcript) = talk(&path, &answers, "\n\ndesk\n\n", true);
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

    /// **Re-running init keeps every answer the flags and replies leave
    /// alone**, the Syncthing settings among them, so it is how a device is
    /// finished off later.
    #[test]
    fn re_running_keeps_what_is_not_changed() {
        let dir = sandbox("rerun");
        let path = dir.join("config.toml");
        let root = dir.join("Sync");
        std::fs::write(
            &path,
            format!(
                "device = \"phone\"\nsyncthing_root = '{}'\n[syncthing]\n\
                 address = \"https://127.0.0.1:8384\"\napikey = \"secret\"\n",
                root.display()
            ),
        )
        .expect("write");
        let (result, transcript) = talk(&path, &Answers::default(), "\n\n\n\n", true);
        let config = result.expect("init");
        assert!(transcript.contains("(now phone — Enter keeps it)"), "{transcript}");
        let kept = format!("(now {} — Enter keeps it)", root.display());
        assert!(transcript.contains(&kept), "{transcript}");
        assert!(!transcript.contains("secret"), "the key is never printed: {transcript}");
        assert_eq!(config.device.as_deref(), Some("phone"));
        assert_eq!(config.syncthing_root, Some(root));
        assert_eq!(config.syncthing.apikey.as_deref(), Some("secret"));
        assert_eq!(config.syncthing.address.as_deref(), Some("https://127.0.0.1:8384"));
    }

    /// **A rename is confirmed**, because it strands every edit made under the
    /// old name; with nobody to confirm it, it takes `--force`.
    #[test]
    fn a_rename_is_confirmed_or_forced() {
        let dir = sandbox("rename");
        let path = dir.join("config.toml");
        let answers = Answers {
            device: Some("phone".into()),
            root: Some(dir.join("Sync")),
            ..Answers::default()
        };
        talk(&path, &answers, "", false).0.expect("first init");

        let desk = Answers { device: Some("desk".into()), ..answers.clone() };
        let (result, _) = talk(&path, &desk, "", false);
        assert!(matches!(result, Err(Error::Rename { .. })), "got {result:?}");

        let (result, transcript) = talk(&path, &desk, "n\n\n", true);
        assert_eq!(result.expect("init").device.as_deref(), Some("phone"), "declined");
        assert!(transcript.contains("Rename this device from `phone` to `desk`?"), "{transcript}");

        let forced = Answers { force: true, ..desk };
        assert_eq!(
            talk(&path, &forced, "", false).0.expect("forced").device.as_deref(),
            Some("desk")
        );
    }

    /// Syncthing's own config supplies the API key when the person agrees.
    #[test]
    fn the_api_key_comes_from_syncthings_own_config() {
        let dir = sandbox("discover");
        let path = dir.join("config.toml");
        let xml = dir.join("config.xml");
        std::fs::write(
            &xml,
            "<configuration><gui tls=\"true\"><address>127.0.0.1:8384</address>\
             <apikey>FromSyncthing</apikey></gui></configuration>",
        )
        .expect("write");
        let machine = Machine { syncthing_configs: vec![xml], ..Machine::default() };
        let answers = Answers {
            device: Some("desk".into()),
            root: Some(dir.join("Sync")),
            ..Answers::default()
        };
        let (result, transcript) = converse(&path, &answers, "\n", true, &machine);
        let config = result.expect("init");
        assert!(transcript.contains("have its API key. Use it"), "{transcript}");
        assert_eq!(config.syncthing.apikey.as_deref(), Some("FromSyncthing"));
        assert_eq!(config.syncthing.address.as_deref(), Some("https://127.0.0.1:8384"));
    }

    /// With nothing found, the key is asked for and can be skipped; given, the
    /// address defaults to Syncthing's usual one.
    #[test]
    fn the_api_key_is_asked_for_and_skippable() {
        let dir = sandbox("askkey");
        let path = dir.join("config.toml");
        let answers = Answers {
            device: Some("phone".into()),
            root: Some(dir.join("Sync")),
            ..Answers::default()
        };
        let (result, transcript) = talk(&path, &answers, "\n", true);
        assert_eq!(result.expect("init").syncthing.apikey, None);
        assert!(transcript.contains("Syncthing API key"), "{transcript}");
        assert!(transcript.contains("(Enter skips)"), "{transcript}");

        let (result, _) = talk(&path, &answers, "k3y\n\n", true);
        let config = result.expect("init");
        assert_eq!(config.syncthing.apikey.as_deref(), Some("k3y"));
        assert_eq!(config.syncthing.address.as_deref(), Some(DEFAULT_ADDRESS));
    }

    /// Termux needs `termux-open` on the path and `~/storage` set up.
    #[test]
    fn termux_problems_name_their_fix() {
        let dir = sandbox("termux");
        let problems = termux_problems(&dir, None);
        assert_eq!(problems.len(), 2, "{problems:?}");
        assert!(problems[0].contains("pkg install termux-api"));
        assert!(problems[1].contains("termux-setup-storage"));

        let bin = dir.join("bin");
        std::fs::create_dir_all(&bin).expect("mkdir");
        std::fs::write(bin.join("termux-open"), "").expect("write");
        std::fs::create_dir_all(dir.join("storage")).expect("mkdir");
        let paths = std::env::join_paths([&bin]).expect("join");
        let problems = termux_problems(&dir, Some(&paths));
        assert!(problems.is_empty(), "{problems:?}");
    }

    /// A relative root is stored absolute, so it does not depend on where
    /// `ds` is started.
    #[test]
    fn a_relative_root_is_stored_absolute() {
        let stored = absolute(PathBuf::from("Sync/Documents"));
        assert!(stored.is_absolute(), "{}", stored.display());
        assert!(stored.ends_with("Sync/Documents"));
        let home = absolute(PathBuf::from("~/Sync"));
        assert_eq!(Some(home), dirs::home_dir().map(|home| home.join("Sync")));
    }

    /// The live list leaves a separator on a folder; the stored root has none.
    #[test]
    fn a_trailing_separator_is_trimmed() {
        assert_eq!(trim_separator("/storage/Sync/"), "/storage/Sync");
        assert_eq!(trim_separator("C:\\Sync\\"), "C:\\Sync");
        assert_eq!(trim_separator("/"), "/");
        assert_eq!(trim_separator("C:\\"), "C:\\");
    }

    /// A WSL whose drives are mounted under `dir` — a Windows drive this test
    /// owns, so the profile scan reads real files on every CI platform.
    fn fake_wsl(dir: &Path) -> crate::wsl::Wsl {
        crate::wsl::Wsl {
            mount_root: format!("{}/", dir.join("mnt").display()),
            distro: Some("Ubuntu".into()),
        }
    }

    /// **Under WSL, a root typed as Explorer shows it is stored as its mount** —
    /// the config holds a path Linux can open, not backslashes.
    #[test]
    fn under_wsl_a_windows_root_is_stored_as_its_mount() {
        let dir = sandbox("wsl-root");
        let path = dir.join("config.toml");
        let wsl = fake_wsl(&dir);
        let answers = Answers { device: Some("desk-wsl".into()), ..Answers::default() };
        let machine = Machine { wsl: Some(wsl.clone()), ..Machine::default() };
        converse(&path, &answers, "C:\\Users\\g\\Sync\n\n", true, &machine).0.expect("init");
        let root = Config::read(&path).expect("read").syncthing_root.expect("root");
        assert_eq!(root, PathBuf::from(format!("{}c/Users/g/Sync", wsl.mount_root)));
    }

    /// **Under WSL, a device name `ds.exe` already uses for this store is
    /// refused** before anything is written, and the refusal offers a name that
    /// would work. The same name on a different store is fine.
    #[test]
    fn under_wsl_a_windows_twin_is_refused() {
        let dir = sandbox("wsl-twin");
        let path = dir.join("config.toml");
        let wsl = fake_wsl(&dir);
        let windows = dir.join("mnt/c/Users/g/AppData/Local/dossier");
        std::fs::create_dir_all(&windows).expect("mkdir");
        std::fs::write(
            windows.join("config.toml"),
            "device = \"desk\"\nsyncthing_root = 'C:\\Users\\g\\Sync'\n",
        )
        .expect("write");

        let answer = |device: &str, root: &str| {
            let answers = Answers {
                device: Some(device.into()),
                root: Some(root.into()),
                ..Answers::default()
            };
            let machine = Machine { wsl: Some(wsl.clone()), ..Machine::default() };
            converse(&path, &answers, "", false, &machine).0
        };
        let refused = answer("desk", r"c:\users\g\sync").expect_err("a twin");
        assert!(matches!(refused, Error::WindowsTwin { .. }), "{refused:?}");
        let message = refused.to_string();
        assert!(
            message.contains("desk-core") && message.contains("--device desk-wsl"),
            "{message}"
        );
        assert!(!path.exists(), "nothing was written");

        answer("desk", r"D:\Other").expect("the same name on another store");
        std::fs::remove_file(&path).expect("reset");
        answer("desk-wsl", r"C:\Users\g\Sync").expect("a name of its own");
    }

    /// **With no terminal, a missing answer is an error and not a wait.** `ds
    /// init` in a pipe or a CI job must fail fast, naming the flag that would
    /// have answered it.
    #[test]
    fn without_a_terminal_a_missing_answer_names_its_flag() {
        let dir = sandbox("notty");
        let (result, _) = talk(&dir.join("config.toml"), &Answers::default(), "", false);
        match result {
            Err(Error::Prompt(crate::prompt::Error::NotATerminal { flag })) => {
                assert_eq!(flag, "--device");
            }
            other => panic!("expected a fail-fast, got {other:?}"),
        }
    }
}
