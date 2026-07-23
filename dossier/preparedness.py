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

"""Preparedness: am I ready, against the date I need the document?

A bundle carries the *event* date (a trip, a joining date, an application). This
pure engine answers whether a document is still valid **then**, not just today —
"the passport expires before the trip", "the ENG-1 lapses before you join". The
key move needs no new rule machinery: validity-at-the-event is exactly the
existing expiry model (:meth:`Document.expiry_status`) evaluated against the event
date, plus an optional required-validity floor (the "valid ≥ 6 months past the
trip" rule an application imposes, carried per requirement in Phase 10 slice 2).

I/O-free and TUI-free, like :mod:`dossier.organize` — the CLI (`ds expiring`), the
expiry watch, and the bundles readiness checklist all consume it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from dossier.model import Bundle, Document, Requirement, Template
from dossier.query import superseded_ids
from dossier.scan import ScanReading


class EventStatus(StrEnum):
    """A document's validity relative to a bundle's event date."""

    OK = "ok"
    EXPIRING = "expiring-by-event"  # valid now, lapses on/near the event
    EXPIRED = "expired-by-event"  # already lapses before the event
    UNKNOWN = "unknown"  # no expiry date (or ignored) — can't judge


def event_status(
    doc: Document, event: date, *, margin_days: int, min_valid_days: int = 0
) -> EventStatus:
    """Whether ``doc`` is valid at ``event`` (plus a required-validity floor).

    ``min_valid_days`` is how long past the event it must *stay* valid (an
    application's rule, e.g. a passport 6 months beyond a trip). ``margin_days`` is
    the usual warn window — the same knob every other expiry surface uses — so a doc
    that lapses just after the cutoff reads as expiring rather than silently OK.
    """
    if doc.expiry_date is None or doc.ignore_expiry:
        return EventStatus.UNKNOWN
    cutoff = event + timedelta(days=min_valid_days)
    if doc.expiry_date < cutoff:
        return EventStatus.EXPIRED
    if doc.expiry_date < cutoff + timedelta(days=margin_days):
        return EventStatus.EXPIRING
    return EventStatus.OK


@dataclass(frozen=True)
class EventFlag:
    """A member document that won't be valid when its bundle needs it."""

    doc_id: str
    bundle_slug: str
    event: date
    status: EventStatus  # EXPIRING or EXPIRED (OK/UNKNOWN are never flagged)


_SEVERITY = {EventStatus.EXPIRED: 0, EventStatus.EXPIRING: 1}


def event_flags(
    docs: list[Document],
    bundles: Iterable[Bundle],
    *,
    today: date,
    margin_days: int,
) -> dict[str, list[EventFlag]]:
    """Per-document flags: members of a *future* dated bundle that lapse by then.

    Only future events count (a past ``date`` is a record, not a deadline);
    superseded and ignored documents never nag. Each document's flags are worst
    (soonest-expired) first, so a caller can take ``[0]`` as the headline.
    """
    superseded = superseded_ids(docs)
    out: dict[str, list[EventFlag]] = {}
    for bundle in bundles:
        if bundle.date is None or bundle.date < today:
            continue
        for doc in docs:
            if bundle.slug not in doc.bundles or doc.id in superseded:
                continue
            status = event_status(doc, bundle.date, margin_days=margin_days)
            if status in (EventStatus.EXPIRED, EventStatus.EXPIRING):
                out.setdefault(doc.id, []).append(
                    EventFlag(doc.id, bundle.slug, bundle.date, status)
                )
    for flags in out.values():
        flags.sort(key=lambda flag: (_SEVERITY[flag.status], flag.event))
    return out


# -- bundle templates (readiness) --------------------------------------------


class ReadyState(StrEnum):
    """A requirement's state within a bundle."""

    GATHERED = "gathered"  # enough valid members
    PROBLEM = "problem"  # matched, but a member lapses by the event
    MISSING = "missing"  # too few members match


def matches_requirement(
    doc: Document, req: Requirement, reading: ScanReading | None = None
) -> bool:
    """Whether ``doc`` satisfies ``req`` — any alias hits its name / tags / scan type.

    A plain alias is a casefolded substring over ``name + tags + document_type``
    (works today: names carry the type words). An alias with a ``/`` is matched
    hierarchically against tags only (``marine`` covers ``marine/coc``), so it
    sharpens as intake writes tags without over-matching a name.
    """
    tags_cf = [tag.casefold() for tag in doc.tags]
    parts = [doc.name, *doc.tags]
    if reading is not None and reading.document_type:
        parts.append(reading.document_type)
    haystack = " ".join(parts).casefold()
    for alias in req.aliases:
        needle = alias.casefold().strip()
        if not needle:
            continue
        if "/" in needle:
            if any(tag == needle or tag.startswith(needle + "/") for tag in tags_cf):
                return True
        elif needle in haystack:
            return True
    return False


@dataclass(frozen=True)
class RequirementCheck:
    requirement: Requirement
    matched: tuple[str, ...]  # member ids satisfying it
    statuses: dict[str, EventStatus]  # per matched member, when the bundle is dated
    candidates: tuple[str, ...]  # store docs that match but aren't members yet

    @property
    def state(self) -> ReadyState:
        if len(self.matched) < self.requirement.count:
            return ReadyState.MISSING
        problem = (EventStatus.EXPIRED, EventStatus.EXPIRING)
        if any(status in problem for status in self.statuses.values()):
            return ReadyState.PROBLEM
        return ReadyState.GATHERED


@dataclass(frozen=True)
class BundleReadiness:
    bundle: Bundle
    template: Template
    checks: tuple[RequirementCheck, ...]
    extras: tuple[str, ...]  # member ids matching no requirement

    @property
    def ready(self) -> bool:
        """Every *non-optional* requirement gathered."""
        return all(
            check.state is ReadyState.GATHERED
            for check in self.checks
            if not check.requirement.optional
        )

    @property
    def summary(self) -> str:
        gathered = sum(1 for c in self.checks if c.state is ReadyState.GATHERED)
        problems = sum(1 for c in self.checks if c.state is ReadyState.PROBLEM)
        missing = sum(1 for c in self.checks if c.state is ReadyState.MISSING)
        parts = [f"{gathered}/{len(self.checks)} ready"]
        if problems:
            parts.append(f"{problems} problem")
        if missing:
            parts.append(f"{missing} missing")
        return " · ".join(parts)


def check_bundle(
    bundle: Bundle,
    template: Template,
    docs: list[Document],
    readings: dict[str, ScanReading],
    *,
    today: date,
    margin_days: int,
) -> BundleReadiness:
    """Measure ``bundle``'s members against ``template`` (event-aware when dated)."""
    superseded = superseded_ids(docs)
    members = [d for d in docs if bundle.slug in d.bundles and d.id not in superseded]
    event = bundle.date if (bundle.date is not None and bundle.date >= today) else None

    checks: list[RequirementCheck] = []
    claimed: set[str] = set()
    for req in template.requires:
        matched: list[str] = []
        statuses: dict[str, EventStatus] = {}
        for doc in members:
            if not matches_requirement(doc, req, readings.get(doc.id)):
                continue
            matched.append(doc.id)
            if event is not None:
                statuses[doc.id] = event_status(
                    doc,
                    event,
                    margin_days=margin_days,
                    min_valid_days=req.min_valid_days,
                )
        claimed.update(matched)
        candidates = tuple(
            d.id
            for d in docs
            if bundle.slug not in d.bundles
            and d.id not in superseded
            and matches_requirement(d, req, readings.get(d.id))
        )
        checks.append(RequirementCheck(req, tuple(matched), statuses, candidates))

    extras = tuple(d.id for d in members if d.id not in claimed)
    return BundleReadiness(bundle, template, tuple(checks), extras)
