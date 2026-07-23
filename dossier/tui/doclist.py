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

"""The document list — an OptionList whose mouse verb takes two clicks."""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widgets import OptionList


class DocumentList(OptionList):
    """A list of documents that separates *pointing at* a row from *activating* it.

    Keyboard `Enter` activates the row under the cursor — which is already where you
    put it, so one keypress is right. A click carries no such history: Textual's
    OptionList moves the cursor **and** activates in the same gesture, so a single
    click on the home both revealed a document's detail and launched its file.

    Here the first click on a row only moves the cursor and posts `Previewed` (the
    home turns that into its detail pane); activating takes a second click, on the
    row now under the cursor. A mis-aimed click costs a look rather than a launched
    PDF, and since a double-click *is* two clicks, it still opens in one gesture.
    Keyboard activation is untouched.
    """

    class Previewed(Message):
        """A click moved the cursor onto a row instead of activating it."""

        def __init__(self, document_list: DocumentList, option_id: str | None) -> None:
            super().__init__()
            self.document_list = document_list
            self.option_id = option_id

        @property
        def control(self) -> DocumentList:
            """The list that was clicked (lets handlers use ``@on(..., "#id")``)."""
            return self.document_list

    async def _on_click(self, event: events.Click) -> None:  # noqa: RUF029 (base is async)
        # Textual dispatches `_on_click` for *every* class in the MRO, subclass first,
        # so this runs before OptionList's own handler — and `prevent_default()` is
        # what keeps that one (which highlights *and* activates) from running. Falling
        # through without it is how the second click activates: do nothing, and the
        # base handler does its normal job.
        index: int | None = event.style.meta.get("option")
        if index is not None and index != self.highlighted:
            option = self.get_option_at_index(index)
            if not option.disabled:
                self.highlighted = index
                self.post_message(self.Previewed(self, option.id))
                event.prevent_default()
                event.stop()  # consumed: this click was the pointing half of the verb
