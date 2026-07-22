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

"""Vision extraction: read a document scan via a local VLM into a structured reading.

`ds scan` feeds these readings into the suggestions layer (issue/expiry dates) and
the reconcile succession matcher. The model is asked for **grounded, verbatim**
fields — dates are copied exactly as printed and parsed later by the same
ambiguity-aware machinery as name-dates (:mod:`dossier.suggest`), never trusted as
the model's own reordering (a scanned ``06/09/2024`` must not silently become June).

The backend is any OpenAI-compatible ``/v1/chat/completions`` endpoint (a llama.cpp
router by default); the model alias and URL are per-device config. Extraction runs
at a low temperature for determinism. Desktop-only (an 8B VLM isn't viable on the
phone); needs the ``scan`` extra (``pypdfium2`` + ``pillow``).
"""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from dossier.config import Config
from dossier.errors import ScanError

# Rendered scans of ID/qualification docs; the first page carries the metadata.
_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"})

_SYSTEM = (
    "You extract structured metadata from a scanned identity or qualification "
    "document. Read only what is visibly printed; never guess. Return null for a "
    "field that is not present."
)
_PROMPT = (
    "Extract this document's metadata as JSON. For issue_date_text and "
    "expiry_date_text, copy the date EXACTLY as printed (same digits and "
    "separators) — do NOT reorder or reformat it. Set is_validity_period true only "
    "when the two dates are a valid-from / valid-to window. evidence is a short "
    "verbatim quote showing the dates."
)
_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string"},
        "issuer": {"type": ["string", "null"]},
        "holder_name": {"type": ["string", "null"]},
        "issue_date_text": {"type": ["string", "null"]},
        "expiry_date_text": {"type": ["string", "null"]},
        "document_number": {"type": ["string", "null"]},
        "is_validity_period": {"type": "boolean"},
        "confidence": {"type": "number"},
        "evidence": {"type": ["string", "null"]},
    },
    # All fields required (nullable) — without this the model omits fields under the
    # router's sampling preset instead of emitting explicit nulls.
    "required": [
        "document_type",
        "issuer",
        "holder_name",
        "issue_date_text",
        "expiry_date_text",
        "document_number",
        "is_validity_period",
        "confidence",
        "evidence",
    ],
}


@dataclass(frozen=True)
class ScanReading:
    """One document's VLM-extracted metadata (dates kept verbatim, as printed)."""

    document_type: str
    issuer: str | None
    holder_name: str | None
    issue_date_text: str | None
    expiry_date_text: str | None
    document_number: str | None
    is_validity_period: bool
    confidence: float
    evidence: str | None
    model: str = ""

    @classmethod
    def from_payload(cls, data: dict, model: str) -> ScanReading:
        def text(key: str) -> str | None:
            value = data.get(key)
            return str(value).strip() or None if value not in (None, "") else None

        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return cls(
            document_type=str(data.get("document_type") or "").strip(),
            issuer=text("issuer"),
            holder_name=text("holder_name"),
            issue_date_text=text("issue_date_text"),
            expiry_date_text=text("expiry_date_text"),
            document_number=text("document_number"),
            is_validity_period=bool(data.get("is_validity_period")),
            confidence=confidence,
            evidence=text("evidence"),
            model=model,
        )

    def as_dict(self) -> dict:
        return asdict(self)


def render_page(path: Path, dpi: int) -> bytes:
    """PNG bytes of the first page (PDFs rasterized; image files passed through)."""
    if path.suffix.lower() in _IMAGE_SUFFIXES:
        return path.read_bytes()
    import pypdfium2 as pdfium  # optional (`scan` extra); imported lazily

    pdf = pdfium.PdfDocument(str(path))
    try:
        bitmap = pdf[0].render(scale=dpi / 72)
        buffer = io.BytesIO()
        bitmap.to_pil().save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        pdf.close()


def extract(path: Path, config: Config, *, timeout: float = 300.0) -> ScanReading:
    """Read ``path`` with the configured VLM and return its structured metadata.

    Raises :class:`ScanError` if the file can't be rendered, the endpoint is
    unreachable, or the response isn't the expected JSON object.
    """
    try:
        png = render_page(path, config.scan_dpi)
    except ImportError as exc:  # pragma: no cover - env-specific
        raise ScanError(
            "ds scan needs the 'scan' extra: pip install 'dossier[scan]'"
        ) from exc
    except Exception as exc:
        raise ScanError(f"could not render {path.name}: {exc}") from exc

    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in ("jpg", "jpeg") else suffix
    data_uri = f"data:image/{mime};base64," + base64.b64encode(png).decode()
    body = {
        "model": config.scan_model,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "reading", "schema": _SCHEMA},
        },
        "temperature": config.scan_temperature,
        "max_tokens": 512,
    }
    payload = _post(config.scan_base_url, body, timeout)
    return ScanReading.from_payload(payload, config.scan_model)


def _post(base_url: str, body: dict, timeout: float) -> dict:
    url = base_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read())
    except urllib.error.URLError as exc:
        raise ScanError(f"VLM unreachable at {url}: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise ScanError(f"VLM request failed: {exc}") from exc
    try:
        content = result["choices"][0]["message"]["content"]
        return json.loads(content)
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ScanError(f"unexpected VLM response: {exc}") from exc
