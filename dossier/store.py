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

"""The flat-file document store.

One Markdown file per document (YAML frontmatter + a Markdown notes body) under
``<root>/.dossier/documents/``; locations and bundles in TOML beside it. Design
guarantees (see DESIGN.md §6):

* **Atomic writes** — a temp file in the *same directory* as the target (so
  ``os.replace`` never crosses a filesystem — ``$TMPDIR`` is a different mount on
  Termux) plus ``fsync``.
* **Deterministic serialization** — fixed key order, every string scalar
  double-quoted (an unquoted ``#`` would start a YAML comment and truncate the
  value), so re-saving an unchanged document is a no-op diff.
* **Conflict exclusion** — Syncthing ``*.sync-conflict-*`` files are never
  loaded; :meth:`Store.list_conflicts` surfaces them instead.
* **Optimistic concurrency** — :meth:`Store.save` refuses to overwrite a file
  whose content changed since it was loaded, and backs up the prior version to a
  local (non-synced) history dir first.
"""

from __future__ import annotations

import hashlib
import io
import os
import tempfile
import tomllib
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path

import tomli_w
from ruamel.yaml import YAML
from ruamel.yaml.scalarstring import DoubleQuotedScalarString as DQ

from dossier.config import Config
from dossier.errors import DocumentExistsError, StaleWriteError, StoreError
from dossier.model import (
    Bundle,
    Document,
    Location,
    ReconcileState,
    Rendition,
    SuggestionState,
)
from dossier.scan import ScanReading

CONFLICT_MARKER = ".sync-conflict-"
TEMP_PREFIX = ".dossier-tmp-"

_DEFAULT_SYNCED_CONFIG = b"""\
# dossier synced settings - shared across devices via Syncthing.

# Days before expiry at which a document is flagged "expiring".
expiry_threshold_days = 90

# Globs (relative to the Syncthing root) scoping reconcile / orphan detection.
include = []
ignore = []
"""


def _represent_none(representer, data):  # ruamel None -> empty scalar callback
    # Emit ``key:`` (empty) for None instead of ``key: null`` — cleaner and it
    # round-trips back to None.
    return representer.represent_scalar("tag:yaml.org,2002:null", "")


def _make_dumper() -> YAML:
    yaml = YAML()  # round-trip dumper honours DoubleQuotedScalarString
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=2, offset=0)
    yaml.representer.add_representer(type(None), _represent_none)
    return yaml


