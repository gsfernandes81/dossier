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

//! Asking the local Syncthing daemon how the sync is going.
//!
//! Syncthing is the only transport in this design, so "is my phone actually
//! going to see this?" is a question about *it*, not about dossier. `ds status`
//! answers it by reading the daemon's REST API — status only, never a write.
//!
//! Three rules, ported from v2 along with the behaviour:
//!
//! * **Reachability problems are states, not errors.** A daemon that is off, or
//!   an API key that is wrong, is something to *report* — the report is still
//!   worth printing, and every caller wants the degraded value rather than an
//!   exception.
//! * **TLS verification is dropped only for loopback, never globally.** On
//!   Termux the API is HTTPS-only with a *self-signed* certificate (v2 Phase 15:
//!   plain http 307-redirects), so verification cannot succeed there. The
//!   exception is scoped to `127.0.0.0/8`, `::1` and `localhost`; for any other
//!   host, an unverified request is refused outright. On loopback the API key is
//!   the real authenticator and the network is the kernel.
//! * **The interesting folder is the one containing the store.** A device may
//!   sync a dozen folders; only one of them decides whether these documents
//!   move.

use std::path::Path;
use std::time::Duration;

use serde_json::Value;

/// Per-request timeout. Loopback answers in milliseconds; anything slower is a
/// daemon that is not going to answer at all, and `ds status` must not hang.
const TIMEOUT: Duration = Duration::from_secs(2);

/// Syncthing's default GUI/REST bind.
pub const DEFAULT_ADDRESS: &str = "https://127.0.0.1:8384";

/// How the sync is going, as one value.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum State {
    /// No address or key in the per-device config — nothing was asked.
    Unconfigured,
    /// Refused before asking: verification cannot be dropped off loopback.
    Refused,
    /// The daemon did not answer.
    Unreachable,
    /// It answered and rejected the key.
    Unauthorized,
    /// Answered, and the store's folder is up to date.
    Idle,
    /// Answered, and the store's folder is scanning or syncing.
    Busy,
}

impl State {
    /// A one-word label, and a marker that survives monochrome.
    #[must_use]
    pub fn label(self) -> &'static str {
        match self {
            State::Unconfigured => "not configured",
            State::Refused => "refused",
            State::Unreachable => "unreachable",
            State::Unauthorized => "unauthorized",
            State::Idle => "idle",
            State::Busy => "syncing",
        }
    }
}

/// What `ds status` prints about Syncthing.
#[derive(Debug, Clone)]
pub struct Status {
    /// The state, always present.
    pub state: State,
    /// Human detail for the unhappy states.
    pub detail: Option<String>,
    /// Daemon version.
    pub version: Option<String>,
    /// The folder whose path contains the store, if one does.
    pub folder: Option<Folder>,
    /// Devices connected right now, excluding this one.
    pub connected: usize,
    /// Devices configured, excluding this one.
    pub devices: usize,
}

impl Default for Status {
    fn default() -> Self {
        Self {
            state: State::Unconfigured,
            detail: None,
            version: None,
            folder: None,
            connected: 0,
            devices: 0,
        }
    }
}

/// The synced folder the store lives in.
#[derive(Debug, Clone)]
pub struct Folder {
    /// Syncthing's folder id.
    pub id: String,
    /// Its label, or the id when it has none.
    pub label: String,
    /// Whether syncing is paused — the one setting that silently stops
    /// everything while looking fine.
    pub paused: bool,
    /// The versioning type, empty when off. **This is the backup**: the design
    /// leans on Syncthing versioning instead of shipping its own undo history.
    pub versioning: String,
    /// `idle`, `scanning`, `syncing`, … as the daemon reports it.
    pub folder_state: Option<String>,
}

/// Where and how to reach the daemon.
#[derive(Debug, Clone)]
pub struct Settings {
    /// Base URL, normalized to include a scheme.
    pub base_url: String,
    /// The REST API key, if configured.
    pub api_key: Option<String>,
    /// Whether to verify TLS.
    pub verify_tls: bool,
}

impl Settings {
    /// Resolve from the per-device config, or `None` when it says nothing.
    ///
    /// An API key with no address means the default bind, because that is what
    /// it means in practice — but an address with no key gets us nothing, since
    /// every endpoint worth reading needs one.
    #[must_use]
    pub fn from_config(config: &crate::config::Syncthing) -> Option<Self> {
        let api_key = config.apikey.clone()?;
        let base_url = normalize(config.address.as_deref().unwrap_or(DEFAULT_ADDRESS));
        Some(Self { base_url, api_key: Some(api_key), verify_tls: config.verify_tls })
    }
}

