# Copyright © 2026-present gsfernandes81
#
# This file is part of "dossier".
#
# dossier is free software: you can redistribute it and/or modify it under the
# terms of the GNU Affero General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later version.
#
# dossier is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
# PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License along with
# dossier. If not, see <https://www.gnu.org/licenses/>.

"""Read-only client for a local Syncthing's REST API (Phase 15).

dossier *orchestrates, it does not own*: Syncthing is never bundled, spawned, or
reimplemented. One status query — :func:`query_status` — feeds three consumers:
``ds doctor``, the home-screen sync glyph, and the sync-aware scan service. The
query is synchronous, timeout-bounded, and cheap (a handful of loopback GETs), so
the Textual TUI calls it from a thread worker, never from the async path.

``urllib``/``ssl`` are imported lazily inside the transport (mirrors ``scan.py``)
so importing this module — which ``doctor`` does — never drags the HTTP stack into
a bare ``ds`` (guarded by ``test_cli_import_stays_lean``).

The GUI serves a self-signed cert; on loopback the API key in the ``X-API-Key``
header is the real authenticator, so an unverified TLS context is used there — and
refused off-box (:func:`_ssl_context`), where a silent downgrade is never right.
"""

from __future__ import annotations

import enum
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

from dossier.config import Config
from dossier.errors import ConfigError

if TYPE_CHECKING:  # ssl is imported lazily in the transport; this is types-only
    import ssl

DEFAULT_ADDRESS = "127.0.0.1:8384"  # Syncthing's default GUI/REST bind
_TIMEOUT = 2.0  # per-request seconds; loopback answers in milliseconds


class SyncState(enum.StrEnum):
    """Overall sync state — the value the glyph and service branch on."""

    UNCONFIGURED = "unconfigured"  # no settings from config or autodiscovery
    UNREACHABLE = "unreachable"  # connect/timeout failure
    UNAUTHORIZED = "unauthorized"  # reachable, but the API key was rejected
    IDLE = "idle"
    SCANNING = "scanning"
    SYNCING = "syncing"


@dataclass(frozen=True)
class SyncthingSettings:
    """Resolved connection settings (explicit config or autodiscovered)."""

    base_url: str  # normalized, e.g. "https://127.0.0.1:8384"
    api_key: str | None = None  # None → only /rest/noauth/health is usable
    verify_tls: bool = False  # False is safe only on loopback (self-signed GUI cert)
    source: str = "config"  # "config" | "config-xml" — for doctor messaging


@dataclass(frozen=True)
class FolderStatus:
    id: str
    label: str
    path: str  # as Syncthing reports it (device-absolute; may differ from our view)
    paused: bool
    versioning: str  # versioning.type; "" == none (the headline doctor check)
    shared_with: int  # other devices sharing this folder (excludes self)
    state: str | None = None  # /rest/db/status "state"; None = not fetched
    need_items: int | None = None  # needTotalItems, for a "syncing (N)" detail


@dataclass(frozen=True)
class DeviceStatus:
    device_id: str
    name: str
    connected: bool
    last_seen: datetime | None = None


@dataclass(frozen=True)
class SyncStatus:
    """The one status object every consumer reads."""

    state: SyncState
    error: str | None = None  # human detail for unreachable / unauthorized
    version: str | None = None
    my_id: str | None = None
    folders: tuple[FolderStatus, ...] = ()
    devices: tuple[DeviceStatus, ...] = ()
    store_folder: FolderStatus | None = None  # the folder whose path contains the store
    connected_devices: int = 0
    total_devices: int = 0  # excludes self


# -- internal transport markers (converted to states by query_status) --------
class _Unreachable(Exception):
    """The daemon did not answer (connect/timeout/transport error)."""


class _Unauthorized(Exception):
    """The daemon answered but rejected the API key (401/403)."""


def resolve_settings(
    config: Config, *, xml_paths: Sequence[Path] | None = None
) -> SyncthingSettings | None:
    """Resolve connection settings: explicit ``[syncthing]`` > ``config.xml`` > None.

    ``xml_paths`` is injectable for tests; when omitted the per-platform candidates
    are used and autodiscovery is skipped on Termux (the phone's ``config.xml`` is
    app-private, so the manual ``apikey`` is the only path there).
    """
    if config.syncthing_apikey or config.syncthing_address:
        return SyncthingSettings(
            base_url=_base_url(config.syncthing_address or DEFAULT_ADDRESS),
            api_key=config.syncthing_apikey or None,
            verify_tls=config.syncthing_verify_tls,
            source="config",
        )
    if xml_paths is None:
        from dossier.platform_open import is_termux

        if is_termux():
            return None
        candidates: Sequence[Path] = _default_xml_paths()
    else:
        candidates = list(xml_paths)
    address, api_key = discover_config_xml(candidates)
    if api_key:
        return SyncthingSettings(
            base_url=_base_url(address or DEFAULT_ADDRESS),
            api_key=api_key,
            verify_tls=False,
            source="config-xml",
        )
    return None


