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
import os
import tempfile
import time
import tomllib
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import tomli_w
import yaml

from dossier.config import Config
from dossier.errors import DocumentExistsError, StaleWriteError, StoreError
from dossier.model import (
    Bundle,
    Document,
    Location,
    ReconcileState,
    Rendition,
    Requirement,
    SuggestionState,
    Template,
)
from dossier.scan import ScanReading

CONFLICT_MARKER = ".sync-conflict-"
TEMP_PREFIX = ".dossier-tmp-"

# Threads for the parallel read phase of load_all. File reads are latency-bound
# and release the GIL, so overlapping them is a big win on a synced/network store;
# ~16 measured optimal on Termux /sdcard (more workers stop helping).
_READ_WORKERS = 16

_DEFAULT_SYNCED_CONFIG = b"""\
# dossier synced settings - shared across devices via Syncthing.

# Days before expiry at which a document is flagged "expiring".
expiry_threshold_days = 90

# Globs (relative to the Syncthing root) scoping reconcile / orphan detection.
include = []
ignore = []
"""


class _Quoted(str):
    """A string that always serialises double-quoted (replaces ruamel's
    ``DoubleQuotedScalarString``): an unquoted ``#`` would start a YAML comment and
    truncate the value, and a bare ``yes``/``12:30`` could change type on reload."""


def _represent_quoted(dumper: yaml.Dumper, data: _Quoted) -> yaml.Node:
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


def _represent_none(dumper: yaml.Dumper, data: None) -> yaml.Node:
    # Emit ``key:`` (empty) for None instead of ``key: null`` — cleaner, round-trips.
    return dumper.represent_scalar("tag:yaml.org,2002:null", "")


# Use libyaml's C loader/dumper when available (desktop PyYAML wheels bundle it; on
# Termux it needs `pkg install libyaml`), else the pure-Python fallback — byte-for-
# byte identical output either way, so a file written on a C device matches one
# written on a pure device (no spurious Syncthing churn). Representers live on a
# private subclass so we never mutate PyYAML's shared dumper classes.
HAS_LIBYAML = yaml.__with_libyaml__
if HAS_LIBYAML:
    _Loader = yaml.CSafeLoader

    class _Dumper(yaml.CSafeDumper):
        pass
else:
    _Loader = yaml.SafeLoader

    class _Dumper(yaml.SafeDumper):  # type: ignore[no-redef]
        pass


_Dumper.add_representer(_Quoted, _represent_quoted)
_Dumper.add_representer(type(None), _represent_none)


def _serialize_frontmatter(mapping: dict[str, object]) -> str:
    return yaml.dump(
        mapping,
        Dumper=_Dumper,
        default_flow_style=False,
        allow_unicode=True,
        width=4096,
        sort_keys=False,
        indent=2,
    )


def libyaml_hint() -> str | None:
    """A one-line nudge to enable the fast C YAML backend, or ``None`` if already on
    it. Self-resolving — returns ``None`` the moment libyaml is active — so callers
    (``ds profile``, the TUI startup notice) can show it unconditionally and it just
    disappears once fixed. Desktop wheels bundle libyaml, so this only fires where
    PyYAML fell back to pure Python (typically Termux without ``pkg install
    libyaml``), where parsing the store is ~10x slower.
    """
    if HAS_LIBYAML:
        return None
    from dossier.platform_open import is_termux

    if is_termux():
        return (
            "YAML is running pure-Python (~10x slower parsing). Speed it up: "
            "`pkg install libyaml`, then reinstall so PyYAML rebuilds against it "
            "(see docs/guide/install.md)."
        )
    return (
        "YAML is running pure-Python (~10x slower parsing). Reinstall dossier so "
        "PyYAML picks up a libyaml-backed build (see docs/guide/install.md)."
    )


@dataclass(frozen=True)
class HistoryEntry:
    """One archived version of a document: when it was replaced, and where it sits.

    Written by every overwriting save (see :meth:`Store._backup`) into the *local*
    history dir, so versions never sync and can't start a sync round of their own.
    """

    doc_id: str
    saved_at: datetime  # UTC — when this version was superseded
    path: Path


