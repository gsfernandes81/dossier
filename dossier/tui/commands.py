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

"""The one catalog of occasional commands, shared by every entrance to them.

The home binds no letters (find-fast: a printable is always the start of a
search), so everything occasional lives here. That makes this list the app's
whole discoverable vocabulary — which is exactly why it must have *one*
definition. Any second copy is a copy that drifts.

Each entry names a :class:`~dossier.tui.home.HomeScreen` ``action_*``, so the
palette, a keybinding and a touch button can all reach the same implementation.
:class:`Kind` groups them because they are not one kind of thing: a verb on the
document under the cursor, a door to another surface, a change to how you are
looking, and a long-running job read very differently, and a flat list of 21
hides that.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Kind(Enum):
    """What a command *is*. The value is the heading it renders under."""

    DOC = "Current document"
    GO = "Go to"
    VIEW = "View"
    OPS = "Maintenance"


@dataclass(frozen=True)
class Entry:
    """One command: what to call it, which home action runs it, and what it does."""

    title: str
    action: str  # a HomeScreen.action_* name
    help: str
    kind: Kind


ENTRIES: tuple[Entry, ...] = (
    # -- verbs on the document under the cursor ------------------------------
    Entry("Open document file", "open_file", "Open the current doc's file", Kind.DOC),
    Entry("Edit document", "edit", "Edit the current document's fields", Kind.DOC),
    Entry("New document", "new", "Create a new document record", Kind.DOC),
    Entry("Add to bundle", "bundle", "Put the current doc in a bundle", Kind.DOC),
    Entry(
        "Accept top suggestion", "accept_suggestion", "Apply the shown hint", Kind.DOC
    ),
    Entry("Move document", "move", "Change the current doc's location", Kind.DOC),
    Entry(
        "Set succession (supersedes)", "supersede", "Link a renewal to older", Kind.DOC
    ),
    Entry(
        "History — restore an earlier version",
        "history",
        "Roll the current document back to a saved version",
        Kind.DOC,
    ),
    Entry(
        "Show in file manager",
        "reveal_file",
        "Reveal the file under the cursor",
        Kind.DOC,
    ),
    Entry(
        "Copy file path", "copy_path", "Put the file's path on the clipboard", Kind.DOC
    ),
    Entry(
        "Scan current document (vision)", "scan_doc", "Read the current doc", Kind.DOC
    ),
    # -- doors to other surfaces ---------------------------------------------
    Entry(
        "Review — reconcile the collection",
        "review",
        "Conflicts, orphans, missing, dups, integrity",
        Kind.GO,
    ),
    Entry("Bundles", "bundles", "Browse and edit document bundles", Kind.GO),
    Entry("Watch expiry", "watch", "The expiry-watch surface", Kind.GO),
    Entry("Intake dropped documents", "intake", "Review + file inbox files", Kind.GO),
    Entry(
        "Settings", "settings", "Icons, scan endpoint/model, expiry threshold", Kind.GO
    ),
    # -- how you are looking at the collection -------------------------------
    Entry(
        "Toggle expiring-only filter",
        "toggle_expiring",
        "Just expiring docs",
        Kind.VIEW,
    ),
    Entry(
        "Toggle issue / expiry dates",
        "toggle_dates",
        "Switch the date column",
        Kind.VIEW,
    ),
    Entry(
        "Search inside scan contents",
        "toggle_search_content",
        "Match transcripts (ctrl+t)",
        Kind.VIEW,
    ),
    # -- long-running jobs ----------------------------------------------------
    Entry(
        "Scan all linked (vision)", "scan_all", "Read every linked document", Kind.OPS
    ),
    Entry("Cancel vision scan", "cancel_scan", "Stop a running vision scan", Kind.OPS),
)
