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

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from dossier import store as store_module
from dossier.config import Config
from dossier.errors import DocumentExistsError, StaleWriteError, StoreError
from dossier.model import Bundle, Document, Location, ReconcileState, Rendition
from dossier.store import TEMP_PREFIX, Store, atomic_write_bytes, unique_id


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


def test_serialize_format_conventions(store: Store):
    # Lock the on-disk conventions the format guarantees, so a YAML-backend change
    # (e.g. the ruamel → PyYAML swap) can't silently alter how files are written.
    doc = Document(
        id="p",
        name="Hash #1",
        tags=["marine"],
        issue_date=date(2020, 1, 5),
        has_physical=True,
        has_digital=False,
        perm_slot=3,  # perm/temp locations stay None
    )
    out = store.serialize(doc)
    assert out.startswith('---\nname: "Hash #1"\n')  # strings double-quoted
    assert '- "marine"\n' in out  # list items quoted too
    assert "issue_date: 2020-01-05\n" in out  # dates: unquoted ISO
    assert "has_physical: true\n" in out and "has_digital: false\n" in out  # bools
    assert "perm_slot: 3\n" in out  # ints bare
    assert "temp_location:\n" in out and "null" not in out  # None -> empty scalar


def _pure_serialize(doc: Document) -> str:
    """Serialize via PyYAML's pure-Python SafeDumper (the no-libyaml fallback)."""
    import yaml

    from dossier.store import (
        _frontmatter_from_document,
        _Quoted,
        _represent_none,
        _represent_quoted,
    )

    class PureDumper(yaml.SafeDumper):
        pass

    PureDumper.add_representer(_Quoted, _represent_quoted)
    PureDumper.add_representer(type(None), _represent_none)
    front = yaml.dump(
        _frontmatter_from_document(doc),
        Dumper=PureDumper,
        default_flow_style=False,
        allow_unicode=True,
        width=4096,
        sort_keys=False,
        indent=2,
    )
    if not front.endswith("\n"):
        front += "\n"
    body = f"{doc.notes}\n" if doc.notes else ""
    return f"---\n{front}---\n{body}"


@pytest.mark.skipif(
    not store_module.HAS_LIBYAML,
    reason="no libyaml here — C and pure are the same path",
)
def test_serialize_c_dumper_matches_pure(store: Store):
    # A file written on a libyaml device (C dumper) must be byte-identical to one
    # written on a pure-Python device — otherwise mixed-device writes churn Syncthing.
    docs = [
        _sample(),
        Document(id="empty", name="", tags=[], notes=""),
        Document(id="u", name="Ünïçodé — #hash: yes 12:30", supersedes="x"),
    ]
    for doc in docs:
        assert store.serialize(doc) == _pure_serialize(doc)


def test_libyaml_hint_self_resolves(monkeypatch: pytest.MonkeyPatch):
    # Active libyaml -> silent (the startup notice/profile hint just disappears).
    monkeypatch.setattr(store_module, "HAS_LIBYAML", True)
    assert store_module.libyaml_hint() is None
    # Pure fallback -> a one-line nudge that names the fix.
    monkeypatch.setattr(store_module, "HAS_LIBYAML", False)
    hint = store_module.libyaml_hint()
    assert hint is not None and "libyaml" in hint


def test_conflicts_excluded_and_listed(store: Store):
    store.save(Document(id="real", name="Real"))
    conflict = store.config.documents_dir / "real.sync-conflict-20260101-abc.md"
    conflict.write_text('---\nname: "Dupe"\n---\n', encoding="utf-8")

    assert [d.id for d in store.load_all()] == ["real"]
    assert conflict in store.list_conflicts()


def test_load_all_parallel_matches_serial(store: Store):
    # load_all reads files in parallel then parses serially; it must return the
    # same documents, in the same order, as reading them one at a time.
    for i in range(25):
        store.save(Document(id=f"doc-{i:02d}", name=f"Doc {i}", tags=[f"t{i}"]))
    paths = list(store.iter_document_paths())
    serial = [store._read(p) for p in paths]
    parallel = store.load_all()
    assert [d.id for d in parallel] == [d.id for d in serial]
    assert [d.name for d in parallel] == [d.name for d in serial]
    assert [d.source_hash for d in parallel] == [d.source_hash for d in serial]


def test_load_all_propagates_parse_errors(store: Store):
    # A malformed file must still raise from the parallel path, not be dropped —
    # matching the old serial comprehension's behaviour.
    store.save(Document(id="ok", name="OK"))
    bad = store.config.documents_dir / "bad.md"
    bad.write_text("---\n- 1\n- 2\n---\n", encoding="utf-8")  # a list, not a mapping
    with pytest.raises(StoreError):
        store.load_all()


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


def test_bundles_round_trip_hierarchical_slug(store: Store):
    slug = "travel/india-2024"  # a '/' in the TOML key must survive round-trip
    store.save_bundles({slug: Bundle(slug=slug, title="India 2024")})
    loaded = store.load_bundles()
    assert slug in loaded
    assert loaded[slug].title == "India 2024"


