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

//! WSL — the Linux build of `ds`, running on a Windows machine.
//!
//! Under the Windows Subsystem for Linux, `ds` is an ordinary Linux binary, and
//! almost everything it does is ordinary Linux: the journal reads, appends,
//! locks and renames work on the Windows drive's mount (`/mnt/c`) as they do
//! anywhere else, and the CI `wsl` leg holds that to account on a real drvfs.
//! What is **not** ordinary is everything that crosses the boundary:
//!
//! - **Opening a file.** There is no Linux desktop to hand it to; the default
//!   application lives on the Windows side, so the path has to become a Windows
//!   path and go to a Windows opener ([`crate::open`]).
//! - **The Syncthing check.** Syncthing usually runs on Windows and reports
//!   folder paths as `C:\Users\…\Sync`, which the store root `/mnt/c/Users/…/Sync`
//!   never matches as text ([`crate::syncthing`]). Under WSL's default NAT
//!   networking, `127.0.0.1` is not even the same machine's loopback.
//! - **Two `ds` on one PC.** `ds.exe` on Windows and `ds` in WSL keep separate
//!   configs and separate lock directories, and a lock taken in one is invisible
//!   to the other. The same device name on both sides would put two processes
//!   on one writer file — the thing the lock exists to prevent — so WSL, the
//!   only side that can see both configs, refuses it ([`windows_twin`]).
//!
//! The translation is **pure string work**, not `wslpath`: it has to be tested
//! on every CI platform, and a subprocess can only be tested where it exists.
//! It honours the one setting that moves the drives, `[automount] root` in
//! `/etc/wsl.conf`; drives mounted by hand somewhere else are not modelled.

use std::path::{Path, PathBuf};
use std::sync::OnceLock;

/// Where WSL mounts the Windows drives unless `/etc/wsl.conf` says otherwise.
pub const DEFAULT_MOUNT_ROOT: &str = "/mnt/";

/// What `ds` needs to know about the WSL it is running under.
///
/// **WSL 2 only.** WSL 1 is not supported — it is not tested, and the one
/// thing known to differ (it shares Windows' loopback, so `ds status`'s NAT
/// explanation is wrong there) is not special-cased.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Wsl {
    /// The directory the drive letters are mounted under, with a trailing `/`
    /// — `/mnt/` by default, so `C:` is `/mnt/c`.
    pub mount_root: String,
    /// This distribution's name (`Ubuntu`), which is how Windows reaches the
    /// Linux side of the filesystem: `\\wsl$\Ubuntu\home\…`.
    pub distro: Option<String>,
}

/// Whether a kernel release string is a WSL kernel.
///
/// Every WSL kernel says so in its release —
/// `5.15.153.1-microsoft-standard-WSL2` — and nothing else does.
#[must_use]
pub fn is_wsl_kernel(osrelease: &str) -> bool {
    osrelease.to_ascii_lowercase().contains("microsoft")
}

/// Whether this process is on WSL, given the kernel's release and the two
/// things only a real WSL distribution has.
///
/// The kernel alone is not enough: **a Docker Desktop container runs on the
/// same WSL kernel** and has no Windows on the other end of anything. So the
/// kernel must be backed by either WSL's own environment (`WSL_DISTRO_NAME`,
/// `WSL_INTEROP`) or the interop registration that lets Linux run a `.exe` —
/// the latter because a cron job or a systemd unit has no WSL environment and
/// is still very much on WSL.
#[must_use]
pub fn detect(osrelease: &str, wsl_env: bool, interop: bool) -> bool {
    is_wsl_kernel(osrelease) && (wsl_env || interop)
}

