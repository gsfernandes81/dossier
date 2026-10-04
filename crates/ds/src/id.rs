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

//! Naming a new record: `<slug>-<device>`, then a counter.
//!
//! The device is in the id because two offline devices creating the same name
//! would otherwise mint one key, and the fold's `create` resets an entity's
//! fields, so the later device would silently wipe the other's work. With the
//! device in it, the only collisions are this device's own, which it can see,
//! and a counter settles them. The two records that leave behind for one paper
//! stay visible rather than merged wrongly.

use std::collections::BTreeSet;

/// What a name becomes when it has nothing a slug can keep.
const FALLBACK: &str = "document";

/// A name as an id fragment: ASCII, lowercase, hyphen-separated.
///
/// Non-ASCII is dropped rather than transliterated, which would need a
/// Unicode table for a string nobody reads; the device and counter keep the
/// id unique either way.
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

/// The id for a record created on `device`, unused in `taken`.
#[must_use]
pub fn mint(name: &str, device: &str, taken: &BTreeSet<&str>) -> String {
    let base = format!("{}-{}", slugify(name), slugify(device));
    if !taken.contains(base.as_str()) {
        return base;
    }
    // Each candidate is new and `taken` is finite, so this ends.
    let mut n = 2;
    loop {
        let id = format!("{base}-{n}");
        if !taken.contains(id.as_str()) {
            return id;
        }
        n += 1;
    }
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

    /// A name with nothing a slug can keep still produces an id.
    #[test]
    fn a_name_with_no_ascii_still_produces_an_id() {
        assert_eq!(slugify("路照"), FALLBACK);
        assert_eq!(slugify("···"), FALLBACK);
        assert_eq!(slugify(""), FALLBACK);
    }

    /// Two devices creating the same name mint different ids.
    #[test]
    fn two_devices_cannot_mint_the_same_id_for_the_same_name() {
        let none = BTreeSet::new();
        assert_eq!(mint("Passport", "desk", &none), "passport-desk");
        assert_eq!(mint("Passport", "phone", &none), "passport-phone");
    }

    /// A repeat on one device counts up.
    #[test]
    fn a_repeat_on_the_same_device_counts_up() {
        let taken = BTreeSet::from(["passport-desk", "passport-desk-2"]);
        assert_eq!(mint("Passport", "desk", &taken), "passport-desk-3");
    }

    /// No minted id is a Windows reserved name: each ends with its device.
    #[test]
    fn a_reserved_name_is_not_reserved_once_the_device_is_on_it() {
        let none = BTreeSet::new();
        for reserved in ["con", "prn", "aux", "nul", "com1", "lpt1"] {
            let id = mint(reserved, "desk", &none);
            assert_eq!(id, format!("{reserved}-desk"));
        }
    }

    /// The device half is slugged too.
    #[test]
    fn the_device_half_is_slugged_as_well() {
        let none = BTreeSet::new();
        assert_eq!(mint("Passport", "Gavin's S24U", &none), "passport-gavin-s-s24u");
    }
}
