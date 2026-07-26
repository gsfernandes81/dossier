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

"""Tests for the CLI dispatch and ``ds init``."""

import argparse
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from dossier import (
    cli,
    config as config_mod,
    doctor,
)
from dossier.config import Config


def test_cli_import_stays_lean():
    # Importing the CLI must not drag in the command-specific machinery (dedup,
    # intake, the service installer, …) or the urllib/http stack — those are
    # deferred into the handful of commands that use them, so a quick `ds expiring`
    # or a bare `ds` starts fast. Run in a subprocess for a pristine sys.modules.
    forbidden = [
        "dossier.answers",
        "dossier.dedup_cache",
        "dossier.export",
        "dossier.init",
        "dossier.intake",
        "dossier.organize",
        "dossier.power",
        "dossier.preparedness",
        "dossier.profiling",
        "dossier.reset",
        "dossier.service",
        "dossier.service_install",
        "urllib.request",
    ]
    code = (
        "import dossier.cli, sys; "
        f"bad=[m for m in {forbidden!r} if m in sys.modules]; "
        "print(','.join(bad))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "", f"CLI import pulled in: {out.stdout.strip()}"


def test_init_creates_layout_and_loadable_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "docs"
    root.mkdir()
    device = tmp_path / "cfg" / "config.toml"
    _redirect_device(monkeypatch, device)

    assert cli.main(["init", "--root", str(root)]) == 0
    assert device.is_file()
    assert (root / ".dossier" / "documents").is_dir()
    assert (root / ".dossier" / "locations.toml").is_file()
    assert (root / ".dossier" / "config.toml").is_file()

    # The config the CLI wrote must load end-to-end.
    cfg = Config.load()
    assert cfg.syncthing_root == root.resolve()
    assert cfg.expiry_threshold_days == 90  # from the seeded synced config


def _redirect_device(monkeypatch: pytest.MonkeyPatch, device: Path) -> None:
    # `ds init` reads its own device path; update_per_device reads config's — both.
    monkeypatch.setattr("dossier.init.per_device_config_path", lambda: device)
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: device)


def test_init_rejects_missing_root_non_interactively(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Non-interactive (no TTY) without --yes: a missing --root is an error, never a
    # silently-created stray folder.
    device = tmp_path / "cfg" / "config.toml"
    _redirect_device(monkeypatch, device)
    monkeypatch.setattr(sys, "stdin", StringIO())  # non-interactive (isatty False)
    assert cli.main(["init", "--root", str(tmp_path / "nope")]) == 1
    assert not device.exists()


def test_init_bare_rerun_leaves_config_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # After setup, a bare non-interactive `ds init` reports already-configured and
    # changes nothing (the meaningful idempotency for scripts/CI).
    root = tmp_path / "docs"
    root.mkdir()
    device = tmp_path / "cfg" / "config.toml"
    _redirect_device(monkeypatch, device)

    assert cli.main(["init", "--root", str(root)]) == 0
    first = device.read_bytes()

    monkeypatch.setattr(sys, "stdin", StringIO())  # non-interactive (isatty False)
    assert cli.main(["init"]) == 0  # no --root → nothing to reconfigure to
    assert device.read_bytes() == first  # unchanged


def test_default_command_without_config_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    # Bare `ds` launches the TUI, which needs a configured device first.
    missing = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: missing)
    assert cli.main([]) == 1
    assert "init" in capsys.readouterr().err.lower()


def _touch_for(argv: list[str], *, termux: bool, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cli, "is_termux", lambda: termux)
    args = cli.build_parser().parse_args(argv)
    return cli._resolve_touch(args)


def test_touch_follows_platform_by_default(monkeypatch: pytest.MonkeyPatch):
    # No flag: the touch UI tracks the platform (on under Termux, off elsewhere).
    assert _touch_for([], termux=True, monkeypatch=monkeypatch) is True
    assert _touch_for([], termux=False, monkeypatch=monkeypatch) is False


def test_mobile_and_desktop_flags_override_platform(monkeypatch: pytest.MonkeyPatch):
    # The point of the flags: drive either UI on any platform (e.g. the touch UI
    # on a desktop terminal, for the tools/ PTY harness).
    assert _touch_for(["--mobile"], termux=False, monkeypatch=monkeypatch) is True
    assert _touch_for(["--desktop"], termux=True, monkeypatch=monkeypatch) is False


def test_mobile_and_desktop_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--mobile", "--desktop"])