impl Wsl {
    /// The WSL this process runs under, or `None` anywhere else — including
    /// native Windows, macOS, and Termux.
    ///
    /// rust: `OnceLock` is a lazily-initialized global — the files are read at
    /// most once per process, and never at all on a platform where the
    /// `cfg!` is false. Nothing on the startup path asks, so the phone never
    /// pays for it.
    #[must_use]
    pub fn current() -> Option<&'static Wsl> {
        static CURRENT: OnceLock<Option<Wsl>> = OnceLock::new();
        CURRENT
            .get_or_init(|| {
                if !cfg!(target_os = "linux") {
                    return None;
                }
                let release = std::fs::read_to_string("/proc/sys/kernel/osrelease").ok()?;
                let env = ["WSL_DISTRO_NAME", "WSL_INTEROP"]
                    .iter()
                    .any(|name| std::env::var_os(name).is_some());
                let interop = ["WSLInterop", "WSLInterop-late"]
                    .iter()
                    .any(|name| Path::new("/proc/sys/fs/binfmt_misc").join(name).exists());
                if !detect(&release, env, interop) {
                    return None;
                }
                let conf = std::fs::read_to_string("/etc/wsl.conf").unwrap_or_default();
                Some(Wsl {
                    mount_root: mount_root_from(&conf),
                    distro: std::env::var("WSL_DISTRO_NAME").ok().filter(|d| !d.is_empty()),
                })
            })
            .as_ref()
    }

    /// A Windows path as this Linux side sees it, or `None` when the text is
    /// not a Windows path at all (a Linux path is left for the caller to use
    /// as it is).
    ///
    /// `C:\Users\g\Sync` and `C:/Users/g/Sync` → `/mnt/c/Users/g/Sync`;
    /// `\\wsl.localhost\Ubuntu\home\g` and `\\wsl$\Ubuntu\home\g` → `/home/g`
    /// when `Ubuntu` is this distribution. The `\\?\` prefix Windows APIs
    /// sometimes hand out is stripped first.
    #[must_use]
    pub fn to_linux(&self, windows: &str) -> Option<PathBuf> {
        let windows = windows.strip_prefix(r"\\?\").unwrap_or(windows);
        if let Some((letter, rest)) = split_drive(windows) {
            let mut out = format!("{}{}", self.mount_root, letter.to_ascii_lowercase());
            for part in rest.split(['\\', '/']).filter(|part| !part.is_empty()) {
                out.push('/');
                out.push_str(part);
            }
            return Some(PathBuf::from(out));
        }
        let unc = windows.strip_prefix(r"\\").or_else(|| windows.strip_prefix("//"))?;
        let mut parts = unc.split(['\\', '/']).filter(|part| !part.is_empty());
        let host = parts.next()?;
        if !host.eq_ignore_ascii_case("wsl.localhost") && !host.eq_ignore_ascii_case("wsl$") {
            return None;
        }
        let distro = parts.next()?;
        if !self.distro.as_deref().is_some_and(|ours| ours.eq_ignore_ascii_case(distro)) {
            return None;
        }
        let rest: Vec<&str> = parts.collect();
        Some(PathBuf::from(format!("/{}", rest.join("/"))))
    }

    /// A path on this Linux side as Windows names it, or `None` when it cannot
    /// be named — a relative path, text that is not UTF-8, or a path inside the
    /// distribution when the distribution's name is unknown.
    ///
    /// `/mnt/c/Users/g/a.pdf` → `C:\Users\g\a.pdf`; `/home/g/a.pdf` →
    /// `\\wsl$\Ubuntu\home\g\a.pdf` — `wsl$` rather than `wsl.localhost`
    /// because every Windows that has WSL understands it, and only newer ones
    /// understand the other.
    #[must_use]
    pub fn to_windows(&self, path: &Path) -> Option<String> {
        let text = path.to_str()?;
        if !text.starts_with('/') {
            return None;
        }
        if let Some((letter, rest)) = self.split_mount(text) {
            let rest = rest.trim_matches('/').replace('/', "\\");
            return Some(format!("{}:\\{rest}", letter.to_ascii_uppercase()));
        }
        let distro = self.distro.as_deref()?;
        Some(format!(r"\\wsl$\{distro}{}", text.trim_end_matches('/').replace('/', "\\")))
    }

    /// Whether a path is on a Windows drive — where names are case-insensitive,
    /// as they are to Windows itself.
    #[must_use]
    pub fn on_windows_drive(&self, path: &Path) -> bool {
        path.to_str().is_some_and(|text| self.split_mount(text).is_some())
    }

    /// `/mnt/c/rest` → `('c', "/rest")`, or `None` for any path not on a drive
    /// mount. The letter must be a whole component: `/mnt/cdrom` is not `C:`.
    fn split_mount<'a>(&self, text: &'a str) -> Option<(char, &'a str)> {
        let rest = text.strip_prefix(self.mount_root.as_str())?;
        let mut chars = rest.chars();
        let letter = chars.next().filter(char::is_ascii_alphabetic)?;
        let after = chars.as_str();
        (after.is_empty() || after.starts_with('/')).then_some((letter, after))
    }
}

