<!-- Copyright © 2026-present gsfernandes81. Part of "dossier" (AGPL-3.0). -->

# Developer / agent context

Notes for anyone (human or agent) working *on* dossier, as opposed to using it — the
non-obvious things that aren't in the code or git history. User-facing docs are in
[`../guide/`](../guide/); conventions and the remote dev-container in
[`../../CLAUDE.md`](../../CLAUDE.md); design rationale in
[`../../DESIGN.md`](../../DESIGN.md); phase status in [`../../ROADMAP.md`](../../ROADMAP.md).

- **[project-context.md](project-context.md)** — what dossier is, the sync topology,
  hard product constraints (no VLM on the phone; scan service built-not-registered;
  real-store read-only), and the measured performance decisions that must not be undone.
- **[ci-gate.md](ci-gate.md)** — how to verify CI honestly: mirror its environment
  (ty as linux/no-extras), run the out-of-`testpaths` driver test on any TUI change, and
  read the run *conclusion* per job rather than trusting `gh run watch`.
- **[testing.md](testing.md)** — testing the Textual layer: never sleep-then-assert
  (poll for the effect), the `DEFAULT_CSS`/`SCOPED_CSS` screen-styling gotcha, and the
  real-terminal PTY driver in `tools/`.

Started with a handoff mid-feature? See [`../../HANDOFF.md`](../../HANDOFF.md) if present.
