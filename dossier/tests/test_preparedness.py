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

"""Tests for the preparedness engine (event-aware validity)."""

from datetime import date, timedelta

from dossier import preparedness
from dossier.model import Bundle, Document
from dossier.preparedness import EventStatus

EVENT = date(2026, 6, 1)
TODAY = date(2026, 1, 1)


def _passport(
    expiry: date | None,
    *,
    bundles: list[str] | None = None,
    ignore_expiry: bool = False,
) -> Document:
    return Document(
        id="pp",
        name="Passport",
        expiry_date=expiry,
        bundles=bundles or [],
        ignore_expiry=ignore_expiry,
    )


# -- event_status ------------------------------------------------------------


def test_event_status_ok_when_valid_past_the_event():
    assert (
        preparedness.event_status(_passport(date(2030, 1, 1)), EVENT, margin_days=90)
        is EventStatus.OK
    )


def test_event_status_expired_when_it_lapses_before_the_event():
    assert (
        preparedness.event_status(
            _passport(EVENT - timedelta(days=1)), EVENT, margin_days=0
        )
        is EventStatus.EXPIRED
    )


def test_event_status_boundary_expiry_on_the_event_is_ok():
    # Expiry == cutoff is not yet expired (last valid day = the event day).
    assert (
        preparedness.event_status(_passport(EVENT), EVENT, margin_days=0)
        is EventStatus.OK
    )


def test_event_status_expiring_within_the_margin():
    soon = EVENT + timedelta(days=30)
    assert (
        preparedness.event_status(_passport(soon), EVENT, margin_days=90)
        is EventStatus.EXPIRING
    )


def test_event_status_min_valid_days_shifts_the_cutoff():
    # Valid a month past the event, but the application needs 6 months → expired.
    doc = _passport(EVENT + timedelta(days=30))
    assert (
        preparedness.event_status(doc, EVENT, margin_days=0, min_valid_days=180)
        is EventStatus.EXPIRED
    )


def test_event_status_unknown_without_expiry_or_when_ignored():
    assert (
        preparedness.event_status(Document(id="a", name="A"), EVENT, margin_days=90)
        is EventStatus.UNKNOWN
    )
    ignored = _passport(EVENT - timedelta(days=1), ignore_expiry=True)
    assert (
        preparedness.event_status(ignored, EVENT, margin_days=90) is EventStatus.UNKNOWN
    )


# -- event_flags -------------------------------------------------------------


def test_event_flags_flags_a_future_member_that_lapses_by_then():
    docs = [_passport(EVENT - timedelta(days=1), bundles=["trip"])]
    bundles = [Bundle(slug="trip", title="Trip", date=EVENT)]
    flags = preparedness.event_flags(docs, bundles, today=TODAY, margin_days=30)
    assert flags["pp"][0].status is EventStatus.EXPIRED
    assert flags["pp"][0].bundle_slug == "trip"
    assert flags["pp"][0].event == EVENT


def test_event_flags_ignores_a_valid_member_and_a_past_event():
    valid = [_passport(date(2030, 1, 1), bundles=["trip"])]
    future = [Bundle(slug="trip", title="Trip", date=EVENT)]
    assert preparedness.event_flags(valid, future, today=TODAY, margin_days=30) == {}

    lapsing = [_passport(date(2019, 1, 1), bundles=["old"])]
    past = [Bundle(slug="old", title="Old", date=date(2018, 1, 1))]
    assert preparedness.event_flags(lapsing, past, today=TODAY, margin_days=30) == {}


def test_event_flags_excludes_superseded_members():
    docs = [
        Document(
            id="old",
            name="Old CoC",
            expiry_date=EVENT - timedelta(days=1),
            bundles=["trip"],
        ),
        Document(
            id="new", name="New CoC", expiry_date=date(2030, 1, 1), supersedes="old"
        ),
    ]
    bundles = [Bundle(slug="trip", title="Trip", date=EVENT)]
    flags = preparedness.event_flags(docs, bundles, today=TODAY, margin_days=30)
    assert "old" not in flags  # renewed away — never nags
