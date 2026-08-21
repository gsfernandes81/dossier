#!/usr/bin/env bash
# Claude Remote Control supervisor for ds-dev. Baked into the image at
# /home/dev/rc-supervisor.sh and started by the entrypoint IN THE BACKGROUND.
#
# It keeps `claude remote-control --spawn worktree` alive so this container can be
# driven from claude.ai/code or the Claude mobile app with nothing to type into a
# `docker exec`.
#
# ── it does not run unless it is asked for ───────────────────────────────────
# The entrypoint starts this only under DS_REMOTE_CONTROL=1 (dev/.env). The
# default way into this container is an ssh session with abduco holding it, and a
# remote-control daemon nobody is driving is a live claude with a permission
# classifier for company. Turn it on for a spell away from a terminal; it lands on
# `make up`, not `make restart`.
#
# ── background, not foreground, and why that changed ─────────────────────────
# This used to be the container's FOREGROUND process, with sshd backgrounded under
# it, so that `docker logs` would show the Claude session. That put an optional
# component on PID 1: a wedged or crashed supervisor took the ssh door — the only
# way in that does not need the docker socket — down with it, and the container
# with that. sshd is the foreground process now (entrypoint.sh), so `docker logs`
# shows sshd and this writes its own log, below.
#
# ── why a supervisor and not `cmd || restart` ────────────────────────────────
# Claude Code's remote-control server has a known class of upstream hangs where the
# PROCESS STAYS ALIVE but wedges and stops accepting new sessions
# (anthropics/claude-code#51267, #40416, #37321 — "remote becomes unresponsive,
# can't start a new session"). A restart-on-exit loop never recovers that, because
# the process never exits. So this also recycles a daemon that is alive-but-wedged
# — but only when doing so costs nothing.
#
# THE SAFETY CONSTRAINT, and the whole point: recycle ONLY at literally 0 sessions.
# A single idle-but-attached session must NOT trigger a recycle — killing it forces
# a painful remote session recovery. A wedged daemon that still holds a live session
# is LEFT ALONE until that session ends. Live work is never traded away to unstick
# the server.
#
# THE POLICY: a freshly started daemon is exempt until it has actually served >=1
# session, so a daemon nobody has used yet is never churned. Once it has been used
# and drops to 0, it gets RC_IDLE_RECYCLE_SECS of *continuous* idle and is then
# recycled exactly once. Net effect: come back after an idle gap and you meet a
# fresh, unwedged daemon; an untouched one is never restarted for no reason.
#
# NOTE: the whole policy depends on `ps` and `pgrep` existing — see session_count.
# They come from procps, which the image now installs explicitly. Without it every
# count reads 0, `used` never becomes 1, and the recycle silently never fires.
set -u

LOG="$HOME/.local/share/remote-control.log"
LOG_MAX_BYTES=${RC_LOG_MAX_BYTES:-2097152}

RC_POLL_SECS=${RC_POLL_SECS:-30}                     # how often to sample the session count
RC_IDLE_RECYCLE_SECS=${RC_IDLE_RECYCLE_SECS:-300}    # continuous 0 sessions before recycle
RC_REPO=${RC_REPO:-/workspace}                       # repo where --spawn worktree operates

# `auto` lets Claude's classifier judge each tool call instead of prompting for all
# of them. Chosen deliberately, and it is a change from this container's earlier
# default of prompting: nobody is sitting at this container's terminal, and a
# prompt awaiting a keypress is itself one of the ways a live session wedges
# (#51267) — and per the safety constraint above we will not kill that live session
# to recover it. The exposure is bounded by remote control being opt-in at all.
# Override with RC_PERMISSION_MODE= (empty) to restore prompting, or acceptEdits.
RC_PERMISSION_MODE=${RC_PERMISSION_MODE:-auto}

mkdir -p "$(dirname "$LOG")"

