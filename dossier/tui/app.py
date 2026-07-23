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
from textual.command import Hit, Hits, Provider
from textual.events import DescendantFocus
from textual.widgets import Input, TextArea

from dossier.config import Config
from dossier.store import Store
from dossier.tui.home import HomeScreen


class DossierCommands(Provider):
    """Command-palette (ctrl+p) entries for every occasional home action.

    This is where the low-frequency actions live now, instead of on dedicated
    letters — searchable by name so a new user has almost nothing to memorise. Each
    delegates to the matching ``HomeScreen.action_*`` so one implementation backs
    the palette, the touch Commands button, and any remaining binding.
    """

    @staticmethod
    def _commands() -> list[tuple[str, str, str]]:
        # (title, home action, help) — the searchable home for occasional actions,
        # so none of these needs a dedicated keybind. Roughly frequency-ordered.
        return [
            ("Reconcile — orphans, missing, duplicates", "reconcile", "Tidy files"),
            ("Resolve sync conflicts", "resolve", "Merge Syncthing conflict copies"),
            ("Doctor — integrity check", "doctor", "Conflicts, refs, dates, files"),
            ("Bundles", "bundles", "Browse and edit document bundles"),
            ("Watch expiry", "watch", "The expiry-watch surface"),
            ("Toggle expiring-only filter", "toggle_expiring", "Just expiring docs"),
            ("Intake dropped documents", "intake", "Review + file inbox files"),
            ("Scan current document (vision)", "scan_doc", "Read the current doc"),
            ("Scan all linked (vision)", "scan_all", "Read every linked document"),
            ("Cancel vision scan", "cancel_scan", "Stop a running vision scan"),
            ("Move document", "move", "Change the current doc's location"),
            ("Set succession (supersedes)", "supersede", "Link a renewal to older"),
            ("Toggle issue / expiry dates", "toggle_dates", "Switch the date column"),
            (
                "Search inside scan contents",
                "toggle_search_content",
                "Match transcripts",
            ),
            ("Settings", "settings", "Icons, scan endpoint/model, expiry threshold"),
        ]

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for title, action, help_text in self._commands():
            score = matcher.match(title)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(title),
                    self._runner(action),
                    help=help_text,
                )

    def _runner(self, action: str):
        app = self.app
        assert isinstance(app, DossierApp)
        return getattr(app.home, f"action_{action}")  # bound; called on selection


class DossierApp(App[None]):
    """Hosts the Miller-columns home screen; a thin shell around it."""

    TITLE = "dossier"
    BINDINGS = [Binding("q", "quit", "Quit")]
    COMMANDS = App.COMMANDS | {DossierCommands}

    def __init__(
        self,
        store: Store,
        config: Config,
        *,
        today: date | None = None,
        touch: bool = False,
    ) -> None:
        super().__init__()
        self._store = store
        self._config = config
        self._today = today or date.today()
        self._touch = touch
        self._home: HomeScreen | None = None

    def get_default_screen(self) -> HomeScreen:
        self._home = HomeScreen(
            self._store, self._config, today=self._today, touch=self._touch
        )
        return self._home

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # A bare `q` from a focused Checkbox/Button in the edit form would quit
        # mid-edit (an Input swallows it, other widgets don't) — suppress it.
        if action == "quit" and self._home is not None and self._home.editing:
            return None
        return True

    @property
    def home(self) -> HomeScreen:
        """The home screen (available once the app has mounted)."""
        if self._home is None:
            raise RuntimeError("home screen not created yet")
        return self._home

    def on_descendant_focus(self, event: DescendantFocus) -> None:
        """Termux keyboard model, applied app-wide (see :meth:`set_mouse_reporting`).

        Focusing a text field drops mouse reporting so the soft keyboard appears;
        focusing anything else restores it. Handled here — not only on the home
        screen — so opening the **command palette** raises the keyboard for typing a
        command (its search box lives on a separate screen the home handler can't
        see).
        """
        if not self._touch:
            return
        typing = isinstance(event.widget, (Input, TextArea))
        self.set_mouse_reporting(not typing)

    def set_mouse_reporting(self, enabled: bool) -> None:
        """Toggle SGR mouse reporting via the driver.

        On Termux, tapping only raises the soft keyboard while mouse tracking is
        *off*, so focusing a text field disables reporting to summon the IME and
        focusing anything else re-enables it (see ``HomeScreen.on_descendant_focus``).
        The driver methods are private and absent on the headless test driver, so
        this is a guarded no-op there.
        """
        driver = getattr(self, "_driver", None)
        if driver is None:
            return
        name = "_enable_mouse_support" if enabled else "_disable_mouse_support"
        method = getattr(driver, name, None)
        if callable(method):
            method()
