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

"""dossier — a cross-platform TUI for tracking personal documents."""

import time

# The earliest in-package moment, captured before any submodule import so the
# DS_TIMING startup probe (tui/home.py) can charge the whole import bill to its
# "imports+init" bucket. Interpreter/site boot *before* this line is visible only
# to an external `time` wrapper — see docs/dev/startup-timing.md.
STARTUP_T0 = time.perf_counter()

__version__ = "0.1.0"
