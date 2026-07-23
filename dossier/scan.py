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
from typing import Any

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

# A separate, larger pass for the full-text transcript (Phase 11) — kept apart from
# the extract schema so it never shares the verbatim-date budget or re-generates the
# structured fields the store already trusts.
_TRANSCRIBE_SYSTEM = (
    "You transcribe scanned documents. Output only what is visibly printed — never "
    "correct, translate, summarise, or invent."
)
_TRANSCRIBE_PROMPT = (
    "Transcribe every legible printed word in this document, top to bottom, "
    "verbatim (same spelling, digits, and punctuation). Then list 5-15 keywords: "
    "names, numbers, reference codes, and organisations that appear on it."
)
_TRANSCRIBE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "transcript": {"type": "string"},
        "keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["transcript", "keywords"],
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
    fingerprint: str = ""  # source file size:mtime, so a re-scan skips unchanged files
    # Full-text transcript + keywords for content search / `ds ask` (Phase 11).
    # Empty until a `ds scan --transcribe` pass; legacy readings default cleanly.
    transcript: str = ""
    keywords: tuple[str, ...] = ()

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
            model=model or str(data.get("model") or ""),
            fingerprint=str(data.get("fingerprint") or ""),
            transcript=str(data.get("transcript") or "").strip(),
            keywords=tuple(
                str(k).strip() for k in (data.get("keywords") or []) if str(k).strip()
            ),
        )

    def as_dict(self) -> dict:
        return asdict(self)


def file_fingerprint(path: Path) -> str:
    """A cheap change-token for a file: ``size:mtime`` (re-scan skips a match)."""
    stat = path.stat()
    return f"{stat.st_size}:{int(stat.st_mtime)}"


def _load_pdfium() -> Any:
    try:  # optional (`scan` extra), imported lazily; absent in CI/default installs
        import pypdfium2  # ty: ignore[unresolved-import]
    except ImportError as exc:
        raise ScanError(
            "ds scan needs the 'scan' extra: pip install 'dossier[scan]'"
        ) from exc
    return pypdfium2


def render_page(path: Path, dpi: int) -> bytes:
    """PNG bytes of the first page (PDFs rasterized; image files passed through)."""
    if path.suffix.lower() in _IMAGE_SUFFIXES:
        return path.read_bytes()
    pdfium = _load_pdfium()
    pdf = pdfium.PdfDocument(str(path))
    try:
        bitmap = pdf[0].render(scale=dpi / 72)
        buffer = io.BytesIO()
        bitmap.to_pil().save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        pdf.close()


def _vision_call(
    path: Path,
    config: Config,
    *,
    system: str,
    prompt: str,
    schema: dict[str, object],
    schema_name: str,
    max_tokens: int,
    timeout: float,
) -> dict:
    """Render ``path`` and post it to the VLM with a JSON-schema response format.

    Raises :class:`ScanError` if the file can't be rendered, the endpoint is
    unreachable, or the response isn't the expected JSON object.
    """
    try:
        png = render_page(path, config.scan_dpi)
    except ScanError:
        raise  # the "install the extra" message — keep it, don't re-wrap
    except Exception as exc:
        raise ScanError(f"could not render {path.name}: {exc}") from exc

    suffix = path.suffix.lower().lstrip(".") or "png"
    mime = "jpeg" if suffix in ("jpg", "jpeg") else suffix
    data_uri = f"data:image/{mime};base64," + base64.b64encode(png).decode()
    body = {
        "model": config.scan_model,
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "schema": schema},
        },
        "temperature": config.scan_temperature,
        "max_tokens": max_tokens,
    }
    return _post(config.scan_base_url, body, timeout)


def extract(path: Path, config: Config, *, timeout: float = 300.0) -> ScanReading:
    """Read ``path`` with the configured VLM and return its structured metadata."""
    payload = _vision_call(
        path,
        config,
        system=_SYSTEM,
        prompt=_PROMPT,
        schema=_SCHEMA,
        schema_name="reading",
        max_tokens=512,
        timeout=timeout,
    )
    return ScanReading.from_payload(payload, config.scan_model)


def transcribe(
    path: Path, config: Config, *, timeout: float = 300.0
) -> tuple[str, tuple[str, ...]]:
    """A second VLM pass: the document's full-text transcript + keywords.

    Kept separate from :func:`extract` so the structured (verbatim-date) reading is
    never re-generated; ``ds scan --transcribe`` backfills these for content search.
    """
    payload = _vision_call(
        path,
        config,
        system=_TRANSCRIBE_SYSTEM,
        prompt=_TRANSCRIBE_PROMPT,
        schema=_TRANSCRIBE_SCHEMA,
        schema_name="transcript",
        max_tokens=4096,  # smaller budgets truncate a full-page transcript's JSON
        timeout=timeout,
    )
    transcript = str(payload.get("transcript") or "").strip()
    raw = payload.get("keywords") or []
    keywords = tuple(str(k).strip() for k in raw if str(k).strip())
    return transcript, keywords


@dataclass(frozen=True)
class ModelInfo:
    """A model the router offers; ``vision`` gates which can back ``ds scan``."""

    id: str
    vision: bool


def list_models(config: Config, *, timeout: float = 15.0) -> list[ModelInfo]:
    """The router's models (``/v1/models``), vision-capable ones first.

    Vision models (image input) are the ones ``ds scan`` can use; a text-only
    model would ignore the page image. Raises :class:`ScanError` if unreachable.
    """
    data = _get(config.scan_base_url.rstrip("/") + "/models", timeout)
    out: list[ModelInfo] = []
    for entry in data.get("data", []) if isinstance(data, dict) else []:
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            continue
        architecture = entry.get("architecture") or {}
        modalities = architecture.get("input_modalities") or []
        out.append(ModelInfo(id=model_id, vision="image" in modalities))
    return sorted(out, key=lambda m: (not m.vision, m.id.lower()))


def _get(url: str, timeout: float) -> dict:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.URLError as exc:
        raise ScanError(f"VLM router unreachable at {url}: {exc.reason}") from exc
    except (TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ScanError(f"could not list models from {url}: {exc}") from exc


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
