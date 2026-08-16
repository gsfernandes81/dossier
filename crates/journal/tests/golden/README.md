<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# Golden vectors — the cross-language contract

These fixtures are the anti-drift mechanism between the two fold
implementations (REWRITE.md §10): the Rust core in `crates/journal` and the
Python satellite's fold. Both read **these files**, not each other's tests.

They are plain JSON on purpose — a Python implementation must be able to run the
suite with no Rust toolchain anywhere in the loop.

## Schema

```json
{
  "name": "tombstone-then-newer-set",
  "why": "prose: why this behaviour is contractual",
  "files": ["<raw journal file body>", "…"],
  "canonical": "<the canonical JSON the fold must produce>",
  "torn": ["<expected torn tail>", "…"],
  "stats": {"malformed": 1},
  "compact": {"file": 0, "at": 1800000000000, "expect_lines": 6}
}
```

| Key | Meaning |
|---|---|
| `name` | must equal the file stem |
| `why` | the reason the vector exists; read it before changing one |
| `files` | raw file **bodies**, exactly as they would sit on disk — trailing newlines are significant (a body that does not end in one has a torn tail) |
| `canonical` | the expected canonical JSON, compared **byte-for-byte** |
| `torn` | optional; the torn tails expected across all files, in file order |
| `stats` | optional; only the health counters this vector pins (`folded`, `opaque`, `malformed`, `orphaned`, `duplicate_keys`) |
| `compact` | optional; compact `files[file]` as of wall clock `at` (ms), keep exactly `expect_lines` lines, and require the fold of the whole store to be **unchanged** |

## Canonical JSON

The comparison string is *not* whatever a serializer produces by default —
serde_json and `json.dumps` disagree on key order and escaping, which would make
the comparison unimplementable. It is defined as:

- keys sorted at every level,
- no insignificant whitespace (`,` and `:` separators),
- UTF-8 with no ASCII escaping,
- integers only (the format has no floats by construction, §3.2),
- shape: `{"enrich":…,"entities":…,"states":…,"tombstones":…}`, where `entities`
  and `states` and `enrich` are `{kind: {id: …}}` and `tombstones` is
  `{kind: [id, …]}`.

In Rust: `Fold::canonical_json()`. In Python the equivalent is
`json.dumps(state, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`.

Health counters are deliberately **excluded** from the canonical string: they
describe the files a given implementation happened to read, not the state.
`stats` pins them separately.

## Rules for changing a vector

A vector is a decision, not a snapshot. If a fold change makes one fail, either
it is a bug in the change, or the contract moved — and if the contract moved,
**REWRITE.md §3 and this fixture change in the same slice**. Never re-record a
canonical string to make a test pass.

## Coverage

`union-commutativity`, `tombstone-then-newer-set`,
`tombstone-then-newer-create`, `id-rename-with-inbound-supersedes`,
`state-per-key-lww-undismiss`, `torn-tail`, `mid-file-garbage`,
`lines-from-the-future`, `enrich-payload-lww`,
`compaction-preserves-fold`, `v2-export-shape`.

`compaction-preserves-fold` carries the two compaction rules that are easiest to
get wrong: an `unset` survives even when the `set` it cancelled is dropped (the
*other* writer may have set that field earlier, and the unset is what keeps it
removed), and a line from a newer format version is retained verbatim rather than
rewritten.

`v2-export-shape` is different in kind from the rest. The others are hand-written
probes of one rule each; that one is a real `dossier/export_journal.py` output —
nested `files` objects, a unicode name, ISO dates, a settings entity, a
namespaced review state, and an enrich payload whose float confidence became an
integer `confidence_permille`. Its canonical string was produced by the Python
fold and independently confirmed byte-for-byte by the Rust fold, so it records an
agreement between two implementations rather than one implementation's output. It
exists so a change to the exporter's shape fails in **both** language suites and
has to be a decision.
