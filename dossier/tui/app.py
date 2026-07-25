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
from textual.events import DescendantFocus, Event, Key, MouseDown
from textual.widgets import Input, TextArea

from dossier.config import Config
from dossier.store import Store
from dossier.tui.home import HomeScreen


class DossierApp(App[None]):
    """Hosts the Miller-columns home screen; a thin shell around it."""

    TITLE = "dossier"
    # Textual's modal command palette is retired: the persistent bar's `:`/`>`
    # command mode replaces it (see HomeScreen). Disabling it drops the system
    # providers and Textual's auto ctrl+p; our own ctrl+p below stays and is
    # repointed at command mode via action_command_palette. The whole occasional
    # vocabulary now lives in dossier.tui.commands, surfaced by the bar.
    ENABLE_COMMAND_PALETTE = False
    # No bare `q` — a printable belongs to search now (find-fast). `ctrl+q` (a
    # Textual built-in priority binding) quits; "Quit" is also a command now. ctrl+p
    # keeps its footer label but opens the `:` bar rather than a modal.
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

    def action_command_palette(self) -> None:
        """Open the home's `:` command mode.

        Named ``command_palette`` on purpose: the header ⭘ icon hard-codes that
        action string and our ctrl+p binding names it, so the key, the icon and the
        touch Commands button all converge here. Only on the home screen — a modal
        has no command bar to open (its footer won't advertise the key either; see
        :meth:`check_action`).
        """
        if self._home is not None and self.screen is self._home:
            self._home.enter_command_mode()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # A bare `q` from a focused Checkbox/Button in the edit form would quit
        # mid-edit (an Input swallows it, other widgets don't) — suppress it.
        if action == "quit" and self._home is not None and self._home.editing:
            return None
        # ctrl+p opens command mode, which only exists on the home screen. False
        # (not None) so a modal's footer doesn't advertise a dead key; ctrl+q still
        # quits everywhere. (Watch/Bundles/Intake/Settings regain command access
        # when Phase B folds them into home modes.)
        return not (
            action == "command_palette"
            and (self._home is None or self.screen is not self._home)
        )

    @property
    def home(self) -> HomeScreen:
        """The home screen (available once the app has mounted)."""
        if self._home is None:
            raise RuntimeError("home screen not created yet")
        return self._home

    async def on_event(self, event: Event) -> None:
        # Any input that isn't Esc cancels a pending Esc-Esc quit, so the two Escs
        # must be consecutive. Hooked at the app (not the screen) because a focused
        # widget's own bindings consume keys before they'd ever reach the screen —
        # e.g. the ↓ that steps into a list, or a pane's letter verb. MouseDown
        # covers taps and the touch buttons; MouseMove deliberately doesn't (noise).
        if (
            self._home is not None
            and isinstance(event, (Key, MouseDown))
            and not event.is_forwarded
            and getattr(event, "key", None) != "escape"
        ):
            self._home.disarm_quit()
        await super().on_event(event)

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
