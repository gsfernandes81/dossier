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

"""Tests for the Syncthing REST client (Phase 15, Slice 1).

Two seams, both exercised: a real (fake) ``http.server`` on ``127.0.0.1:0`` so the
transport + JSON parsing run for real (no live Syncthing needed, works on the
Windows+Linux CI matrix), and direct calls into the pure helpers.
"""

from __future__ import annotations

import http.server
import json
import os
import ssl
import sys
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from dossier import syncthing
from dossier.config import Config
from dossier.errors import ConfigError
from dossier.syncthing import (
    FolderStatus,
    SyncState,
    SyncStatus,
    SyncthingSettings,
)


def _default_state() -> dict:
    return {
        "key": "testkey",
        "version": {"version": "v1.27.0", "os": "android", "arch": "arm64"},
        "status": {"myID": "SELF"},
        "folders": [
            {
                "id": "docs",
                "label": "Docs",
                "path": "/placeholder",  # overridden per test to a real path
                "paused": False,
                "versioning": {"type": "staggered"},
                "devices": [{"deviceID": "SELF"}, {"deviceID": "OTHER"}],
            },
            {
                "id": "weird",
                "label": "Weird",
                # a non-ASCII char → json.dumps escapes it \uXXXX; json.loads must
                # decode it (proves we parse, never regex).
                "path": "/tmp/Xfer – G & S",
                "paused": False,
                "versioning": {"type": ""},
                "devices": [{"deviceID": "SELF"}],
            },
        ],
        "devices": [
            {"deviceID": "SELF", "name": "me"},
            {"deviceID": "OTHER", "name": "laptop"},
        ],
        "connections": {"connections": {"OTHER": {"connected": True}}},
        "dbstatus": {"state": "idle", "needTotalItems": 0},
    }


@pytest.fixture
def st_server():
    """A fake Syncthing REST server; pretty-prints JSON and gates on X-API-Key."""
    state = _default_state()

    class Handler(http.server.BaseHTTPRequestHandler):
        # Signature matches BaseHTTPRequestHandler.log_message so ty accepts the
        # override; the body just silences the test server's stderr logging.
        def log_message(self, format: str, *args: object) -> None:
            pass

        def _send(self, code: int, obj: object) -> None:
            body = json.dumps(obj, indent=2).encode()  # pretty, like real Syncthing
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
            path = self.path.split("?", 1)[0]
            if path == "/rest/noauth/health":
                return self._send(200, {"status": "OK"})
            if self.headers.get("X-API-Key") != state["key"]:
                return self._send(403, {"error": "forbidden"})
            mapping = {
                "/rest/system/version": state["version"],
                "/rest/system/status": state["status"],
                "/rest/config/folders": state["folders"],
                "/rest/config/devices": state["devices"],
                "/rest/system/connections": state["connections"],
                "/rest/db/status": state["dbstatus"],
            }
            if path in mapping:
                return self._send(200, mapping[path])
            return self._send(404, {"error": "not found"})

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    settings = SyncthingSettings(base_url=f"http://127.0.0.1:{port}", api_key="testkey")
    try:
        yield SimpleNamespace(
            settings=settings, state=state, base_url=settings.base_url
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)


# -- query_status against the fake server ------------------------------------
def test_query_status_happy(st_server, tmp_path: Path):
    st_server.state["folders"][0]["path"] = str(tmp_path)  # the store's folder
    status = syncthing.query_status(
        Config(syncthing_root=tmp_path), settings=st_server.settings
    )
    assert status.state is SyncState.IDLE
    assert status.version == "v1.27.0"
    assert status.my_id == "SELF"
    assert status.store_folder is not None
    assert status.store_folder.id == "docs"
    assert status.store_folder.versioning == "staggered"
    assert status.store_folder.shared_with == 1  # OTHER, excluding self
    assert status.connected_devices == 1
    assert status.total_devices == 1  # OTHER only; SELF is not a peer
    # the \uXXXX-escaped path decoded and parsed
    assert any("Xfer" in folder.path for folder in status.folders)


def test_query_status_reports_versioning_off(st_server, tmp_path: Path):
    # the store sits in a folder with no versioning — the headline doctor finding
    st_server.state["folders"][0]["path"] = str(tmp_path)
    st_server.state["folders"][0]["versioning"] = {"type": ""}
    status = syncthing.query_status(
        Config(syncthing_root=tmp_path), settings=st_server.settings
    )
    assert status.store_folder is not None
    assert status.store_folder.versioning == ""


