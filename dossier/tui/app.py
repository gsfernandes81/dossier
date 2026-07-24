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
from textual.command import DiscoveryHit, Hit, Hits, Provider
from textual.events import DescendantFocus
from textual.widgets import Input, TextArea

from dossier.config import Config
from dossier.store import Store
from dossier.tui.commands import ENTRIES, Entry, Kind
from dossier.tui.home import HomeScreen


class DossierCommands(Provider):
    """Command-palette entries for every occasional home action.

    The home binds no letters (find-fast), so this is the app's whole occasional
    vocabulary — see :mod:`dossier.tui.commands` for the catalog itself, which is
    deliberately defined once and shared.

    Implements ``discover`` as well as ``search``: without it the palette opens
    showing only Textual's own Theme/Quit/Screenshot commands and *none* of ours,
    so you had to already know a command existed in order to find it.
    """

    def _hit(self, entry: Entry, display: object | None = None):
        return {
            "command": self._runner(entry.action),
            "text": entry.title,
            "help": f"{entry.kind.value} · {entry.help}",
        }

    async def discover(self) -> Hits:
        """Everything, grouped, before a single character is typed."""
        for entry in sorted(ENTRIES, key=lambda e: (list(Kind).index(e.kind), e.title)):
            yield DiscoveryHit(entry.title, **self._hit(entry))

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for entry in ENTRIES:
            score = matcher.match(entry.title)
            if score > 0:
                yield Hit(score, matcher.highlight(entry.title), **self._hit(entry))

    def _runner(self, action: str):
        app = self.app
        assert isinstance(app, DossierApp)

        def run() -> None:
            # Respect the same gate the keys do. The palette used to call the bound
            # action directly, which meant it could do things a keypress was
            # explicitly forbidden to do — "Edit document" mid-edit silently wiped
            # the in-progress form, and in review-mode the document verbs acted on
            # the *hidden* documents cursor. check_action is the app's one answer to
            # "is this actionable right now", so ask it.
            home = app.home
            if home.check_action(action, ()) is not True:
                home.notify("not available right now", severity="warning")
                return
            getattr(home, f"action_{action}")()

        return run


class DossierApp(App[None]):
    """Hosts the Miller-columns home screen; a thin shell around it."""

    TITLE = "dossier"
    # No bare `q` — a printable belongs to search now (find-fast). `ctrl+q` (a
    # Textual built-in priority binding) quits, and the palette's system "Quit"
    # command covers touch.
    COMMANDS = App.COMMANDS | {DossierCommands}
    # Bind ctrl+p ourselves so it *shows*. Textual adds this binding automatically
    # only if we haven't, and its own copy is `show=False` — which left the app's
    # entire occasional vocabulary behind a key the UI never mentioned.
    BINDINGS = [Binding("ctrl+p", "command_palette", "Commands", priority=True)]

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