def discover_config_xml(paths: Sequence[Path]) -> tuple[str | None, str | None]:
    """``(gui_address, apikey)`` from the first readable Syncthing ``config.xml``."""
    import xml.etree.ElementTree as ET

    for path in paths:
        try:
            if not path.is_file():
                continue
            root = ET.parse(path).getroot()
        except (OSError, ET.ParseError):
            continue
        gui = root.find("gui")
        if gui is None:
            continue
        api_el = gui.find("apikey")
        addr_el = gui.find("address")
        api_key = api_el.text.strip() if api_el is not None and api_el.text else None
        address = addr_el.text.strip() if addr_el is not None and addr_el.text else None
        if gui.get("tls") == "true" and address and "://" not in address:
            address = "https://" + address
        if api_key:
            return address, api_key
    return None, None


def query_status(
    config: Config,
    *,
    settings: SyncthingSettings | None = None,
    timeout: float = _TIMEOUT,
    fetch_folder_state: bool = True,
) -> SyncStatus:
    """The shared status query. Reachability problems are *states*, never raised —
    doctor, the glyph, and the service all want the degraded value, not an except.
    """
    if settings is None:
        settings = resolve_settings(config)
    if settings is None:
        return SyncStatus(state=SyncState.UNCONFIGURED)

    try:  # /rest/system/version is the first keyed call → it gates reach vs auth.
        version_doc = _get_json(settings, "/rest/system/version", timeout)
    except _Unauthorized as exc:
        return SyncStatus(state=SyncState.UNAUTHORIZED, error=str(exc))
    except _Unreachable as exc:
        return SyncStatus(state=SyncState.UNREACHABLE, error=str(exc))

    version = (
        _str(version_doc.get("version")) if isinstance(version_doc, dict) else None
    )
    status_doc = _try(settings, "/rest/system/status", timeout)
    my_id = _str(status_doc.get("myID")) if isinstance(status_doc, dict) else None
    folders = _parse_folders(_try(settings, "/rest/config/folders", timeout))
    devices = _parse_devices(
        _try(settings, "/rest/config/devices", timeout),
        _try(settings, "/rest/system/connections", timeout),
        my_id,
    )
    store_folder = folder_containing(config.syncthing_root, folders)
    state = SyncState.IDLE
    if fetch_folder_state and store_folder is not None:
        store_folder, state = _fold_state(settings, store_folder, timeout)
        folders = tuple(store_folder if f.id == store_folder.id else f for f in folders)
    return SyncStatus(
        state=state,
        version=version,
        my_id=my_id,
        folders=folders,
        devices=devices,
        store_folder=store_folder,
        connected_devices=sum(1 for d in devices if d.connected),
        total_devices=len(devices),
    )


def folder_containing(
    root: Path, folders: Sequence[FolderStatus]
) -> FolderStatus | None:
    """The synced folder whose path is an ancestor of (or equal to) ``root``.

    Both sides are canonicalized first (:func:`_canon`) so the Termux view
    (``~/storage/shared/…``, a symlink) matches Syncthing's ``/storage/emulated/0/…``
    report. Longest match wins when folders nest.
    """
    store = _canon(root)
    best: FolderStatus | None = None
    best_len = -1
    for folder in folders:
        canon = _canon(Path(folder.path))
        try:
            contained = PurePath(store).is_relative_to(canon)
        except ValueError:
            contained = False
        if contained and len(canon) > best_len:
            best, best_len = folder, len(canon)
    return best


def probe_health(
    base_url: str, *, timeout: float = _TIMEOUT, verify_tls: bool = False
) -> bool:
    """``/rest/noauth/health`` — no key. Lets doctor distinguish "running but
    dossier has no API key" from "not running" on an unconfigured device.
    """
    settings = SyncthingSettings(base_url=base_url, api_key=None, verify_tls=verify_tls)
    try:
        _get_json(settings, "/rest/noauth/health", timeout)
        return True
    except (_Unreachable, _Unauthorized):
        return False


# -- parsing helpers ---------------------------------------------------------
def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _try(settings: SyncthingSettings, path: str, timeout: float) -> object | None:
    """Best-effort GET: return the decoded body, or None on any transport error.

    Used for the non-gating endpoints — once ``version`` proved reach + auth, a
    later hiccup degrades that field rather than failing the whole status.
    """
    try:
        return _get_json(settings, path, timeout)
    except (_Unreachable, _Unauthorized):
        return None


def _parse_folders(doc: object) -> tuple[FolderStatus, ...]:
    if not isinstance(doc, list):
        return ()
    out: list[FolderStatus] = []
    for item in doc:
        if not isinstance(item, dict):
            continue
        versioning = item.get("versioning")
        vtype = (
            _str(versioning.get("type")) or "" if isinstance(versioning, dict) else ""
        )
        devices = item.get("devices")
        shared = len(devices) - 1 if isinstance(devices, list) and devices else 0
        out.append(
            FolderStatus(
                id=_str(item.get("id")) or "",
                label=_str(item.get("label")) or "",
                path=_str(item.get("path")) or "",
                paused=bool(item.get("paused")),
                versioning=vtype,
                shared_with=max(shared, 0),
            )
        )
    return tuple(out)