def test_query_status_syncing_state(st_server, tmp_path: Path):
    st_server.state["folders"][0]["path"] = str(tmp_path)
    st_server.state["dbstatus"] = {"state": "syncing", "needTotalItems": 4}
    status = syncthing.query_status(
        Config(syncthing_root=tmp_path), settings=st_server.settings
    )
    assert status.state is SyncState.SYNCING
    assert status.store_folder is not None and status.store_folder.need_items == 4


def test_query_status_unreachable(tmp_path: Path):
    settings = SyncthingSettings(base_url="http://127.0.0.1:1", api_key="k")
    status = syncthing.query_status(
        Config(syncthing_root=tmp_path), settings=settings, timeout=1.0
    )
    assert status.state is SyncState.UNREACHABLE
    assert status.error


def test_query_status_bad_key(st_server, tmp_path: Path):
    bad = replace(st_server.settings, api_key="wrong")
    status = syncthing.query_status(Config(syncthing_root=tmp_path), settings=bad)
    assert status.state is SyncState.UNAUTHORIZED


def test_query_status_unconfigured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(syncthing, "resolve_settings", lambda *a, **k: None)
    status = syncthing.query_status(Config(syncthing_root=tmp_path))
    assert status.state is SyncState.UNCONFIGURED


def test_probe_health(st_server):
    assert syncthing.probe_health(st_server.base_url) is True


def test_probe_health_false_when_down():
    assert syncthing.probe_health("http://127.0.0.1:1", timeout=1.0) is False


# -- folder_containing (path normalization) ----------------------------------
def _folder(path: Path, fid: str = "f") -> FolderStatus:
    return FolderStatus(
        id=fid, label="", path=str(path), paused=False, versioning="", shared_with=0
    )


def test_folder_containing_ancestor(tmp_path: Path):
    match = syncthing.folder_containing(tmp_path / "sub" / "deep", (_folder(tmp_path),))
    assert match is not None and match.id == "f"


def test_folder_containing_equal(tmp_path: Path):
    assert syncthing.folder_containing(tmp_path, (_folder(tmp_path),)) is not None


def test_folder_containing_none(tmp_path: Path):
    folders = (_folder(tmp_path / "other"),)
    assert syncthing.folder_containing(tmp_path / "store", folders) is None


def test_folder_containing_longest_wins(tmp_path: Path):
    folders = (_folder(tmp_path, "outer"), _folder(tmp_path / "a", "inner"))
    match = syncthing.folder_containing(tmp_path / "a" / "b", folders)
    assert match is not None and match.id == "inner"


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlink creation needs privilege on Windows CI"
)
def test_folder_containing_follows_symlink(tmp_path: Path):
    real = tmp_path / "emulated"
    real.mkdir()
    link = tmp_path / "shared"
    os.symlink(real, link)  # the Termux ~/storage/shared → /storage/emulated/0 case
    folders = (_folder(real / "Documents"),)
    match = syncthing.folder_containing(link / "Documents" / "x", folders)
    assert match is not None and match.id == "f"


# -- resolve_settings + config.xml autodiscovery -----------------------------
def test_resolve_settings_explicit(tmp_path: Path):
    config = Config(
        syncthing_root=tmp_path, syncthing_apikey="k", syncthing_address="1.2.3.4:9"
    )
    settings = syncthing.resolve_settings(config, xml_paths=[])
    assert settings is not None
    assert settings.api_key == "k"
    assert settings.base_url == "https://1.2.3.4:9"
    assert settings.source == "config"


def test_resolve_settings_autodiscovers_config_xml(tmp_path: Path):
    xml = tmp_path / "config.xml"
    xml.write_text(
        '<configuration><gui tls="true"><address>127.0.0.1:8384</address>'
        "<apikey>XYZ</apikey></gui></configuration>",
        encoding="utf-8",
    )
    settings = syncthing.resolve_settings(
        Config(syncthing_root=tmp_path), xml_paths=[xml]
    )
    assert settings is not None
    assert settings.api_key == "XYZ"
    assert settings.source == "config-xml"
    assert settings.base_url == "https://127.0.0.1:8384"


