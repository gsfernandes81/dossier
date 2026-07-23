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
from dossier.model import Bundle, Document, Requirement, Template
from dossier.preparedness import EventStatus, ReadyState
from dossier.scan import ScanReading

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


# -- matches_requirement -----------------------------------------------------


def test_matches_requirement_by_name_substring():
    doc = Document(id="p", name="British Passport 2024")
    assert preparedness.matches_requirement(doc, Requirement("passport", ("passport",)))


def test_matches_requirement_by_scan_document_type():
    doc = Document(id="d", name="scan001")
    reading = ScanReading.from_payload({"document_type": "ENG-1 Medical"}, model="m")
    req = Requirement("eng1", ("eng-1",))
    assert preparedness.matches_requirement(doc, req, reading)
    assert not preparedness.matches_requirement(doc, req)  # nothing without the reading


def test_matches_requirement_hierarchical_tag_alias():
    doc = Document(id="c", name="Cert", tags=["marine/coc"])
    assert preparedness.matches_requirement(doc, Requirement("x", ("marine/coc",)))
    # a /-alias matches tags only — not a name that happens to contain the text
    named = Document(id="n", name="marine/foo in the name")
    assert not preparedness.matches_requirement(
        named, Requirement("x", ("marine/foo",))
    )


def test_matches_requirement_defaults_alias_to_the_label():
    doc = Document(id="p", name="My Passport")
    assert preparedness.matches_requirement(
        doc, Requirement("passport")
    )  # no match given


# -- check_bundle ------------------------------------------------------------


def test_check_bundle_gathered_missing_and_problem():
    docs = [
        Document(
            id="pp", name="Passport", bundles=["trip"], expiry_date=date(2030, 1, 1)
        ),
        Document(
            id="coc", name="CoC Card", bundles=["trip"], expiry_date=date(2026, 3, 1)
        ),  # lapses before the event
    ]
    bundle = Bundle(slug="trip", title="Trip", date=EVENT)
    template = Template(
        slug="trip",
        title="Trip",
        requires=(
            Requirement("passport", ("passport",)),
            Requirement("coc", ("coc",)),
            Requirement("photo", ("photo",)),  # no member matches
        ),
    )
    readiness = preparedness.check_bundle(
        bundle, template, docs, {}, today=TODAY, margin_days=30
    )
    by_label = {c.requirement.label: c for c in readiness.checks}
    assert by_label["passport"].state is ReadyState.GATHERED
    assert by_label["coc"].state is ReadyState.PROBLEM  # expired-by-event
    assert by_label["photo"].state is ReadyState.MISSING
    assert not readiness.ready
    assert "1/3 ready" in readiness.summary


def test_check_bundle_optional_missing_does_not_block_ready():
    docs = [
        Document(
            id="pp", name="Passport", bundles=["trip"], expiry_date=date(2030, 1, 1)
        )
    ]
    bundle = Bundle(slug="trip", title="Trip", date=EVENT)
    template = Template(
        slug="trip",
        title="Trip",
        requires=(
            Requirement("passport", ("passport",)),
            Requirement("photo", ("photo",), optional=True),
        ),
    )
    readiness = preparedness.check_bundle(
        bundle, template, docs, {}, today=TODAY, margin_days=30
    )
    assert readiness.ready  # the optional-missing photo doesn't block


def test_check_bundle_reports_extras_and_candidates():
    docs = [
        Document(
            id="pp", name="Passport", bundles=["trip"], expiry_date=date(2030, 1, 1)
        ),
        Document(id="misc", name="Random Note", bundles=["trip"]),  # matches nothing
        Document(id="pp2", name="Old Passport", bundles=[]),  # matches, not a member
    ]
    bundle = Bundle(slug="trip", title="Trip", date=None)  # undated: presence only
    template = Template(
        slug="trip", title="Trip", requires=(Requirement("passport", ("passport",)),)
    )
    readiness = preparedness.check_bundle(
        bundle, template, docs, {}, today=TODAY, margin_days=30
    )
    assert readiness.extras == ("misc",)
    assert "pp2" in readiness.checks[0].candidates
    assert readiness.checks[0].state is ReadyState.GATHERED  # undated → no problem
