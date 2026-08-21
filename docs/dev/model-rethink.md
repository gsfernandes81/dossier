# The model rethink — edges, chains, bundles

**Status: design in progress. Nothing here is implemented, and nothing here should
be implemented without the user.** R4's verb set is complete (create, edit, undo,
redo, delete) and the port is deliberately paused at that point.

This note exists because the plan below R4 was never really *designed*. §8's
disposition table asks *"what happens to each v2 feature"*, so nearly everything
marked **Port** was ported because it existed, not because it was re-argued for
v3. The parts that were genuinely re-derived — the journal, the search, the
phone-shaped UI — are the parts that feel right. This is the re-derivation of the
rest.

The user's standard, in their words: **"as simple at base yet as versatile as
git"**, and on the five review surfaces, **"feels like bloat"**.

---

## 1 · Settled

### Edges replace four mechanisms

Today `tags`, `bundles`, `location`+`slot`+`subslot`, `supersedes` and `files` are
five separate mechanisms with five editors and five bits of fold logic. They
become one:

```
(document) —kind→ (target)      with a payload
```

`kind` is a **small closed enum**, so it is checked by the compiler rather than by
spelling. That is the whole reason this beats the set-with-a-name-prefix model the
user first proposed (`loc-cert-file`, `succeeded-by-<name>`): a prefix convention
is a type system with no checker, where `succeded-by-passport` silently creates a
new, permanently empty concept.

Edges carry the two things sets lack and this domain is full of:

- **direction** — succession is `A → B`, not a set containing both;
- **a payload** — the slot address lives here, so it needs no naming hack.

Known kinds so far: `located-in`, `succeeded-by`, `in-bundle`/`needed-for`,
`has-file`, `slice-of`. Adding one later is adding one variant — which is what
keeps the door open for templates (§2) without building anything now.

**Membership stays stored on the document side, never on the target side.** Under
§3.2's whole-list LWW, two devices filing different documents into one folder must
not collide, and they only avoid it if each writes its own document's list.

### The succession chain is the identity — `kind` is dead

A `kind` field on documents was proposed and **rejected by the user**, correctly:
*"The day I get a driving licence for a second country, `kind:driving licence`
rots. I already have multiple countries' seaman's books."* A UK licence and an
Indian licence are not interchangeable, do not supersede each other, and cannot
both answer "bring your driving licence". The taxonomy would immediately need
`(type, issuer)`, and that is two fields and a maintenance chore that rots at
every border.

What replaces it costs nothing, because **it is already implemented**: if `A` is
superseded by `B`, the user has already asserted they are the same thing. A
succession chain is therefore an equivalence class built by hand, one edge at a
time, with nothing to rot.

- Two countries' seaman's books are two chains, automatically, because neither was
  ever superseded by the other. Nothing had to know what a seaman's book *is*.
- A document never renewed is a chain of one.
- *"Do I hold a valid X?"* is *"what is at the head of this chain, and has it
  expired?"*

It is also **more accurate than a type** for the real case: a Panama transit does
not need "a seaman's book", it needs the Indian one. Pointing at a document is the
correct specificity; pointing at a type was always a lie needing patches.

