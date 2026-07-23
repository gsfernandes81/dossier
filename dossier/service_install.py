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

"""Install the background scan service as a Windows Scheduled Task / systemd timer.

**Build-but-don't-run by contract.** Planning is pure (:func:`plan_install` /
:func:`plan_uninstall` just describe artifacts + the registration commands), and
nothing touches the system until :func:`apply` is called with ``register=True`` —
which the CLI only does on an explicit ``--yes``. The default path prints the
resolved command, the full generated artifact(s), and the exact commands it
*would* run, then stops. The OS-level power/idle settings baked into the artifacts
are defense-in-depth; the in-process gate in :mod:`dossier.service` stays
authoritative (it also covers power-saver, which the task settings do not).
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from shlex import join as shell_join
from xml.sax.saxutils import escape as xml_escape

import platformdirs

from dossier.config import APP_NAME

TASK_NAME = "dossier-scan"
# A fixed anchor for the daily/hourly Windows trigger — the hourly repetition is
# what actually matters, so the start date only needs to be in the past.
_WINDOWS_START_BOUNDARY = "2024-01-01T09:00:00"


# -- the resolved run command ------------------------------------------------


def resolve_run_command() -> list[str]:
    """The argv a scheduler should invoke for one pass.

    Prefers the installed ``ds`` console script beside the interpreter; falls back
    to ``<python> -m dossier`` when it isn't on disk (e.g. an editable checkout).
    """
    interpreter = Path(sys.executable)
    script = interpreter.with_name("ds.exe" if _is_windows() else "ds")
    if script.exists():
        return [str(script), "service", "run"]
    return [str(interpreter), "-m", "dossier", "service", "run"]


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


# -- pure artifact generators ------------------------------------------------


def windows_task_xml(run_command: list[str]) -> str:
    """A Task Scheduler v1.2 XML: hourly, plugged-in + idle only, single instance."""
    command = xml_escape(run_command[0])
    arguments = xml_escape(" ".join(run_command[1:]))
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>dossier background scan — plugged-in and idle only.</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{_WINDOWS_START_BOUNDARY}</StartBoundary>
      <Repetition>
        <Interval>PT1H</Interval>
        <StopAtDurationEnd>false</StopAtDurationEnd>
      </Repetition>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
      <Enabled>true</Enabled>
    </CalendarTrigger>
  </Triggers>
  <Settings>
    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>
    <RunOnlyIfIdle>true</RunOnlyIfIdle>
    <IdleSettings>
      <Duration>PT10M</Duration>
      <WaitTimeout>PT1H</WaitTimeout>
      <StopOnIdleEnd>true</StopOnIdleEnd>
    </IdleSettings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions>
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def systemd_service_unit(run_command: list[str]) -> str:
    """A oneshot user unit that runs one pass, plugged-in only and at idle priority."""
    return f"""[Unit]
Description=dossier background scan (plugged-in only)
ConditionACPower=true

[Service]
Type=oneshot
ExecStart={shell_join(run_command)}
Nice=19
IOSchedulingClass=idle
CPUSchedulingPolicy=idle
"""


def systemd_timer_unit() -> str:
    """A user timer: 10 min after boot, then hourly; no catch-up storm after resume."""
    return """[Unit]
Description=dossier background scan timer

[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
Persistent=false

[Install]
WantedBy=timers.target
"""


# -- install / uninstall plans -----------------------------------------------


@dataclass(frozen=True)
class Artifact:
    path: Path
    content: str


@dataclass(frozen=True)
class InstallPlan:
    supported: bool
    platform: str  # windows | linux | unsupported
    run_command: list[str] = field(default_factory=list)
    artifacts: tuple[Artifact, ...] = ()
    commands: tuple[list[str], ...] = ()  # register (install) / remove (uninstall)
    removes: tuple[Path, ...] = ()  # artifact files an uninstall deletes
    note: str = ""


def _windows_service_dir() -> Path:
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False)) / "service"


def _systemd_user_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def plan_install(run_command: list[str] | None = None) -> InstallPlan:
    """Describe (without touching anything) how to install the service here."""
    run_command = run_command or resolve_run_command()
    if _is_windows():
        xml_path = _windows_service_dir() / f"{TASK_NAME}.xml"
        return InstallPlan(
            supported=True,
            platform="windows",
            run_command=run_command,
            artifacts=(Artifact(xml_path, windows_task_xml(run_command)),),
            commands=(
                ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"],
            ),
        )
    if _is_linux():
        unit_dir = _systemd_user_dir()
        return InstallPlan(
            supported=True,
            platform="linux",
            run_command=run_command,
            artifacts=(
                Artifact(
                    unit_dir / f"{TASK_NAME}.service", systemd_service_unit(run_command)
                ),
                Artifact(unit_dir / f"{TASK_NAME}.timer", systemd_timer_unit()),
            ),
            commands=(
                ["systemctl", "--user", "daemon-reload"],
                ["systemctl", "--user", "enable", "--now", f"{TASK_NAME}.timer"],
            ),
            note=(
                "headless box? `loginctl enable-linger $USER` lets the timer run "
                "without an active login (run it yourself; not done here)."
            ),
        )
    return InstallPlan(
        supported=False,
        platform="unsupported",
        note="the scan service is desktop-only by design (no VLM on the phone).",
    )


def plan_uninstall() -> InstallPlan:
    """Describe how to remove the service (mirror of :func:`plan_install`)."""
    if _is_windows():
        xml_path = _windows_service_dir() / f"{TASK_NAME}.xml"
        return InstallPlan(
            supported=True,
            platform="windows",
            commands=(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"],),
            removes=(xml_path,),
        )
    if _is_linux():
        unit_dir = _systemd_user_dir()
        return InstallPlan(
            supported=True,
            platform="linux",
            commands=(
                ["systemctl", "--user", "disable", "--now", f"{TASK_NAME}.timer"],
                ["systemctl", "--user", "daemon-reload"],
            ),
            removes=(
                unit_dir / f"{TASK_NAME}.service",
                unit_dir / f"{TASK_NAME}.timer",
            ),
        )
    return InstallPlan(supported=False, platform="unsupported")


def status_query_command() -> list[str] | None:
    """The command that reports whether the service is registered (or ``None``)."""
    if _is_windows():
        return ["schtasks", "/Query", "/TN", TASK_NAME]
    if _is_linux():
        return ["systemctl", "--user", "is-enabled", f"{TASK_NAME}.timer"]
    return None


# -- applying a plan (only when the caller explicitly opts in) ----------------

_Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _default_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, check=False)


def apply(plan: InstallPlan, *, runner: _Runner = _default_runner) -> list[str]:
    """Write the plan's artifacts and run its commands. Returns a log of steps.

    Callers reach here only after an explicit confirmation — this is the sole
    place the module changes system state. ``runner`` is injectable so tests never
    shell out.
    """
    log: list[str] = []
    for artifact in plan.artifacts:
        artifact.path.parent.mkdir(parents=True, exist_ok=True)
        artifact.path.write_text(artifact.content, encoding="utf-8")
        log.append(f"wrote {artifact.path}")
    for target in plan.removes:
        if target.exists():
            target.unlink()
            log.append(f"removed {target}")
    for argv in plan.commands:
        result = runner(argv)
        status = "ok" if result.returncode == 0 else f"exit {result.returncode}"
        log.append(f"$ {shell_join(argv)}  → {status}")
    return log
