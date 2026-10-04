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

//! The dossier journal store: the format contract of
//! [`REWRITE.md`](../../../REWRITE.md) in code.
//!
//! Each writer (`desk-core`, `phone-core`, `desk-lab`) appends to **its own**
//! JSONL file and no other, and Syncthing replicates the directory. No file
//! ever has two authors, so conflicts are structurally impossible rather than
//! merely handled. State is the deterministic [`fold`](fold()) of the union of
//! every file, ordered by the hybrid logical clock pair `(ts, w)`; losing a
//! file loses that writer's contribution and nothing else.

// Includes the numeric-cast lints: a truncating cast in a timestamp is a bug.
#![warn(clippy::pedantic)]
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
pub use store::{Anomaly, Journal, Load, Namespace, Stamp};
pub use watermark::{Damage, HighWater, Mark};
pub use writer::{replace_file, Draft, Hlc, Writer};
