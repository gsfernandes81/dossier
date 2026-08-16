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
//! # Why this is a library, with a thin binary on top
//!
//! Everything above the journal splits cleanly into *what the data means*, *what
//! the screen does*, and *what the terminal does*. Only the last needs a
//! terminal, so only that is in `main.rs`; the rest is here, where the rules can
//! be tested directly and at speed.
//!
//! - [`doc`] — a folded journal turned into documents: shelf order, expiry
//!   standing, the file `Enter` opens, the search haystack.
//! - [`search`] — v2's typo-tolerant matching contract, ported: exact always
//!   wins, and a short query never fuzzes.
//! - [`app`] — the Elm-style loop's update half: `Msg` in, state changed,
//!   [`app::Effect`] out. Every REWRITE.md §4.5 interaction invariant is a rule
//!   in here and a test beside it.
//! - [`find`] / [`detail`] — the view half: the Find surface and the record,
//!   laid out to match the approved mockups.
//! - [`input`] — terminal events to messages; the only module that knows
//!   crossterm exists.
//! - [`layout`] / [`theme`] — the responsive thresholds and cell-width
//!   arithmetic, and the semantic colour tokens.
//! - [`open`] — handing a file to the platform's opener, with the guidance that
//!   makes a missing opener fixable.
//! - [`config`] / [`load`] — the small per-device TOML, and the one path from a
//!   directory to a store that every entry point shares.
//! - [`status`] — what `ds status` reports, as data that is rendered twice: in
//!   full for a person, problems only for cron.
//! - [`syncthing`] — asking the local daemon how the sync is going, status only,
//!   with the loopback-scoped TLS exception Termux forces.
//!
//! # Reading this code
//!
//! Per REWRITE.md §4.6 the codebase doubles as Rust learning material: every
//! public item says what it is *and why it exists*, `// rust:` notes mark
//! idioms that would surprise a Python developer, and each test states the
//! invariant it defends before asserting it.

#![warn(clippy::pedantic)]
#![forbid(unsafe_code)]

pub mod app;
pub mod config;
pub mod detail;
pub mod doc;
pub mod find;
pub mod input;
pub mod layout;
pub mod load;
pub mod open;
pub mod search;
pub mod status;
pub mod syncthing;
pub mod theme;

pub use app::{update, Effect, Model, Msg};
pub use doc::{Doc, FileRef, Location, Status, Store};
pub use theme::Theme;