class Store:
    """Reads and writes the ``.dossier`` data folder for a :class:`Config`."""

    def __init__(
        self, config: Config, *, now: Callable[[], datetime] | None = None
    ) -> None:
        self.config = config
        self._load_yaml = YAML(typ="safe")
        self._dump_yaml = _make_dumper()
        # Injectable clock so bundle-creation stamps are deterministic in tests.
        self._now = now or (lambda: datetime.now(UTC))

    # -- layout --------------------------------------------------------------

    def ensure_layout(self) -> None:
        """Create ``.dossier/documents`` and the TOML files if absent."""
        self.config.documents_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.config.locations_path, self.config.bundles_path):
            if not path.exists():
                atomic_write_bytes(path, b"")
        if not self.config.synced_config_path.exists():
            atomic_write_bytes(self.config.synced_config_path, _DEFAULT_SYNCED_CONFIG)

    # -- documents -----------------------------------------------------------

    def document_path(self, doc_id: str) -> Path:
        return self.config.documents_dir / f"{doc_id}.md"

    def iter_document_paths(self) -> list[Path]:
        """Every loadable document file, sorted; conflicts and temps excluded."""
        docs_dir = self.config.documents_dir
        if not docs_dir.is_dir():
            return []
        out: list[Path] = []
        for path in sorted(docs_dir.glob("*.md")):
            if CONFLICT_MARKER in path.name or path.name.startswith(TEMP_PREFIX):
                continue
            out.append(path)
        return out

    def list_conflicts(self) -> list[Path]:
        """Syncthing conflict files under ``.dossier`` (never auto-loaded)."""
        meta = self.config.meta_dir
        if not meta.is_dir():
            return []
        return sorted(
            p for p in meta.rglob("*") if CONFLICT_MARKER in p.name and p.is_file()
        )

    def load(self, doc_id: str) -> Document:
        return self._read(self.document_path(doc_id))

    def load_all(self) -> list[Document]:
        return [self._read(path) for path in self.iter_document_paths()]

    def _read(self, path: Path) -> Document:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise StoreError(f"could not read {path}: {exc}") from exc
        front, notes = _split_frontmatter(raw.decode("utf-8"), path)
        data = self._load_yaml.load(front)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise StoreError(f"{path}: frontmatter is not a mapping")
        doc = _document_from_frontmatter(data, notes)
        doc.id = path.stem
        doc.source_hash = _hash(raw)
        return doc

    def save(self, doc: Document) -> Document:
        """Persist ``doc``, guarding against clobbering a changed-on-disk copy.

        Updates ``doc.source_hash`` in place to the newly written content hash.
        """
        if not doc.id:
            raise StoreError("cannot save a document with an empty id")
        self.config.documents_dir.mkdir(parents=True, exist_ok=True)
        target = self.document_path(doc.id)

        if target.exists():
            prior = target.read_bytes()
            if doc.source_hash is None:
                raise DocumentExistsError(doc.id)
            if _hash(prior) != doc.source_hash:
                raise StaleWriteError(doc.id)
            self._backup(target, prior)
        elif doc.source_hash is not None:
            # Loaded earlier, but gone now — deleted underneath us.
            raise StaleWriteError(doc.id)

        payload = self.serialize(doc).encode("utf-8")
        atomic_write_bytes(target, payload)
        doc.source_hash = _hash(payload)
        return doc

    def _backup(self, target: Path, data: bytes) -> None:
        # A pre-overwrite history backup is best-effort: write ``data`` (the prior
        # content, already read by save) atomically and never let a failure block
        # the actual save — the user's edit still lands even if the backup can't.
        try:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            dest_dir = self.config.history_dir / target.stem
            atomic_write_bytes(dest_dir / f"{stamp}.md", data)
            _prune_history(dest_dir, keep=10)
        except OSError:
            return

    def serialize(self, doc: Document) -> str:
        buf = io.StringIO()
        self._dump_yaml.dump(_frontmatter_from_document(doc), buf)
        front = buf.getvalue()
        if not front.endswith("\n"):
            front += "\n"
        body = f"{doc.notes}\n" if doc.notes else ""
        return f"---\n{front}---\n{body}"

    # -- locations & bundles -------------------------------------------------

    def load_locations(self) -> dict[str, Location]:
        out: dict[str, Location] = {}
        for slug, raw in _read_toml_or_empty(self.config.locations_path).items():
            if not isinstance(raw, dict):
                continue
            out[slug] = Location(
                slug=slug,
                title=str(raw.get("title", slug)),
                notes=str(raw.get("notes", "")),
            )
        return out

    def save_locations(self, locations: dict[str, Location]) -> None:
        data: dict[str, dict[str, str]] = {}
        for slug, loc in sorted(locations.items()):
            entry: dict[str, str] = {"title": loc.title}
            if loc.notes:
                entry["notes"] = loc.notes
            data[slug] = entry
        self._write_toml(self.config.locations_path, data)

    def load_bundles(self) -> dict[str, Bundle]:
        out: dict[str, Bundle] = {}
        for slug, raw in _read_toml_or_empty(self.config.bundles_path).items():
            if not isinstance(raw, dict):
                continue
            export_dir = raw.get("export_dir")
            out[slug] = Bundle(
                slug=slug,
                title=str(raw.get("title", slug)),
                date=_as_date(raw.get("date")),
                created=_as_datetime(raw.get("created")),
                export_dir=str(export_dir) if export_dir else None,
                notes=str(raw.get("notes", "")),
            )
        return out

    def save_bundles(self, bundles: dict[str, Bundle]) -> None:
        """Persist bundles, stamping ``created`` on any that lack it."""
        data: dict[str, dict[str, object]] = {}
        for slug, bundle in sorted(bundles.items()):
            if bundle.created is None:
                bundle.created = self._now().replace(microsecond=0)
            entry: dict[str, object] = {"title": bundle.title}
            if bundle.date is not None:
                entry["date"] = bundle.date  # native TOML date
            entry["created"] = bundle.created  # native TOML datetime
            if bundle.export_dir:
                entry["export_dir"] = bundle.export_dir
            if bundle.notes:
                entry["notes"] = bundle.notes
            data[slug] = entry
        self._write_toml(self.config.bundles_path, data)

    # -- reconcile sidecar ---------------------------------------------------

    def load_reconcile(self) -> ReconcileState:
        """Load the reconcile-decisions sidecar (absent → an empty state)."""
        raw = _read_toml_or_empty(self.config.reconcile_path)
        return ReconcileState(
            dismissed=set(_as_str_list(raw.get("dismissed"))),
            ignore=_as_str_list(raw.get("ignore")),
            missing_ok=_as_str_set_map(raw.get("missing_ok")),
            folded=_as_str_set_map(raw.get("folded")),
            succession_dismissed=set(_as_str_list(raw.get("succession_dismissed"))),
        )

    def save_reconcile(self, state: ReconcileState) -> None:
        """Persist the reconcile sidecar deterministically (sorted throughout)."""
        data: dict[str, object] = {}
        if state.dismissed:
            data["dismissed"] = sorted(state.dismissed)
        if state.ignore:
            data["ignore"] = sorted(state.ignore)
        missing_ok = {
            path: sorted(ids) for path, ids in state.missing_ok.items() if ids
        }
        if missing_ok:
            data["missing_ok"] = {k: missing_ok[k] for k in sorted(missing_ok)}
        folded = {keep: sorted(subs) for keep, subs in state.folded.items() if subs}
        if folded:
            data["folded"] = {k: folded[k] for k in sorted(folded)}
        if state.succession_dismissed:
            data["succession_dismissed"] = sorted(state.succession_dismissed)
        self._write_toml(self.config.reconcile_path, data)

    # -- suggestions sidecar -------------------------------------------------

    def load_suggestions(self) -> SuggestionState:
        """Load the dismissed-suggestions sidecar (absent → an empty state)."""
        raw = _read_toml_or_empty(self.config.suggestions_path)
        return SuggestionState(dismissed=set(_as_str_list(raw.get("dismissed"))))

    def save_suggestions(self, state: SuggestionState) -> None:
        """Persist the suggestions sidecar deterministically (sorted keys)."""
        data: dict[str, object] = {}
        if state.dismissed:
            data["dismissed"] = sorted(state.dismissed)
        self._write_toml(self.config.suggestions_path, data)

    # -- scan readings sidecar (ds scan) -------------------------------------

    def load_scans(self) -> dict[str, ScanReading]:
        """VLM readings keyed by document id (absent → empty). Synced, so a phone
        that can't run the model still benefits from a desktop scan."""
        return self._load_readings(self.config.scans_path)

    def save_scans(self, readings: Mapping[str, ScanReading]) -> None:
        """Persist readings deterministically (sorted ids; nulls dropped for TOML)."""
        self._save_readings(self.config.scans_path, readings)

    def load_intake_cache(self) -> dict[str, ScanReading]:
        """Intake's VLM-reading cache, keyed by root-relative path (absent → empty).

        Lets `ds import`/`ds intake` reuse a reading whose file is unchanged (the
        ``fingerprint`` still matches), so a big sweep doesn't re-run the model.
        """
        return self._load_readings(self.config.intake_cache_path)

    def save_intake_cache(self, cache: Mapping[str, ScanReading]) -> None:
        self._save_readings(self.config.intake_cache_path, cache)

    def _load_readings(self, path: Path) -> dict[str, ScanReading]:
        raw = _read_toml_or_empty(path)
        out: dict[str, ScanReading] = {}
        for key, table in raw.items():
            if isinstance(table, dict):
                out[key] = ScanReading.from_payload(dict(table), model="")
        return out

    def _save_readings(self, path: Path, readings: Mapping[str, ScanReading]) -> None:
        data: dict[str, object] = {}
        for key in sorted(readings):
            data[key] = {
                field: value
                for field, value in readings[key].as_dict().items()
                if value is not None  # TOML has no null
            }
        self._write_toml(path, data)

    @staticmethod
    def _write_toml(path: Path, data: Mapping[str, object]) -> None:
        """Atomically serialize ``data`` to a TOML sidecar (the one write path)."""
        atomic_write_bytes(path, tomli_w.dumps(data).encode("utf-8"))


