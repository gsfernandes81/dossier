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

"""Tests for the flat-file store — round-tripping and the durability guards."""

from datetime import date
from pathlib import Path

import pytest

from dossier.config import Config
from dossier.errors import DocumentExistsError, StaleWriteError
from dossier.model import Document, Location, ReconcileState, Rendition
from dossier.store import TEMP_PREFIX, Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    cfg = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_history")
    st = Store(cfg)
    st.ensure_layout()
    return st


def _sample() -> Document:
    return Document(
        id="coc-card-2025",
        name='Certificate #2048 : the "real" one',
        tags=["marine", "marine/coc"],
        bundles=["us-visa"],
        issue_date=date(2025, 2, 10),
        expiry_date=date(2026, 9, 28),
        has_physical=True,
        has_digital=True,
        files=[Rendition(label="default", path="Marine/CoC Card.pdf", primary=True)],
        perm_location="cert-file-2048",
        perm_slot=8,
        notes="Some notes\nwith two lines.",
    )


def test_round_trip(store: Store):
    doc = _sample()
    store.save(doc)
    loaded = store.load("coc-card-2025")

    assert loaded.id == "coc-card-2025"
    assert loaded.name == doc.name
    assert loaded.tags == doc.tags
    assert loaded.bundles == doc.bundles
    assert loaded.issue_date == doc.issue_date
    assert loaded.expiry_date == doc.expiry_date
    assert loaded.has_physical and loaded.has_digital
    assert loaded.files == doc.files
    assert loaded.perm_location == "cert-file-2048"
    assert loaded.perm_slot == 8
    assert loaded.perm_subslot is None
    assert loaded.temp_location is None
    assert loaded.notes == "Some notes\nwith two lines."
    assert loaded.ignore_expiry is False  # default when absent
    assert loaded.supersedes is None


def test_supersedes_and_ignore_expiry_round_trip(store: Store):
    store.save(
        Document(
            id="passport-2026",
            name="Passport #2048",
            ignore_expiry=True,
            supersedes="passport-2016",
        )
    )
    text = store.document_path("passport-2026").read_text(encoding="utf-8")
    assert "ignore_expiry: true" in text
    assert '"passport-2016"' in text  # slug quoted like every other scalar

    loaded = store.load("passport-2026")
    assert loaded.ignore_expiry is True
    assert loaded.supersedes == "passport-2016"


def test_name_with_hash_is_quoted_and_survives(store: Store):
    # An unquoted ` #` would start a YAML comment and silently truncate the name.
    store.save(Document(id="x", name="Cert File #2048"))
    text = store.document_path("x").read_text(encoding="utf-8")
    assert '"Cert File #2048"' in text
    assert store.load("x").name == "Cert File #2048"


def test_serialization_is_deterministic(store: Store):
    store.save(_sample())
    first = store.document_path("coc-card-2025").read_bytes()
    store.save(store.load("coc-card-2025"))
    second = store.document_path("coc-card-2025").read_bytes()
    assert first == second


def test_conflicts_excluded_and_listed(store: Store):
    store.save(Document(id="real", name="Real"))
    conflict = store.config.documents_dir / "real.sync-conflict-20260101-abc.md"
    conflict.write_text('---\nname: "Dupe"\n---\n', encoding="utf-8")

    assert [d.id for d in store.load_all()] == ["real"]
    assert conflict in store.list_conflicts()


def test_atomic_write_leaves_no_temp_files(store: Store):
    store.save(_sample())
    leftovers = [
        p
        for p in store.config.documents_dir.iterdir()
        if p.name.startswith(TEMP_PREFIX)
    ]
    assert leftovers == []


def test_new_document_id_collision(store: Store):
    store.save(Document(id="dup", name="First"))
    # A fresh Document (source_hash is None) whose id already exists on disk.
    with pytest.raises(DocumentExistsError):
        store.save(Document(id="dup", name="Second"))


def test_stale_write_is_rejected(store: Store):
    store.save(Document(id="race", name="v1"))
    loaded = store.load("race")
    # Another device (Syncthing) / a hand-edit changes the file underneath us.
    store.document_path("race").write_text('---\nname: "v2"\n---\n', encoding="utf-8")
    loaded.name = "v3"
    with pytest.raises(StaleWriteError):
        store.save(loaded)


def test_backup_written_on_overwrite(store: Store):
    store.save(Document(id="h", name="v1"))  # create: no backup
    loaded = store.load("h")
    loaded.name = "v2"
    store.save(loaded)  # overwrite: backs up v1 first

    backups = list((store.config.history_dir / "h").glob("*.md"))
    assert len(backups) == 1
    assert "v1" in backups[0].read_text(encoding="utf-8")


def test_locations_round_trip_with_hash_title(store: Store):
    store.save_locations(
        {"cert-file-2048": Location(slug="cert-file-2048", title="Cert File #2048")}
    )
    loaded = store.load_locations()
    assert loaded["cert-file-2048"].title == "Cert File #2048"


def test_reconcile_sidecar_round_trips_paths_with_slashes(store: Store):
    state = ReconcileState(
        dismissed={"Wallpapers/bg.jpg", "a.txt"},
        ignore=["Wallpapers/*"],
        missing_ok={"Marine/PSCRB Cert gone.pdf": {"pscrb"}},
        folded={"Marine/CoC Card.pdf": {"Applications/2024/CoC Card.pdf"}},
    )
    store.save_reconcile(state)
    loaded = store.load_reconcile()
    assert loaded.dismissed == {"Wallpapers/bg.jpg", "a.txt"}
    assert loaded.ignore == ["Wallpapers/*"]
    assert loaded.missing_ok == {"Marine/PSCRB Cert gone.pdf": {"pscrb"}}
    assert loaded.folded == {"Marine/CoC Card.pdf": {"Applications/2024/CoC Card.pdf"}}


def test_reconcile_sidecar_empty_and_absent(store: Store):
    assert store.load_reconcile() == ReconcileState()  # absent file → empty
    store.save_reconcile(ReconcileState())
    assert store.load_reconcile() == ReconcileState()  # empty round-trips


def test_reconcile_sidecar_write_is_deterministic(store: Store):
    state = ReconcileState(dismissed={"b.jpg", "a.jpg"}, ignore=["z/*", "y/*"])
    store.save_reconcile(state)
    first = store.config.reconcile_path.read_bytes()
    store.save_reconcile(store.load_reconcile())
    assert store.config.reconcile_path.read_bytes() == first  # sorted, stable