def test_resolve_settings_explicit_beats_xml(tmp_path: Path):
    xml = tmp_path / "config.xml"
    xml.write_text(
        "<configuration><gui><apikey>XYZ</apikey></gui></configuration>",
        encoding="utf-8",
    )
    config = Config(syncthing_root=tmp_path, syncthing_apikey="explicit")
    settings = syncthing.resolve_settings(config, xml_paths=[xml])
    assert settings is not None and settings.api_key == "explicit"
    assert settings.source == "config"


def test_resolve_settings_unconfigured(tmp_path: Path):
    assert (
        syncthing.resolve_settings(Config(syncthing_root=tmp_path), xml_paths=[])
        is None
    )


def test_resolve_settings_termux_skips_autodiscovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("dossier.platform_open.is_termux", lambda: True)
    # xml_paths=None → real autodiscovery path, which Termux must short-circuit.
    assert syncthing.resolve_settings(Config(syncthing_root=tmp_path)) is None


def test_discover_config_xml_absent(tmp_path: Path):
    assert syncthing.discover_config_xml([tmp_path / "nope.xml"]) == (None, None)


# -- TLS context safety ------------------------------------------------------
def test_ssl_context_loopback_is_unverified():
    ctx = syncthing._ssl_context(False, "127.0.0.1")
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_NONE


def test_ssl_context_refuses_nonloopback_unverified():
    with pytest.raises(ConfigError):
        syncthing._ssl_context(False, "192.168.1.5")


def test_ssl_context_verified_is_strict():
    ctx = syncthing._ssl_context(True, "192.168.1.5")
    assert isinstance(ctx, ssl.SSLContext) and ctx.verify_mode == ssl.CERT_REQUIRED


# -- wait_for_idle (the scan-service settle seam) ----------------------------
def _idle_folder() -> FolderStatus:
    return FolderStatus(
        id="docs", label="Docs", path="/p", paused=False, versioning="", shared_with=1
    )


def test_wait_for_idle_returns_idle_when_settled(tmp_path: Path):
    config = Config(syncthing_root=tmp_path)
    status = SyncStatus(state=SyncState.IDLE, store_folder=_idle_folder())
    slept: list[float] = []
    got = syncthing.wait_for_idle(
        config, status_fn=lambda *a, **k: status, sleep=slept.append
    )
    assert got == "idle" and slept == []  # already idle → no waiting


def test_wait_for_idle_polls_until_idle(tmp_path: Path):
    config = Config(syncthing_root=tmp_path)
    states = iter([SyncState.SYNCING, SyncState.SCANNING, SyncState.IDLE])
    folder = _idle_folder()
    slept: list[float] = []
    got = syncthing.wait_for_idle(
        config,
        poll=5.0,
        status_fn=lambda *a, **k: SyncStatus(state=next(states), store_folder=folder),
        sleep=slept.append,
    )
    assert got == "idle" and slept == [5.0, 5.0]  # two waits, then idle


def test_wait_for_idle_times_out_while_syncing(tmp_path: Path):
    config = Config(syncthing_root=tmp_path)
    folder = _idle_folder()
    slept: list[float] = []
    got = syncthing.wait_for_idle(
        config,
        timeout=10.0,
        poll=5.0,
        status_fn=lambda *a, **k: SyncStatus(
            state=SyncState.SYNCING, store_folder=folder
        ),
        sleep=slept.append,
    )
    assert got == "timeout" and slept == [5.0, 5.0]  # waited 0, 5, then 10 >= timeout


def test_wait_for_idle_unavailable_when_unreachable(tmp_path: Path):
    config = Config(syncthing_root=tmp_path)
    got = syncthing.wait_for_idle(
        config,
        status_fn=lambda *a, **k: SyncStatus(state=SyncState.UNREACHABLE),
        sleep=lambda _s: None,
    )
    assert got == "unavailable"


def test_wait_for_idle_unavailable_without_a_store_folder(tmp_path: Path):
    config = Config(syncthing_root=tmp_path)
    got = syncthing.wait_for_idle(
        config,
        status_fn=lambda *a, **k: SyncStatus(state=SyncState.IDLE, store_folder=None),
        sleep=lambda _s: None,
    )
    assert got == "unavailable"  # store not in a synced folder → nothing to race
