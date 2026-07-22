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

Design choices (DESIGN.md §10): slug references, state pseudo-locations folded
into the has_physical/has_digital flags, file matching ranked so category folders
beat application/trip-folder copies, and bundles only *suggested*, never
auto-created.

**Expiries** are taken from the Notion *Marine Documents* table (`export`'s
``expiries`` list) — the authoritative source; they are **not** inferred from
document names (that was too aggressive — e.g. sea-service testimonials that
record a date range don't expire). Issue dates are still parsed from names for
now; that name parsing is slated to become dismissable *suggestions* (see
ROADMAP.md), not an authority.
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
    # ISO-style tokens lead with a 4-digit year (YYYY-MM-DD) and must NOT be read
    # dayfirst, or "2022-01-06" flips to 2022-06-01. Only DD-first numeric tokens
    # (10-07-26) are dayfirst.
    iso_like = bool(re.match(r"\d{4}[-/]", token))
    try:
        parsed = du_parser.parse(token, dayfirst=not iso_like, yearfirst=iso_like)
    except (ValueError, OverflowError, TypeError):
        return None
    return parsed.date()


def _token_unambiguous(token: str) -> bool:
    # A spelled-out month or a 4-digit year fixes the day/month order.
    return bool(re.search(r"[A-Za-z]", token)) or bool(re.search(r"\d{4}", token))


def _parse_expiries(value: object) -> dict[str, date]:
    """Map document name -> authoritative expiry date.

    Sourced from the Notion *Marine Documents* table's ``Expiry`` field (the only
    place structured expiries live), exported as ``[{"name", "expiry": ISO}]``.
    Keys are the exact document names, matched against the export's documents.
    """
    out: dict[str, date] = {}
    for entry in _as_list(value):
        name = _as_str(_get(entry, "name"))
        raw = _as_opt_str(_get(entry, "expiry"))
        if not name or not raw:
            continue
        try:
            out[name] = date.fromisoformat(raw)
        except ValueError:
            continue
    return out


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


_FUZZY_FLOOR = 0.5  # min share of a file's tokens present in the doc name
_FUZZY_AUTO = 0.8  # min score to auto-link (below this it's only a suggestion)
_MIN_SHARED_TOKENS = 2


def _tokens(text: str) -> frozenset[str]:
    ascii_text = (
        unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    )
    return frozenset(
        tok for tok in re.split(r"[^a-z0-9]+", ascii_text.lower()) if len(tok) >= 2
    )


class FileIndex:
    """Index of relative POSIX file paths for exact and fuzzy name matching."""

    def __init__(self, relative_paths: list[str]) -> None:
        self._by_key: dict[str, list[str]] = {}
        self._tokens_by_path: dict[str, frozenset[str]] = {}
        for path in relative_paths:
            stem = PurePosixPath(path).stem
            key = _norm_key(stem)
            if key:
                self._by_key.setdefault(key, []).append(path)
            tokens = _tokens(stem)
            if tokens:
                self._tokens_by_path[path] = tokens

    def match(self, name: str) -> MatchResult:
        candidates = self._by_key.get(_norm_key(name), [])
        if not candidates:
            return MatchResult(None, (), "no-match")
        if len(candidates) == 1:
            return MatchResult(candidates[0], tuple(candidates), "unique")
        best = min(candidates, key=_rank)  # category folders beat bundle copies
        return MatchResult(best, tuple(candidates), "ambiguous")

    def fuzzy_best(self, name: str, exclude: set[str]) -> tuple[str, float] | None:
        """Best fuzzy candidate ``(path, score)`` for ``name``, or None.

        Score is the fraction of the *file's* tokens present in the document
        name, so a file named as a short version of the doc scores near 1.0.
        Ties break toward category folders over bundle-folder copies.
        """
        target = _tokens(name)
        if not target:
            return None
        best: tuple[str, float] | None = None
        best_key: tuple[float, bool, int, str] | None = None
        for path, file_tokens in self._tokens_by_path.items():
            if path in exclude:
                continue
            shared = target & file_tokens
            if len(shared) < _MIN_SHARED_TOKENS:
                continue
            score = len(shared) / len(file_tokens)
            if score < _FUZZY_FLOOR:
                continue
            in_bundle, depth, _ = _rank(path)
            key = (-score, in_bundle, depth, path)
            if best_key is None or key < best_key:
                best, best_key = (path, score), key
        return best


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
    expiries = _parse_expiries(export.get("expiries"))
    used: set[str] = set()
    claimed: set[str] = set()
    unmatched: list[Document] = []

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
        # Only the issue date is taken from the name now; flag it when the token
        # was an ambiguous two-digit date. Expiry comes from the Marine table.
        if dates.issue is not None and dates.note:
            plan.issues.append(MigrationIssue(slug, "uncertain-date", dates.note))

        doc = Document(
            id=slug,
            name=name,
            issue_date=dates.issue,
            expiry_date=expiries.get(name),
            has_physical=flags.has_physical,
            has_digital=flags.has_digital,
            files=[],
            perm_location=name_to_slug.get(flags.location or ""),
            perm_slot=perm_slot,
            perm_subslot=perm_sub,
            temp_location=name_to_slug.get(temp_flags.location or ""),
            temp_slot=temp_slot,
            temp_subslot=temp_sub,
            notes=_as_str(_get(entry, "notes")),
        )
        plan.documents.append(doc)

        match = index.match(name)
        if match.path is not None:
            doc.files = [Rendition(label="default", path=match.path, primary=True)]
            doc.has_digital = True
            claimed.add(match.path)
            if match.status == "ambiguous":
                plan.issues.append(
                    MigrationIssue(
                        slug,
                        "multi-file-match",
                        f"{len(match.candidates)} candidates; picked {match.path}",
                    )
                )
        elif doc.has_digital:
            unmatched.append(doc)

        if _as_bool(_get(entry, "carried_to_india")):
            plan.bundle_suggestions.setdefault("carried-to-india", []).append(slug)

    _resolve_fuzzy(index, unmatched, claimed, plan)
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


def _resolve_fuzzy(
    index: FileIndex,
    unmatched: list[Document],
    claimed: set[str],
    plan: MigrationPlan,
) -> None:
    """Fuzzy-link docs the exact pass missed; auto-link only unambiguous best."""
    by_id = {doc.id: doc for doc in unmatched}
    proposals: dict[str, tuple[str, float]] = {}
    for doc in unmatched:
        best = index.fuzzy_best(doc.name, claimed)
        if best is not None:
            proposals[doc.id] = best

    contenders: dict[str, list[tuple[str, float]]] = {}
    for doc_id, (path, score) in proposals.items():
        contenders.setdefault(path, []).append((doc_id, score))

    for path, group in contenders.items():
        if len(group) == 1 and group[0][1] >= _FUZZY_AUTO:
            doc_id, score = group[0]
            by_id[doc_id].files = [Rendition(label="default", path=path, primary=True)]
            plan.issues.append(
                MigrationIssue(
                    doc_id, "fuzzy-match", f"auto-linked {path} ({score:.2f})"
                )
            )
        else:
            for doc_id, score in group:
                plan.issues.append(
                    MigrationIssue(
                        doc_id, "suggested-match", f"maybe {path} ({score:.2f})"
                    )
                )

    for doc in unmatched:
        if doc.id not in proposals:
            plan.issues.append(
                MigrationIssue(doc.id, "no-file-match", "no soft copy matched")
            )


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
