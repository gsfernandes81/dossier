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

//! Physical locations: a tree of any depth, folded from `location` entities.
//!
//! The model is REWRITE.md §4.7.

use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};

/// A document's `location` value meaning it has no hard copy.
pub const DIGITAL_ONLY: &str = "none";

/// A physical location as stored.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Location {
    /// The journal entity id.
    pub id: String,
    /// What the user calls it.
    pub name: String,
    /// The location it sits in as stored; `None` at the top level.
    pub parent: Option<String>,
}

/// Where a document's hard copy is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HardCopy<'a> {
    /// No location yet, or one that has since been deleted.
    Unfiled,
    /// Marked as having no hard copy.
    DigitalOnly,
    /// Filed in this live location.
    At(&'a str),
}

/// The folded tree of physical locations.
///
/// Parents here are *effective*: a parent that names a deleted location, or a
/// location caught in a loop, reads as the top level, so the tree is always a
/// forest whatever two offline devices wrote.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Tree {
    nodes: BTreeMap<String, Location>,
    parents: BTreeMap<String, Option<String>>,
    children: BTreeMap<Option<String>, Vec<String>>,
    looped: BTreeSet<String>,
}

impl Tree {
    /// Builds the tree from every live location.
    #[must_use]
    pub fn new(locations: impl IntoIterator<Item = Location>) -> Self {
        let nodes: BTreeMap<String, Location> =
            locations.into_iter().map(|location| (location.id.clone(), location)).collect();
        let looped = loops(&nodes);
        let parents: BTreeMap<String, Option<String>> = nodes
            .values()
            .map(|location| {
                let parent = location
                    .parent
                    .clone()
                    .filter(|parent| nodes.contains_key(parent) && !looped.contains(&location.id));
                (location.id.clone(), parent)
            })
            .collect();
        let mut children: BTreeMap<Option<String>, Vec<String>> = BTreeMap::new();
        for (id, parent) in &parents {
            children.entry(parent.clone()).or_default().push(id.clone());
        }
        for siblings in children.values_mut() {
            siblings
                .sort_by(|a, b| natural_cmp(&nodes[a].name, &nodes[b].name).then_with(|| a.cmp(b)));
        }
        Self { nodes, parents, children, looped }
    }

    /// Whether there are no locations at all.
    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    /// How many locations there are.
    #[must_use]
    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    /// The live location with this id.
    #[must_use]
    pub fn get(&self, id: &str) -> Option<&Location> {
        self.nodes.get(id)
    }

    /// Every location, by id.
    pub fn iter(&self) -> impl Iterator<Item = &Location> {
        self.nodes.values()
    }

    /// The location `id` sits in, or `None` at the top level.
    #[must_use]
    pub fn parent(&self, id: &str) -> Option<&str> {
        self.parents.get(id).and_then(Option::as_deref)
    }

    /// The locations directly inside `parent` (`None` for the top level), in
    /// sibling order.
    #[must_use]
    pub fn children(&self, parent: Option<&str>) -> &[String] {
        self.children.get(&parent.map(str::to_string)).map_or(&[], Vec::as_slice)
    }

    /// The ids from the top level down to `id`, inclusive.
    #[must_use]
    pub fn ancestry(&self, id: &str) -> Vec<&str> {
        let mut chain = Vec::new();
        let mut at = self.nodes.get_key_value(id).map(|(key, _)| key.as_str());
        while let Some(id) = at {
            chain.push(id);
            at = self.parent(id);
        }
        chain.reverse();
        chain
    }

    /// The names from the top level down to `id`, joined with ` › `.
    #[must_use]
    pub fn path(&self, id: &str) -> String {
        self.ancestry(id)
            .iter()
            .map(|id| self.nodes[*id].name.as_str())
            .collect::<Vec<_>>()
            .join(" › ")
    }

    /// `id` and every location inside it, parents before children.
    #[must_use]
    pub fn subtree(&self, id: &str) -> Vec<&str> {
        let mut out = Vec::new();
        let mut stack: Vec<&str> =
            self.nodes.get_key_value(id).map(|(key, _)| key.as_str()).into_iter().collect();
        while let Some(at) = stack.pop() {
            out.push(at);
            stack.extend(self.children(Some(at)).iter().rev().map(String::as_str));
        }
        out
    }

