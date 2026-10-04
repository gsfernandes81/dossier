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

//! Handing a file to the platform's opener — the payoff of the whole `Enter`
//! path.
//!
//! A port of v2's `platform_open.py` (DESIGN §7), including the part that
//! matters most in practice: **verify the opener exists and report what to do
//! when it does not**. On Termux, `termux-open` comes from the Termux:API
//! package *and* its companion app, and an F-Droid/Play-Store mismatch between
//! them makes it a silent no-op — which is indistinguishable from "the app is
//! broken" unless the message says otherwise.
//!
//! Reveal-in-file-manager and copy-path are v2 verbs that arrive with the detail
//! surface's actions in R4; this slice is the one verb `Enter` needs.
//!
//! **Under WSL** the file goes to Windows, because that is where the default
//! application is: the path is translated ([`crate::wsl`]) and handed to
//! `rundll32 url.dll,FileProtocolHandler` — the shell's own "open this", in one
//! argument. Not `explorer.exe`, whose parser splits a name at its commas even
//! inside quotes; not `cmd /C start`, which re-reads `&` and `^` in a name that
//! WSL's interop left unquoted for having no spaces; not `wslview`, which is an
//! optional package.

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::wsl::Wsl;

/// Why a file could not be opened. Every variant carries what to do about it.
#[derive(Debug, thiserror::Error)]
pub enum OpenError {
    /// The path is not on this device — usually a store that has not finished
    /// syncing, which is worth saying rather than blaming the opener.
    #[error("not on this device yet, Syncthing may still be catching up: {0}")]
    Missing(PathBuf),
    /// The platform's opener is not installed.
    #[error("{opener} not found — {hint}")]
    NoOpener {
        /// The command that was looked for.
        opener: String,
        /// What to install, in the user's terms.
        hint: String,
    },
    /// The opener ran and failed.
    #[error("{opener} could not open {path}: {detail}")]
    Failed {
        /// The command that ran.
        opener: String,
        /// What it was asked to open.
        path: PathBuf,
        /// Its own complaint, or the exit code when it had none.
        detail: String,
    },
    /// The opener could not be started at all.
    #[error("could not run {opener}: {source}")]
    Spawn {
        /// The command.
        opener: String,
        /// The underlying error.
        #[source]
        source: std::io::Error,
    },
}

/// What opens a file on the Windows side of WSL.
const RUNDLL32: &str = "rundll32.exe";

/// `rundll32.exe` is not on `PATH` and not where Windows keeps it.
const WSL_PATH_HINT: &str = "Windows' programs are not on PATH here — check `appendWindowsPath` \
                             under `[interop]` in /etc/wsl.conf";

/// Linux refused to run a Windows program at all.
const WSL_INTEROP_HINT: &str = "WSL cannot run Windows programs — set `enabled = true` under \
                                `[interop]` in /etc/wsl.conf, then `wsl --shutdown`";

/// `ENOEXEC`: what `exec` of a `.exe` returns when WSL's interop is switched
/// off, since the kernel then sees a PE file it has no loader for. The value is
/// the same on every Linux architecture, and a one-number dependency on `libc`
/// would cost more than it says.
const ENOEXEC: i32 = 8;

const TERMUX_HINT: &str = "run `pkg install termux-api` and install the Termux:API app from the \
                           same source as Termux (an F-Droid/Play-Store mismatch makes \
                           termux-open a silent no-op)";

/// Whether this process is running under Termux on Android.
///
/// Both signals are checked because neither is guaranteed: `TERMUX_VERSION` is
/// absent in some login shells, and `PREFIX` can be inherited by a subshell that
/// unset the other.
#[must_use]
pub fn is_termux() -> bool {
    std::env::var("PREFIX").is_ok_and(|prefix| prefix.contains("com.termux"))
        || std::env::var_os("TERMUX_VERSION").is_some()
}

