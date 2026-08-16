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

"""The v2 → journal export and its parity gate (REWRITE.md §6 R2, §7).

Every test here goes through the **real serialization path** — export to ops,
render them as JSONL bytes, parse them back, fold — rather than comparing
in-memory objects. That is where a migration actually loses things: a date
becoming a datetime, a set arriving in a different order, an integer coming back
as a string. Comparing the objects the exporter just built would agree with
itself and prove nothing.

The store used here is synthetic and built through the real `Store` API, on
`tmp_path`. The real ~948-document parity run is the user's to make, on their
own machine, read-only (`docs/dev/` and the standing real-store rule).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from dossier.config import Config
from dossier.export_journal import (
    check_parity,
    doc_fields,
    export,
    reading_payload,
    review_states,
    unexported_document_fields,
)
from dossier.journal import fold, parse_body
from dossier.model import (
    Bundle,
    Document,
    Location,
    ReconcileState,
    Rendition,
    SuggestionState,
)
from dossier.scan import ScanReading
from dossier.store import Store


def _reading(text: str = "", confidence: float = 0.875) -> ScanReading:
    return ScanReading(
        document_type="passport",
        issuer="Govt of India",
        holder_name="A Name",
        issue_date_text="01 JAN 2020",
        expiry_date_text="01 JAN 2030",
        document_number="Z1234567",
        is_validity_period=False,
        confidence=confidence,
        evidence="printed on page 1",
        model="qwen-vl",
        fingerprint="1234:5678",
        transcript=text,
        keywords=("passport", "india"),
    )


@pytest.fixture
def store(tmp_path: Path) -> Store:
    """A small but representative v2 store, built through the real API."""
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "history")
    store = Store(config)
    store.ensure_layout()

    store.save_locations(
        {
            "cert-file": Location(slug="cert-file", title="Cert File", notes="shelf 2"),
            "pouch": Location(slug="pouch", title="Passport Pouch"),
        }
    )
    store.save_bundles(
        {
            "us-visa": Bundle(
                slug="us-visa",
                title="US Visa",
                date=dt.date(2027, 3, 1),
                created=dt.datetime(2026, 1, 2, 3, 4, 5, tzinfo=dt.UTC),
                export_dir="Exports/us-visa",
                notes="interview set",
            )
        }
    )

    store.save(
        Document(
            id="passport",
            name="Passport (IN)",
            tags=["identity", "travel"],
            bundles=["us-visa"],
            issue_date=dt.date(2020, 1, 1),
            expiry_date=dt.date(2030, 1, 1),
            has_physical=True,
            has_digital=True,
            files=[
                Rendition(label="complete", path="Scans/passport.pdf", primary=True),
                Rendition(label="front", path="Scans/passport-front.jpg"),
            ],
            perm_location="pouch",
            perm_slot=1,
            perm_subslot=2,
            notes="renewed in 2020",
        )
    )
    store.save(
        Document(
            id="coc-2019",
            name="COC Certificate — 海事証明書",
            ignore_expiry=True,
            has_physical=True,
            perm_location="cert-file",
            perm_slot=8,
            temp_location="pouch",
            temp_slot=3,
        )
    )
    store.save(
        Document(id="coc-2025", name="COC Certificate 2025", supersedes="coc-2019")
    )

    store.save_reconcile(
        ReconcileState(
            dismissed={"Inbox/junk.pdf", "Inbox/other.pdf"},
            ignore=["Archive/**"],
            missing_ok={"Scans/lost.pdf": {"passport"}},
            folded={"Scans/a.pdf": {"Scans/a-copy.pdf"}},
            dup_dismissed={"Scans/b.pdf": {"Scans/c.pdf"}},
            succession_dismissed={"coc-2025\x00coc-2019"},
        )
    )
    state = SuggestionState()
    state.dismiss_key("passport:issue_date")
    state.dismiss_key("bundle:us-visa:coc-2025")
    store.save_suggestions(state)

    store.save_scans({"Scans/passport.pdf": _reading("full transcript here")})
    store.save_intake_cache({"Inbox/new-scan.jpg": _reading()})
    return store


def _roundtrip(store: Store):
    """Export → JSONL bytes → parse → fold, the path a real cutover takes."""
    exported = export(store)
    lines, torn = parse_body(exported.meta_body)
    assert torn is None, "an exported journal is never torn"
    enrich_lines, enrich_torn = parse_body(exported.enrich_body)
    assert enrich_torn is None
    return fold(lines + enrich_lines), exported


def test_parity_holds_across_the_whole_store(store: Store) -> None:
    """The gate itself: a folded export must match the store field-by-field."""
    folded, _ = _roundtrip(store)
    problems = check_parity(store, folded)
    assert not problems, "\n".join(str(p) for p in problems)


def test_the_export_carries_no_damage(store: Store) -> None:
    """Nothing the exporter writes may be unreadable or ambiguous."""
    folded, exported = _roundtrip(store)
    assert not folded.stats.has_anomalies
    assert folded.stats.malformed == 0
    assert folded.stats.opaque == 0
    assert folded.stats.folded == len(exported.meta) + len(exported.enrich)


def test_the_export_is_idempotent(store: Store) -> None:
    """Exporting an unchanged store twice gives identical bytes.

    What makes a cutover rehearsal free, and a re-run after a fix safe.
    """
    assert export(store).meta_body == export(store).meta_body
    assert export(store).enrich_body == export(store).enrich_body


def test_documents_survive_their_awkward_values(store: Store) -> None:
    """Dates, unicode, multi-file docs and temp locations round-trip intact."""
    folded, _ = _roundtrip(store)
    passport = folded.get("doc", "passport")
    assert passport is not None
    assert passport["expiry_date"] == "2030-01-01"
    assert passport["files"][0] == {
        "label": "complete",
        "path": "Scans/passport.pdf",
        "primary": True,
    }
    coc = folded.get("doc", "coc-2019")
    assert coc is not None
    assert coc["name"].endswith("海事証明書"), "unicode names survive JSONL"
    assert coc["ignore_expiry"] is True
    assert (coc["temp_location"], coc["temp_slot"]) == ("pouch", 3)


def test_settings_are_exported_as_ops(store: Store) -> None:
    """Losing these at cutover would silently reset scope and filing (§6 R2)."""
    folded, _ = _roundtrip(store)
    settings = folded.get("settings", "synced")
    assert settings is not None
    assert settings["expiry_threshold_days"] == store.config.expiry_threshold_days
    assert settings["reconcile_ignore"] == ["Archive/**"]
    assert settings["intake_filed"] == store.config.intake_filed


def test_review_decisions_become_per_key_state(store: Store) -> None:
    """Each reconcile suppression becomes one LWW state entry, namespaced."""
    folded, _ = _roundtrip(store)
    states = {
        key: value for (ent, key), value in folded.states.items() if ent == "review"
    }
    assert states["orphan:Inbox/junk.pdf"] == "dismissed"
    assert states["missing:passport:Scans/lost.pdf"] == "acked"
    assert states["dup:Scans/a.pdf:Scans/a-copy.pdf"] == "folded"
    assert states["dup:Scans/b.pdf:Scans/c.pdf"] == "dismissed"
    # The v2 key is "newer\x00older"; a NUL in a JSON string is legal but hostile.
    assert states["succession:coc-2025:coc-2019"] == "dismissed"
    assert not any("\x00" in key for key in states), "no NULs reach the journal"


def test_a_scan_reading_loses_its_float(store: Store) -> None:
    """The format bans floats (§3.2), so `confidence` becomes an integer permille.

    Renamed rather than rounded in place, so nothing can read it as a fraction.
    """
    payload = reading_payload(_reading(confidence=0.875))
    assert payload["confidence_permille"] == 875
    assert "confidence" not in payload

    folded, _ = _roundtrip(store)
    reading = folded.enrich[("reading", "Scans/passport.pdf")]
    assert reading["confidence_permille"] == 875
    assert reading["transcript"] == "full transcript here"
    assert folded.stats.malformed == 0, "a float would have been rejected at parse"


def test_parity_catches_a_lost_document(store: Store) -> None:
    """The gate has to fail when something is missing, or it guards nothing."""
    folded, _ = _roundtrip(store)
    del folded.entities[("doc", "passport")]
    problems = check_parity(store, folded)
    assert any(p.kind == "doc" and p.key == "passport" for p in problems)


def test_parity_catches_a_changed_field(store: Store) -> None:
    """…and when a value arrives subtly wrong."""
    folded, _ = _roundtrip(store)
    folded.entities[("doc", "passport")]["expiry_date"] = "2030-01-02"
    problems = check_parity(store, folded)
    assert any(p.field == "expiry_date" for p in problems), problems


def test_parity_catches_an_invented_entity(store: Store) -> None:
    """Both directions: an entity the journal made up is a failure too."""
    folded, _ = _roundtrip(store)
    folded.entities[("doc", "ghost")] = {"name": "Ghost"}
    problems = check_parity(store, folded)
    assert any(p.key == "ghost" for p in problems)


def test_an_empty_store_exports_and_passes_parity(tmp_path: Path) -> None:
    """A fresh store is a legitimate input — the rehearsal must not need data."""
    config = Config(syncthing_root=tmp_path, history_dir=tmp_path / "history")
    store = Store(config)
    store.ensure_layout()
    folded, _ = _roundtrip(store)
    assert not check_parity(store, folded)


def test_every_document_field_is_accounted_for() -> None:
    """A new field on the v2 model must be a decision, not a silent loss.

    This fails the moment someone adds an attribute to `Document`, forcing the
    question "does this belong in the journal?" now rather than at cutover.
    """
    assert unexported_document_fields() == set()


def test_review_state_keys_are_stable(store: Store) -> None:
    """Deterministic keys and order — v2 holds these in *sets*, whose iteration
    order would otherwise leak into the export and break idempotence."""
    reconcile = store.load_reconcile()
    first = review_states(reconcile)
    assert first == review_states(reconcile)
    assert list(first) == list(review_states(reconcile)), "ordering is stable too"
    assert first["orphan:Inbox/junk.pdf"] == "dismissed"


def test_doc_fields_omits_empty_values() -> None:
    """A sparse journal: absence and v2's `None` mean the same thing."""
    minimal = doc_fields(Document(id="x", name="X"))
    assert minimal == {"name": "X"}
