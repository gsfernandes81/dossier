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

"""The third Miller column as a widget: a document's detail, read or editable.

A :class:`~textual.widgets.ContentSwitcher` frames a read view — the pure
:func:`dossier.tui.detail.render_detail` dropped into a ``Static`` — and an edit
form over the same document. ``id="detail"`` is preserved so every responsive
``#detail`` CSS rule and the home screen's focus handling stay unchanged.

Edit mode is explicit: the home screen calls :meth:`start_edit`; ``ctrl+s`` saves,
``Esc`` discards (a second ``Esc`` confirms when the form is dirty), and — on a
:class:`StaleWriteError` — ``ctrl+r`` reloads the on-disk copy. Nothing mutates
the document until a successful save, so discard is free. The pane posts
:class:`EditingChanged` / :class:`Saved` / :class:`ReloadRequested` up to the home
screen, which owns the document list and the key-gating that stops the home
bindings from firing mid-edit.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Checkbox, ContentSwitcher, Input, Label, Static, TextArea

from dossier.errors import StaleWriteError, StoreError
from dossier.migrate import slugify
from dossier.model import Document
from dossier.query import DocumentView
from dossier.store import Store
from dossier.tui import detail, forms
from dossier.tui.glyphs import GlyphSet

_READ = "detail-body"
_FORM = "detail-form"

# Field ids in tab order (also the shared `.df-field` class scopes Tab cycling).
_NAME = "f-name"
_ISSUE = "f-issue"
_EXPIRY = "f-expiry"
_PERM = "f-perm"
_TEMP = "f-temp"
_TAGS = "f-tags"
_PHYSICAL = "f-physical"
_DIGITAL = "f-digital"
_IGNORE = "f-ignore"
_NOTES = "f-notes"

# Actions live only while editing; hidden (→ keys bubble to the home screen) in
# read mode via check_action.
_EDIT_ACTIONS = frozenset(
    {"save", "cancel_edit", "reload_base", "focus_next_field", "focus_prev_field"}
)


class DetailPane(VerticalScroll):
    """Detail of the selected document, read-only or inline-editable."""

    DEFAULT_CSS = """
    DetailPane #detail-form Input, DetailPane #detail-form TextArea {
        margin-bottom: 1;
    }
    DetailPane #detail-form TextArea { height: 5; }
    DetailPane #f-flags { height: auto; margin-bottom: 1; }
    DetailPane #f-flags Checkbox { width: auto; margin-right: 2; border: none; }
    DetailPane .df-label { color: $text-muted; }
    DetailPane .df-hint { color: $text-muted; margin-top: 1; }
    """
    BINDINGS = [
        ("ctrl+s", "save", "Save"),
        ("escape", "cancel_edit", "Discard"),
        ("ctrl+r", "reload_base", "Reload on-disk"),
        ("tab", "focus_next_field", "Next"),
        ("shift+tab", "focus_prev_field", "Prev"),
    ]

    editing: reactive[bool] = reactive(False)

    class EditingChanged(Message):
        """Posted when the pane enters or leaves edit mode."""

        def __init__(self, editing: bool) -> None:
            self.editing = editing
            super().__init__()

    class Saved(Message):
        """Posted after a successful save; carries the saved document id."""

        def __init__(self, doc_id: str) -> None:
            self.doc_id = doc_id
            super().__init__()

    class ReloadRequested(Message):
        """Posted after a ctrl+r reload, so the home screen resyncs its list."""

    def __init__(
        self, store: Store, *, glyphs: GlyphSet, id: str | None = None
    ) -> None:
        super().__init__(id=id)
        self._store = store
        self._glyphs = glyphs
        self.can_focus = True
        self._doc = Document()
        self._is_new = False
        self._focus_target = _NAME
        self._snapshot: tuple[object, ...] = ()
        self._discard_armed = False

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial=_READ):
            yield Static(id=_READ)
            with Vertical(id=_FORM):
                yield Label("Name", classes="df-label")
                yield Input(id=_NAME, classes="df-field")
                yield Label("Issue date (YYYY-MM-DD)", classes="df-label")
                yield Input(id=_ISSUE, placeholder="YYYY-MM-DD", classes="df-field")
                yield Label("Expiry date (YYYY-MM-DD)", classes="df-label")
                yield Input(id=_EXPIRY, placeholder="YYYY-MM-DD", classes="df-field")
                yield Label("Permanent location (slug)", classes="df-label")
                yield Input(id=_PERM, classes="df-field")
                yield Label("Temporary location (slug)", classes="df-label")
                yield Input(id=_TEMP, classes="df-field")
                yield Label("Tags (space-separated)", classes="df-label")
                yield Input(id=_TAGS, classes="df-field")
                with Horizontal(id="f-flags"):
                    yield Checkbox("Physical", id=_PHYSICAL, classes="df-field")
                    yield Checkbox("Digital", id=_DIGITAL, classes="df-field")
                    yield Checkbox("Ignore expiry", id=_IGNORE, classes="df-field")
                yield Label("Notes", classes="df-label")
                yield TextArea(id=_NOTES, classes="df-field")
                yield Label(
                    "ctrl+s save · Esc discard · ctrl+r reload on-disk",
                    classes="df-hint",
                )

    # -- read view -----------------------------------------------------------

    def show_document(
        self,
        view: DocumentView,
        *,
        location_label: str | None,
        chain: list[Document],
        superseded_by: Document | None,
    ) -> None:
        """Render one document into the read view (a no-op while editing)."""
        if self.editing:
            return
        self.query_one(f"#{_READ}", Static).update(
            detail.render_detail(
                view,
                location_label=location_label,
                chain=chain,
                superseded_by=superseded_by,
                glyphs=self._glyphs,
            )
        )

    def clear(self) -> None:
        """Blank the read view (no document selected)."""
        if self.editing:
            return
        self.query_one(f"#{_READ}", Static).update("")

    # -- edit mode -----------------------------------------------------------

    def start_edit(
        self,
        doc: Document,
        docs: list[Document],
        *,
        is_new: bool = False,
        focus: str = _NAME,
    ) -> None:
        """Enter edit mode over ``doc``. ``focus`` is the field id to land on."""
        self._doc = doc
        self._is_new = is_new
        self._focus_target = focus
        self._populate_form(doc)
        self._snapshot = self._form_values()
        self._discard_armed = False
        self.editing = True

    def watch_editing(self, editing: bool) -> None:
        self.query_one(ContentSwitcher).current = _FORM if editing else _READ
        self.scroll_home(animate=False)
        self.post_message(self.EditingChanged(editing))
        if editing:
            self.query_one(f"#{self._focus_target}").focus()

    def action_save(self) -> None:
        doc = self._doc
        try:
            issue = forms.parse_iso(self.query_one(f"#{_ISSUE}", Input).value)
            expiry = forms.parse_iso(self.query_one(f"#{_EXPIRY}", Input).value)
        except ValueError as exc:
            self.notify(f"invalid date: {exc}", severity="error")
            return
        name = self.query_one(f"#{_NAME}", Input).value.strip()
        if self._is_new and not name:
            self.notify("a new document needs a name", severity="error")
            return
        doc.name = name
        if self._is_new:
            doc.id = forms.unique_id(self._store, slugify(name))
        doc.issue_date = issue
        doc.expiry_date = expiry
        doc.perm_location = forms.slug(self.query_one(f"#{_PERM}", Input).value)
        doc.temp_location = forms.slug(self.query_one(f"#{_TEMP}", Input).value)
        doc.tags = self.query_one(f"#{_TAGS}", Input).value.split()
        doc.has_physical = self.query_one(f"#{_PHYSICAL}", Checkbox).value
        doc.has_digital = self.query_one(f"#{_DIGITAL}", Checkbox).value
        doc.ignore_expiry = self.query_one(f"#{_IGNORE}", Checkbox).value
        doc.notes = self.query_one(f"#{_NOTES}", TextArea).text.strip()

        try:
            self._store.save(doc)
        except StaleWriteError:
            self.notify(
                "changed on disk — ctrl+r reloads (discards your edits)",
                severity="error",
            )
            return
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            return
        self.editing = False
        self.post_message(self.Saved(doc.id))

    def action_cancel_edit(self) -> None:
        if self._dirty() and not self._discard_armed:
            self._discard_armed = True
            self.notify("unsaved changes — Esc again discards, ctrl+s saves")
            return
        self._discard_armed = False
        self.editing = False

    def action_reload_base(self) -> None:
        if self._is_new or not self._doc.id:
            return  # nothing on disk to reload
        try:
            fresh = self._store.load(self._doc.id)
        except StoreError as exc:
            self.notify(str(exc), severity="error")
            return
        self._doc = fresh
        self._populate_form(fresh)
        self._snapshot = self._form_values()
        self._discard_armed = False
        self.notify("reloaded from disk")
        self.post_message(self.ReloadRequested())

    def action_focus_next_field(self) -> None:
        self.screen.focus_next(".df-field")

    def action_focus_prev_field(self) -> None:
        self.screen.focus_previous(".df-field")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in _EDIT_ACTIONS:
            return True if self.editing else None  # None → bubble to the home screen
        return True

    # -- dirty tracking ------------------------------------------------------

    @on(Input.Changed)
    @on(Checkbox.Changed)
    @on(TextArea.Changed)
    def _field_changed(self) -> None:
        self._discard_armed = False  # any edit disarms a pending discard

    def _populate_form(self, doc: Document) -> None:
        self.query_one(f"#{_NAME}", Input).value = doc.name
        self.query_one(f"#{_ISSUE}", Input).value = forms.iso(doc.issue_date)
        self.query_one(f"#{_EXPIRY}", Input).value = forms.iso(doc.expiry_date)
        self.query_one(f"#{_PERM}", Input).value = doc.perm_location or ""
        self.query_one(f"#{_TEMP}", Input).value = doc.temp_location or ""
        self.query_one(f"#{_TAGS}", Input).value = " ".join(doc.tags)
        self.query_one(f"#{_PHYSICAL}", Checkbox).value = doc.has_physical
        self.query_one(f"#{_DIGITAL}", Checkbox).value = doc.has_digital
        self.query_one(f"#{_IGNORE}", Checkbox).value = doc.ignore_expiry
        self.query_one(f"#{_NOTES}", TextArea).text = doc.notes

    def _form_values(self) -> tuple[object, ...]:
        return (
            self.query_one(f"#{_NAME}", Input).value,
            self.query_one(f"#{_ISSUE}", Input).value,
            self.query_one(f"#{_EXPIRY}", Input).value,
            self.query_one(f"#{_PERM}", Input).value,
            self.query_one(f"#{_TEMP}", Input).value,
            self.query_one(f"#{_TAGS}", Input).value,
            self.query_one(f"#{_PHYSICAL}", Checkbox).value,
            self.query_one(f"#{_DIGITAL}", Checkbox).value,
            self.query_one(f"#{_IGNORE}", Checkbox).value,
            self.query_one(f"#{_NOTES}", TextArea).text,
        )

    def _dirty(self) -> bool:
        return self._form_values() != self._snapshot