/// Add a scheme if the config gave a bare `host:port`, and drop a trailing slash.
fn normalize(address: &str) -> String {
    let with_scheme = if address.contains("://") {
        address.to_string()
    } else {
        // https, not http: Termux's Syncthing 307-redirects plain http, and a
        // redirect would take the request out of the loopback TLS exception.
        format!("https://{address}")
    };
    with_scheme.trim_end_matches('/').to_string()
}

/// The host part of a base URL, `[::1]` brackets removed.
#[must_use]
pub fn host_of(base_url: &str) -> &str {
    let rest = base_url.split_once("://").map_or(base_url, |(_, rest)| rest);
    let rest = rest.split('/').next().unwrap_or(rest);
    // `[::1]:8384` — an IPv6 literal's own colons are inside the brackets, so
    // the brackets have to come off before the port is split away.
    if let Some(inside) = rest.strip_prefix('[') {
        if let Some(end) = inside.find(']') {
            return &inside[..end];
        }
    }
    rest.split(':').next().unwrap_or(rest)
}

/// Whether a host is this machine talking to itself.
#[must_use]
pub fn is_loopback(host: &str) -> bool {
    host == "localhost" || host == "::1" || host.starts_with("127.")
}

/// Query the daemon. Never fails — every failure is a [`State`].
#[must_use]
pub fn query(settings: &Settings, root: &Path) -> Status {
    let host = host_of(&settings.base_url);
    if !settings.verify_tls && !is_loopback(host) {
        return Status {
            state: State::Refused,
            detail: Some(format!(
                "refusing to skip TLS verification for {host}: that exception is \
                 only safe on loopback, where the certificate is self-signed and \
                 the API key is the real authenticator"
            )),
            ..Status::default()
        };
    }

    let agent = agent(settings.verify_tls);
    // `/rest/system/version` first: it is the cheapest keyed call, so it is what
    // separates "cannot reach" from "reached, key rejected".
    let version = match get(&agent, settings, "/rest/system/version") {
        Ok(doc) => doc,
        Err(Failure::Unauthorized(detail)) => {
            return Status { state: State::Unauthorized, detail: Some(detail), ..Status::default() }
        }
        Err(Failure::Unreachable(detail)) => {
            return Status { state: State::Unreachable, detail: Some(detail), ..Status::default() }
        }
    };

    let folders = get(&agent, settings, "/rest/config/folders").ok();
    let folder = folders.as_ref().and_then(|doc| folder_containing(doc, root));
    let folder = folder.map(|mut folder| {
        if let Ok(doc) = get(&agent, settings, &format!("/rest/db/status?folder={}", folder.id)) {
            folder.folder_state = string(&doc, "state");
        }
        folder
    });
    let (connected, devices) = device_counts(&agent, settings);

    let state = match folder.as_ref().and_then(|f| f.folder_state.as_deref()) {
        Some("idle") | None => State::Idle,
        Some(_) => State::Busy,
    };
    Status { state, detail: None, version: string(&version, "version"), folder, connected, devices }
}

/// Why a request did not produce a document.
enum Failure {
    /// Reached, and the key was rejected.
    Unauthorized(String),
    /// Not reached, or the answer was not usable.
    Unreachable(String),
}

fn agent(verify_tls: bool) -> ureq::Agent {
    let mut config = ureq::Agent::config_builder().timeout_global(Some(TIMEOUT));
    if !verify_tls {
        // Reached only after the loopback check above. Scoped to *this* agent,
        // which exists for the length of one status query — nothing else in the
        // process makes an unverified request.
        config =
            config.tls_config(ureq::tls::TlsConfig::builder().disable_verification(true).build());
    }
    config.build().into()
}

fn get(agent: &ureq::Agent, settings: &Settings, path: &str) -> Result<Value, Failure> {
    let url = format!("{}{path}", settings.base_url);
    let mut request = agent.get(&url);
    if let Some(key) = &settings.api_key {
        request = request.header("X-API-Key", key);
    }
    match request.call() {
        Ok(response) => response
            .into_body()
            .read_json::<Value>()
            .map_err(|error| Failure::Unreachable(format!("{path}: {error}"))),
        Err(ureq::Error::StatusCode(code @ (401 | 403))) => {
            Err(Failure::Unauthorized(format!("{path}: HTTP {code} — check the API key")))
        }
        Err(error) => Err(Failure::Unreachable(format!("{url}: {error}"))),
    }
}

