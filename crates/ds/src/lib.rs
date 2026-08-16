// Copyright © 2026-present gsfernandes81
//
// This file is part of "dossier".
//
// dossier is free software: you can redistribute it and/or modify it under the
// terms of the GNU Affero General Public License as published by the Free Software
// Foundation, either version 3 of the License, or (at your option) any later version.
//
// dossier is distributed in the hope that it will be useful, but WITHOUT ANY
// WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
// PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
//
// You should have received a copy of the GNU Affero General Public License along with
// dossier. If not, see <https://www.gnu.org/licenses/>.

//! `ds` — the dossier v3 core.
//!
//! Phase R3 of [`REWRITE.md`](../../../REWRITE.md): the read-only daily driver —
//! browse, search, open, and `ds status`. The layout it renders is the approved
//! plan in [`REWRITE-UI.md`](../../../REWRITE-UI.md), and the mockups in
//! `docs/dev/mockups/` are the reference the finished surface is measured
//! against.
//!
//! # Why this is a library first
//!
//! Everything above the journal splits cleanly into *what the data means* and
//! *what the screen does*, and only the second needs a terminal. This slice is
//! the first half — the view model and search — so the rules that decide what a
//! row says can be tested directly, at speed, without a `TestBackend` in the
//! way. The binary and the TUI arrive on top of it.
//!
//! - [`doc`] — a folded journal turned into documents: shelf order, expiry
//!   standing, the file `Enter` opens, the search haystack.
//! - [`search`] — v2's typo-tolerant matching contract, ported: exact always
//!   wins, and a short query never fuzzes.
//!
//! # Reading this code
//!
//! Per REWRITE.md §4.6 the codebase doubles as Rust learning material: every
//! public item says what it is *and why it exists*, `// rust:` notes mark
//! idioms that would surprise a Python developer, and each test states the
//! invariant it defends before asserting it.

#![warn(clippy::pedantic)]
#![forbid(unsafe_code)]

pub mod doc;
pub mod search;

pub use doc::{Doc, FileRef, Location, Status, Store};
