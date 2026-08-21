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

//! Naming a new document.
//!
//! REWRITE.md §3.2: *"Doc `id` stays the slug (v2 rules: reserved-name guard,
//! collision suffixing)"*. This is that rule, plus **one addition the spec did
//! not consider** — see below. Ids minted before v3 keep whatever the exporter
//! gave them; this only decides what a document created *in* v3 is called.
//!
//! # Why a new id carries the device that made it
//!
//! v2 suffixed collisions against the store (`passport`, then `passport-2`),
//! which works when one process owns the whole store. **Under a journal it does
//! not.** Two devices, both offline, both creating "Passport" would each find
//! `passport` free and each mint it, and the fold treats them as one entity —
//! worse than a merge, because [`journal::fold`] makes a `create` **reset the
//! entity's fields**, so whichever create sorts later in `(ts, w)` order wipes
//! the other device's work. Nothing warns; the loser's ops are simply orphaned.
//!
//! Suffixing harder does not help: uniqueness by consulting a shared list is
//! exactly the coordination a sync-only, offline-first store cannot do. So the
//! id includes something no other device can produce — **the device name**, the
//! same one `ds init` fixed as the first half of the writer id:
//!
//! ```text
//! "Passport (IN)"  on desk   →  passport-in-desk
//! "Passport (IN)"  on phone  →  passport-in-phone
//! ```
//!
//! A collision is then only possible against this device's *own* documents,
//! which it can see, and which its own writer orders correctly — so the counter
//! suffix is back to being the local, coordination-free thing v2 assumed.
//!
//! It also makes the reserved-name guard fall out for free: `con` becomes
//! `con-desk`, and there is no Windows device called `con-<anything>`.
//!
//! **This is an addition to §3.2, which is otherwise frozen.** It changes no
//! op, no field and no fold rule — only what string a *new* document is keyed
//! by — and it is confined to this function.
//!
//! # What it leaves behind: two documents for one thing
//!
//! The other device's `passport-phone` and this one's `passport-desk` are now
//! two records of the same passport, and **nothing merges them yet**. That is
//! the trade taken deliberately: a duplicate you can see beats a merge you
//! cannot. The bare-slug alternative does not merge them *correctly* either —
//! it merges them wrongly and silently.
//!
//! The merge verb is unbuilt and unspecified, but it is not new machinery: §3.2
//! already contracts this exact op sequence for an **id rename** — copy the
//! fields, fix up every inbound `supersedes`, re-emit the effective `state`,
//! `delete` the old id, all as consecutive ops from one writer. A merge is that
//! without the `create`. Detection has an exact signal in the case that matters:
//! two documents listing the **same file path**, which is what two devices
//! filing the same synced PDF produce.
//!
//! Note that v2's duplicate machinery is no help here — its `dedup` clusters
//! duplicate *files* by perceptual hash, and this is two documents over one
//! file, which is the opposite shape.

use std::collections::BTreeSet;

/// What a name becomes when it has nothing a slug can keep.
const FALLBACK: &str = "document";

/// A name as an id fragment: ASCII, lowercase, hyphen-separated.
///
/// **Non-ASCII is dropped rather than transliterated.** v2 folded through NFKD
/// so `Café` became `cafe`; doing that here would mean a Unicode table in a
/// binary whose whole point is a 100 ms cold start, to improve a string the user
/// never sees. A name with nothing ASCII in it slugs to [`FALLBACK`] and is
/// still unique, because the device and the counter are appended after.
#[must_use]
pub fn slugify(name: &str) -> String {
    let mut slug = String::with_capacity(name.len());
    for ch in name.chars() {
        if ch.is_ascii_alphanumeric() {
            slug.extend(ch.to_lowercase());
        } else if !slug.ends_with('-') {
            slug.push('-');
        }
    }
    let trimmed = slug.trim_matches('-');
    if trimmed.is_empty() {
        FALLBACK.to_string()
    } else {
        trimmed.to_string()
    }
}

/// The id for a document being created on this device, unused in `taken`.
///
/// See the module header for why the device is part of it rather than only part
/// of the tie-break.
///
/// # Panics
/// It cannot: the search is bounded at `taken.len() + 2`, which offers
/// `taken.len() + 1` distinct candidates against at most `taken.len()` ids that
/// could block them. The bound is written down rather than left to an open range
/// because an unbounded search for a free name is the shape that hangs the UI if
/// the reasoning above is ever wrong.
#[must_use]
pub fn mint(name: &str, device: &str, taken: &BTreeSet<&str>) -> String {
    let base = format!("{}-{}", slugify(name), slugify(device));
    if !taken.contains(base.as_str()) {
        return base;
    }
    // Only this device's own ids can be in the way here, so counting up is a
    // local decision — the thing the bare slug could not claim.
    (2..=taken.len() + 2)
        .map(|n| format!("{base}-{n}"))
        .find(|id| !taken.contains(id.as_str()))
        .expect("more candidates than there are ids to block them")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_name_becomes_a_slug() {
        assert_eq!(slugify("Passport (IN)"), "passport-in");
        assert_eq!(slugify("  ENG-1  Medical  "), "eng-1-medical");
        assert_eq!(slugify("COC/Certificate"), "coc-certificate");
    }

    /// A name with nothing a slug can keep still has to produce one — the
    /// document exists either way, and an empty id is not a key.
    #[test]
    fn a_name_with_no_ascii_still_produces_an_id() {
        assert_eq!(slugify("路照"), FALLBACK);
        assert_eq!(slugify("···"), FALLBACK);
        assert_eq!(slugify(""), FALLBACK);
    }

    /// **The device is in the id, not just in the tie-break.** Two devices both
    /// offline, both creating the same document, must not mint the same key: a
    /// `create` resets the entity's fields, so the later one would wipe the
    /// other device's work rather than merely merging with it.
    #[test]
    fn two_devices_cannot_mint_the_same_id_for_the_same_name() {
        let none = BTreeSet::new();
        assert_eq!(mint("Passport", "desk", &none), "passport-desk");
        assert_eq!(mint("Passport", "phone", &none), "passport-phone");
    }

    /// The counter is back to being what v2 assumed: a purely local decision,
    /// because the only ids that can collide now are this device's own.
    #[test]
    fn a_repeat_on_the_same_device_counts_up() {
        let taken = BTreeSet::from(["passport-desk", "passport-desk-2"]);
        assert_eq!(mint("Passport", "desk", &taken), "passport-desk-3");
    }

    /// The Windows reserved-name guard §3.2 asks for, for free: every minted id
    /// ends with `-<device>`, and `con-desk` is not a reserved name.
    #[test]
    fn a_reserved_name_is_not_reserved_once_the_device_is_on_it() {
        let none = BTreeSet::new();
        for reserved in ["con", "prn", "aux", "nul", "com1", "lpt1"] {
            let id = mint(reserved, "desk", &none);
            assert_eq!(id, format!("{reserved}-desk"));
        }
    }

    /// A device name is slugged too — `ds init`'s grammar is stricter than this,
    /// but an id is not the place to discover that it was not.
    #[test]
    fn the_device_half_is_slugged_as_well() {
        let none = BTreeSet::new();
        assert_eq!(mint("Passport", "Gavin's S24U", &none), "passport-gavin-s-s24u");
    }
}