def test_bundle_template_field_round_trips(store: Store):
    slug = "travel/india-2024"
    store.save_bundles(
        {slug: Bundle(slug=slug, title="India", template="schengen-visa")}
    )
    assert store.load_bundles()[slug].template == "schengen-visa"


def test_load_templates_parses_requirements_tolerantly(store: Store):
    store.config.templates_path.write_text(
        "[schengen-visa]\n"
        'title = "Schengen visa"\n'
        "[[schengen-visa.require]]\n"
        'label = "passport"\n'
        'match = ["passport"]\n'
        "min_valid_days = 180\n"
        "[[schengen-visa.require]]\n"
        'label = "photo"\n'
        "count = 2\n"
        "optional = true\n"
        "[[schengen-visa.require]]\n"  # junk: no label → dropped, not an error
        'match = ["ignored"]\n',
        encoding="utf-8",
    )
    templates = store.load_templates()
    tpl = templates["schengen-visa"]
    assert tpl.title == "Schengen visa"
    assert [r.label for r in tpl.requires] == ["passport", "photo"]  # labelless dropped
    passport, photo = tpl.requires
    assert passport.match == ("passport",) and passport.min_valid_days == 180
    assert photo.count == 2 and photo.optional is True
    assert photo.aliases == ("photo",)  # defaults to the label


def test_load_templates_absent_is_empty(store: Store):
    assert store.load_templates() == {}


def test_bundles_persist_date_and_stamp_created(tmp_path: Path):
    cfg = Config(syncthing_root=tmp_path, history_dir=tmp_path / "_h")
    fixed = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    store = Store(cfg, now=lambda: fixed)  # injected clock
    store.ensure_layout()
    slug = "travel/india-2024"
    store.save_bundles({slug: Bundle(slug=slug, title="India", date=date(2024, 3, 11))})

    loaded = store.load_bundles()[slug]
    assert loaded.date == date(2024, 3, 11)  # native TOML date round-trips
    assert loaded.created == fixed  # stamped on first save

    # A later save with a different clock must NOT re-stamp an existing created.
    later = Store(cfg, now=lambda: datetime(2027, 1, 1, tzinfo=UTC))
    later.save_bundles(later.load_bundles())
    assert later.load_bundles()[slug].created == fixed


def test_reconcile_sidecar_round_trips_paths_with_slashes(store: Store):
    state = ReconcileState(
        dismissed={"Wallpapers/bg.jpg", "a.txt"},
        ignore=["Wallpapers/*"],
        missing_ok={"Marine/PSCRB Cert gone.pdf": {"pscrb"}},
        folded={"Marine/CoC Card.pdf": {"Applications/2024/CoC Card.pdf"}},
        dup_dismissed={"Forms/Page 1.pdf": {"Forms/Page 2.pdf"}},
    )
    store.save_reconcile(state)
    loaded = store.load_reconcile()
    assert loaded.dismissed == {"Wallpapers/bg.jpg", "a.txt"}
    assert loaded.ignore == ["Wallpapers/*"]
    assert loaded.missing_ok == {"Marine/PSCRB Cert gone.pdf": {"pscrb"}}
    assert loaded.folded == {"Marine/CoC Card.pdf": {"Applications/2024/CoC Card.pdf"}}
    assert loaded.dup_dismissed == {"Forms/Page 1.pdf": {"Forms/Page 2.pdf"}}


def test_dismissing_a_cluster_settles_it_without_hiding_its_files(store: Store):
    """A false-positive verdict must not do what folding does.

    Folding asserts the copies *are* the same file and hides them from the orphan
    list; using it to silence a false positive would make a genuinely different
    document — one still awaiting adoption — vanish from the list that would have
    prompted you to adopt it.
    """
    state = ReconcileState(dup_dismissed={"a.pdf": {"b.pdf"}})
    assert state.covers("a.pdf", ["b.pdf"])  # settled: stays off the duplicates tab
    assert "b.pdf" not in state.suppressed_orphans()  # but still adoptable
    assert ReconcileState(folded={"a.pdf": {"b.pdf"}}).suppressed_orphans() == {"b.pdf"}

    # A new copy is new evidence, so the cluster comes back for a fresh look —
    # the same rule folding follows.
    assert not state.covers("a.pdf", ["b.pdf", "c.pdf"])


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


def test_unique_id_is_case_insensitive(store: Store):
    """A new id must not collide with an existing one under case-folding.

    On a case-sensitive FS (Linux/Termux) the old exact-match guard let
    ``passport`` coexist with ``Passport``; Syncthing then delivered that pair as a
    name collision to a case-insensitive device. The divergence would only surface
    on the Linux CI leg — that's what this test pins.
    """
    store.save(Document(id="Passport", name="Passport"))
    assert unique_id(store, "passport") == "passport-2"  # folds onto "Passport"
    assert unique_id(store, "PASSPORT") == "PASSPORT-2"  # still folds, keeps case
    assert unique_id(store, "licence") == "licence"  # no clash → unchanged


