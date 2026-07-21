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

"""Exception types for dossier."""


class DossierError(Exception):
    """Base class for all dossier errors."""


class ConfigError(DossierError):
    """The configuration is missing or invalid."""


class StoreError(DossierError):
    """Base class for storage-layer errors."""


class DocumentExistsError(StoreError):
    """A new document's id collides with a file already on disk."""

    def __init__(self, doc_id: str) -> None:
        super().__init__(f"a document with id {doc_id!r} already exists")
        self.doc_id = doc_id


class StaleWriteError(StoreError):
    """The on-disk file changed since the document was loaded.

    Raised by :meth:`dossier.store.Store.save` when the file's content hash no
    longer matches the hash captured at load time — another device (via
    Syncthing) or a hand-edit changed it underneath us. The caller should reload
    and retry rather than clobber the newer copy.
    """

    def __init__(self, doc_id: str) -> None:
        super().__init__(
            f"document {doc_id!r} changed on disk since it was loaded; "
            "reload before saving"
        )
        self.doc_id = doc_id