    /// Whether `id` is `ancestor` or inside it.
    #[must_use]
    pub fn is_within(&self, id: &str, ancestor: &str) -> bool {
        self.ancestry(id).contains(&ancestor)
    }

    /// Every location in sibling order, parents before children.
    #[must_use]
    pub fn shelf(&self) -> Vec<&str> {
        let mut out = Vec::new();
        for top in self.children(None) {
            out.extend(self.subtree(top));
        }
        out
    }

    /// The locations whose path matches `query`, in shelf order: exact matches,
    /// or fuzzy ones only when nothing matches exactly, as the Find view does.
    #[must_use]
    pub fn search(&self, query: &str) -> Vec<&str> {
        let paths: Vec<(&str, String)> =
            self.shelf().into_iter().map(|id| (id, crate::search::fold(&self.path(id)))).collect();
        let pass = |fuzzy: bool| -> Vec<&str> {
            paths
                .iter()
                .filter(|(_, path)| crate::search::matches(path, query, fuzzy))
                .map(|(id, _)| *id)
                .collect()
        };
        let exact = pass(false);
        if !exact.is_empty() || !crate::search::can_fuzz(query) {
            return exact;
        }
        pass(true)
    }

    /// The locations two devices moved into each other, which read as top level.
    #[must_use]
    pub fn looped(&self) -> &BTreeSet<String> {
        &self.looped
    }

    /// The sibling of a new or renamed location already called `name`, if any.
    ///
    /// Names compare as search compares, so `Slot 1` and `slot 1` clash.
    #[must_use]
    pub fn sibling_named(
        &self,
        parent: Option<&str>,
        name: &str,
        except: Option<&str>,
    ) -> Option<&str> {
        let wanted = crate::search::fold(name.trim());
        self.children(parent)
            .iter()
            .filter(|id| Some(id.as_str()) != except)
            .find(|id| crate::search::fold(self.nodes[*id].name.trim()) == wanted)
            .map(String::as_str)
    }

    /// Reads a document's raw `location` value.
    #[must_use]
    pub fn hard_copy<'a>(&'a self, location: Option<&str>) -> HardCopy<'a> {
        match location {
            Some(DIGITAL_ONLY) => HardCopy::DigitalOnly,
            Some(id) => {
                self.nodes.get_key_value(id).map_or(HardCopy::Unfiled, |(key, _)| HardCopy::At(key))
            }
            None => HardCopy::Unfiled,
        }
    }
}

/// The locations whose stored parent chain comes back to themselves.
fn loops(nodes: &BTreeMap<String, Location>) -> BTreeSet<String> {
    let mut looped = BTreeSet::new();
    for start in nodes.keys() {
        let mut seen = BTreeSet::new();
        let mut at = start.as_str();
        while let Some(parent) = nodes.get(at).and_then(|location| location.parent.as_deref()) {
            if parent == start {
                looped.insert(start.clone());
                break;
            }
            if !nodes.contains_key(parent) || !seen.insert(parent) {
                break;
            }
            at = parent;
        }
    }
    looped
}