# -- frontmatter (de)serialization ------------------------------------------


def _split_frontmatter(text: str, path: Path) -> tuple[str, str]:
    """Split ``---`` frontmatter from the Markdown body. Body newlines trimmed."""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise StoreError(f"{path}: missing YAML frontmatter (no leading '---')")
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") == "---":
            front = "".join(lines[1:i])
            body = "".join(lines[i + 1 :])
            return front, body.strip("\n")
    raise StoreError(f"{path}: unterminated frontmatter (no closing '---')")


def _document_from_frontmatter(data: dict[str, object], notes: str) -> Document:
    return Document(
        name=_as_str(data.get("name")),
        tags=_as_str_list(data.get("tags")),
        bundles=_as_str_list(data.get("bundles")),
        issue_date=_as_date(data.get("issue_date")),
        expiry_date=_as_date(data.get("expiry_date")),
        ignore_expiry=_as_bool(data.get("ignore_expiry")),
        supersedes=_as_opt_str(data.get("supersedes")),
        has_physical=_as_bool(data.get("has_physical")),
        has_digital=_as_bool(data.get("has_digital")),
        files=_as_renditions(data.get("files")),
        perm_location=_as_opt_str(data.get("perm_location")),
        perm_slot=_as_opt_int(data.get("perm_slot")),
        perm_subslot=_as_opt_int(data.get("perm_subslot")),
        temp_location=_as_opt_str(data.get("temp_location")),
        temp_slot=_as_opt_int(data.get("temp_slot")),
        temp_subslot=_as_opt_int(data.get("temp_subslot")),
        notes=notes,
    )