class Store:
    """Reads and writes the ``.dossier`` data folder for a :class:`Config`."""

    def __init__(
        self, config: Config, *, now: Callable[[], datetime] | None = None
    ) -> None:
        self.config = config
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
        """Load every document, reading the files in parallel then parsing serially.

        The read is I/O-latency-bound — brutal one-at-a-time on a synced/network
        store (hundreds of slow opens on Termux's ``/sdcard``), but it overlaps
        cleanly because file reads release the GIL. Parsing is CPU/GIL-bound, so it
        stays serial (threads would just thrash the GIL). ``ThreadPoolExecutor.map``
        keeps output order and re-raises the first read error, so behaviour matches
        the old serial comprehension — only faster.
        """
        paths = list(self.iter_document_paths())
        if not paths:
            return []
        with ThreadPoolExecutor(max_workers=_READ_WORKERS) as pool:
            blobs = pool.map(self._read_bytes, paths)  # parallel I/O, ordered
            return [
                self._parse(path, raw) for path, raw in zip(paths, blobs, strict=True)
            ]

    def read_document(self, path: Path) -> Document:
        """Parse a document from an arbitrary path (e.g. a ``.sync-conflict-`` copy).

        ``id`` is set from the file stem and is meaningless for a conflict file —
        callers merging a conflict keep the live document's id, not this one.
        """
        return self._read(path)

    def _read(self, path: Path) -> Document:
        return self._parse(path, self._read_bytes(path))

    def _read_bytes(self, path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise StoreError(f"could not read {path}: {exc}") from exc

    def _parse(self, path: Path, raw: bytes) -> Document:
        front, notes = _split_frontmatter(raw.decode("utf-8"), path)
        data = yaml.load(front, Loader=_Loader)
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

    _STAMP = "%Y%m%dT%H%M%S%fZ"

    def history(self, doc_id: str) -> list[HistoryEntry]:
        """Archived prior versions of a document, **newest first**.

        Every overwriting save already writes one; this only surfaces what is there.
        Files whose name isn't a stamp are ignored rather than fatal — the history
        dir is a plain folder a human may have poked at.
        """
        entries: list[HistoryEntry] = []
        for path in (self.config.history_dir / doc_id).glob("*.md"):
            try:
                saved_at = datetime.strptime(path.stem, self._STAMP).replace(tzinfo=UTC)
            except ValueError:
                continue
            entries.append(HistoryEntry(doc_id, saved_at, path))
        return sorted(entries, key=lambda e: e.saved_at, reverse=True)

    def restore(self, entry: HistoryEntry) -> Document:
        """Write an archived version back as the current one.

        The restore is an ordinary save, so the version it replaces is archived in
        turn — undoing is itself undoable, and nothing is ever lost. Only the
        *content* comes from the archive: the id is the live document's (it is the
        filename), and the stale-write hash is the live file's, so the check still
        compares against what is on disk now rather than against the archive.
        """
        target = self.document_path(entry.doc_id)
        doc = self._parse(target, self._read_bytes(entry.path))
        try:
            doc.source_hash = self.load(entry.doc_id).source_hash
        except StoreError:
            doc.source_hash = None  # deleted since it was archived — recreate it
        return self.save(doc)

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
        """Render a document to its on-disk form: ``---`` frontmatter + Markdown body.

        Deterministic (fixed key order, double-quoted scalars) so re-saving an
        unchanged document is a byte-identical no-op — see the module docstring.
        Exposed for callers that need the exact bytes without writing (e.g. the
        round-trip lint in ``doctor``).
        """
        front = _serialize_frontmatter(_frontmatter_from_document(doc))
        if not front.endswith("\n"):
            front += "\n"
        body = f"{doc.notes}\n" if doc.notes else ""
        return f"---\n{front}---\n{body}"

    # -- locations & bundles -------------------------------------------------

    def load_locations(self, path: Path | None = None) -> dict[str, Location]:
        out: dict[str, Location] = {}
        raw_toml = _read_toml_or_empty(path or self.config.locations_path)
        for slug, raw in raw_toml.items():
            if not isinstance(raw, dict):
                continue
            out[slug] = Location(
                slug=slug,
                title=str(raw.get("title", slug)),
                notes=str(raw.get("notes", "")),
            )
        return out

    def serialize_locations(self, locations: Mapping[str, Location]) -> bytes:
        data: dict[str, dict[str, str]] = {}
        for slug, loc in sorted(locations.items()):
            entry: dict[str, str] = {"title": loc.title}
            if loc.notes:
                entry["notes"] = loc.notes
            data[slug] = entry
        return tomli_w.dumps(data).encode("utf-8")

    def save_locations(self, locations: dict[str, Location]) -> None:
        atomic_write_bytes(
            self.config.locations_path, self.serialize_locations(locations)
        )

    def load_bundles(self, path: Path | None = None) -> dict[str, Bundle]:
        out: dict[str, Bundle] = {}
        for slug, raw in _read_toml_or_empty(path or self.config.bundles_path).items():
            if not isinstance(raw, dict):
                continue
            export_dir = raw.get("export_dir")
            template = raw.get("template")
            out[slug] = Bundle(
                slug=slug,
                title=str(raw.get("title", slug)),
                date=_as_date(raw.get("date")),
                created=_as_datetime(raw.get("created")),
                export_dir=str(export_dir) if export_dir else None,
                notes=str(raw.get("notes", "")),
                template=str(template) if template else None,
            )
        return out

    def serialize_bundles(self, bundles: Mapping[str, Bundle]) -> bytes:
        """Serialize bundles, stamping ``created`` on any that lack it (mutates)."""
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
            if bundle.template:
                entry["template"] = bundle.template
            data[slug] = entry
        return tomli_w.dumps(data).encode("utf-8")

    def save_bundles(self, bundles: dict[str, Bundle]) -> None:
        """Persist bundles, stamping ``created`` on any that lack it."""
        atomic_write_bytes(self.config.bundles_path, self.serialize_bundles(bundles))

    def load_templates(self) -> dict[str, Template]:
        """Bundle-readiness checklists from ``templates.toml`` (absent → ``{}``).

        Hand-authored, synced, keyed by slug; tolerant of missing/junk fields so a
        typo degrades one requirement rather than erroring the whole file.
        """
        out: dict[str, Template] = {}
        for slug, raw in _read_toml_or_empty(self.config.templates_path).items():
            if not isinstance(raw, dict):
                continue
            requires: list[Requirement] = []
            require = raw.get("require")
            for req in require if isinstance(require, list) else []:
                if not isinstance(req, dict):
                    continue
                label = str(req.get("label", "")).strip()
                if not label:
                    continue
                match_raw = req.get("match")
                aliases = match_raw if isinstance(match_raw, list) else []
                match = tuple(str(a) for a in aliases if str(a).strip())
                requires.append(
                    Requirement(
                        label=label,
                        match=match,
                        count=_as_int(req.get("count"), 1),
                        min_valid_days=_as_int(req.get("min_valid_days"), 0),
                        optional=bool(req.get("optional", False)),
                    )
                )
            out[slug] = Template(
                slug=slug,
                title=str(raw.get("title", slug)),
                requires=tuple(requires),
            )
        return out

    # -- reconcile sidecar ---------------------------------------------------

    def load_reconcile(self, path: Path | None = None) -> ReconcileState:
        """Load the reconcile-decisions sidecar (absent → an empty state)."""
        raw = _read_toml_or_empty(path or self.config.reconcile_path)
        return ReconcileState(
            dismissed=set(_as_str_list(raw.get("dismissed"))),
            ignore=_as_str_list(raw.get("ignore")),
            missing_ok=_as_str_set_map(raw.get("missing_ok")),
            folded=_as_str_set_map(raw.get("folded")),
            dup_dismissed=_as_str_set_map(raw.get("dup_dismissed")),
            succession_dismissed=set(_as_str_list(raw.get("succession_dismissed"))),
        )

    @staticmethod
    def serialize_reconcile(state: ReconcileState) -> bytes:
        """Serialize the reconcile sidecar deterministically (sorted throughout)."""
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
        not_dups = {
            keep: sorted(subs) for keep, subs in state.dup_dismissed.items() if subs
        }
        if not_dups:
            data["dup_dismissed"] = {k: not_dups[k] for k in sorted(not_dups)}
        if state.succession_dismissed:
            data["succession_dismissed"] = sorted(state.succession_dismissed)
        return tomli_w.dumps(data).encode("utf-8")

    def save_reconcile(self, state: ReconcileState) -> None:
        """Persist the reconcile sidecar deterministically (sorted throughout)."""
        atomic_write_bytes(self.config.reconcile_path, self.serialize_reconcile(state))

    # -- suggestions sidecar -------------------------------------------------

    def load_suggestions(self, path: Path | None = None) -> SuggestionState:
        """Load the dismissed-suggestions sidecar (absent → an empty state)."""
        raw = _read_toml_or_empty(path or self.config.suggestions_path)
        return SuggestionState(dismissed=set(_as_str_list(raw.get("dismissed"))))

    @staticmethod
    def serialize_suggestions(state: SuggestionState) -> bytes:
        """Serialize the suggestions sidecar deterministically (sorted keys)."""
        data: dict[str, object] = {}
        if state.dismissed:
            data["dismissed"] = sorted(state.dismissed)
        return tomli_w.dumps(data).encode("utf-8")

    def save_suggestions(self, state: SuggestionState) -> None:
        """Persist the suggestions sidecar deterministically (sorted keys)."""
        atomic_write_bytes(
            self.config.suggestions_path, self.serialize_suggestions(state)
        )

    # -- scan readings sidecar (ds scan) -------------------------------------

    def load_scans(self, path: Path | None = None) -> dict[str, ScanReading]:
        """VLM readings keyed by document id (absent → empty). Synced, so a phone
        that can't run the model still benefits from a desktop scan."""
        return self._load_readings(path or self.config.scans_path)

    def save_scans(self, readings: Mapping[str, ScanReading]) -> None:
        """Persist readings deterministically (sorted ids; nulls dropped for TOML)."""
        self._save_readings(self.config.scans_path, readings)

    def load_intake_cache(self, path: Path | None = None) -> dict[str, ScanReading]:
        """Intake's VLM-reading cache, keyed by root-relative path (absent → empty).

        Lets `ds import`/`ds intake` reuse a reading whose file is unchanged (the
        ``fingerprint`` still matches), so a big sweep doesn't re-run the model.
        """
        return self._load_readings(path or self.config.intake_cache_path)

    def save_intake_cache(self, cache: Mapping[str, ScanReading]) -> None:
        self._save_readings(self.config.intake_cache_path, cache)

    def _load_readings(self, path: Path) -> dict[str, ScanReading]:
        raw = _read_toml_or_empty(path)
        out: dict[str, ScanReading] = {}
        for key, table in raw.items():
            if isinstance(table, dict):
                out[key] = ScanReading.from_payload(dict(table), model="")
        return out

    @staticmethod
    def serialize_readings(readings: Mapping[str, ScanReading]) -> bytes:
        data: dict[str, object] = {}
        for key in sorted(readings):
            data[key] = {
                field: value
                for field, value in readings[key].as_dict().items()
                # Drop nulls (no TOML null) and empties (""/()) so a transcript-less
                # reading serializes byte-identically to before Phase 11 — no churn.
                if value not in (None, "", ())
            }
        return tomli_w.dumps(data).encode("utf-8")

    def _save_readings(self, path: Path, readings: Mapping[str, ScanReading]) -> None:
        atomic_write_bytes(path, self.serialize_readings(readings))

    # -- recoverable archive (conflict resolution) ---------------------------

    def stash(self, category: str, name: str, data: bytes) -> Path:
        """Write ``data`` to the local (non-synced) history under ``category/name``.

        Timestamped so repeated stashes never collide, and kept off the synced
        tree so archiving a losing conflict copy can't itself start a new sync
        round. Used by :mod:`dossier.resolve` to make every merge recoverable.
        """
        stamp = self._now().strftime("%Y%m%dT%H%M%S%fZ")
        dest = self.config.history_dir / category / f"{name}.{stamp}"
        atomic_write_bytes(dest, data)
        return dest


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
        "name": _Quoted(doc.name),
        "tags": [_Quoted(t) for t in doc.tags],
        "bundles": [_Quoted(b) for b in doc.bundles],
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
        "label": _Quoted(rendition.label),
        "path": _Quoted(rendition.path),
        "primary": rendition.primary,
    }


