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

//! The dossier v3 journal store.
//!
//! Phase R1 of [`REWRITE.md`](../../../REWRITE.md). This crate is the **format
//! contract** (§3) in code: what an op is, what a journal file may be called,
//! and how a pile of ops becomes the current state. It is a library with no
//! terminal, no CLI and no global state, so the invariants can be hammered
//! directly by tests — and so the Python satellite has something precise to
//! match, since both implementations fold the same files.
//!
//! # The idea in one paragraph
//!
//! Each writer (`desk-core`, `phone-core`, `desk-lab`) appends to **its own**
//! JSONL file and no other. Syncthing replicates the directory. Because no file
//! ever has two authors, Syncthing never sees two versions of one file to
//! reconcile — conflicts are structurally impossible rather than merely
//! handled. State is the deterministic [`fold`](fold()) of the union of every
//! file, ordered by the hybrid logical clock pair `(ts, w)`. Adding a device
//! adds a file; losing a file loses that writer's contribution and nothing
//! else.
//!
//! # What is here so far
//!
//! - [`op`] — the line format: parse, classify, round-trip, torn-tail handling.
//! - [`fold`] — ops in, state out, plus the health counters `ds status` reports.
//! - [`names`] — the frozen filename grammar that decides what gets folded.
//! - [`store`] — reading a journal directory: the `meta`/`enrich` split,
//!   per-file reports, and the anomalies `ds status` reports.
//! - [`watermark`] — truncation detection: the high-water marks that catch a
//!   journal reverted behind Syncthing's back.
//! - [`writer`] — appending: the hybrid logical clock, the one-process-per-writer
//!   lock, and the torn-tail repair that has to happen before any append.
//! - [`compact`] — shrinking a writer's own file without changing what it means.
//!
//! # Reading this code
//!
//! Per REWRITE.md §4.6 the codebase doubles as Rust learning material: every
//! public item says what it is *and why it exists*, and `// rust:` notes mark
//! places where an idiom would surprise a Python developer. Tests state the
//! invariant they defend in a sentence before asserting it.

// Pedantic is on for the workspace and its findings are triaged in place rather
// than silenced wholesale (§4.6). Unlike the R0.2 spike, this crate handles real
// user data, so the numeric-cast lints stay *on* here — a truncating cast in a
// timestamp is a bug, not display arithmetic.
#![warn(clippy::pedantic)]
// There is no need for `unsafe` anywhere in this design.
#![forbid(unsafe_code)]

pub mod compact;
pub mod fold;
pub mod names;
pub mod op;
pub mod store;
pub mod watermark;
pub mod writer;

pub use compact::{plan as compaction_plan, Plan as CompactionPlan};
pub use fold::{fold, Entity, EntityKey, Fold, FoldStats};
pub use op::{parse_body, parse_line, Line, Op, OpKind, OpaqueReason, FORMAT_VERSION};
pub use store::{Anomaly, Journal, Load, Namespace};
pub use watermark::{Damage, HighWater, Mark};
pub use writer::{Draft, Hlc, Writer};
