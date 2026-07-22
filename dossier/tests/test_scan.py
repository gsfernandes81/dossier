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

"""Tests for the vision extraction engine (the VLM call itself is mocked)."""

from dataclasses import replace
from pathlib import Path

import pytest

from dossier import scan
from dossier.config import Config
from dossier.errors import ScanError
from dossier.store import Store

_PAYLOAD = {
    "document_type": "Certificate of Competency",
    "issuer": "MCA",
    "holder_name": "Fernandes Gavin",
    "issue_date_text": "06/09/2024",
    "expiry_date_text": "28/09/2026",
    "document_number": "CoC0085036",
    "is_validity_period": True,
    "confidence": 0.98,
    "evidence": "Issue Date: 06/09/2024",
}


def _cfg(tmp_path: Path) -> Config:
    return Config(syncthing_root=tmp_path)


def test_config_scan_defaults(tmp_path: Path):
    cfg = _cfg(tmp_path)
    assert cfg.scan_base_url.endswith("/v1")
    assert cfg.scan_model == "qwen3vl"
    assert cfg.scan_temperature == 0.1


def test_reading_keeps_dates_verbatim():
    reading = scan.ScanReading.from_payload(_PAYLOAD, model="qwen3vl")
    # The parsed date is NOT trusted here — the raw text is kept for later,
    # ambiguity-aware parsing (a scanned 06/09/2024 must not silently become June).
    assert reading.issue_date_text == "06/09/2024"
    assert reading.expiry_date_text == "28/09/2026"
    assert reading.is_validity_period is True
    assert reading.model == "qwen3vl"


def test_reading_coerces_missing_and_bad_fields():
    reading = scan.ScanReading.from_payload(
        {"document_type": "Passport", "confidence": "oops"}, model="m"
    )
    assert reading.issuer is None and reading.expiry_date_text is None
    assert reading.is_validity_period is False
    assert reading.confidence == 0.0  # non-numeric confidence → 0.0, never crashes


def test_extract_posts_configured_model_and_low_temperature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(scan, "render_page", lambda path, dpi: b"PNG")
    seen: dict = {}

    def fake_post(base_url, body, timeout):
        seen["model"] = body["model"]
        seen["temperature"] = body["temperature"]
        seen["has_image"] = any(
            part.get("type") == "image_url"
            for part in body["messages"][-1]["content"]
        )
        return _PAYLOAD

    monkeypatch.setattr(scan, "_post", fake_post)
    reading = scan.extract(tmp_path / "coc.pdf", _cfg(tmp_path))
    assert reading.document_number == "CoC0085036"
    assert seen == {"model": "qwen3vl", "temperature": 0.1, "has_image": True}


def test_extract_raises_scanerror_on_transport_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(scan, "render_page", lambda path, dpi: b"PNG")

    def boom(base_url, body, timeout):
        raise ScanError("VLM unreachable")

    monkeypatch.setattr(scan, "_post", boom)
    with pytest.raises(ScanError):
        scan.extract(tmp_path / "coc.pdf", _cfg(tmp_path))


def test_scans_sidecar_round_trips(tmp_path: Path):
    store = Store(_cfg(tmp_path))
    store.ensure_layout()
    reading = replace(
        scan.ScanReading.from_payload(_PAYLOAD, model="qwen3vl"), fingerprint="99:123"
    )
    store.save_scans({"coc": reading})
    back = store.load_scans()
    assert back["coc"].document_type == "Certificate of Competency"
    assert back["coc"].expiry_date_text == "28/09/2026"  # verbatim survives the trip
    assert back["coc"].is_validity_period is True
    assert back["coc"].fingerprint == "99:123"  # so a re-scan can skip it
