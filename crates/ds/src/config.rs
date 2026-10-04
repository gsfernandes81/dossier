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

//! The per-device config file: only what differs between devices — where the
//! Syncthing folder is mounted, what this device is called, and how to reach
//! its Syncthing API. Everything shared lives in the journal.
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
//! A missing file is not an error: a fresh device has none until `ds init`.

use std::path::{Path, PathBuf};

use serde::Deserialize;

/// Environment override for the config directory. Tests need it: `dirs`
/// resolves the Windows directory through the Known Folder API, which ignores
/// `HOME` and `APPDATA`, so nothing else can sandbox it there.
pub const DIR_ENV: &str = "DS_CONFIG_DIR";

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
    /// Whether to verify the TLS certificate; off by default because Termux's
    /// API is HTTPS-only with a self-signed certificate, and only ever on
    /// loopback.
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
    /// The file could not be written.
    #[error("cannot write {path}: {source}")]
    Write {
        /// The file.
        path: PathBuf,
        /// The underlying error.
        #[source]
        source: std::io::Error,
    },
}

/// This device's config file path: `~/.config/dossier/config.toml` on Linux
/// and Termux, `%LOCALAPPDATA%\\dossier` on Windows. [`DIR_ENV`] overrides the
/// directory.
#[must_use]
pub fn path() -> Option<PathBuf> {
    if let Some(dir) = std::env::var_os(DIR_ENV) {
        return Some(PathBuf::from(dir).join("config.toml"));
    }
    let base = dirs::config_local_dir().or_else(dirs::config_dir)?;
    Some(base.join("dossier").join("config.toml"))
}

/// Environment override for the state directory, for the same Windows reason
/// as [`DIR_ENV`].
pub const STATE_DIR_ENV: &str = "DS_STATE_DIR";

/// Where this device keeps state that must never sync: the writer's lock,
/// which in the synced folder would lock the other device out of its journal.
#[must_use]
pub fn state_dir() -> Option<PathBuf> {
    if let Some(dir) = std::env::var_os(STATE_DIR_ENV) {
        return Some(PathBuf::from(dir));
    }
    Some(dirs::data_local_dir().or_else(dirs::data_dir)?.join("dossier"))
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
        // Only a shell expands `~`, and this file is written by hand.
        config.syncthing_root = config.syncthing_root.map(expand_home);
        Ok(config)
    }

    /// The file this config would be written as, by hand so it can carry the
    /// comments a person editing it needs.
    #[must_use]
    pub fn render(&self) -> String {
        use std::fmt::Write as _;
        let mut out = String::from(
            "# dossier — this device's config.\n\
             #\n\
             # Only what genuinely differs between devices lives here; everything\n\
             # shared is in the journal as ops. Written by `ds init`, and safe to\n\
             # edit by hand.\n\n",
        );
        if let Some(root) = &self.syncthing_root {
            out.push_str("# The Syncthing folder root. Every stored document path is relative\n");
            out.push_str("# to it, and the journal lives at <root>/.dossier/journal.\n");
            let _ = writeln!(out, "syncthing_root = {}", quote(&root.display().to_string()));
        }
        if let Some(device) = &self.device {
            out.push_str("\n# This device's name — the first half of its writer id.\n");
            let _ = writeln!(out, "device = {}", quote(device));
        }
        if self.syncthing.address.is_some() || self.syncthing.apikey.is_some() {
            out.push_str("\n# The local Syncthing REST API, for `ds status`'s health check.\n");
            out.push_str("[syncthing]\n");
            if let Some(address) = &self.syncthing.address {
                let _ = writeln!(out, "address = {}", quote(address));
            }
            if let Some(apikey) = &self.syncthing.apikey {
                let _ = writeln!(out, "apikey = {}", quote(apikey));
            }
            if self.syncthing.verify_tls {
                out.push_str("verify_tls = true\n");
            }
        }
        out
    }

    /// Write this config, replacing whatever is there, through a temp file in
    /// the same directory: a rename across devices fails, and a half-written
    /// config is a device that cannot find its store.
    ///
    /// # Errors
    /// [`Error::Write`] for any filesystem failure, naming the file.
    pub fn save(&self, path: &Path) -> Result<(), Error> {
        let fail = |source| Error::Write { path: path.to_path_buf(), source };
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(fail)?;
        }
        let temp = path.with_extension(format!("toml.tmp-{}", std::process::id()));
        journal::replace_file(path, &temp, self.render().as_bytes()).map_err(fail)
    }
}

/// One TOML string literal, escaped.
///
/// Via `toml::Value` rather than by hand: a Windows path is full of backslashes
/// and every one of them needs escaping in a basic string.
fn quote(value: &str) -> String {
    toml::Value::String(value.to_string()).to_string()
}

/// Expands a leading `~` against the home directory.
pub(crate) fn expand_home(path: PathBuf) -> PathBuf {
    let Ok(rest) = path.strip_prefix("~") else { return path };
    dirs::home_dir().map_or(path.clone(), |home| home.join(rest))
}

/// Makes a relative path absolute against the working directory. An absolute
/// one is left alone: `std::path::absolute` would also rewrite its separators
/// on Windows, moving a WSL mount path off its prefix.
pub(crate) fn absolute(path: &Path) -> PathBuf {
    if path.is_relative() {
        std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf())
    } else {
        path.to_path_buf()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(dir: &Path, body: &str) -> PathBuf {
        let path = dir.join("config.toml");
        std::fs::write(&path, body).expect("write");
        path
    }

    /// The whole file is three keys and a table — anything more belongs in the
    /// journal.
    #[test]
    fn a_full_config_reads_back() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = write(
            dir.path(),
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

    #[test]
    fn an_empty_config_is_valid() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = write(dir.path(), "");
        let config = Config::read(&path).expect("read");
        assert!(config.syncthing_root.is_none());
        assert!(config.syncthing.address.is_none());
    }

    /// Treated as absent, a broken file would look like a lost store.
    #[test]
    fn a_broken_config_names_itself() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = write(dir.path(), "syncthing_root = [oops");
        let error = Config::read(&path).unwrap_err();
        assert!(matches!(error, Error::Parse { .. }));
        assert!(error.to_string().contains("config.toml"));
    }

    /// Config files get hand-written.
    #[test]
    fn a_tilde_root_expands() {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = write(dir.path(), "syncthing_root = \"~/Sync\"");
        let config = Config::read(&path).expect("read");
        let root = config.syncthing_root.unwrap();
        assert!(!root.starts_with("~"), "still a tilde: {}", root.display());
        assert!(root.ends_with("Sync"));
    }
}