# ── log filtering ───────────────────────────────────────────────────────────
# The daemon's stdout is a TUI that repaints itself every few seconds. Appended
# raw, it grew megabytes of cursor-control escapes a day and buried the handful of
# supervisor lines that actually say what happened. destiny-director hit this
# first and filters for the same reason.
#
# So: strip ANSI, drop blanks, and keep a line only if it is not one already seen.
# That collapses the repaint block to a single copy. Keyed on the line TEXT rather
# than on matching known TUI wording, so an upstream rewording cannot quietly start
# leaking noise back in. `[rc-supervisor]` lines are ALWAYS kept and clear the
# dedupe table, so a real error recurring after a state change is recorded again
# rather than suppressed forever by a match from hours ago. Suppressed repeats are
# counted, so the file never implies a quiet period that wasn't. Rotation keeps one
# previous generation, so this can never be a disk risk on a Pi.
#
# One filter for the life of the supervisor, installed with the `exec` below — not
# one per daemon launch, which would leak a process on every recycle.
rc_filter() {
  awk -v logf="$LOG" -v maxbytes="$LOG_MAX_BYTES" '
    function emit(line) {
      if (bytes + length(line) + 1 > maxbytes) {   # rotate BEFORE overflowing
        close(logf); system("mv -f \"" logf "\" \"" logf ".1\" 2>/dev/null"); bytes = 0
      }
      print line >> logf; fflush(logf); bytes += length(line) + 1
    }
    function flush_supp() {
      if (nsupp > 0) { emit("  (suppressed " nsupp " repeated TUI line(s))"); nsupp = 0 }
    }
    BEGIN {
      # Start accounting from the file as it already is, so an oversized log left
      # by a previous run rotates away on the first write instead of growing
      # further. `wc -c "file"` rather than a redirect, so wc reports a missing
      # file to the stderr we are discarding instead of the shell announcing it.
      cmd = "wc -c \"" logf "\" 2>/dev/null"
      if ((cmd | getline l) > 0) { split(l, f, " "); bytes = f[1] + 0 }
      close(cmd); nseen = 0; nsupp = 0
    }
    {
      line = $0
      gsub(/\033\][^\007\033]*(\007|\033\\)/, "", line)   # OSC (e.g. the ]8;; links)
      gsub(/\033\[[0-9;?]*[ -\/]*[@-~]/, "", line)        # CSI (cursor moves, colour)
      gsub(/\r/, "", line); sub(/[ \t]+$/, "", line)
      if (line ~ /^[ \t]*$/) next
      if (line ~ /\[rc-supervisor\]/) {
        flush_supp(); delete seen; nseen = 0; emit(line); next
      }
      if (line in seen) { nsupp++; next }
      if (nseen >= 500) { delete seen; nseen = 0 }        # bound the table
      seen[line] = 1; nseen++; flush_supp(); emit(line)
    }
    END { flush_supp() }
  '
}

exec > >(rc_filter) 2>&1