/// The folder whose path is an ancestor of the store root.
///
/// Compared as text after both sides are canonicalized, because Termux's view of
/// shared storage (`~/storage/shared/…`) is a symlink to the path Syncthing
/// reports (`/storage/emulated/0/…`).
#[must_use]
pub fn folder_containing(doc: &Value, root: &Path) -> Option<Folder> {
    let root = canonical(root);
    doc.as_array()?.iter().find_map(|entry| {
        let path = canonical(Path::new(entry.get("path")?.as_str()?));
        (root == path || root.starts_with(&format!("{path}/"))).then(|| Folder {
            id: string(entry, "id").unwrap_or_default(),
            label: string(entry, "label")
                .filter(|label| !label.is_empty())
                .or_else(|| string(entry, "id"))
                .unwrap_or_default(),
            paused: entry.get("paused").and_then(Value::as_bool).unwrap_or(false),
            versioning: entry
                .get("versioning")
                .and_then(|v| v.get("type"))
                .and_then(Value::as_str)
                .unwrap_or("")
                .to_string(),
            folder_state: None,
        })
    })
}

/// A path as comparable text: symlinks resolved when possible, separators
/// normalized, no trailing slash.
fn canonical(path: &Path) -> String {
    let resolved = path.canonicalize().unwrap_or_else(|_| path.to_path_buf());
    resolved.to_string_lossy().replace('\\', "/").trim_end_matches('/').to_string()
}

/// Connected and configured device counts, this device excluded.
fn device_counts(agent: &ureq::Agent, settings: &Settings) -> (usize, usize) {
    let Ok(status) = get(agent, settings, "/rest/system/status") else { return (0, 0) };
    let me = string(&status, "myID").unwrap_or_default();
    let Ok(devices) = get(agent, settings, "/rest/config/devices") else { return (0, 0) };
    let others: Vec<String> = devices
        .as_array()
        .map(|list| {
            list.iter().filter_map(|d| string(d, "deviceID")).filter(|id| id != &me).collect()
        })
        .unwrap_or_default();
    let connected = get(agent, settings, "/rest/system/connections")
        .ok()
        .and_then(|doc| {
            let map = doc.get("connections")?.as_object()?.clone();
            Some(
                others
                    .iter()
                    .filter(|id| {
                        map.get(*id)
                            .and_then(|c| c.get("connected"))
                            .and_then(Value::as_bool)
                            .unwrap_or(false)
                    })
                    .count(),
            )
        })
        .unwrap_or(0);
    (connected, others.len())
}

