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

"""Tests for the domain models."""

from datetime import date

from dossier.model import Document, ExpiryStatus, Rendition


def test_effective_location_prefers_temp():
    doc = Document(perm_location="cert-file", perm_slot=2, temp_location="ship")
    assert doc.is_temp_located
    assert doc.effective_location == "ship"
    assert doc.effective_slot is None  # temp location has no slot set


def test_effective_location_falls_back_to_permanent():
    doc = Document(perm_location="cert-file", perm_slot=2, perm_subslot=3)
    assert not doc.is_temp_located
    assert doc.effective_location == "cert-file"
    assert doc.effective_slot == 2
    assert doc.effective_subslot == 3


def test_primary_rendition():
    complete = Rendition(label="complete", path="a.pdf")
    front_back = Rendition(label="front-and-back", path="b.pdf", primary=True)
    assert Document(files=[complete, front_back]).primary_rendition() is front_back
    assert Document(files=[complete]).primary_rendition() is complete
    assert Document().primary_rendition() is None


def test_expiry_status_boundaries():
    today = date(2026, 7, 21)
    assert Document().expiry_status(today, 90) is ExpiryStatus.NONE

    yesterday = Document(expiry_date=date(2026, 7, 20))
    assert yesterday.expiry_status(today, 90) is ExpiryStatus.EXPIRED

    on_today = Document(expiry_date=today)
    assert on_today.expiry_status(today, 90) is ExpiryStatus.EXPIRING

    on_threshold = Document(expiry_date=date(2026, 10, 19))  # today + 90 days
    assert on_threshold.expiry_status(today, 90) is ExpiryStatus.EXPIRING

    beyond = Document(expiry_date=date(2027, 1, 1))
    assert beyond.expiry_status(today, 90) is ExpiryStatus.OK