/// Open `path` with the platform's default application.
///
/// # Errors
/// [`OpenError`] when the file is absent, the opener is missing, or it failed.
pub fn open_file(path: &Path) -> Result<(), OpenError> {
    if !path.exists() {
        return Err(OpenError::Missing(path.to_path_buf()));
    }
    if let Some(wsl) = Wsl::current() {
        return open_on_windows(wsl, path);
    }
    let (opener, hint) = platform_opener();
    let mut command = Command::new(&opener);
    if cfg!(target_os = "windows") && opener == "cmd" {
        // The empty string is `start`'s title argument. Without it, a quoted
        // path is taken *as* the title and nothing opens — a decades-old
        // `cmd.exe` wart, and the reason this is not just `start <path>`.
        command.args(["/C", "start", ""]).arg(path);
    } else {
        command.arg(path);
    }
    run(&opener, command, path, &hint)
}

/// Open a file from WSL with Windows' default application.
fn open_on_windows(wsl: &Wsl, path: &Path) -> Result<(), OpenError> {
    // A relative root (`--root .`) is still a path Windows must be able to
    // name from wherever *it* starts.
    let absolute = std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
    let Some(windows) = wsl.to_windows(&absolute) else {
        return Err(OpenError::Failed {
            opener: RUNDLL32.into(),
            path: path.to_path_buf(),
            detail: "Windows has no name for it: it is inside this distribution and \
                     WSL_DISTRO_NAME is not set"
                .into(),
        });
    };
    // `PATH` first, then where Windows keeps it, for a WSL configured with
    // `appendWindowsPath = false`.
    let system = format!("{}c/Windows/System32/{RUNDLL32}", wsl.mount_root);
    for opener in [RUNDLL32, system.as_str()] {
        let mut command = Command::new(opener);
        command.args(["url.dll,FileProtocolHandler", &windows]);
        // A Windows program started from a directory inside the distribution
        // gets a `\\wsl$` working directory, which some of them warn about on
        // stderr — and stderr is where this module looks for a complaint.
        let drive = PathBuf::from(format!("{}c", wsl.mount_root));
        if drive.is_dir() {
            command.current_dir(drive);
        }
        match run(opener, command, path, WSL_PATH_HINT) {
            Err(OpenError::NoOpener { .. }) => {}
            Err(OpenError::Spawn { source, .. }) if source.raw_os_error() == Some(ENOEXEC) => {
                return Err(OpenError::NoOpener {
                    opener: RUNDLL32.into(),
                    hint: WSL_INTEROP_HINT.into(),
                });
            }
            done => return done,
        }
    }
    Err(OpenError::NoOpener { opener: RUNDLL32.into(), hint: WSL_PATH_HINT.into() })
}

/// The opener for this platform, and what to tell the user if it is missing.
fn platform_opener() -> (String, String) {
    if is_termux() {
        return ("termux-open".into(), TERMUX_HINT.into());
    }
    if cfg!(target_os = "windows") {
        // `cmd /C start` rather than a bare `start`: `start` is a shell builtin,
        // not an executable, so there is nothing on PATH to find.
        return ("cmd".into(), "cmd.exe is missing from PATH".into());
    }
    if cfg!(target_os = "macos") {
        return ("open".into(), "no 'open' on PATH".into());
    }
    ("xdg-open".into(), "no 'xdg-open' on PATH — install xdg-utils".into())
}

/// Run a prepared opener and turn what happened into an [`OpenError`] that
/// says what to do about it.
fn run(opener: &str, mut command: Command, path: &Path, hint: &str) -> Result<(), OpenError> {
    let output = match command.output() {
        Ok(output) => output,
        Err(source) if source.kind() == std::io::ErrorKind::NotFound => {
            return Err(OpenError::NoOpener { opener: opener.into(), hint: hint.into() })
        }
        Err(source) => return Err(OpenError::Spawn { opener: opener.into(), source }),
    };
    if output.status.success() {
        return Ok(());
    }
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    let detail = if stderr.is_empty() {
        output
            .status
            .code()
            .map_or_else(|| "killed by a signal".into(), |c| format!("exit code {c}"))
    } else {
        stderr
    };
    Err(OpenError::Failed { opener: opener.into(), path: path.to_path_buf(), detail })
}

