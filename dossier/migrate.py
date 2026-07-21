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

"""One-time migration from a Notion export into the dossier store.

Consumes a JSON export of the Notion Documents + Document Storage databases and
the local file tree, and produces a :class:`MigrationPlan` (documents, locations,
and a list of issues to review). Nothing is written until ``apply_plan`` runs.

Design choices (DESIGN.md §10): slug references, ``dayfirst`` date parsing with
every two-digit numeric date flagged, state pseudo-locations folded into the
has_physical/has_digital flags, file matching ranked so category folders beat
application/trip-folder copies, and bundles only *suggested*, never auto-created.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import PurePosixPath
from typing import Any

from dateutil import parser as du_parser

from dossier.config import Config
from dossier.errors import DocumentExistsError
from dossier.model import Document, Location, Rendition
from dossier.store import Store

_STATE_LOCATION_NAMES = frozenset({"softcopy only", "destroyed"})
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
_BUNDLE_PATH_HINTS = (
    "application",
    "attempt",
    "uploaded",
    "submission",
)


# -- slugs -------------------------------------------------------------------


def slugify(name: str) -> str:
    ascii_name = (
        unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not slug:
        slug = "document"
    if slug in _WINDOWS_RESERVED:
        slug = f"{slug}-doc"
    return slug


def _unique_slug(base: str, used: set[str]) -> str:
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}-{n}", n + 1
    used.add(slug)
    return slug


# -- slots -------------------------------------------------------------------


def decode_slot(value: float | None) -> tuple[int | None, int | None]:
    """Split the Notion decimal slot (e.g. ``1.3``) into (slot, subslot)."""
    if value is None:
        return (None, None)
    slot = int(value)
    sub = round((float(value) - slot) * 10)
    return (slot, sub or None)


# -- dates -------------------------------------------------------------------


@dataclass(frozen=True)
class DateParse:
    issue: date | None = None
    expiry: date | None = None
    note: str = ""  # non-empty means "review this"


_DATE_TOKEN = re.compile(
    r"("
    r"\d{1,2}[-/][A-Za-z]{3,9}[-/]\d{2,4}"  # 07-Jan-2026
    r"|\d{4}[-/][A-Za-z]{3,9}[-/]\d{1,2}"  # 2020-dec-11
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"  # 10-07-26
    r"|\d{4}[-/]\d{1,2}[-/]\d{1,2}"  # 2019-05-19
    r")"
)


def _parse_token(token: str) -> date | None:
    try:
        return du_parser.parse(token, dayfirst=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


def _token_unambiguous(token: str) -> bool:
    # A spelled-out month or a 4-digit year fixes the day/month order.
    return bool(re.search(r"[A-Za-z]", token)) or bool(re.search(r"\d{4}", token))


def parse_dates(name: str) -> DateParse:
    """Pull issue/expiry dates out of a document name (dayfirst)."""
    pairs = [(tok, _parse_token(tok)) for tok in _DATE_TOKEN.findall(name)]
    valid = [(tok, dt) for tok, dt in pairs if dt is not None]
    if not valid:
        return DateParse()
    lower = name.lower()
    unambiguous = all(_token_unambiguous(tok) for tok, _ in valid)
    flag = "" if unambiguous else "two-digit numeric date; verify day/month order"

    if len(valid) >= 2 and " to " in f" {lower} ":
        return DateParse(issue=valid[0][1], expiry=valid[1][1], note=flag)
    if "expir" in lower or "exp " in lower:
        return DateParse(expiry=valid[-1][1], note=flag)
    if "issue" in lower:
        return DateParse(issue=valid[0][1], note=flag)
    return DateParse(
        expiry=valid[0][1], note="date with no issue/expiry keyword; guessed expiry"
    )


# -- physical/digital flags --------------------------------------------------


@dataclass(frozen=True)
class DerivedFlags:
    has_physical: bool
    has_digital: bool
    location: str | None  # None when the storage was a state pseudo-location


def derive_flags(storage_name: str | None, notes: str | None) -> DerivedFlags:
    name_l = (storage_name or "").strip().lower()
    notes_l = (notes or "").lower()
    is_state = name_l in _STATE_LOCATION_NAMES
    no_soft_copy = "no soft copy" in notes_l or "no softcopy" in notes_l
    return DerivedFlags(
        has_physical=bool(storage_name) and not is_state,
        has_digital=name_l == "softcopy only" or not no_soft_copy,
        location=None if is_state else (storage_name or None),
    )


# -- file matching -----------------------------------------------------------


@dataclass(frozen=True)
class MatchResult:
    path: str | None
    candidates: tuple[str, ...]
    status: str  # "unique" | "ambiguous" | "no-match"


def _norm_key(text: str) -> str:
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def _rank(path: str) -> tuple[bool, int, str]:
    lower = path.lower()
    in_bundle = any(hint in lower for hint in _BUNDLE_PATH_HINTS)
    return (in_bundle, path.count("/"), path)


class FileIndex:
    """Index of relative POSIX file paths, keyed by a normalised basename."""

    def __init__(self, relative_paths: list[str]) -> None:
        self._by_key: dict[str, list[str]] = {}
        for path in relative_paths:
            key = _norm_key(PurePosixPath(path).stem)
            if key:
                self._by_key.setdefault(key, []).append(path)

    def match(self, name: str) -> MatchResult:
        candidates = self._by_key.get(_norm_key(name), [])
        if not candidates:
            return MatchResult(None, (), "no-match")
        if len(candidates) == 1:
            return MatchResult(candidates[0], tuple(candidates), "unique")
        best = min(candidates, key=_rank)  # category folders beat bundle copies
        return MatchResult(best, tuple(candidates), "ambiguous")


def build_file_index(config: Config) -> FileIndex:
    """Index every file under the Syncthing root except the ``.dossier`` folder."""
    root = config.syncthing_root
    meta = config.meta_dir
    paths: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or meta in path.parents:
            continue
        paths.append(path.relative_to(root).as_posix())
    return FileIndex(paths)


# -- plan --------------------------------------------------------------------


@dataclass(frozen=True)
class MigrationIssue:
    doc: str
    kind: str
    detail: str


@dataclass
class MigrationPlan:
    documents: list[Document] = field(default_factory=list)
    locations: dict[str, Location] = field(default_factory=dict)
    issues: list[MigrationIssue] = field(default_factory=list)
    bundle_suggestions: dict[str, list[str]] = field(default_factory=dict)


def build_plan(export: Mapping[str, object], index: FileIndex) -> MigrationPlan:
    plan = MigrationPlan()
    name_to_slug = _build_locations(export, plan)
    used: set[str] = set()

    for entry in _as_list(export.get("documents")):
        name = _as_str(_get(entry, "name"))
        if not name:
            continue
        base = slugify(name)
        slug = _unique_slug(base, used)
        if slug != base:
            plan.issues.append(
                MigrationIssue(slug, "slug-collision", f"name collided; using {slug}")
            )

        flags = derive_flags(
            _as_opt_str(_get(entry, "permanent_storage")),
            _as_opt_str(_get(entry, "notes")),
        )
        temp_flags = derive_flags(_as_opt_str(_get(entry, "temp_storage")), None)
        perm_slot, perm_sub = decode_slot(_as_opt_float(_get(entry, "permanent_slot")))
        temp_slot, temp_sub = decode_slot(_as_opt_float(_get(entry, "temp_slot")))
        dates = parse_dates(name)

        match = index.match(name)
        files, has_digital = _resolve_files(match, flags, slug, plan)
        if dates.note:
            plan.issues.append(MigrationIssue(slug, "uncertain-date", dates.note))

        plan.documents.append(
            Document(
                id=slug,
                name=name,
                issue_date=dates.issue,
                expiry_date=dates.expiry,
                has_physical=flags.has_physical,
                has_digital=has_digital,
                files=files,
                perm_location=name_to_slug.get(flags.location or ""),
                perm_slot=perm_slot,
                perm_subslot=perm_sub,
                temp_location=name_to_slug.get(temp_flags.location or ""),
                temp_slot=temp_slot,
                temp_subslot=temp_sub,
                notes=_as_str(_get(entry, "notes")),
            )
        )
        if _as_bool(_get(entry, "carried_to_india")):
            plan.bundle_suggestions.setdefault("carried-to-india", []).append(slug)

    return plan


def _build_locations(
    export: Mapping[str, object], plan: MigrationPlan
) -> dict[str, str]:
    name_to_slug: dict[str, str] = {}
    for loc in _as_list(export.get("locations")):
        loc_name = _as_str(_get(loc, "name"))
        if not loc_name or loc_name.strip().lower() in _STATE_LOCATION_NAMES:
            continue
        slug = slugify(loc_name)
        plan.locations[slug] = Location(slug=slug, title=loc_name)
        name_to_slug[loc_name] = slug
    return name_to_slug


def _resolve_files(
    match: MatchResult, flags: DerivedFlags, slug: str, plan: MigrationPlan
) -> tuple[list[Rendition], bool]:
    if match.path is not None:
        if match.status == "ambiguous":
            plan.issues.append(
                MigrationIssue(
                    slug,
                    "multi-file-match",
                    f"{len(match.candidates)} candidates; picked {match.path}",
                )
            )
        return [Rendition(label="default", path=match.path, primary=True)], True
    if flags.has_digital:
        plan.issues.append(
            MigrationIssue(slug, "no-file-match", "no soft copy matched; link manually")
        )
    return [], flags.has_digital


# -- apply -------------------------------------------------------------------


def apply_plan(store: Store, plan: MigrationPlan) -> int:
    """Write the plan's locations and documents; return the number written."""
    store.ensure_layout()
    if plan.locations:
        locations = store.load_locations()
        locations.update(plan.locations)
        store.save_locations(locations)
    written = 0
    for doc in plan.documents:
        try:
            store.save(doc)
        except DocumentExistsError:
            continue
        written += 1
    return written


# -- export value coercions --------------------------------------------------


def _get(obj: object, key: str) -> object:
    return obj.get(key) if isinstance(obj, dict) else None


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_str(value: object) -> str:
    return "" if value is None else str(value)


def _as_opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _as_opt_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _as_bool(value: object) -> bool:
    return value is True or value == "__YES__"
