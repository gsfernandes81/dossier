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

//! `ds` — the TUI and CLI over a `journal` store. Everything that decides
//! lives in this library, testable without a terminal; `main.rs` is the shell
//! around it. The plan is [`REWRITE.md`](../../../REWRITE.md) and the layout
//! [`REWRITE-UI.md`](../../../REWRITE-UI.md).

#![warn(clippy::pedantic)]
#![forbid(unsafe_code)]

pub mod app;
pub mod bundles;
pub mod check;
pub mod complete;
pub mod config;
pub mod detail;
pub mod doc;
pub mod edit;
pub mod find;
pub mod follow;
pub mod id;
pub mod init;
pub mod input;
pub mod layout;
pub mod load;
pub mod locpick;
pub mod open;
pub mod pick;
pub mod place;
pub mod prompt;
pub mod scans;
pub mod search;
pub mod sheet;
pub mod status;
pub mod syncthing;
pub mod theme;
pub mod versions;
pub mod wsl;

pub use app::{update, Effect, Model, Msg, View};
pub use doc::{Bundle, Doc, FileRef, Member, Membership, Status, Store};
pub use place::{HardCopy, Location, Tree};
pub use theme::Theme;