def _parse_devices(
    devices_doc: object, connections_doc: object, my_id: str | None
) -> tuple[DeviceStatus, ...]:
    conns: dict = {}
    if isinstance(connections_doc, dict):
        c = connections_doc.get("connections")
        if isinstance(c, dict):
            conns = c
    if not isinstance(devices_doc, list):
        return ()
    out: list[DeviceStatus] = []
    for item in devices_doc:
        if not isinstance(item, dict):
            continue
        did = _str(item.get("deviceID")) or ""
        if not did or did == my_id:  # the local device is not a sync peer
            continue
        entry = conns.get(did)
        connected = bool(entry.get("connected")) if isinstance(entry, dict) else False
        out.append(
            DeviceStatus(
                device_id=did,
                name=_str(item.get("name")) or did[:7],
                connected=connected,
            )
        )
    return tuple(out)


def _fold_state(
    settings: SyncthingSettings, folder: FolderStatus, timeout: float
) -> tuple[FolderStatus, SyncState]:
    """Fetch a folder's live sync state (best-effort — absent ⇒ IDLE)."""
    import urllib.parse

    q = urllib.parse.quote(folder.id, safe="")
    doc = _try(settings, f"/rest/db/status?folder={q}", timeout)
    if not isinstance(doc, dict):
        return folder, SyncState.IDLE
    raw = _str(doc.get("state")) or ""
    need = doc.get("needTotalItems")
    updated = replace(
        folder,
        state=raw or None,
        need_items=need
        if isinstance(need, int) and not isinstance(need, bool)
        else None,
    )
    return updated, _map_folder_state(raw)


def _map_folder_state(raw: str) -> SyncState:
    lowered = raw.lower()
    if lowered.startswith("scan"):
        return SyncState.SCANNING
    if lowered.startswith(("sync", "clean", "prep")):
        return SyncState.SYNCING
    return SyncState.IDLE  # unknown / "idle" / no state → degrade calm, not alarmed


# -- settings + transport internals ------------------------------------------
def _base_url(address: str) -> str:
    address = address.strip().rstrip("/")
    return address if "://" in address else "https://" + address


def _default_xml_paths() -> list[Path]:
    """Per-platform Syncthing ``config.xml`` candidates (desktop autodiscovery)."""
    paths: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:  # Windows
        paths.append(Path(local) / "Syncthing" / "config.xml")
    state = os.environ.get("XDG_STATE_HOME")
    if state:
        paths.append(Path(state) / "syncthing" / "config.xml")
    home = Path.home()
    paths.append(home / ".local" / "state" / "syncthing" / "config.xml")  # v1.27+
    paths.append(home / ".config" / "syncthing" / "config.xml")  # legacy
    return paths


def _canon(path: Path) -> str:
    path = path.expanduser()
    try:
        path = (
            path.resolve()
        )  # follows symlinks (~/storage/shared → /storage/emulated/0)
    except OSError:
        path = Path(os.path.abspath(path))
    return os.path.normcase(str(path))  # case-folds on Windows/NTFS


def _host_of(base_url: str) -> str:
    rest = base_url.split("://", 1)[-1].split("/", 1)[0]
    if rest.startswith("["):  # [::1]:8384
        return rest[1 : rest.index("]")]
    return rest.split(":", 1)[0]


def _is_loopback(host: str) -> bool:
    return host in ("localhost", "::1") or host.startswith("127.")


def _ssl_context(verify: bool, host: str) -> ssl.SSLContext:
    import ssl

    if verify:
        return ssl.create_default_context()
    if not _is_loopback(host):
        raise ConfigError(
            f"refusing verify_tls=false for non-loopback host {host!r}: an "
            "unverified TLS context is only safe on loopback, where the GUI cert is "
            "self-signed and the API key is the real authenticator."
        )
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get_json(settings: SyncthingSettings, path: str, timeout: float) -> object:
    """One GET (with ``X-API-Key`` when set); ``json.loads`` the body.

    ``json.loads`` handles Syncthing's pretty-printed output and ``\\uXXXX``
    escapes — never parse it with a regex. A dedicated opener carries the loopback
    TLS context so an ``http→https`` 307 stays inside the unverified handler.
    """
    import urllib.error
    import urllib.request

    request = urllib.request.Request(settings.base_url + path)
    if settings.api_key:
        request.add_header("X-API-Key", settings.api_key)
    context = _ssl_context(settings.verify_tls, _host_of(settings.base_url))
    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise _Unauthorized(f"{path}: {exc.code} {exc.reason}") from exc
        raise _Unreachable(f"{path}: {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise _Unreachable(f"{settings.base_url}: {exc.reason}") from exc
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise _Unreachable(f"{settings.base_url}: {exc}") from exc
