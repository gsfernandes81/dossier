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

"""Tests for the scan-service installer (:mod:`dossier.service_install`).

The generated artifacts are string-asserted and the registration subprocess is
injected, so nothing here ever touches the real Task Scheduler / systemd.
"""

import subprocess
import sys
from pathlib import Path

from dossier import service_install
from dossier.service_install import Artifact, InstallPlan, apply

# -- pure artifact generators ------------------------------------------------


def test_windows_task_xml_bakes_in_power_and_idle_guards():
    xml = service_install.windows_task_xml(["C:/py/ds.exe", "service", "run"])
    assert "<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>" in xml
    assert "<StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>" in xml
    assert "<RunOnlyIfIdle>true</RunOnlyIfIdle>" in xml
    assert "<Interval>PT1H</Interval>" in xml
    assert "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>" in xml
    assert "<Command>C:/py/ds.exe</Command>" in xml
    assert "<Arguments>service run</Arguments>" in xml


def test_systemd_units_are_ac_only_idle_and_hourly():
    unit = service_install.systemd_service_unit(["/usr/bin/ds", "service", "run"])
    assert "ConditionACPower=true" in unit
    assert "Type=oneshot" in unit
    assert "ExecStart=/usr/bin/ds service run" in unit
    assert "Nice=19" in unit and "IOSchedulingClass=idle" in unit
    timer = service_install.systemd_timer_unit()
    assert "OnUnitActiveSec=1h" in timer
    assert "Persistent=false" in timer  # no catch-up storm after a resume
    assert "WantedBy=timers.target" in timer


def test_resolve_run_command_ends_with_service_run():
    assert service_install.resolve_run_command()[-2:] == ["service", "run"]


def test_plan_install_describes_this_platform():
    plan = service_install.plan_install(["ds", "service", "run"])
    if sys.platform.startswith(("win", "linux")):
        assert plan.supported and plan.artifacts and plan.commands
        assert plan.platform in ("windows", "linux")
    else:
        assert not plan.supported


# -- applying a plan (mocked runner; tmp artifact paths) ---------------------


def _ok(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 0, "", "")


def test_apply_writes_artifacts_and_runs_commands(tmp_path: Path):
    artifact = Artifact(tmp_path / "service" / "dossier-scan.xml", "<Task/>")
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return _ok(argv)

    plan = InstallPlan(
        supported=True,
        platform="test",
        artifacts=(artifact,),
        commands=(["schtasks", "/Create"],),
    )
    log = apply(plan, runner=runner)
    assert artifact.path.read_text() == "<Task/>"  # artifact written
    assert calls == [["schtasks", "/Create"]]  # registration invoked
    assert any("wrote" in line for line in log)


def test_apply_writes_the_task_xml_as_utf16(tmp_path: Path):
    # Task Scheduler reads the XML per its `encoding="UTF-16"` declaration, so the
    # bytes on disk must actually be UTF-16 (with a BOM), not UTF-8.
    artifact = Artifact(tmp_path / "t.xml", "<Task>é</Task>", encoding="utf-16")
    apply(
        InstallPlan(supported=True, platform="test", artifacts=(artifact,)),
        runner=_ok,
    )
    raw = artifact.path.read_bytes()
    assert raw[:2] in (b"\xff\xfe", b"\xfe\xff")  # a UTF-16 byte-order mark
    assert artifact.path.read_text(encoding="utf-16") == "<Task>é</Task>"


def test_apply_removes_files_on_uninstall(tmp_path: Path):
    target = tmp_path / "dossier-scan.timer"
    target.write_text("[Timer]\n")
    plan = InstallPlan(supported=True, platform="test", removes=(target,))
    apply(plan, runner=_ok)
    assert not target.exists()
