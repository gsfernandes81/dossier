#!/usr/bin/env bash
# The readouts for ds-dev, on the HOST side: what state the container is in, what
# a rebuild landed, and the start-up lines. Called by dev/Makefile —
#
#     make status | make verify | make boot-log
#
# — and not meant to be run directly, though nothing stops you: it takes SUDO and
# CONTAINER from the environment and defaults both.
#
# It only ever READS. Everything that changes the container is a Makefile target;
# keeping the two apart is what makes it safe to run any of this at any time,
# including against a container that is not there.
#
# Ported from or3/dev/status.sh, minus its vessel-LAN tunnel checks (dossier has
# no second machine to reach) and plus the toolchain readouts, because this image
# now has to carry the whole Rust gate as well as the Python one.
set -u

CONTAINER="${CONTAINER:-ds-dev}"
DOCKER=(${SUDO:-} docker)

d() { "${DOCKER[@]}" "$@"; }

# tool <label> <command…> — one line per tool, and a MISSING that says which build
# step failed to land rather than an empty line you have to interpret.
tool() {
  local label=$1; shift
  # stderr kept, not discarded: when the fault is the docker daemon rather than a
  # missing tool, "MISSING" would be a lie. Truncated so one broken daemon does not
  # wrap every row of the table.
  printf '%-10s: %s\n' "$label" "$(d exec "$CONTAINER" "$@" 2>&1 | head -1 | cut -c1-72 | grep . || echo 'MISSING')"
}

# ── status ──────────────────────────────────────────────────────────────────
status() {
  # One read of every process's cmdline, used by the lines below. Straight out of
  # /proc rather than via pgrep, so this readout does not itself depend on procps
  # being in the image: a status line that reports a healthy daemon as missing is
  # worse than one that is not there.
  local procs
  procs="$(d exec "$CONTAINER" sh -c \
      'for p in /proc/[0-9]*/cmdline; do tr "\0" " " < "$p" 2>/dev/null; echo; done' 2>/dev/null || true)"

  # Container ID, not StartedAt: StartedAt necessarily changes across a legitimate
  # stop/start, so it cannot tell a cycle from a recreate. The ID can.
  # `| grep .` before the fallback, not just `||`: a docker CLI that cannot reach
  # its daemon exits non-zero AND prints an empty line on stdout, which would
  # otherwise put a blank line in front of the word "absent".
  printf 'container : %s\n' "$(d inspect -f '{{.Name}} {{.State.Status}} id={{slice .Id 0 12}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}}' "$CONTAINER" 2>/dev/null | grep . || echo 'absent')"
  printf 'published : %s\n' "$(d port "$CONTAINER" 2222/tcp 2>/dev/null | grep . || echo 'none')"

  # sshd is the container's foreground process now, so this line and the
  # container's own status say the same thing from two directions. If they ever
  # disagree, believe this one: a container `running` with its payload dead has
  # gone wrong in a way worth seeing spelled out.
  printf 'sshd      : %s\n' "$(grep -q 'sshd' <<<"$procs" \
      && echo 'running in the foreground (the way in)' \
      || echo 'NOT running — nothing to ssh into; see: make boot-log')"

  # The sessions are the work. Read as the SOCKET DIRECTORY rather than by running
  # `abduco`, whose own listing carries a header and wants a terminal — a status
  # check has to tell "no sessions" from "the listing did not work". One socket,
  # one session; abduco removes it when the session ends.
  local sessions
  sessions="$(d exec "$CONTAINER" sh -c 'ls -1 "$HOME/.abduco" 2>/dev/null' 2>/dev/null)"
  if [ -n "$sessions" ]; then
    printf 'sessions  : abduco — %s\n' "$(tr '\n' ' ' <<<"$sessions" | sed 's/ *$//')"
  else
    printf 'sessions  : %s\n' 'none (make claude, or ssh in and start one)'
  fi

  printf 'claude    : %s\n' "$(grep 'claude' <<<"$procs" \
      | grep -vE 'remote-control|rc-supervisor|/proc/' | grep -q . \
      && echo 'a claude is running in this container' || echo 'no claude running')"

  # `claude auth status`, not "is there a credentials file": a file holding empty
  # tokens exists and reports logged out, so the file's presence proves nothing.
  # And the grep is load-bearing — the command exits 0 either way.
  printf 'auth      : %s\n' "$(d exec "$CONTAINER" claude auth status 2>/dev/null | grep -q '"loggedIn": *true' \
      && echo 'claude logged in' || echo 'claude NOT logged in — run: make login')"
  printf 'gh        : %s\n' "$(d exec "$CONTAINER" gh auth status 2>/dev/null | grep -m1 'Logged in' | sed 's/^[[:space:]]*//' \
      | grep . || echo 'NOT logged in — run: make login (git push works without it)')"
  printf 'remote    : %s\n' "$(grep -q 'remote-control' <<<"$procs" \
      && echo 'remote-control daemon running' \
      || echo 'off (DS_REMOTE_CONTROL=1 in dev/.env, then make up)')"
  printf 'workspace : %s\n' "$(d exec "$CONTAINER" git -C /workspace log --oneline -1 2>/dev/null || echo 'no git checkout at /workspace')"
}

