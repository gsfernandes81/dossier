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

"""Power-state policy for the background scan service — never run on battery.

The scan service is a hard requirement to stay idle on battery or any power-saver
mode (see ROADMAP Phase 13). This module is that guard, split so it's testable
without hardware:

* :func:`decide` is a **pure** function over a :class:`PowerSample` — the only
  place the policy lives, and the unit-test target.
* the reader functions (:func:`read_sample` and the per-OS probes) gather a
  sample; each takes an injectable source so tests feed synthetic data.

The bias is deliberately conservative: "*never* on battery" cannot be honoured
while unsure, so an **unknown** AC state also skips. A missing power-saver signal
(a desktop without power-profiles-daemon) is *not* a blocker — only a positive
saver reading gates.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_LINUX_SUPPLY_ROOT = Path("/sys/class/power_supply")


@dataclass(frozen=True)
class PowerSample:
    """A point-in-time power reading. ``None`` means "couldn't tell"."""

    on_ac: bool | None  # True plugged in · False on battery · None unknown
    saver: bool | None  # True power-saver active · False off · None unknown
    percent: int | None
    source: str  # which probe produced this (for logging/diagnostics)


@dataclass(frozen=True)
class Decision:
    run: bool
    reason: str


def decide(sample: PowerSample, *, assume_ac: bool = False) -> Decision:
    """The policy: run only when provably on AC and not in a power-saver mode.

    ``assume_ac`` is the per-device escape hatch for a desktop whose firmware
    reports no power supply — it asserts "this machine is always plugged in".
    """
    on_ac = True if assume_ac else sample.on_ac
    if on_ac is False:
        return Decision(False, "on battery")
    if on_ac is None:
        return Decision(False, "AC power state unknown")
    if sample.saver is True:
        return Decision(False, "power saver active")
    return Decision(True, "on AC power")


def read_sample() -> PowerSample:
    """Probe this platform's power state (best-effort; unknowns become ``None``)."""
    if sys.platform.startswith("win"):
        return read_windows()
    from dossier.platform_open import is_termux

    if is_termux():
        return read_termux()
    if sys.platform.startswith("linux"):
        return read_linux()
    return PowerSample(None, None, None, f"unsupported:{sys.platform}")


# -- Windows -----------------------------------------------------------------


def parse_windows_status(ac_line: int, status_flag: int) -> PowerSample:
    """Map a ``SYSTEM_POWER_STATUS`` into a sample. Pure — tested on any OS.

    ``ACLineStatus``: 0 offline (battery) · 1 online (AC) · 255 unknown.
    ``SystemStatusFlag`` bit 0: battery-saver on (Windows 10+).
    """
    on_ac = {0: False, 1: True}.get(ac_line)  # 255/other → None
    saver = bool(status_flag & 1)
    return PowerSample(on_ac=on_ac, saver=saver, percent=None, source="windows")


def read_windows() -> PowerSample:  # pragma: no cover - exercised only on Windows
    import ctypes

    if sys.platform != "win32":
        # ``ctypes.windll`` is Windows-only; this guard proves that to the type
        # checker (so a Linux/CI analysis doesn't flag it) and never fires at
        # runtime — read_sample only calls this on Windows.
        return PowerSample(None, None, None, "windows-unavailable")

    class _Status(ctypes.Structure):
        _fields_ = (
            ("ACLineStatus", ctypes.c_ubyte),
            ("BatteryFlag", ctypes.c_ubyte),
            ("BatteryLifePercent", ctypes.c_ubyte),
            ("SystemStatusFlag", ctypes.c_ubyte),
            ("BatteryLifeTime", ctypes.c_ulong),
            ("BatteryFullLifeTime", ctypes.c_ulong),
        )

    status = _Status()
    ok = ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status))
    if not ok:
        return PowerSample(None, None, None, "windows-error")
    percent = status.BatteryLifePercent
    return PowerSample(
        on_ac=parse_windows_status(status.ACLineStatus, status.SystemStatusFlag).on_ac,
        saver=bool(status.SystemStatusFlag & 1),
        percent=percent if percent != 255 else None,
        source="windows",
    )


# -- Linux (sysfs) -----------------------------------------------------------


def read_linux(root: Path = _LINUX_SUPPLY_ROOT) -> PowerSample:
    """Read AC state from ``/sys/class/power_supply`` (root injectable for tests).

    A tower with no power-supply class at all reads as AC (that's why a true
    ``None`` is rare on Linux). Power-saver comes from ``powerprofilesctl``; its
    absence is ``None`` (no-saver assumed), never a blocker.
    """
    supplies = sorted(root.glob("*")) if root.is_dir() else []
    mains_online = any(
        _read_text(s / "type") == "Mains" and _read_text(s / "online") == "1"
        for s in supplies
    )
    battery_discharging = any(
        _read_text(s / "type") == "Battery"
        and _read_text(s / "status") == "Discharging"
        for s in supplies
    )
    if mains_online:
        on_ac: bool | None = True
    elif battery_discharging:
        on_ac = False
    elif not supplies:
        on_ac = True  # a desktop tower with no reported supplies
    else:
        on_ac = None
    return PowerSample(
        on_ac=on_ac, saver=read_linux_saver(), percent=None, source="linux-sysfs"
    )


def read_linux_saver() -> bool | None:  # pragma: no cover - subprocess wrapper
    exe = shutil.which("powerprofilesctl")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "get"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() == "power-saver"


def _read_text(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError:
        return ""


# -- Termux (Android) --------------------------------------------------------


def parse_termux_battery(data: dict[str, object]) -> PowerSample:
    """Map ``termux-battery-status`` JSON to a sample. Pure — tested on any OS."""
    plugged = str(data.get("plugged", "")).upper()
    on_ac = (plugged != "UNPLUGGED") if plugged else None
    percent = data.get("percentage")
    return PowerSample(
        on_ac=on_ac,
        saver=None,  # Android power-saver isn't exposed here; treated as unknown-off
        percent=percent if isinstance(percent, int) else None,
        source="termux",
    )


def read_termux() -> PowerSample:  # pragma: no cover - needs the Termux:API app
    exe = shutil.which("termux-battery-status")
    if not exe:
        return PowerSample(None, None, None, "termux-no-api")
    try:
        out = subprocess.run(
            [exe], capture_output=True, text=True, timeout=10, check=False
        )
        data = json.loads(out.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return PowerSample(None, None, None, "termux-error")
    if not isinstance(data, dict):
        return PowerSample(None, None, None, "termux-error")
    return parse_termux_battery(data)
