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

"""The app shell. State and behaviour live in :class:`~dossier.tui.home.HomeScreen`."""

from __future__ import annotations

from datetime import date

from textual.app import App
from textual.binding import Binding

from dossier.config import Config
from dossier.store import Store
from dossier.tui.home import HomeScreen


class DossierApp(App[None]):
    """Hosts the Miller-columns home screen; a thin shell around it."""

    TITLE = "dossier"
    BINDINGS = [Binding("q", "quit", "Quit")]

    def __init__(
        self, store: Store, config: Config, *, today: date | None = None
    ) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._today = today or date.today()
        self._home: HomeScreen | None = None

    def get_default_screen(self) -> HomeScreen:
        self._home = HomeScreen(self._store, self._config, today=self._today)
        return self._home

    @property
    def home(self) -> HomeScreen:
        """The home screen (available once the app has mounted)."""
        if self._home is None:
            raise RuntimeError("home screen not created yet")
        return self._home