def _frontmatter_from_document(doc: Document) -> dict[str, object]:
    # Insertion order is the on-disk key order (ruamel preserves it).
    return {
        "name": DQ(doc.name),
        "tags": [DQ(t) for t in doc.tags],
        "bundles": [DQ(b) for b in doc.bundles],
        "issue_date": doc.issue_date,
        "expiry_date": doc.expiry_date,
        "ignore_expiry": doc.ignore_expiry,
        "supersedes": _dq_or_none(doc.supersedes),
        "has_physical": doc.has_physical,
        "has_digital": doc.has_digital,
        "files": [_rendition_to_map(r) for r in doc.files],
        "perm_location": _dq_or_none(doc.perm_location),
        "perm_slot": doc.perm_slot,
        "perm_subslot": doc.perm_subslot,
        "temp_location": _dq_or_none(doc.temp_location),
        "temp_slot": doc.temp_slot,
        "temp_subslot": doc.temp_subslot,
    }


def _rendition_to_map(rendition: Rendition) -> dict[str, object]:
    return {
        "label": DQ(rendition.label),
        "path": DQ(rendition.path),
        "primary": rendition.primary,
    }


def _dq_or_none(value: str | None) -> object:
    return None if value is None else DQ(value)


# -- value coercions (tolerant of hand-edited frontmatter) ------------------


def _as_str(value: object) -> str:
    return "" if value is None else str(value)


def _as_opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _as_bool(value: object) -> bool:
    # A hand-edited *quoted* flag (`ignore_expiry: "false"`) is a truthy string to
    # bare bool(); read common false-y words as False so hand-edits behave.
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off")
    return bool(value)


def _as_opt_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)  # a hand-edited `perm_slot: 8.0` still loads as 8
    try:
        return int(str(value))
    except ValueError:
        return None


def _as_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _as_str_set_map(value: object) -> dict[str, set[str]]:
    """A ``{str: [str, ...]}`` TOML table → ``{str: set[str]}`` (tolerant)."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, set[str]] = {}
    for key, raw in value.items():
        items = set(_as_str_list(raw))
        if items:
            out[str(key)] = items
    return out


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _as_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    return None


def _as_renditions(value: object) -> list[Rendition]:
    if not isinstance(value, list):
        return []
    out: list[Rendition] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if not path:
            continue
        out.append(
            Rendition(
                label=str(item.get("label", "")),
                path=str(path),
                primary=_as_bool(item.get("primary", False)),
            )
        )
    return out


# -- filesystem helpers ------------------------------------------------------


def unique_id(store: Store, base: str) -> str:
    """``base``, suffixed ``-2``, ``-3``… until no document file collides on disk.

    The single collision guard for a new document id, used by every surface that
    creates one (adopt, the detail pane, intake).
    """
    candidate, n = base, 2
    while store.document_path(candidate).exists():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a same-directory temp file + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_toml_or_empty(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise StoreError(f"could not read {path}: {exc}") from exc


def _prune_history(dest_dir: Path, keep: int) -> None:
    backups = sorted(dest_dir.glob("*.md"))
    for old in backups[:-keep]:
        old.unlink(missing_ok=True)