/// `C:\rest` or `C:/rest` or `C:` → `('C', "\rest")`.
fn split_drive(text: &str) -> Option<(char, &str)> {
    let mut chars = text.chars();
    let letter = chars.next().filter(char::is_ascii_alphabetic)?;
    let rest = chars.as_str().strip_prefix(':')?;
    (rest.is_empty() || rest.starts_with(['\\', '/'])).then_some((letter, rest))
}

/// The drive mount root from `/etc/wsl.conf`'s text, with a trailing `/`.
///
/// The file is INI, not TOML — values are unquoted as often as not — so this
/// is a line scan for `root` under `[automount]` rather than a parse. Anything
/// it cannot read means the default, which is what WSL itself does.
#[must_use]
pub fn mount_root_from(conf: &str) -> String {
    let mut in_automount = false;
    for line in conf.lines() {
        let line = line.split(['#', ';']).next().unwrap_or("").trim();
        if let Some(section) = line.strip_prefix('[').and_then(|s| s.strip_suffix(']')) {
            in_automount = section.trim().eq_ignore_ascii_case("automount");
            continue;
        }
        if !in_automount {
            continue;
        }
        let Some((key, value)) = line.split_once('=') else { continue };
        if !key.trim().eq_ignore_ascii_case("root") {
            continue;
        }
        let value = value.trim().trim_matches(['"', '\'']);
        if value.starts_with('/') {
            return if value.ends_with('/') { value.to_string() } else { format!("{value}/") };
        }
    }
    DEFAULT_MOUNT_ROOT.to_string()
}

/// A root typed in the form Windows writes it, made usable here.
///
/// `ds init` on WSL is asked "where is the Syncthing folder?" by someone who
/// knows the answer as Explorer shows it — `C:\Users\g\Sync` — and a config
/// holding that text would be a relative path with backslashes in it to Linux.
/// Anything that is not a Windows path comes back unchanged.
#[must_use]
pub fn native_root(wsl: Option<&Wsl>, typed: PathBuf) -> PathBuf {
    let translated = wsl.zip(typed.to_str()).and_then(|(wsl, text)| wsl.to_linux(text));
    translated.unwrap_or(typed)
}

/// Whether two paths name the same place, with Windows' case rules on a
/// Windows drive and Linux's everywhere else.
#[must_use]
pub fn same_place(wsl: &Wsl, a: &Path, b: &Path) -> bool {
    if wsl.on_windows_drive(a) && wsl.on_windows_drive(b) {
        let fold = |p: &Path| p.to_string_lossy().trim_end_matches('/').to_ascii_lowercase();
        return fold(a) == fold(b);
    }
    a == b
}

/// A `ds.exe` on the Windows side of this machine that calls itself `device`
/// and keeps the same store — the one configuration WSL must refuse.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Twin {
    /// The Windows-side config that claims the name, as a WSL path.
    pub config: PathBuf,
    /// The writer id the two would share.
    pub writer: String,
}

/// Look for a Windows-side `ds` that shares this device's name and store.
///
/// The Windows config lives at `%LOCALAPPDATA%\dossier\config.toml`, which from
/// here is `<mount>/<drive>/Users/<user>/AppData/Local/dossier/config.toml`.
/// Every drive and every profile is looked at rather than "the current user's":
/// the WSL user name need not be the Windows one, finding the Windows one costs
/// an interop round trip, profiles are not always on `C:`, and a second account
/// on one PC sharing a store *and* a device name is exactly as broken. A profile
/// that cannot be read is not a match.
///
/// A Windows config with no root is treated as the same store: it can only be
/// writing through `--root`, which is invisible from here, and a false refusal
/// costs a rename where a missed one costs the journal.
///
/// Cost: one directory listing and a stat per profile on the Windows drive —
/// which is why it runs at `ds init` and at the writer's first open, never at
/// launch.
#[must_use]
pub fn windows_twin(wsl: &Wsl, device: &str, root: Option<&Path>) -> Option<Twin> {
    // `--root .` is a relative path, and relative text never matches a drive
    // path — which would let the one collision this exists for slip through.
    // Only a relative one is touched: `absolute` also normalizes separators on
    // Windows, which would move an absolute root off its own mount prefix.
    let root = root.map(|root| {
        if root.is_relative() {
            std::path::absolute(root).unwrap_or_else(|_| root.to_path_buf())
        } else {
            root.to_path_buf()
        }
    });
    let root = root.as_deref();
    let drives = std::fs::read_dir(&wsl.mount_root).ok()?;
    let mut profiles = drives
        .filter_map(Result::ok)
        .filter(|drive| {
            let name = drive.file_name();
            name.len() == 1
                && name.to_str().is_some_and(|n| n.chars().all(|c| c.is_ascii_alphabetic()))
        })
        .filter_map(|drive| std::fs::read_dir(drive.path().join("Users")).ok())
        .flatten()
        .filter_map(Result::ok);
    profiles.find_map(|profile| {
        let config =
            profile.path().join("AppData").join("Local").join("dossier").join("config.toml");
        let theirs = crate::config::Config::read(&config).ok()?;
        twin_of(wsl, device, root, &theirs)
            .then(|| Twin { config, writer: crate::init::writer_id(device) })
    })
}

