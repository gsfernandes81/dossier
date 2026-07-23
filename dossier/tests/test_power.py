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

"""Tests for the power-state policy (:mod:`dossier.power`)."""

from pathlib import Path

from dossier import power
from dossier.power import PowerSample


def _sample(on_ac: bool | None, saver: bool | None = False) -> PowerSample:
    return PowerSample(on_ac=on_ac, saver=saver, percent=None, source="test")


# -- the pure policy ---------------------------------------------------------


def test_decide_runs_only_on_ac_without_saver():
    assert power.decide(_sample(True, saver=False)).run is True
    assert power.decide(_sample(True, saver=None)).run is True  # unknown saver is fine


def test_decide_skips_on_battery():
    decision = power.decide(_sample(False))
    assert decision.run is False and decision.reason == "on battery"


def test_decide_skips_when_ac_state_unknown():
    # "never on battery" can't be honoured while unsure, so unknown AC skips too.
    decision = power.decide(_sample(None))
    assert decision.run is False and "unknown" in decision.reason


def test_decide_skips_in_power_saver_even_on_ac():
    decision = power.decide(_sample(True, saver=True))
    assert decision.run is False and decision.reason == "power saver active"


def test_assume_ac_overrides_unknown_but_not_saver():
    assert power.decide(_sample(None), assume_ac=True).run is True  # forced on AC
    # ...but a power-saver reading still gates even with assume_ac.
    assert power.decide(_sample(None, saver=True), assume_ac=True).run is False


# -- Windows status parsing (pure; runs on any OS) ---------------------------


def test_parse_windows_status_maps_ac_line_and_saver_bit():
    assert power.parse_windows_status(0, 0).on_ac is False  # offline → battery
    assert power.parse_windows_status(1, 0).on_ac is True  # online → AC
    assert power.parse_windows_status(255, 0).on_ac is None  # unknown
    assert power.parse_windows_status(1, 1).saver is True  # bit 0 set → saver on
    assert power.parse_windows_status(1, 0).saver is False


# -- Linux sysfs (injected root) ---------------------------------------------


def _supply(root: Path, name: str, **files: str) -> None:
    directory = root / name
    directory.mkdir(parents=True)
    for filename, content in files.items():
        (directory / filename).write_text(content)


def test_read_linux_online_mains_is_ac(tmp_path: Path):
    _supply(tmp_path, "AC", type="Mains", online="1")
    _supply(tmp_path, "BAT0", type="Battery", status="Charging")
    assert power.read_linux(tmp_path).on_ac is True


def test_read_linux_discharging_battery_is_on_battery(tmp_path: Path):
    _supply(tmp_path, "AC", type="Mains", online="0")
    _supply(tmp_path, "BAT0", type="Battery", status="Discharging")
    assert power.read_linux(tmp_path).on_ac is False


def test_read_linux_no_supplies_is_ac(tmp_path: Path):
    # A desktop tower with no power-supply class at all → treated as plugged in.
    assert power.read_linux(tmp_path).on_ac is True


def test_read_linux_ambiguous_is_unknown(tmp_path: Path):
    # Mains present but offline, and no discharging battery → genuinely unknown.
    _supply(tmp_path, "AC", type="Mains", online="0")
    assert power.read_linux(tmp_path).on_ac is None


# -- Termux battery JSON (pure) ----------------------------------------------


def test_parse_termux_battery_plugged_and_unplugged():
    assert power.parse_termux_battery({"plugged": "PLUGGED_AC"}).on_ac is True
    assert power.parse_termux_battery({"plugged": "UNPLUGGED"}).on_ac is False
    assert power.parse_termux_battery({}).on_ac is None  # missing → unknown