/// Resolve a stored document path against the Syncthing root.
///
/// Paths in the data model are POSIX and **relative to the device's Syncthing
/// root** (DESIGN §4/§6) — never absolute, never per-device — so this is the one
/// place a stored path becomes a real one. Windows accepts forward slashes, so
/// the join needs no separator translation.
#[must_use]
pub fn resolve(root: &Path, stored: &str) -> PathBuf {
    root.join(stored)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A path that is not on the device is reported as *not synced yet*, not as
    /// an opener failure — the difference decides what the user does next.
    #[test]
    fn a_missing_file_names_itself_and_syncthing() {
        let error = open_file(Path::new("/definitely/not/here.pdf")).unwrap_err();
        assert!(matches!(error, OpenError::Missing(_)));
        assert!(error.to_string().contains("Syncthing"));
    }

    /// Stored paths are relative to the Syncthing root and resolved exactly
    /// once, here.
    #[test]
    fn stored_paths_resolve_against_the_root() {
        let joined = resolve(Path::new("/home/u/Sync"), "Marine/coc.pdf");
        assert!(joined.ends_with("Marine/coc.pdf"));
        assert!(joined.starts_with("/home/u/Sync"));
    }

    /// The WSL hints name the setting to change and the file it lives in —
    /// both failures otherwise look like "Windows is broken".
    #[test]
    fn the_wsl_hints_name_the_setting_and_its_file() {
        for (hint, setting) in
            [(WSL_PATH_HINT, "appendWindowsPath"), (WSL_INTEROP_HINT, "enabled = true")]
        {
            assert!(hint.contains(setting), "{hint}");
            assert!(hint.contains("/etc/wsl.conf"), "{hint}");
        }
    }

    /// **The WSL opener, end to end, with a stand-in `rundll32.exe`.** Put
    /// where Windows keeps the real one, it is found by the `PATH`-miss
    /// fallback — the `appendWindowsPath = false` case — and records what it was
    /// given: the translated Windows path as **one** argument (spaces and a
    /// comma intact), after the handler, started from the drive.
    ///
    /// Skipped on a real WSL, where the real `rundll32.exe` is on `PATH` and
    /// would try to open the file on the desktop.
    #[cfg(unix)]
    #[test]
    fn under_wsl_a_file_goes_to_rundll32_as_one_windows_path() {
        use std::os::unix::fs::PermissionsExt;
        if Wsl::current().is_some() {
            return;
        }
        let base = std::env::temp_dir().join(format!("ds-open-wsl-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let system = base.join("mnt/c/Windows/System32");
        std::fs::create_dir_all(&system).expect("mkdir");
        let log = base.join("argv");
        let fake = system.join(RUNDLL32);
        std::fs::write(
            &fake,
            format!("#!/bin/sh\n{{ pwd; printf '%s\\n' \"$@\"; }} > '{}'\n", log.display()),
        )
        .expect("write");
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).expect("chmod");

        let wsl = Wsl {
            mount_root: format!("{}/", base.join("mnt").display()),
            distro: Some("Ubuntu".into()),
        };
        let file = base.join("mnt/c/Users/g/a b,c.pdf");
        open_on_windows(&wsl, &file).expect("opened");

        let seen = std::fs::read_to_string(&log).expect("the stand-in ran");
        let lines: Vec<&str> = seen.lines().collect();
        let drive = base.join("mnt/c");
        assert_eq!(
            lines,
            [drive.to_str().unwrap(), "url.dll,FileProtocolHandler", r"C:\Users\g\a b,c.pdf"]
        );
        let _ = std::fs::remove_dir_all(&base);
    }

    /// The Termux hint names both halves of the install, because having only one
    /// is the failure that looks like a bug in this app.
    #[test]
    fn the_termux_hint_covers_the_package_and_the_app() {
        assert!(TERMUX_HINT.contains("pkg install termux-api"));
        assert!(TERMUX_HINT.contains("Termux:API app"));
    }
}
