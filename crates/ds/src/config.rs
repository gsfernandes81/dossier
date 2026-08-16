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

//! The per-device config file — and nothing else.
//!
//! REWRITE.md §2: per-device config stays a small TOML file in the platform
//! config directory, holding **only** what genuinely differs between this phone
//! and that desktop — where the Syncthing folder is mounted, what this device
//! calls itself, and how to reach the local Syncthing API. Everything shared
//! moved into the journal as ops, so there is no second file to keep in sync and
//! no whole-file last-writer-wins special case.
//!
//! ```toml
//! syncthing_root = "/storage/emulated/0/Sync/Documents"
//! device = "phone"
//!
//! [syncthing]
//! address = "https://127.0.0.1:8384"
//! apikey = "…"
//! ```
//!
//! **A missing config file is not an error.** A fresh device has none until
//! `ds init` (R4) writes one, and `--root` covers every case in between — which
//! is what lets R3 be daily-driven against an exported copy before cutover.

use std::path::{Path, PathBuf};

use serde::Deserialize;

/// Where the config lives and what it says.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Config {
    /// The Syncthing folder root. Every stored document path is relative to it.
    pub syncthing_root: Option<PathBuf>,
    /// This device's name — the first half of a writer id (`phone-core`).
    pub device: Option<String>,
    /// How to reach the local Syncthing REST API.
    #[serde(default)]
    pub syncthing: Syncthing,
}

/// The local Syncthing API connection. Per-device because the address and key
/// differ on every machine.
#[derive(Debug, Clone, Default, Deserialize)]
pub struct Syncthing {
    /// Base URL, e.g. `https://127.0.0.1:8384`.
    pub address: Option<String>,
    /// The REST API key from Syncthing's own settings.
    pub apikey: Option<String>,
    /// Whether to verify the TLS certificate.
    ///
    /// Defaults to **false** because on Termux the API is HTTPS-only with a
    /// *self-signed* certificate (v2 Phase 15 finding: plain http 307-redirects).
    /// The exception is scoped to loopback and nothing else — see the status
    /// slice, which is where the client lives.
    #[serde(default)]
    pub verify_tls: bool,
}

/// Failure to read a config file that exists.
#[derive(Debug, thiserror::Error)]
pub enum Error {
    /// The file is there but could not be read.
    #[error("cannot read {path}: {source}")]
    Read {
        /// The file.
        path: PathBuf,
        /// The underlying error.
        #[source]
        source: std::io::Error,
    },
    /// The file is there but is not valid TOML, or has the wrong shape.
    #[error("{path} is not valid config: {source}")]
    Parse {
        /// The file.
        path: PathBuf,
        /// What the parser objected to.
        #[source]
        source: toml::de::Error,
    },
}

/// This device's config file path.
///
/// `~/.config/dossier/config.toml` on Linux and Termux, `%LOCALAPPDATA%\dossier`
/// on Windows — the same places v2's `platformdirs` chose, so a device that
/// already has one keeps it.
#[must_use]
pub fn path() -> Option<PathBuf> {
    let base = dirs::config_local_dir().or_else(dirs::config_dir)?;
    Some(base.join("dossier").join("config.toml"))
}

impl Config {
    /// Read this device's config, or return the empty default if there is none.
    ///
    /// # Errors
    /// [`Error`] only when a file exists and cannot be read or parsed. A device
    /// with no config yet is a normal state, not a failure.
    pub fn load() -> Result<Self, Error> {
        match path() {
            Some(path) if path.is_file() => Self::read(&path),
            _ => Ok(Self::default()),
        }
    }

    /// Read a specific file.
    ///
    /// # Errors
    /// [`Error`] when it cannot be read or parsed.
    pub fn read(path: &Path) -> Result<Self, Error> {
        let text = std::fs::read_to_string(path)
            .map_err(|source| Error::Read { path: path.to_path_buf(), source })?;
        let mut config: Self = toml::from_str(&text)
            .map_err(|source| Error::Parse { path: path.to_path_buf(), source })?;
        // `~` is not a path component to the OS — only to a shell. A config file
        // is hand-written often enough that expanding it here is worth the four
        // lines it costs.
        config.syncthing_root = config.syncthing_root.map(expand_home);
        Ok(config)
    }
}

/// Expand a leading `~` against the home directory.
fn expand_home(path: PathBuf) -> PathBuf {
    let Ok(rest) = path.strip_prefix("~") else { return path };
    dirs::home_dir().map_or(path.clone(), |home| home.join(rest))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(dir: &Path, body: &str) -> PathBuf {
        let path = dir.join("config.toml");
        std::fs::write(&path, body).expect("write");
        path
    }

    fn temp_dir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("ds-config-{name}"));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).expect("mkdir");
        dir
    }

    /// The whole file is three keys and a table — anything more belongs in the
    /// journal, and this test is where that rule is visible.
    #[test]
    fn a_full_config_reads_back() {
        let dir = temp_dir("full");
        let path = write(
            &dir,
            r#"
                syncthing_root = "/storage/emulated/0/Sync"
                device = "phone"

                [syncthing]
                address = "https://127.0.0.1:8384"
                apikey = "secret"
            "#,
        );
        let config = Config::read(&path).expect("read");
        assert_eq!(config.syncthing_root.unwrap(), Path::new("/storage/emulated/0/Sync"));
        assert_eq!(config.device.as_deref(), Some("phone"));
        assert_eq!(config.syncthing.address.as_deref(), Some("https://127.0.0.1:8384"));
        assert!(!config.syncthing.verify_tls, "loopback + self-signed is the Termux reality");
    }

    /// **A device with no config is a normal state**, not a failure — `--root`
    /// covers it, which is exactly how R3 is driven before `ds init` exists.
    #[test]
    fn an_empty_config_is_valid() {
        let dir = temp_dir("empty");
        let path = write(&dir, "");
        let config = Config::read(&path).expect("read");
        assert!(config.syncthing_root.is_none());
        assert!(config.syncthing.address.is_none());
    }

    /// A broken file is reported with its path and the parser's complaint —
    /// never silently treated as absent, which would look like a lost store.
    #[test]
    fn a_broken_config_names_itself() {
        let dir = temp_dir("broken");
        let path = write(&dir, "syncthing_root = [oops");
        let error = Config::read(&path).unwrap_err();
        assert!(matches!(error, Error::Parse { .. }));
        assert!(error.to_string().contains("config.toml"));
    }

    /// `~` is expanded, because config files get hand-written.
    #[test]
    fn a_tilde_root_expands() {
        let dir = temp_dir("tilde");
        let path = write(&dir, "syncthing_root = \"~/Sync\"");
        let config = Config::read(&path).expect("read");
        let root = config.syncthing_root.unwrap();
        assert!(!root.starts_with("~"), "still a tilde: {}", root.display());
        assert!(root.ends_with("Sync"));
    }
}