fn string(doc: &Value, field: &str) -> Option<String> {
    doc.get(field).and_then(Value::as_str).map(str::to_string)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpListener;

    /// A one-request-at-a-time HTTP server that answers from a fixed table.
    ///
    /// Real sockets rather than a mocked transport: the things worth testing here
    /// are the header, the status codes and the URLs, and a mock would be a test
    /// of the mock.
    fn serve(
        routes: Vec<(&'static str, u16, &'static str)>,
    ) -> (String, std::thread::JoinHandle<Vec<String>>) {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
        let port = listener.local_addr().expect("addr").port();
        let handle = std::thread::spawn(move || {
            let mut seen = Vec::new();
            for _ in 0..routes.len() {
                let Ok((stream, _)) = listener.accept() else { break };
                let mut reader = BufReader::new(&stream);
                let mut request_line = String::new();
                if reader.read_line(&mut request_line).is_err() {
                    break;
                }
                let mut key = None;
                loop {
                    let mut header = String::new();
                    if reader.read_line(&mut header).unwrap_or(0) == 0 || header.trim().is_empty() {
                        break;
                    }
                    // Header names are case-insensitive and clients pick their
                    // own casing — matching the literal spelling would test the
                    // client's style rather than that the key was sent.
                    let lower = header.to_ascii_lowercase();
                    if let Some(value) = lower.strip_prefix("x-api-key:") {
                        key = Some(value.trim().to_string());
                    }
                }
                let path = request_line.split_whitespace().nth(1).unwrap_or("").to_string();
                seen.push(format!("{path} key={}", key.unwrap_or_default()));
                let (_, code, body) = routes
                    .iter()
                    .find(|(route, _, _)| path.starts_with(route))
                    .copied()
                    .unwrap_or(("", 404, "{}"));
                let mut stream = &stream;
                let _ = write!(
                    stream,
                    "HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = stream.flush();
            }
            seen
        });
        (format!("http://127.0.0.1:{port}"), handle)
    }

    fn settings(base_url: String) -> Settings {
        Settings { base_url, api_key: Some("k".into()), verify_tls: true }
    }

    /// **A daemon that is not running is a state, not an error** — `ds status`
    /// still prints everything else it knows.
    #[test]
    fn an_absent_daemon_is_a_state() {
        // Port 1 is reserved and never listening.
        let status = query(&settings("http://127.0.0.1:1".into()), Path::new("/tmp"));
        assert_eq!(status.state, State::Unreachable);
        assert!(status.detail.is_some());
    }

    /// A rejected key is told apart from an absent daemon, because the fix is
    /// completely different.
    #[test]
    fn a_rejected_key_is_told_apart_from_an_absent_daemon() {
        let (url, handle) = serve(vec![("/rest/system/version", 403, "{}")]);
        let status = query(&settings(url), Path::new("/tmp"));
        assert_eq!(status.state, State::Unauthorized);
        assert!(status.detail.unwrap().contains("API key"));
        let seen = handle.join().expect("server");
        assert_eq!(seen, ["/rest/system/version key=k"], "the key is sent as a header");
    }

    /// **Verification is never dropped off loopback.** The request is refused
    /// before a socket is opened, and the report says why.
    #[test]
    fn skipping_verification_off_loopback_is_refused() {
        let settings = Settings {
            base_url: "https://sync.example.com:8384".into(),
            api_key: Some("k".into()),
            verify_tls: false,
        };
        let status = query(&settings, Path::new("/tmp"));
        assert_eq!(status.state, State::Refused);
        assert!(status.detail.unwrap().contains("only safe on loopback"));
    }

    /// Loopback is recognized in all the forms a config file writes it.
    #[test]
    fn loopback_is_recognized_in_every_form() {
        for url in ["https://127.0.0.1:8384", "http://localhost:8384", "https://[::1]:8384"] {
            assert!(is_loopback(host_of(url)), "{url}");
        }
        for url in ["https://sync.example.com", "https://192.168.1.5:8384"] {
            assert!(!is_loopback(host_of(url)), "{url}");
        }
    }

    /// A bare `host:port` gets **https**, because Termux's daemon redirects
    /// plain http — and a redirect would leave the loopback TLS exception.
    #[test]
    fn a_bare_address_becomes_https() {
        assert_eq!(normalize("127.0.0.1:8384"), "https://127.0.0.1:8384");
        assert_eq!(normalize("http://127.0.0.1:8384/"), "http://127.0.0.1:8384");
    }

    /// The folder that matters is the one containing the store — not the first
    /// one, and not all of them.
    #[test]
    fn the_store_folder_is_the_one_containing_the_root() {
        let doc = serde_json::json!([
            {"id": "photos", "label": "Photos", "path": "/home/u/Photos", "paused": false},
            {"id": "docs", "label": "Documents", "path": "/home/u/Sync",
             "paused": true, "versioning": {"type": "staggered"}},
        ]);
        let folder = folder_containing(&doc, Path::new("/home/u/Sync/Marine")).expect("found");
        assert_eq!(folder.id, "docs");
        assert!(folder.paused, "a paused folder is exactly what status must reveal");
        assert_eq!(folder.versioning, "staggered");

        assert!(
            folder_containing(&doc, Path::new("/elsewhere")).is_none(),
            "no folder covers it, and saying so beats guessing"
        );
    }

    /// A folder path that equals the root counts, and one that merely shares a
    /// prefix does not — `/home/u/Sync2` is not inside `/home/u/Sync`.
    #[test]
    fn folder_matching_is_by_path_component_not_by_prefix() {
        let doc = serde_json::json!([{"id": "docs", "path": "/home/u/Sync", "paused": false}]);
        assert!(folder_containing(&doc, Path::new("/home/u/Sync")).is_some());
        assert!(folder_containing(&doc, Path::new("/home/u/Sync2")).is_none());
    }

    /// The happy path: a version, the store's folder, its state, and the device
    /// counts — with this device excluded from them.
    #[test]
    fn a_healthy_daemon_reports_the_folder_and_the_peers() {
        let (url, handle) = serve(vec![
            ("/rest/system/version", 200, r#"{"version":"v1.27.0"}"#),
            ("/rest/config/folders", 200, r#"[{"id":"docs","label":"Docs","path":"/tmp"}]"#),
            ("/rest/db/status", 200, r#"{"state":"syncing"}"#),
            ("/rest/system/status", 200, r#"{"myID":"SELF"}"#),
            ("/rest/config/devices", 200, r#"[{"deviceID":"SELF"},{"deviceID":"PHONE"}]"#),
            ("/rest/system/connections", 200, r#"{"connections":{"PHONE":{"connected":true}}}"#),
        ]);
        let status = query(&settings(url), Path::new("/tmp"));
        handle.join().expect("server");

        assert_eq!(status.state, State::Busy, "a syncing folder is not idle");
        assert_eq!(status.version.as_deref(), Some("v1.27.0"));
        assert_eq!(status.folder.as_ref().unwrap().label, "Docs");
        assert_eq!(status.folder.unwrap().folder_state.as_deref(), Some("syncing"));
        assert_eq!((status.connected, status.devices), (1, 1), "self is not a peer");
    }
}
