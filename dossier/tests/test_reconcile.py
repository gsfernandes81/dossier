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

"""Tests for the reconcile engine (orphans, missing files, dup clusters)."""

from pathlib import Path

from dossier import reconcile
from dossier.config import Config
from dossier.model import Document, ReconcileState, Rendition
from dossier.store import Store


def _setup(tmp_path: Path) -> tuple[Store, Config]:
    root = tmp_path / "docs"
    (root / "Marine").mkdir(parents=True)
    (root / "Wallpapers").mkdir()
    (root / ".stversions").mkdir()
    (root / "Marine" / "CoC Card.pdf").write_bytes(b"x")  # linked
    (root / "Marine" / "PSCRB Cert.pdf").write_bytes(
        b"x"
    )  # orphan; fuzzy-matches a doc
    (root / "Wallpapers" / "bg.jpg").write_bytes(b"x")  # orphan junk
    (root / ".stversions" / "old.pdf").write_bytes(b"x")  # sync noise -> excluded
    config = Config(syncthing_root=root, history_dir=tmp_path / "_history")
    store = Store(config)
    store.ensure_layout()
    store.save(
        Document(
            id="coc",
            name="CoC Card",
            files=[Rendition("d", "Marine/CoC Card.pdf", primary=True)],
        )
    )
    store.save(
        Document(
            id="pscrb",
            name="PSCRB Cert",
            files=[
                Rendition("d", "Marine/PSCRB Cert gone.pdf", primary=True)
            ],  # missing
        )
    )
    return store, config


def test_scan_files_scopes_and_excludes_noise(tmp_path: Path):
    _, config = _setup(tmp_path)
    files = reconcile.scan_files(config)
    assert "Marine/CoC Card.pdf" in files
    assert "Wallpapers/bg.jpg" in files
    assert not any(".stversions" in f for f in files)  # Syncthing noise excluded
    assert not any(".dossier" in f for f in files)  # meta excluded


def test_scan_files_respects_ignore_glob(tmp_path: Path):
    _, config = _setup(tmp_path)
    config.ignore = ["Wallpapers/*"]
    files = reconcile.scan_files(config)
    assert not any(f.startswith("Wallpapers/") for f in files)


def test_scan_files_excludes_dotfiles_and_dot_dirs(tmp_path: Path):
    _, config = _setup(tmp_path)
    root = config.syncthing_root
    (root / ".DS_Store").write_bytes(b"x")  # dotfile
    (root / "Marine" / "._CoC Card.pdf").write_bytes(b"x")  # AppleDouble sidecar
    (root / ".hidden").mkdir()
    (root / ".hidden" / "secret.pdf").write_bytes(b"x")  # file under a dot-dir

    files = reconcile.scan_files(config)
    assert ".DS_Store" not in files
    assert "Marine/._CoC Card.pdf" not in files
    assert not any(f.startswith(".hidden/") for f in files)
    assert "Marine/CoC Card.pdf" in files  # a real document is still found
    assert "Marine/CoC Card.pdf" in files


def test_run_reports_orphans_missing_and_suggestions(tmp_path: Path):
    store, config = _setup(tmp_path)
    report = reconcile.run(store, config)

    orphan_paths = {o.path for o in report.orphans}
    assert "Marine/PSCRB Cert.pdf" in orphan_paths  # unlinked file
    assert "Wallpapers/bg.jpg" in orphan_paths
    assert "Marine/CoC Card.pdf" not in orphan_paths  # linked, not an orphan

    assert any(m.doc_id == "pscrb" for m in report.missing)  # links a gone file

    pscrb = next(o for o in report.orphans if o.path == "Marine/PSCRB Cert.pdf")
    assert pscrb.suggestion == "pscrb"  # fuzzy-matches the document name
    assert report.groups is None  # no hashes supplied


def test_run_with_hashes_adds_duplicate_clusters(tmp_path: Path):
    store, config = _setup(tmp_path)
    report = reconcile.run(
        store, config, pages_by_file={"a.pdf": [1, 2], "b.pdf": [1, 2]}
    )
    assert report.groups is not None
    assert len(report.groups) == 1


def test_state_dismissed_orphan_is_filtered(tmp_path: Path):
    store, config = _setup(tmp_path)
    state = ReconcileState(dismissed={"Wallpapers/bg.jpg"})
    report = reconcile.run(store, config, state=state)
    paths = {o.path for o in report.orphans}
    assert "Wallpapers/bg.jpg" not in paths  # dismissed → hidden
    assert "Marine/PSCRB Cert.pdf" in paths  # others unaffected


def test_state_acked_missing_is_filtered(tmp_path: Path):
    store, config = _setup(tmp_path)
    state = ReconcileState(missing_ok={"Marine/PSCRB Cert gone.pdf": {"pscrb"}})
    report = reconcile.run(store, config, state=state)
    assert not any(m.doc_id == "pscrb" for m in report.missing)


def test_state_ignore_glob_scopes_scan(tmp_path: Path):
    _, config = _setup(tmp_path)
    files = reconcile.scan_files(config, ["Wallpapers/*"])
    assert not any(f.startswith("Wallpapers/") for f in files)
    assert "Marine/PSCRB Cert.pdf" in files


def test_state_folded_suppresses_only_recorded_clusters(tmp_path: Path):
    store, config = _setup(tmp_path)
    pages = {"keep.pdf": [1, 2, 3], "copy.pdf": [1, 2]}  # copy ⊂ keep
    state = ReconcileState(folded={"keep.pdf": {"copy.pdf"}})
    report = reconcile.run(store, config, pages_by_file=pages, state=state)
    assert report.groups == []  # fully accounted for → suppressed

    # A new, unrecorded copy resurfaces the whole cluster for a fresh decision.
    pages["copy2.pdf"] = [1, 2]
    report = reconcile.run(store, config, pages_by_file=pages, state=state)
    assert report.groups is not None
    assert len(report.groups) == 1