def test_organize_cli_dry_run_then_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from dossier.model import Document, Rendition
    from dossier.store import Store

    root = tmp_path / "docs"
    root.mkdir()
    device = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr(cli, "per_device_config_path", lambda: device)
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: device)
    assert cli.main(["init", "--root", str(root)]) == 0

    config = Config.load()
    (config.syncthing_root / "Marine").mkdir()
    (config.syncthing_root / "Marine" / "scan.pdf").write_bytes(b"x")
    Store(config).save(
        Document(
            id="coc", name="CoC Card", files=[Rendition("d", "Marine/scan.pdf", True)]
        )
    )

    # Dry run: reports the rename, writes nothing.
    assert cli.main(["organize"]) == 0
    assert "Marine/scan.pdf  ->  Marine/coc-card.pdf" in capsys.readouterr().out
    assert (config.syncthing_root / "Marine" / "scan.pdf").exists()

    # Apply: renames on disk and rewrites the rendition path.
    assert cli.main(["organize", "--apply"]) == 0
    assert (config.syncthing_root / "Marine" / "coc-card.pdf").exists()
    assert not (config.syncthing_root / "Marine" / "scan.pdf").exists()
    assert Store(config).load("coc").files[0].path == "Marine/coc-card.pdf"


def test_intake_cli_dry_run_then_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from dossier import scan as scan_mod
    from dossier.scan import ScanReading
    from dossier.store import Store

    root = tmp_path / "docs"
    root.mkdir()
    device = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr(cli, "per_device_config_path", lambda: device)
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: device)
    assert cli.main(["init", "--root", str(root)]) == 0

    synced = root / ".dossier" / "config.toml"
    synced.write_text(synced.read_text() + '\n[intake]\ninbox = "Inbox"\n', "utf-8")
    (root / "Inbox").mkdir()
    (root / "Inbox" / "scan.pdf").write_bytes(b"x")
    reading = ScanReading.from_payload(
        {"document_type": "Passport", "confidence": 0.9}, model="fake"
    )
    monkeypatch.setattr(scan_mod, "extract", lambda p, c: reading)  # no live VLM

    # Dry run: proposes, writes nothing.
    assert cli.main(["intake"]) == 0
    assert "Passport" in capsys.readouterr().out
    assert (root / "Inbox" / "scan.pdf").exists()

    # Apply: files the record and moves the file to the fallback folder.
    assert cli.main(["intake", "--apply", "--yes"]) == 0
    assert not (root / "Inbox" / "scan.pdf").exists()
    assert (root / "Filed" / "passport.pdf").exists()
    assert any(d.name == "Passport" for d in Store(Config.load()).load_all())


def test_import_cli_caches_readings_then_files_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from dossier import scan as scan_mod
    from dossier.scan import ScanReading

    root = tmp_path / "docs"
    root.mkdir()
    device = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr(cli, "per_device_config_path", lambda: device)
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: device)
    assert cli.main(["init", "--root", str(root)]) == 0

    (root / "Papers").mkdir()
    (root / "Papers" / "a.pdf").write_bytes(b"x")
    calls: list[Path] = []

    def fake(path: Path, _config: Config) -> ScanReading:
        calls.append(path)
        return ScanReading.from_payload({"document_type": "Report"}, model="fake")

    monkeypatch.setattr(scan_mod, "extract", fake)

    # First dry-run scans once and caches the reading.
    assert cli.main(["import", str(root / "Papers")]) == 0
    assert "Report" in capsys.readouterr().out
    assert len(calls) == 1

    # Second dry-run reuses the cache — the VLM is not called again.
    assert cli.main(["import", str(root / "Papers")]) == 0
    capsys.readouterr()
    assert len(calls) == 1

    # Apply files it in place (a bulk import renames where it sits).
    assert cli.main(["import", str(root / "Papers"), "--apply", "--yes"]) == 0
    assert len(calls) == 1  # still served from cache
    assert (root / "Papers" / "report.pdf").exists()
    assert not (root / "Papers" / "a.pdf").exists()