/// The rule [`windows_twin`] applies to one Windows config, split out so it can
/// be tested without a Windows drive.
#[must_use]
pub fn twin_of(
    wsl: &Wsl,
    device: &str,
    root: Option<&Path>,
    theirs: &crate::config::Config,
) -> bool {
    if theirs.device.as_deref() != Some(device) {
        return false;
    }
    let their_root = theirs
        .syncthing_root
        .as_ref()
        .map(|r| r.to_str().and_then(|text| wsl.to_linux(text)).unwrap_or_else(|| r.clone()));
    match (root, their_root) {
        (Some(ours), Some(theirs)) => same_place(wsl, ours, &theirs),
        _ => true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;

    fn wsl() -> Wsl {
        Wsl { mount_root: DEFAULT_MOUNT_ROOT.into(), distro: Some("Ubuntu".into()) }
    }

    /// A WSL kernel is recognised, and an ordinary Linux kernel — or Termux's
    /// Android one — is not mistaken for one.
    #[test]
    fn a_wsl_kernel_is_recognised_and_nothing_else_is() {
        assert!(is_wsl_kernel("5.15.153.1-microsoft-standard-WSL2\n"));
        assert!(!is_wsl_kernel("6.8.0-45-generic"));
        assert!(!is_wsl_kernel("5.10.198-android12-9-g5a1b"));
    }

    /// **A Docker Desktop container is not WSL**, though it runs on WSL's
    /// kernel: without WSL's environment or its interop there is no Windows to
    /// hand anything to. Either one is enough, because cron has only the second.
    #[test]
    fn the_kernel_alone_is_not_wsl() {
        let release = "5.15.153.1-microsoft-standard-WSL2";
        assert!(!detect(release, false, false), "a container on the WSL kernel");
        assert!(detect(release, true, false), "interop off, env set");
        assert!(detect(release, false, true), "cron: interop, no env");
        assert!(!detect("6.8.0-45-generic", true, true), "stray variables on real Linux");
    }

    /// A drive path in either slash style lands on its drvfs mount, with the
    /// letter lowercased the way WSL mounts it.
    #[test]
    fn a_drive_path_lands_on_its_mount() {
        let wsl = wsl();
        for windows in
            [r"C:\Users\g\Sync", "C:/Users/g/Sync", r"c:\Users\g\Sync\", r"\\?\C:\Users\g\Sync"]
        {
            assert_eq!(
                wsl.to_linux(windows),
                Some(PathBuf::from("/mnt/c/Users/g/Sync")),
                "{windows}"
            );
        }
        assert_eq!(wsl.to_linux(r"D:\"), Some(PathBuf::from("/mnt/d")));
        assert_eq!(wsl.to_linux("E:"), Some(PathBuf::from("/mnt/e")));
    }

    /// Windows' view of *this* distribution comes home; another distribution's,
    /// or a network share, has no Linux name here and says so.
    #[test]
    fn a_unc_path_comes_home_only_from_this_distribution() {
        let wsl = wsl();
        assert_eq!(wsl.to_linux(r"\\wsl.localhost\Ubuntu\home\g"), Some(PathBuf::from("/home/g")));
        assert_eq!(wsl.to_linux(r"\\wsl$\ubuntu\home\g"), Some(PathBuf::from("/home/g")));
        assert_eq!(wsl.to_linux(r"\\wsl.localhost\Debian\home\g"), None);
        assert_eq!(wsl.to_linux(r"\\nas\share\Sync"), None);
    }

    /// Linux text is not a Windows path, so it is left for the caller as it is.
    #[test]
    fn a_linux_path_is_not_translated() {
        assert_eq!(wsl().to_linux("/home/g/Sync"), None);
        assert_eq!(wsl().to_linux("Sync"), None);
    }

    /// A file on a drive mount opens as its drive path; one inside the
    /// distribution opens through `\\wsl$`.
    #[test]
    fn a_linux_path_is_named_for_windows() {
        let wsl = wsl();
        let windows = |p: &str| wsl.to_windows(Path::new(p));
        assert_eq!(
            windows("/mnt/c/Users/g/Sync/a b.pdf").as_deref(),
            Some(r"C:\Users\g\Sync\a b.pdf")
        );
        assert_eq!(windows("/mnt/d").as_deref(), Some(r"D:\"));
        assert_eq!(windows("/home/g/a.pdf").as_deref(), Some(r"\\wsl$\Ubuntu\home\g\a.pdf"));
        assert_eq!(windows("relative/a.pdf"), None);
    }

    /// `/mnt/cdrom` is a directory under the mount root, not drive `C:` — the
    /// letter has to be a whole path component.
    #[test]
    fn a_drive_letter_is_a_whole_component() {
        let wsl = wsl();
        assert!(!wsl.on_windows_drive(Path::new("/mnt/cdrom/x")));
        assert_eq!(
            wsl.to_windows(Path::new("/mnt/cdrom/x")).as_deref(),
            Some(r"\\wsl$\Ubuntu\mnt\cdrom\x")
        );
    }

    /// With no distribution name, a path inside the distribution cannot be
    /// named for Windows — and saying so beats guessing one.
    #[test]
    fn an_unknown_distribution_cannot_name_its_own_files() {
        let wsl = Wsl { distro: None, ..wsl() };
        assert_eq!(wsl.to_windows(Path::new("/home/g/a.pdf")), None);
        assert_eq!(wsl.to_windows(Path::new("/mnt/c/a.pdf")).as_deref(), Some(r"C:\a.pdf"));
    }

    /// A moved mount root is honoured in both directions.
    #[test]
    fn a_custom_mount_root_is_honoured() {
        let conf = "[boot]\nsystemd=true\n\n[automount]\nenabled = true\nroot = /win   # moved\n";
        let wsl = Wsl { mount_root: mount_root_from(conf), distro: None };
        assert_eq!(wsl.mount_root, "/win/");
        assert_eq!(wsl.to_linux(r"C:\x"), Some(PathBuf::from("/win/c/x")));
        assert_eq!(wsl.to_windows(Path::new("/win/c/x")).as_deref(), Some(r"C:\x"));
        assert_eq!(wsl.to_windows(Path::new("/mnt/c/x")), None, "/mnt is not a drive any more");
    }

    /// `root` only counts under `[automount]`, quoted or not; anything else is
    /// the default.
    #[test]
    fn the_mount_root_is_read_only_from_automount() {
        assert_eq!(mount_root_from(""), "/mnt/");
        assert_eq!(mount_root_from("[network]\nroot = /nope\n"), "/mnt/");
        assert_eq!(mount_root_from("[AutoMount]\nroot=\"/drives/\"\n"), "/drives/");
        assert_eq!(mount_root_from("[automount]\nroot = relative\n"), "/mnt/");
    }

    /// A root typed the way Explorer shows it is stored the way Linux needs it;
    /// off WSL, or for a Linux path, the typing is kept.
    #[test]
    fn a_typed_windows_root_becomes_native() {
        let wsl = wsl();
        let typed = PathBuf::from(r"C:\Users\g\Sync");
        assert_eq!(native_root(Some(&wsl), typed.clone()), PathBuf::from("/mnt/c/Users/g/Sync"));
        assert_eq!(native_root(None, typed.clone()), typed);
        assert_eq!(native_root(Some(&wsl), "/home/g/Sync".into()), PathBuf::from("/home/g/Sync"));
    }

    /// On a drive, case does not distinguish places — Windows would not let
    /// `Sync` and `sync` both exist. Inside the distribution it does.
    #[test]
    fn case_matters_only_off_the_windows_drive() {
        let wsl = wsl();
        let same = |a: &str, b: &str| same_place(&wsl, Path::new(a), Path::new(b));
        assert!(same("/mnt/c/Users/G/Sync", "/mnt/c/users/g/sync/"));
        assert!(!same("/home/g/Sync", "/home/g/sync"));
        assert!(!same("/mnt/c/Users/g/Sync", "/mnt/c/Users/g/Sync2"));
    }

    /// **On a real WSL, this module agrees with WSL's own `wslpath`.**
    ///
    /// Only the CI `wsl` leg sets `DS_EXPECT_WSL`, with the checkout on a
    /// Windows drive: there, detection must find WSL, and translating the
    /// working directory each way must give
    /// what `wslpath` gives. Everywhere else this is a no-op, which is honest —
    /// the pure tests above are what run there.
    #[test]
    fn a_real_wsl_agrees_with_wslpath() {
        if std::env::var_os("DS_EXPECT_WSL").is_none() {
            return;
        }
        let wsl = Wsl::current().expect("DS_EXPECT_WSL is set, so this is WSL");

        let here = std::env::current_dir().expect("cwd");
        assert!(wsl.on_windows_drive(&here), "the CI leg runs on drvfs: {}", here.display());
        let wslpath = |flag: &str, arg: &str| {
            let out =
                std::process::Command::new("wslpath").args([flag, arg]).output().expect("wslpath");
            assert!(out.status.success(), "wslpath {flag} {arg}: {out:?}");
            String::from_utf8(out.stdout).expect("utf-8").trim().to_string()
        };
        let windows = wsl.to_windows(&here).expect("a drive path has a Windows name");
        assert_eq!(windows, wslpath("-w", here.to_str().expect("utf-8")));
        assert_eq!(wsl.to_linux(&windows), Some(PathBuf::from(wslpath("-u", &windows))));
    }

    fn windows_config(device: &str, root: Option<&str>) -> Config {
        Config {
            device: Some(device.into()),
            syncthing_root: root.map(PathBuf::from),
            ..Config::default()
        }
    }

    /// **The twin rule.** Same name and same store is the collision; a
    /// different name, or the same name on a different store, is not — two
    /// journals can each have a `desk-core` without ever meeting.
    #[test]
    fn a_twin_is_the_same_name_on_the_same_store() {
        let wsl = wsl();
        let ours = Path::new("/mnt/c/Users/g/Sync");
        let twin = |device, root| twin_of(&wsl, "desk", Some(ours), &windows_config(device, root));
        assert!(twin("desk", Some(r"C:\Users\G\Sync")), "same store, Windows spelling and case");
        assert!(!twin("desk-win", Some(r"C:\Users\g\Sync")), "different name");
        assert!(!twin("desk", Some(r"D:\Other")), "different store");
    }

    /// When either side's store is unknown, the names alone decide — a false
    /// refusal costs a rename, a missed one costs the journal.
    #[test]
    fn an_unknown_store_counts_as_the_same_one() {
        let wsl = wsl();
        assert!(twin_of(&wsl, "desk", Some(Path::new("/mnt/c/S")), &windows_config("desk", None)));
        assert!(twin_of(&wsl, "desk", None, &windows_config("desk", Some(r"C:\S"))));
    }

    /// The scan reads real files: a Windows profile tree under a fake mount
    /// root, with one config that collides and one that does not.
    #[test]
    fn the_scan_finds_a_twin_in_any_windows_profile() {
        let base = std::env::temp_dir().join(format!("ds-wsl-twin-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let profile = |user: &str, body: &str| {
            let dir = base.join("c/Users").join(user).join("AppData/Local/dossier");
            std::fs::create_dir_all(&dir).expect("mkdir");
            std::fs::write(dir.join("config.toml"), body).expect("write");
        };
        profile("Public", "device = \"laptop\"\n");
        profile("g", "device = \"desk\"\nsyncthing_root = 'C:\\Users\\g\\Sync'\n");
        let wsl = Wsl { mount_root: format!("{}/", base.display()), distro: None };
        let ours = PathBuf::from(format!("{}c/Users/g/Sync", wsl.mount_root));

        let twin = windows_twin(&wsl, "desk", Some(&ours)).expect("the collision is found");
        assert_eq!(twin.writer, "desk-core");
        assert!(twin.config.ends_with("c/Users/g/AppData/Local/dossier/config.toml"));
        assert_eq!(windows_twin(&wsl, "desk-wsl", Some(&ours)), None);
        let _ = std::fs::remove_dir_all(&base);
    }
}