log() { printf '%s [rc-supervisor] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

# is_descendant <pid> <ancestor> — walk the PPID chain, bounded, so we only ever
# count sessions belonging to OUR daemon and never a stray `claude` someone
# started in an abduco session or a docker exec.
is_descendant() {
  local p=$1 anc=$2 i=0 pp
  while [ "${p:-0}" -gt 1 ] && [ "$i" -lt 20 ]; do
    pp=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
    [ -z "$pp" ] && return 1
    [ "$pp" = "$anc" ] && return 0
    p=$pp; i=$((i + 1))
  done
  return 1
}

# session_count <daemon_pid> — how many sessions this daemon is running. Measured
# TWO independent ways; we take the MAX so it can only ever OVER-count, never
# under-count. Under-counting would mean recycling while a session is live —
# exactly the outcome the safety constraint forbids.
#   (1) `claude agents --json` entries whose pid descends from the daemon. This
#       reads LOCAL state, so it cannot hang on a wedged daemon (but can go stale).
#   (2) session-helper processes (`claude … --sdk-url …/sessions/`) that descend
#       from the daemon — the backstop when (1) is stale.
session_count() {
  local rc=$1 n_agents=0 n_procs=0 pid
  for pid in $(claude agents --json 2>/dev/null | python3 -c '
import sys, json
try:
    data = json.load(sys.stdin)
except Exception:
    data = []
for item in data if isinstance(data, list) else []:
    if isinstance(item, dict) and item.get("pid"):
        print(item["pid"])
' 2>/dev/null); do
    is_descendant "$pid" "$rc" && n_agents=$((n_agents + 1))
  done
  for pid in $(pgrep -f -- '--sdk-url .*/sessions/' 2>/dev/null); do
    is_descendant "$pid" "$rc" && n_procs=$((n_procs + 1))
  done
  [ "$n_agents" -ge "$n_procs" ] && echo "$n_agents" || echo "$n_procs"
}

# kill_daemon <pid> — reap the daemon and its whole process group. Only ever
# called at 0 sessions, so there is no live session helper to lose.
kill_daemon() {
  local rc=$1 _
  kill -TERM -"$rc" 2>/dev/null || kill -TERM "$rc" 2>/dev/null || true
  for _ in $(seq 1 10); do kill -0 "$rc" 2>/dev/null || return 0; sleep 1; done
  kill -KILL -"$rc" 2>/dev/null || kill -KILL "$rc" 2>/dev/null || true
}

# Idle until someone has logged this container in, rather than failing without a
# login: run `make dev-login` once and the daemon comes up on its own within a
# minute, no restart needed. The test greps for loggedIn true — `claude auth
# status` EXITS 0 WHEN LOGGED OUT, so the older `claude auth status >/dev/null`
# form fell straight through and launched a daemon that could not authenticate.
log "waiting for a Claude login (run: make dev-login)"
until claude auth status 2>/dev/null | grep -q '"loggedIn": *true'; do sleep 10; done
log "authenticated; supervising remote-control (poll=${RC_POLL_SECS}s idle-recycle=${RC_IDLE_RECYCLE_SECS}s mode=${RC_PERMISSION_MODE:-default})"

perm_args=()
[ -n "$RC_PERMISSION_MODE" ] && perm_args=(--permission-mode "$RC_PERMISSION_MODE")

while true; do
  # Drop admin entries for worktrees whose directories are already gone — combats
  # the orphaned-environment buildup behind #37321. Safe HERE and only here: those
  # worktrees are registered under /workspace paths, which resolve inside this
  # container. Running the same command against the HOST's clone would find none
  # of them resolvable and unregister the lot.
  git -C "$RC_REPO" worktree prune 2>/dev/null || true

  # setsid → the daemon leads its own process group (pgid == its pid), so session
  # helpers share that pgid and `kill -TERM -<pid>` reaps the whole tree on
  # recycle. No redirection: the daemon inherits the filtered stdout installed by
  # the `exec` above. Redirecting it straight at $LOG would bypass the filter and
  # put the raw repaint stream back in the file.
  setsid claude remote-control --spawn worktree --no-create-session-in-dir \
    "${perm_args[@]}" &
  rc_pid=$!
  log "started remote-control pid=$rc_pid (spawn=worktree, no-create-session-in-dir)"

  used=0        # has this daemon served >=1 session since it started?
  idle_since=0  # $SECONDS when it last dropped to 0 sessions (0 = not currently idle)
  recycled=0    # has the one-shot idle recycle already fired this idle stretch?

  while kill -0 "$rc_pid" 2>/dev/null; do
    sleep "$RC_POLL_SECS"
    n=$(session_count "$rc_pid")
    if [ "${n:-0}" -gt 0 ]; then
      used=1; idle_since=0
      continue
    fi
    # 0 sessions from here down.
    [ "$used" = 1 ] || continue      # fresh, never-used daemon → nothing to recover
    [ "$recycled" = 1 ] && continue  # already did the one-shot recycle this stretch
    if [ "$idle_since" = 0 ]; then
      idle_since=$SECONDS
    elif [ $((SECONDS - idle_since)) -ge "$RC_IDLE_RECYCLE_SECS" ]; then
      log "idle at 0 sessions for ${RC_IDLE_RECYCLE_SECS}s after use → recycling pid=$rc_pid"
      kill_daemon "$rc_pid"
      recycled=1
      break  # fall through to the outer loop, which starts a fresh daemon
    fi
  done

  if [ "$recycled" != 1 ]; then
    log "remote-control pid=$rc_pid exited on its own; restarting in 10s"
    sleep 10
  fi
done