def test_expiring_cli_lines_and_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from datetime import date, timedelta

    from dossier.model import Bundle, Document
    from dossier.store import Store

    root = tmp_path / "docs"
    root.mkdir()
    device = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr(cli, "per_device_config_path", lambda: device)
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: device)
    assert cli.main(["init", "--root", str(root)]) == 0
    config = Config.load()
    store = Store(config)
    today = date.today()
    capsys.readouterr()  # drop the init banner

    # Clean store → exit 0, empty stdout (the cron contract).
    assert cli.main(["expiring"]) == 0
    assert capsys.readouterr().out == ""

    # An expired doc → exit 1, one exact line.
    exp = today - timedelta(days=3)
    store.save(Document(id="cert", name="Old Cert", expiry_date=exp))
    assert cli.main(["expiring"]) == 1
    assert capsys.readouterr().out.strip() == f"{exp}  {'expired':8}  Old Cert"

    # An event row: valid today, but lapses before a future bundle needs it.
    lapses = today + timedelta(days=10)
    store.save(Document(id="pp", name="Passport", expiry_date=lapses, bundles=["trip"]))
    store.save_bundles(
        {"trip": Bundle(slug="trip", title="Trip", date=today + timedelta(days=200))}
    )
    capsys.readouterr()  # drain
    assert cli.main(["expiring", "--days", "5"]) == 1  # narrow window: Old Cert + event
    lines = capsys.readouterr().out.strip().splitlines()
    event_line = next(line for line in lines if "Passport" in line)
    assert "event" in event_line and "· needed" in event_line and "trip" in event_line

    # --no-events drops the event row; unknown bundle is exit 2.
    assert cli.main(["expiring", "--days", "5", "--no-events"]) == 1
    assert "Passport" not in capsys.readouterr().out
    assert cli.main(["expiring", "--bundle", "nope"]) == 2


def _configured_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Config:
    root = tmp_path / "docs"
    root.mkdir()
    device = tmp_path / "cfg" / "config.toml"
    monkeypatch.setattr(cli, "per_device_config_path", lambda: device)
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: device)
    assert cli.main(["init", "--root", str(root)]) == 0
    return Config.load()


def test_ask_cli_answers_expiry_and_reports_no_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from datetime import date

    from dossier.model import Document
    from dossier.store import Store

    config = _configured_store(tmp_path, monkeypatch)
    Store(config).save(
        Document(id="eng1", name="ENG-1 Medical", expiry_date=date(2028, 5, 21))
    )
    capsys.readouterr()

    assert cli.main(["ask", "when", "does", "my", "ENG-1", "expire"]) == 0
    assert "2028-05-21" in capsys.readouterr().out
    assert cli.main(["ask", "airspeed", "velocity", "swallow"]) == 1  # no match


def test_open_cli_dry_run_prints_without_opening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from dossier.model import Document, Rendition
    from dossier.store import Store

    config = _configured_store(tmp_path, monkeypatch)
    (config.syncthing_root / "Marine").mkdir()
    (config.syncthing_root / "Marine" / "coc.pdf").write_bytes(b"x")
    Store(config).save(
        Document(
            id="coc",
            name="Certificate of Competency",
            files=[Rendition("d", "Marine/coc.pdf", True)],
        )
    )
    opened: list[object] = []
    monkeypatch.setattr(cli, "open_file", lambda p: opened.append(p))
    capsys.readouterr()

    # -n prints the resolved path and does not open.
    assert cli.main(["open", "competency", "-n"]) == 0
    out = capsys.readouterr().out
    assert "coc" in out and "coc.pdf" in out
    assert opened == []

    # Without -n it invokes the platform opener.
    assert cli.main(["open", "competency"]) == 0
    assert len(opened) == 1


def test_scan_transcribe_backfills_and_skips_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from dossier import scan as scan_mod
    from dossier.model import Document, Rendition
    from dossier.store import Store

    config = _configured_store(tmp_path, monkeypatch)
    (config.syncthing_root / "a.pdf").write_bytes(b"x")
    store = Store(config)
    store.save(Document(id="d", name="Doc", files=[Rendition("f", "a.pdf", True)]))
    store.save_scans(  # a reading with NO transcript yet
        {"d": scan_mod.ScanReading.from_payload({"document_type": "Doc"}, "m")}
    )
    calls: list[int] = []

    def fake_transcribe(path, cfg):
        calls.append(1)
        return "full printed text", ("printed", "text")

    monkeypatch.setattr(scan_mod, "transcribe", fake_transcribe)
    capsys.readouterr()

    assert cli.main(["scan", "--transcribe"]) == 0
    reading = store.load_scans()["d"]
    assert reading.transcript == "full printed text"
    assert reading.keywords == ("printed", "text")
    assert len(calls) == 1

    # Second run: the transcript is present, so it's skipped (no VLM call).
    assert cli.main(["scan", "--transcribe"]) == 0
    assert len(calls) == 1


