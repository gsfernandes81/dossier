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

"""Integrity and review diagnostics for the store (drives ``ds doctor``).

Checks: Syncthing conflict files, location-slug referential integrity, round-trip
lint (files that would change on next save), missing rendition files, and
ambiguous dates — 2-digit-year dates whose day/month order can't be pinned down.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from dossier import query
from dossier.config import Config
from dossier.model import Document, Location
from dossier.store import Store


@dataclass(frozen=True)
class Finding:
    check: str
    subject: str  # a document id, or a file path for conflict findings
    detail: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def by_check(self) -> dict[str, list[Finding]]:
        grouped: dict[str, list[Finding]] = {}
        for finding in self.findings:
            grouped.setdefault(finding.check, []).append(finding)
        return grouped


def run(store: Store, config: Config) -> Report:
    report = Report()
    for path in store.list_conflicts():
        report.findings.append(Finding("sync-conflict", path.name, str(path)))

    docs = store.load_all()
    locations = store.load_locations()
    report.findings += _check_location_refs(docs, locations)
    report.findings += _check_round_trip(store, docs)
    report.findings += _check_files(docs, config.syncthing_root)
    report.findings += _check_dates(docs)
    return report


def _check_location_refs(
    docs: list[Document], locations: dict[str, Location]
) -> list[Finding]:
    out: list[Finding] = []
    for doc in docs:
        for kind, slug in (("perm", doc.perm_location), ("temp", doc.temp_location)):
            if slug and slug not in locations:
                out.append(
                    Finding(
                        "location-ref",
                        doc.id,
                        f"{kind}_location {slug!r} is not a known location",
                    )
                )
    return out


def _check_round_trip(store: Store, docs: list[Document]) -> list[Finding]:
    out: list[Finding] = []
    for doc in docs:
        try:
            on_disk = store.document_path(doc.id).read_text(encoding="utf-8")
        except OSError:
            continue
        if store.serialize(doc) != on_disk:
            out.append(
                Finding(
                    "round-trip",
                    doc.id,
                    "file would change on next save (hand-edited or legacy format)",
                )
            )
    return out


def _check_files(docs: list[Document], root: Path) -> list[Finding]:
    out: list[Finding] = []
    for doc in docs:
        for rendition in doc.files:
            if not query.resolve_path(root, rendition.path).exists():
                out.append(
                    Finding(
                        "missing-file",
                        doc.id,
                        f"linked file not found: {rendition.path}",
                    )
                )
    return out


# -- ambiguous dates ---------------------------------------------------------

# An all-numeric 3-part date with a 2-digit year at the end position.
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2})\b")


def _readings(a: int, b: int, c: int) -> set[date]:
    """Distinct valid dates for a numeric token across plausible orderings.

    Guards both axes: day/month order (``DD-MM-YY`` vs ``MM-DD-YY``) AND year
    position (``21-08-23`` is 2023-08-21 as ``DD-MM-YY`` but 2021-08-23 as
    ``YY-MM-DD``). More than one distinct valid reading ⇒ ambiguous.
    """
    out: set[date] = set()
    for year, month, day in (
        (2000 + c, b, a),  # DD-MM-YY
        (2000 + c, a, b),  # MM-DD-YY
        (2000 + a, b, c),  # YY-MM-DD
    ):
        try:
            out.add(date(year, month, day))
        except ValueError:
            continue
    return out


def _ambiguous_tokens(name: str) -> list[str]:
    """Numeric 2-digit-year tokens with more than one plausible reading."""
    out: list[str] = []
    for match in _NUMERIC_DATE.finditer(name):
        a, b, c = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if len(_readings(a, b, c)) >= 2:
            out.append(match.group(0))
    return out


def _span_resolves_order(doc: Document) -> bool:
    # A well-ordered issue < expiry pair means the day/month reading is
    # self-consistent, so we don't flag it for order review.
    return (
        doc.issue_date is not None
        and doc.expiry_date is not None
        and doc.issue_date < doc.expiry_date
    )


def _check_dates(docs: list[Document]) -> list[Finding]:
    out: list[Finding] = []
    for doc in docs:
        if (
            doc.issue_date is not None
            and doc.expiry_date is not None
            and doc.issue_date > doc.expiry_date
        ):
            out.append(
                Finding(
                    "date-order",
                    doc.id,
                    f"issue {doc.issue_date} is after expiry {doc.expiry_date}",
                )
            )
        tokens = _ambiguous_tokens(doc.name)
        if tokens and not _span_resolves_order(doc):
            out.append(
                Finding(
                    "ambiguous-date",
                    doc.id,
                    f"2-digit-year date order unclear: {', '.join(tokens)}",
                )
            )
    return out