# ── verify ──────────────────────────────────────────────────────────────────
# What to run after `make up`: the container's own state first, then the tools a
# rebuild is supposed to have landed. A rebuild is exactly where a tool silently
# fails to arrive — a stage that FAILED would have failed the build, but a COPY or
# a tar that landed the wrong path would not.
verify() {
  status
  printf '\n'
  tool abduco  abduco -v
  tool gh      gh --version
  tool screen  screen --version
  tool claude  claude --version
  printf '\n'
  # The Rust gate's four commands need all of these. rustfmt and clippy are asked
  # for by name rather than inferred from `cargo --version`, because a `--profile
  # minimal` toolchain has cargo and neither of them.
  tool rustc     rustc --version
  tool cargo     cargo --version
  tool rustfmt   cargo fmt --version
  tool clippy    cargo clippy --version
  tool clang     clang --version
  tool llvm-ar   llvm-ar --version
  printf '%-10s: %s\n' 'musl tgt' "$(d exec "$CONTAINER" rustup target list --installed 2>/dev/null \
      | grep -q aarch64-unknown-linux-musl \
      && echo 'aarch64-unknown-linux-musl installed' \
      || echo 'MISSING — rustup target add aarch64-unknown-linux-musl')"
  printf '%-10s: %s\n' 'cargo tgt' "$(d exec "$CONTAINER" sh -c 'echo "$CARGO_TARGET_DIR"' 2>/dev/null \
      | grep . || echo 'unset — builds would land in the bind-mounted /workspace/target')"
  printf '\n'
  tool python  python3 --version
  tool uv      uv --version
  printf '%-10s: %s\n' 'venv' "$(d exec "$CONTAINER" sh -c 'ls /home/dev/venv/bin/python >/dev/null 2>&1 && echo "/home/dev/venv (outside the bind mount)"' 2>/dev/null \
      | grep . || echo 'MISSING — the uv sync in the image build did not land')"
  printf '\n'
  printf 'The phone binary the Rust gate cross-builds lands at\n'
  printf '  /home/dev/cargo-target/aarch64-unknown-linux-musl/release/ds\n'
  printf 'inside the container — NOT under /workspace/target. Copy it out with\n'
  printf '  docker cp %s:/home/dev/cargo-target/aarch64-unknown-linux-musl/release/ds .\n' "$CONTAINER"
}

# ── boot-log ────────────────────────────────────────────────────────────────
# The START of the log, not the end. `make logs` follows sshd's stream; the lines
# that say why a start went wrong are the entrypoint's, at the very top. ANSI
# stripped so nothing that reached the head can hide them.
boot_log() {
  d logs "$CONTAINER" 2>&1 \
    | sed -E 's/\x1b\][^\x07]*\x07//g; s/\x1b\[[0-9;?]*[ -\/]*[@-~]//g; s/\r//g' \
    | grep -v '^[[:space:]]*$' | head -n "${1:-80}"
}

case "${1:-status}" in
  status)   status ;;
  verify)   verify ;;
  boot-log) boot_log "${2:-80}" ;;
  *) printf 'status.sh: unknown readout "%s" — status | verify | boot-log\n' "$1" >&2; exit 1 ;;
esac