def test_service_subcommands_wire_up():
    parser = cli.build_parser()
    assert parser.parse_args(["service", "run"]).func is cli.cmd_service_run
    assert parser.parse_args(["service", "install"]).func is cli.cmd_service_install
    assert parser.parse_args(["service", "uninstall"]).func is cli.cmd_service_uninstall
    assert parser.parse_args(["service", "status"]).func is cli.cmd_service_status
    assert parser.parse_args(["service"]).func is cli.cmd_service


def test_service_install_dry_run_prints_plan_without_registering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _configured_store(tmp_path, monkeypatch)
    # Guard the contract: a dry run must never invoke apply().
    from dossier import service_install

    monkeypatch.setattr(
        service_install,
        "apply",
        lambda *a, **k: pytest.fail("dry run must not register"),
    )
    capsys.readouterr()
    assert cli.main(["service", "install"]) == 0
    out = capsys.readouterr().out
    assert "dry run" in out and "service run" in out


def test_resolve_cli_reports_nothing_when_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    _configured_store(tmp_path, monkeypatch)
    capsys.readouterr()
    assert cli.main(["resolve"]) == 0
    assert "no sync conflicts" in capsys.readouterr().out


def test_resolve_cli_dry_run_reports_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from dossier.model import Document
    from dossier.store import Store

    config = _configured_store(tmp_path, monkeypatch)
    store = Store(config)
    store.save(Document(id="eng-1", name="Passport"))
    live = store.document_path("eng-1")
    conflict = live.with_name("eng-1.sync-conflict-20260101-120000-AAAAAAA.md")
    conflict.write_bytes(store.serialize(Document(id="eng-1", name="Renewed")).encode())
    capsys.readouterr()

    assert cli.main(["resolve"]) == 0  # dry-run is not a failure
    out = capsys.readouterr().out
    assert "would merge" in out and "name" in out  # names the contested field
    assert conflict.exists()  # nothing written
    assert store.load("eng-1").name == "Passport"


def test_relative_to_root_resolves_cwd_first_then_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # A deep store root, mirroring the real "…/Official Documents".
    home = tmp_path / "home"
    root = home / "Proton Drive" / "Docs"
    (root / "Applications").mkdir(parents=True)
    config = Config(syncthing_root=root, history_dir=tmp_path / "_h")

    # The reported bug: a cwd-relative path TO the root must not be doubled onto it.
    monkeypatch.chdir(home)
    assert cli._relative_to_root(config, "./Proton Drive/Docs") == "."
    assert (
        cli._relative_to_root(config, "./Proton Drive/Docs/Applications")
        == "Applications"
    )

    # From inside the root, "." means the whole tree.
    monkeypatch.chdir(root)
    assert cli._relative_to_root(config, ".") == "."

    # A bare subfolder name still resolves root-relative from an unrelated cwd.
    monkeypatch.chdir(tmp_path)
    assert cli._relative_to_root(config, "Applications") == "Applications"

    # A path outside the root is rejected.
    assert cli._relative_to_root(config, str(tmp_path / "elsewhere")) is None


def test_progress_name_truncates_long_basenames():
    assert cli._progress_name("Marine/short.pdf") == "short.pdf"  # short: unchanged
    long_name = "x" * 60 + ".pdf"
    out = cli._progress_name(f"Dir/{long_name}")
    assert len(out) == 40 and out.endswith("…")  # truncated to the column width


def test_cmd_doctor_splits_warnings_from_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """`ds doctor` counts only warnings as findings; advisory/skipped items (a
    Syncthing not configured, say) print under a separate 'notes' block, never
    inflating the count or reading as a failure."""
    (tmp_path / ".dossier").mkdir()
    config = Config(syncthing_root=tmp_path)
    monkeypatch.setattr(cli, "_load_config", lambda: config)
    report = doctor.Report(
        findings=[
            doctor.Finding("syncthing-versioning", "Docs", "file versioning is off"),
            doctor.Finding("syncthing-unconfigured", "syncthing", "no API key", "info"),
        ]
    )
    monkeypatch.setattr(cli.doctor, "run", lambda *a, **k: report)

    rc = cli.cmd_doctor(argparse.Namespace())
    out = capsys.readouterr().out
    assert rc == 0
    assert "doctor: 1 finding(s)" in out  # the info note is not counted
    assert "syncthing-versioning" in out and "file versioning is off" in out
    assert "doctor: notes" in out
    assert "syncthing-unconfigured" in out
    # the warning is printed before the notes block
    assert out.index("syncthing-versioning") < out.index("doctor: notes")