def test_atomic_write_uses_a_same_directory_temp(
    store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The temp file lives in the target's own directory, never ``$TMPDIR``.

    On Termux ``$TMPDIR`` is a separate mount, so a cross-directory ``os.replace``
    would raise ``EXDEV``; keeping the temp beside the target makes the rename
    atomic on every platform. Runs identically on each CI leg.
    """
    monkeypatch.setenv("TMPDIR", str(tmp_path / "other-mount"))
    seen: dict[str, str] = {}
    real_mkstemp = store_module.tempfile.mkstemp

    def recording_mkstemp(*, prefix: str, dir: str) -> tuple[int, str]:
        seen["dir"] = dir  # atomic_write_bytes only ever passes prefix + dir
        return real_mkstemp(prefix=prefix, dir=dir)

    monkeypatch.setattr(store_module.tempfile, "mkstemp", recording_mkstemp)
    target = store.config.meta_dir / "probe.bin"
    atomic_write_bytes(target, b"data")
    assert seen["dir"] == str(target.parent)  # same dir as target, not TMPDIR
    assert target.read_bytes() == b"data"


def test_atomic_write_retries_a_transient_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """A cloud-sync FS (Proton Drive) briefly locks the target; the replace retries."""
    real_replace = store_module.os.replace
    calls = {"n": 0}

    def flaky(src: str, dst: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(5, "Access is denied")  # first attempt: locked
        real_replace(src, dst)

    monkeypatch.setattr(store_module.os, "replace", flaky)
    monkeypatch.setattr(store_module.time, "sleep", lambda _s: None)  # no real waiting

    target = tmp_path / "probe.bin"
    atomic_write_bytes(target, b"data")
    assert calls["n"] == 2  # retried once, then succeeded
    assert target.read_bytes() == b"data"


def test_atomic_write_raises_after_a_persistent_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    def denied(_src: str, _dst: str) -> None:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(store_module.os, "replace", denied)
    monkeypatch.setattr(store_module.time, "sleep", lambda _s: None)

    with pytest.raises(PermissionError):
        atomic_write_bytes(tmp_path / "probe.bin", b"data")
    # the temp file is cleaned up, not left behind
    assert not list(tmp_path.glob(f"{TEMP_PREFIX}*"))


# -- history / restore -------------------------------------------------------


def _save_notes(store: Store, doc_id: str, notes: str) -> None:
    """Save a document with new notes, reading it fresh so the hash check passes."""
    doc = store.load(doc_id)
    doc.notes = notes
    store.save(doc)


def test_history_lists_prior_versions_newest_first(store: Store):
    store.save(Document(id="passport", name="Passport", notes="v1"))
    assert store.history("passport") == []  # a first save overwrites nothing

    _save_notes(store, "passport", "v2")
    _save_notes(store, "passport", "v3")
    entries = store.history("passport")
    assert [e.doc_id for e in entries] == ["passport", "passport"]
    assert entries[0].saved_at > entries[1].saved_at  # newest first
    # Each archive holds the content it *replaced*, not the content that replaced it.
    assert "v2" in entries[0].path.read_text(encoding="utf-8")
    assert "v1" in entries[1].path.read_text(encoding="utf-8")


def test_restore_brings_back_a_version_and_archives_the_one_it_replaced(store: Store):
    # "Undo is always undoable": restoring is an ordinary save, so the version it
    # displaces is archived in turn and nothing is ever lost.
    store.save(Document(id="passport", name="Passport", notes="v1"))
    _save_notes(store, "passport", "v2")

    restored = store.restore(store.history("passport")[0])
    assert restored.notes == "v1"
    assert store.load("passport").notes == "v1"

    entries = store.history("passport")
    assert "v2" in entries[0].path.read_text(encoding="utf-8"), "the undo is undoable"
    again = store.restore(entries[0])
    assert again.notes == "v2"


def test_restore_takes_only_content_from_the_archive(store: Store):
    # The id comes from the live filename and the stale-write hash from the live
    # file, so a restore can't resurrect a stale id or trip its own hash check.
    store.save(Document(id="passport", name="Passport", notes="v1"))
    _save_notes(store, "passport", "v2")
    entry = store.history("passport")[0]
    entry.path.write_text(
        entry.path.read_text(encoding="utf-8").replace('id: "passport"', 'id: "bogus"'),
        encoding="utf-8",
    )
    assert store.restore(entry).id == "passport"


def test_restore_recreates_a_document_deleted_since(store: Store):
    store.save(Document(id="passport", name="Passport", notes="v1"))
    _save_notes(store, "passport", "v2")
    entry = store.history("passport")[0]
    store.document_path("passport").unlink()
    assert store.restore(entry).notes == "v1"
    assert store.load("passport").notes == "v1"


def test_history_ignores_files_that_are_not_stamps(store: Store):
    store.save(Document(id="passport", name="Passport", notes="v1"))
    _save_notes(store, "passport", "v2")
    (store.config.history_dir / "passport" / "notes-to-self.md").write_text("hi")
    assert len(store.history("passport")) == 1  # the stray file is skipped, not fatal


def test_history_is_empty_for_an_unknown_document(store: Store):
    assert store.history("nope") == []