**What is genuinely lost:** succession *suggestion* ("this new passport probably
replaces your old one"). Without a type it falls back to name similarity, which is
what v2 did. That is a heuristic on a suggestion the user can decline, costing one
keystroke when wrong — not worth a taxonomy that rots.

**What survives of the word `kind`:** only a closed enum the *program* writes and
the user never types, distinguishing a document from an event from a template so
the browse list does not fill with trips. It cannot rot by construction. These are
two different concepts that were briefly given one name; that was a mistake.

### The list shows chain heads

*User: "Chain heads is an epiphany. We definitely want that and only want to
enable full views behind a command."*

Browse shows the head of each chain. Superseded versions stop cluttering the list
without being deleted, and the full chain is available behind an explicit command.
The store is already halfway there — `Doc.superseded` is derived, and superseded
documents already drop out of the expiry watch. This makes the *list* agree with
the *watch* about what the user currently owns.

**Open:** what the command is called and how history is displayed. Also: the
header's attention count and every filter should be chain-head-scoped too, or the
list and the counts will disagree.

### Insert-and-shift is deleted, not simplified

*User: "there's no case where something is inserted between 8 and 9 currently
because they are physical, attached slots to a folder."*

Slots are physical compartments riveted into a folder. A slot address is a fixed
label, never a position in a sequence, so nothing can be inserted and nothing ever
shifts. **The fiddliest feature in the plan simply does not exist.**

A slot also holds *several* documents (the user's correction that killed an
earlier append-only-slots idea), so `location / slot / subslot` is a **container
path**, not an index, and documents within a slot are unordered.

Two things fall out free: a location can know how many slots it has, so filing into
slot 13 of a 12-slot folder is catchable; and the slot picker becomes a **list you
choose from** rather than a number you type — the difference between a picker and a
form field on a phone.

### Readiness is deleted; the event date replaces it

*User: "readiness isn't really needed but the standard red expired in ds would work
just fine for that."*

Correct, with one refinement worth keeping. Plain "expired today" will not warn
that an ENG-1 lapses six weeks *before* a trip three months out — which is exactly
the case readiness existed for. Under an event with a date, that is a **variable
substitution in a function that already exists**: when looking at a bundle, colour
expiry against the *event's* date instead of today's. No `min_valid_days`, no
checklists, no rules engine.

---

## 2 · Settled: bundles

### An event is a document

A trip has a name, a date, notes, and a set of related documents — which is a
description of a `doc`. So the event is an ordinary document, **its expiry date is
the event date**, and membership is one edge kind.

Everything applies for free: create, rename, edit, delete, undo, search, and the
expiry colouring — because it *is* a document. No bundle entity, no bundle editor,
no bundle-rename machinery, no readiness subsystem. The event goes red when it has
passed, which is correct and cost nothing.

### Templates land on top without needing anything now

*User: "Event is a document feels idiomatic but I want to leave the door open for
templates in the future."*

A template is a document too. The only difference is what its edges do:

```
panama-2026      —took→   passport-in-2019            a record: frozen, concrete
transit-template —needs→  passport-in-2019 → resolves to the head of its chain
```

Same edge, one flag, two variants of the enum. Renew the passport and every
template following that chain updates itself — the self-updating behaviour `kind`
was reaching for, delivered by machinery already shipped.

This also draws an honest distinction: **a past event is a record** (concrete,
frozen — you took the 2019 passport, that is history) and **a future event or
template is a plan** (follows the chain).

**Nothing needs building now to keep this door open.** That is the payoff of the
closed enum. The one thing to stay deliberate about is that edges carry a payload,
because a payload is where a *role* would live ("this passport is here as the
passport"), and roles are how a template's slot knows what filled it.

### Selection — the ephemeral half

Half of grouping is not durable. "These four, right now, for this" is a
**selection** — git's index, essentially. Multi-select in the list, verbs act on
the selection, and *"save this selection as an event"* is one more verb.

That splits bundles cleanly: ephemeral selections need no entity and store nothing;
durable bundles become event documents. It also pays off well beyond bundles —
bulk tag, bulk file, bulk supersede — and bulk filing is probably the most tedious
thing in the app today.

### A standing export to `bundles/<name>/`

*User: "export would be a one time thing or a keep this bundle exported in this
bundles/<name> folder kind of thing."*

The standing version is the bigger feature, and the reason is not convenience:
**a folder Syncthing carries means the bundle works without dossier at all.** At an
immigration desk you open the file manager, not a TUI. The one moment these
documents are actually needed is the moment you least want to depend on a binary
launching correctly on a phone in a queue.

It also collapses the earlier fork: **a one-time export is a standing export you
never refresh.** Same verb, same manifest, same folder — the only difference is
whether the bundle was saved. One mechanism, not two.

Chain-following makes refresh correct for free: a *plan* bundle follows chains, so
refreshing after a renewal swaps the old scan for the new one. A *record* bundle is
frozen and is never refreshed.

**Duplication is accepted** (user's call): every bundled file exists twice and syncs
twice. For a store of this size that is fine; it is a decision, not a surprise.

**File naming:** `01 Passport (IN).pdf`, `02 Seaman's Book.pdf` — in the order the
documents will be asked for, taken from the bundle's edge order rather than
alphabetically. The user already does this by hand (see §5).

---

## 3 · Open

### Who owns the bundle folder

*User: "We need both devices to own them in some way. Undecided how."*

The obvious objection to shared ownership is that two devices regenerating
`bundles/panama-2026/` produce Syncthing conflict files — reintroducing through
the back door the exact problem the journal exists to prevent.

**The proposed way out: make the export a deterministic pure function of the
fold.** The journal never conflicts because each writer owns a *file*, not because
one device does all the work. The same trick applies here, one level up: if
regenerating a bundle from a given folded state always produces **byte-identical
files with identical mtimes**, then two devices generating it produce no difference
for Syncthing to see, and regenerating is a no-op whenever nothing changed.

Under that rule ownership stops mattering:

- both devices may regenerate freely;
- when the bundle genuinely changes, both write the *same* new bytes, so the worst
  case is a brief flap rather than a conflict;
- the folder converges because the journal converges — derived state that is a pure
  function of converged state is itself convergent.

Deletion is the sharp edge: device A removes a file because a document left the
bundle, device B has not folded that op yet and re-creates it. This flaps until
both have the same journal state, then settles. Self-healing, but noisy, and the
noise is in the user's real files tree.

**To verify before committing to this:** that Syncthing's scanner really does see
no change for identical content *and* identical mtime, in both directions, on
Android and Windows. The mechanism is right in principle; the claim about the
scanner is untested and must not be assumed.

**The two-folder arrangement is not the answer here** — see §3's own entry below.
It covers **journals only**, so bundles live in the ordinary bidirectional files
folder and get no single-writer guarantee from it. That removes the alternative
this question had, leaving determinism as the surviving proposal and making the
Syncthing scanner check load-bearing rather than merely prudent.

### When it refreshes

*User: "Not for old events, for events in the future: on ds run would be acceptable
but that's just my first instinct to reach for it."*

The instinct works, with one binding constraint: **it must not be on the startup
path.** §9's budget is launch-to-usable under 100 ms on the phone, and scanning and
copying files would blow it outright.

The shape that fits:

- the **staleness check** is a pure in-memory comparison of the manifest against
  the fold — microseconds, safe to do at launch;
- the **copy** happens on a background thread after first paint, the same shape
  scans already use;
- `ds status` gets one line — *"2 bundle folders are stale"* — which fits the
  router's existing rule that every line names the verb that fixes it.

"Old" versus "future" is free: the event document's date against today.

### The derived-files rule

**This is the one the user explicitly asked to have written down.**

> Dossier owns `bundles/` completely and touches nothing outside it, ever. Within
> it, dossier only ever deletes or overwrites files it recorded putting there —
> the manifest is the record. Anything else in that directory is left strictly
> alone.

It matters because a standing export is **the first feature that writes *derived*
files into the real files tree**, and REWRITE.md §7's standing guarantee is that
the tree is untouched except for user-approved `organize`/`file` moves. That
guarantee needs a stated exception rather than a silent one.

The second half of the rule is what makes both deletion and ingest safe at once:
an unrecognised file in a bundle folder is a *candidate* to be ingested, never
garbage to be cleaned up.

Per the user, this stays in the plan for now and goes into the code only if it is
implemented this way.

### Deletion, and the folder as an intake surface

*User: "We have the trash bins. Bundles must be duplicates of existing documents
and any documents added must be ingested and be made new documents or found to be
subsets / slices etc."*

This makes `bundles/<name>/` **bidirectional**, which is more interesting than the
one-way export it started as: a file dropped in there from a phone's file manager
gets ingested on the next run — becoming a new document, or being recognised as a
`slice-of` an existing one (the user's "subset copies" from the very first
message, now an edge kind rather than a naming convention).

Syncthing versioning means deletion is recoverable, so the risk is lower than
first framed. The manifest still earns its keep, and its real job is not
documentation: **dossier only ever deletes files it recorded putting there.**
Anything else in that folder is left strictly alone — which is also what makes
ingest safe, because an unrecognised file is a candidate, never garbage.

### The Syncthing two-folder arrangement: journals only

*User: "a separate send and a separate receive folder for the journals only."*
(Recorded here because it was decided in an earlier session that did not survive
in full.)

Each device gets a **send-only** folder holding the journals it writes, and a
**receive-only** folder holding the journals it receives. The real files tree —
the PDFs and scans — stays in an ordinary bidirectional folder.

What it buys is **structural enforcement of §3.1's single-writer rule at the
transport layer** rather than only in the application. A device's own journal
cannot be overwritten from the network, because Syncthing never writes into a
send-only folder; and another device's journal cannot be damaged locally, because
the app never writes into the receive-only one. §3.3's "journal damage without a
conflict file" (a versioning restore, a partial sync, a byte-length shrink) stops
being a thing the app has to defend against and becomes a thing that mostly cannot
happen. The local high-water marks stay worth keeping as a backstop.

It scales as one folder per **writer**: send-only on its owner, receive-only
everywhere else. Two devices, two folders. The per-device setup this implies is
exactly why the user wants `ds` to configure it rather than doing it by hand
(REWRITE.md §7).

**Consequences to work through before building it:**

- **§3.1 says the journal directory is `<syncthing_root>/.dossier/journal/`.**
  Two Syncthing folders means two paths, so this becomes something like
  `journal/out/` and `journal/in/` — a §3.1 change, needing an amendment note in
  the same style as the id one, since §3 is otherwise frozen.
- **The fold must read both roots.** `Journal::new` takes a single directory
  today. Sibling subdirectories under one parent is the neater shape, if
  Syncthing is happy sharing a parent between two folders — worth verifying.
- **The compaction temp file** (`<writer>.jsonl.tmp-<pid>`) now lives in a
  send-only folder and would propagate. The `.stignore` step already in R7's
  cutover checklist is unchanged and still required.
- **The satellite** writes as `desk-lab` in the `enrich` namespace from the same
  machine as `desk-core`; it presumably shares the desk's send folder rather than
  getting a third. A detail, not a blocker.

### Still unanswered

- What the full-chain-history command is called, and how a chain is drawn.
- Whether the review surfaces survive at all as surfaces, or become saved queries
  over the one list. The user's *"5 review surfaces feels like bloat"* is
  unresolved; the queries idea is untested against what the five actually do.
- Where the document-merge verb lands, given all of the above (previously placed in
  the review surface — see REWRITE.md §3.2's amendment note).

---

## 4 · Rejected, with reasons

Kept so nobody re-proposes them without new information.

| Idea | Why not |
|---|---|
| Sets named by prefix (`loc-`, `succeeded-by-`) | A type system with no checker; a typo makes a silently-empty concept. Git gets away with `refs/heads/` because *git* writes those names — here the user types them. |
| Naming a succession by the successor's *name* | Names change, ids do not. Every reference goes stale on the first rename, and the fold cannot tell that from a set you meant to create. |
| `kind` as a user-facing taxonomy | Rots at the first second-country document. Succession chains give the same grouping for free and more accurately. |
| Append-only sparse slots | A slot holds several documents, so slot numbers are not per-document positions. |
| Insert-and-shift | Slots are physically attached to the folder. Nothing can be inserted. |
| Readiness rules, `min_valid_days`, checklists | The event's date plus the existing expiry colouring covers the 5% that was load-bearing. |
| Bundle templates as a subsystem | A template is a document whose edges follow chains. |

---

## 5 · A method worth reusing

Twice now the user's **existing manual habits** have predicted the right model
better than the data model did:

- documents are already named `Passport (IN)` — the country is *in the name*,
  which is precisely why a country-blind `kind` rotted on contact;
- bundle files are already numbered `01`, `02`, `03` by hand — which says ordering
  matters, is currently manual, and should be automated rather than invented.

*User, on the numbering: "This may or may not be a hint we're on the right track."*

It is a hint, and a specific one: when a model matches what someone already does by
hand, the tool is **formalising an existing practice rather than imposing a new
one**, which is the strongest available predictor that it will actually get used.
The manual system is the requirements document. Read it before designing the
feature.