def test_cmd_doctor_all_clear_when_only_store_is_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    (tmp_path / ".dossier").mkdir()
    config = Config(syncthing_root=tmp_path)
    monkeypatch.setattr(cli, "_load_config", lambda: config)
    monkeypatch.setattr(cli.doctor, "run", lambda *a, **k: doctor.Report(findings=[]))
    assert cli.cmd_doctor(argparse.Namespace()) == 0
    assert "all clear" in capsys.readouterr().out


def _init_device(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Init a device and redirect every per-device-config lookup to a tmp file."""
    root = tmp_path / "root"
    root.mkdir()
    device = tmp_path / "device.toml"
    # `ds init`, cli's own `per_device_config_path`, and config.update_syncthing all
    # resolve the path independently — redirect all three at their source.
    monkeypatch.setattr("dossier.init.per_device_config_path", lambda: device)
    monkeypatch.setattr(cli, "per_device_config_path", lambda: device)
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: device)
    assert cli.main(["init", "--root", str(root)]) == 0
    return root


def test_syncthing_key_sets_the_apikey(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _init_device(tmp_path, monkeypatch)
    assert cli.main(["syncthing", "key", "SECRETKEY123"]) == 0
    assert Config.load().syncthing_apikey == "SECRETKEY123"


def test_syncthing_key_prompts_when_omitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _init_device(tmp_path, monkeypatch)
    # The key is read with echo off (getpass) so it never lands in shell history.
    monkeypatch.setattr("getpass.getpass", lambda *a, **k: "  PROMPTED-KEY  ")
    assert cli.main(["syncthing", "key"]) == 0
    assert Config.load().syncthing_apikey == "PROMPTED-KEY"  # stripped


def test_syncthing_key_rejects_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _init_device(tmp_path, monkeypatch)
    assert cli.main(["syncthing", "key", "   "]) == 2
    assert Config.load().syncthing_apikey is None  # nothing written


def test_syncthing_key_preserves_a_previously_set_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # The [syncthing] table is read-merged, so setting the key later keeps the address.
    _init_device(tmp_path, monkeypatch)
    assert cli.main(["syncthing", "address", "127.0.0.1:9999"]) == 0
    assert cli.main(["syncthing", "key", "K"]) == 0
    cfg = Config.load()
    assert cfg.syncthing_address == "127.0.0.1:9999"
    assert cfg.syncthing_apikey == "K"


def test_syncthing_forget_clears_key_and_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    _init_device(tmp_path, monkeypatch)
    cli.main(["syncthing", "address", "127.0.0.1:9999"])
    cli.main(["syncthing", "key", "K"])
    assert cli.main(["syncthing", "forget"]) == 0
    cfg = Config.load()
    assert cfg.syncthing_apikey is None and cfg.syncthing_address is None


def test_syncthing_key_requires_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    missing = tmp_path / "nope.toml"
    monkeypatch.setattr(cli, "per_device_config_path", lambda: missing)
    monkeypatch.setattr(config_mod, "per_device_config_path", lambda: missing)
    assert cli.main(["syncthing", "key", "K"]) == 2
    assert "ds init" in capsys.readouterr().err


def test_syncthing_status_masks_the_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from dossier import syncthing

    _init_device(tmp_path, monkeypatch)
    cli.main(["syncthing", "key", "SUPERSECRETVALUE"])
    # Canned status so the reachability probe never touches the network.
    monkeypatch.setattr(
        syncthing,
        "query_status",
        lambda *a, **k: syncthing.SyncStatus(
            state=syncthing.SyncState.IDLE, version="1.99"
        ),
    )
    assert cli.main(["syncthing"]) == 0  # bare command → status
    out = capsys.readouterr().out
    assert "SUPERSECRETVALUE" not in out  # never echo the secret in full
    assert "api-key: set" in out and "source: config" in out
    assert "idle" in out and "1.99" in out


def test_syncthing_status_unconfigured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from dossier import syncthing

    _init_device(tmp_path, monkeypatch)
    monkeypatch.setattr(syncthing, "resolve_settings", lambda *a, **k: None)
    assert cli.main(["syncthing", "status"]) == 0
    out = capsys.readouterr().out
    assert "unconfigured" in out and "ds syncthing key" in out
