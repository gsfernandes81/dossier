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

"""Launch the real DossierApp against a throwaway store (never the user's docs).

Usage: ``python run_tui_temp.py <store_root> [--touch]``. Builds a small, fixed
sample store under ``<store_root>`` then runs the app in this terminal, so an
outside driver (``ptyterm.PtyTerm``) can drive it deterministically.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from dossier.config import Config
from dossier.model import Document, Location, Rendition
from dossier.store import Store
from dossier.tui import DossierApp

TODAY = date(2026, 7, 21)


def build(root: Path) -> tuple[Store, Config]:
    config = Config(syncthing_root=root, history_dir=root / "_history")
    store = Store(config)
    store.ensure_layout()
    store.save_locations(
        {
            "file": Location(slug="file", title="Filing cabinet"),
            "wallet": Location(slug="wallet", title="Wallet"),
        }
    )
    docs = [
        Document(
            id="passport",
            name="Passport",
            tags=["identity"],
            perm_location="file",
            perm_slot=1,
            has_physical=True,
            has_digital=True,
            files=[Rendition(label="scan", path="passport.pdf", primary=True)],
        ),
        Document(
            id="coc",
            name="CoC Certificate of Competency",
            tags=["marine"],
            perm_location="file",
            perm_slot=2,
            expiry_date=date(2026, 9, 1),
        ),
        Document(
            id="us-visa",
            name="US B1/B2 Visa",
            perm_location="wallet",
            expiry_date=date(2026, 8, 5),
        ),
        Document(
            id="stcw",
            name="STCW Basic Safety Training",
            perm_location="file",
            perm_slot=3,
            expiry_date=date(2027, 2, 1),
        ),
        Document(
            id="degree",
            name="BSc Marine Engineering",
            perm_location="file",
            perm_slot=4,
        ),
    ]
    for doc in docs:
        store.save(doc)
    (root / "passport.pdf").write_bytes(b"%PDF-1.4 fake")
    return store, config


def main() -> None:
    root = Path(sys.argv[1])
    root.mkdir(parents=True, exist_ok=True)
    store, config = build(root)
    touch = "--touch" in sys.argv
    DossierApp(store, config, today=TODAY, touch=touch).run()


if __name__ == "__main__":
    main()