def _dq_or_none(value: str | None) -> object:
    return None if value is None else _Quoted(value)


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


def _as_int(value: object, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


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
    """``base``, suffixed ``-2``, ``-3``… until nothing collides (case-insensitively).

    The single collision guard for a new document id, used by every surface that
    creates one (adopt, the detail pane, intake). The comparison is **casefolded**:
    a new ``passport`` must not land beside an existing ``Passport`` on a
    case-sensitive filesystem (Linux/Termux), because Syncthing would then deliver
    that pair as a name collision to a case-insensitive device (Windows/macOS).
    """
    existing = {path.stem.casefold() for path in store.iter_document_paths()}
    candidate, n = base, 2
    while candidate.casefold() in existing:
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` via a same-directory temp file + ``os.replace``.

    The final rename is retried with a short backoff on ``PermissionError``
    (Windows ``WinError 5``): on a cloud-sync filesystem (Proton Drive, OneDrive,
    Dropbox) — or with an indexer / antivirus in the loop — the sync daemon can
    hold a transient handle on the just-written target, which clears in a moment.
    A run that saves a growing sidecar after every file would otherwise crash.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _replace_with_retry(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _replace_with_retry(tmp: Path, path: Path, *, attempts: int = 6) -> None:
    """``os.replace`` with exponential backoff on a transient ``PermissionError``."""
    for attempt in range(attempts):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise  # genuinely locked / no permission — surface it
            time.sleep(0.1 * (2**attempt))  # 0.1, 0.2, 0.4, 0.8, 1.6 s


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