/// Orders names the way a person reads them: `slot 2` before `slot 10`.
///
/// Runs of digits compare as numbers; everything else compares folded, as
/// search folds it.
#[must_use]
pub fn natural_cmp(a: &str, b: &str) -> Ordering {
    let (a, b) = (crate::search::fold(a), crate::search::fold(b));
    let mut left = a.chars().peekable();
    let mut right = b.chars().peekable();
    loop {
        match (left.peek().copied(), right.peek().copied()) {
            (None, None) => return Ordering::Equal,
            (None, Some(_)) => return Ordering::Less,
            (Some(_), None) => return Ordering::Greater,
            (Some(x), Some(y)) if x.is_ascii_digit() && y.is_ascii_digit() => {
                let take = |chars: &mut std::iter::Peekable<std::str::Chars>| {
                    let mut run = String::new();
                    while let Some(c) = chars.next_if(char::is_ascii_digit) {
                        run.push(c);
                    }
                    run
                };
                let (x, y) = (take(&mut left), take(&mut right));
                let (x, y) = (x.trim_start_matches('0'), y.trim_start_matches('0'));
                let order = x.len().cmp(&y.len()).then_with(|| x.cmp(y));
                if order != Ordering::Equal {
                    return order;
                }
            }
            (Some(x), Some(y)) => {
                if x != y {
                    return x.cmp(&y);
                }
                left.next();
                right.next();
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(id: &str, name: &str, parent: Option<&str>) -> Location {
        Location { id: id.into(), name: name.into(), parent: parent.map(str::to_string) }
    }

    fn desk() -> Tree {
        Tree::new([
            at("desk", "desk", None),
            at("folder", "leather folder", Some("desk")),
            at("s10", "slot 10", Some("folder")),
            at("s2", "slot 2", Some("folder")),
            at("pouch", "passport pouch", Some("desk")),
            at("bag", "ship bag", None),
        ])
    }

    /// Siblings sort by name with numbers compared as numbers.
    #[test]
    fn siblings_read_in_natural_order() {
        let tree = desk();
        assert_eq!(tree.children(Some("folder")), ["s2", "s10"]);
        assert_eq!(tree.children(None), ["desk", "bag"]);
        assert_eq!(tree.shelf(), ["desk", "folder", "s2", "s10", "pouch", "bag"]);
        assert_eq!(natural_cmp("Slot 2", "slot 10"), Ordering::Less);
        assert_eq!(natural_cmp("slot 02", "slot 2"), Ordering::Equal);
    }

    /// The path names every location from the top level down.
    #[test]
    fn a_path_reads_from_the_top() {
        let tree = desk();
        assert_eq!(tree.path("s2"), "desk › leather folder › slot 2");
        assert_eq!(tree.subtree("folder"), ["folder", "s2", "s10"]);
        assert!(tree.is_within("s10", "desk"));
        assert!(!tree.is_within("desk", "s10"));
    }

    /// A parent that names a deleted location reads as the top level.
    #[test]
    fn an_orphan_reads_as_top_level() {
        let tree = Tree::new([at("pouch", "pouch", Some("gone"))]);
        assert_eq!(tree.parent("pouch"), None);
        assert_eq!(tree.children(None), ["pouch"]);
    }

    /// Only the locations in a loop read as top level; one hanging off the loop
    /// stays where it was put.
    #[test]
    fn a_loop_breaks_at_its_members_only() {
        let tree =
            Tree::new([at("a", "a", Some("b")), at("b", "b", Some("c")), at("c", "c", Some("b"))]);
        assert_eq!(tree.looped().iter().collect::<Vec<_>>(), ["b", "c"]);
        assert_eq!(tree.parent("a"), Some("b"));
        assert_eq!(tree.parent("b"), None);
        assert_eq!(tree.parent("c"), None);
        assert_eq!(tree.shelf(), ["b", "a", "c"]);
    }

    /// Sibling names clash as search compares them, and a location never
    /// clashes with itself when renamed.
    #[test]
    fn sibling_names_clash_folded() {
        let tree = desk();
        assert_eq!(tree.sibling_named(Some("folder"), " Slot 2", None), Some("s2"));
        assert_eq!(tree.sibling_named(Some("folder"), "slot 2", Some("s2")), None);
        assert_eq!(tree.sibling_named(None, "slot 2", None), None);
    }

    /// Search matches the full path, so a slot is found by its folder's name.
    #[test]
    fn search_matches_the_whole_path() {
        let tree = desk();
        assert_eq!(tree.search("folder slot"), ["s2", "s10"], "word by word");
        assert_eq!(tree.search("leather"), ["folder", "s2", "s10"]);
        assert_eq!(tree.search("slot 1"), ["s10"]);
        assert_eq!(tree.search("pasport"), ["pouch"], "the typo falls back to fuzzy");
    }

    /// A raw `location` reads as a hard copy only when it names a live location.
    #[test]
    fn a_dangling_location_reads_as_unfiled() {
        let tree = desk();
        assert_eq!(tree.hard_copy(Some("s2")), HardCopy::At("s2"));
        assert_eq!(tree.hard_copy(Some(DIGITAL_ONLY)), HardCopy::DigitalOnly);
        assert_eq!(tree.hard_copy(Some("gone")), HardCopy::Unfiled);
        assert_eq!(tree.hard_copy(None), HardCopy::Unfiled);
    }
}
