#!/usr/bin/env bash
# Claude Remote Control supervisor for the dossier dev container. Baked into the image
# at /home/dev/rc-supervisor.sh and run as the FOREGROUND process by the entrypoint, so
# the Claude session is what `docker logs` surfaces (sshd runs in the background). This
# script loops forever — it re-launches the daemon on exit — so it is itself the process
# keeping the container alive.
#
# It runs `claude remote-control --spawn worktree --no-create-session-in-dir` as a
# long-lived service so you can drive this container from claude.ai/code or the Claude
# mobile app, with nothing to type in a `docker exec`.
#
# WHY A REAL SUPERVISOR (not just `cmd || restart`): Claude Code's remote-control
# server has a known class of upstream hangs where the PROCESS STAYS ALIVE but wedges
# and stops accepting new sessions (anthropics/claude-code#51267, #40416, #37321 —
# "remote becomes unresponsive, can't start a new session"). A restart-on-exit loop
# never recovers that, because the process never exits. So this watchdog also RECYCLES
# a daemon that is alive-but-wedged — but only when doing so is free.
#
# THE SAFETY CONSTRAINT (deliberate, and the whole point): recycle ONLY at a literal
# 0/32 sessions. A single idle-but-attached session (1/32 doing nothing) must NOT
# trigger a recycle — killing it forces a painful remote session recovery. So a wedged
# daemon that still holds a live session is LEFT ALONE until that session ends. We
# never trade away live work to unstick the server.
#
# THE POLICY: a freshly started daemon is exempt until it has actually served ≥1
# session (a brand-new daemon isn't wedged, and this stops a perpetually-idle daemon
# from being churned). Once it has been used and then drops to 0 sessions, we give it
# RC_IDLE_RECYCLE_SECS of *continuous* idle and then recycle it exactly ONCE. Net
# effect: whenever you return after an idle gap you meet a fresh, unwedged daemon,
# but an untouched daemon is never restarted for no reason.
#
# NOTE ON PERMISSION MODE: we run with the DEFAULT permission mode (prompts kept on),
# per the maintainer's choice. The upside is no blanket auto-approve; the tradeoff is
# that a permission prompt awaiting a local keypress can itself wedge a daemon *while a
# session is live* (#51267) — and per the constraint above we will not kill that live
# session to recover it. That wedge clears on its own once the session ends (then the
# idle recycle fires). Set RC_PERMISSION_MODE to override (e.g. acceptEdits).
set -u

LOG="$HOME/.local/share/remote-control.log"
mkdir -p "$(dirname "$LOG")"

# As the container's foreground process, our stdout/stderr IS `docker logs`. Mirror
# everything to both there and the persisted logfile (still handy from `docker exec`),
# so the supervisor's own lines and the daemon's output both show up in `docker logs`.
exec > >(tee -a "$LOG") 2>&1

RC_POLL_SECS=${RC_POLL_SECS:-30}            # how often to sample the session count
RC_IDLE_RECYCLE_SECS=${RC_IDLE_RECYCLE_SECS:-300}   # continuous 0/32 before recycle
RC_REPO=${RC_REPO:-/workspace}              # repo where --spawn worktree operates
RC_PERMISSION_MODE=${RC_PERMISSION_MODE:-}  # empty => daemon default (keep prompting)

log() { printf '%s [rc-supervisor] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

# is_descendant <pid> <ancestor> — walk the PPID chain (bounded) so we only ever count
# sessions belonging to OUR daemon, never an unrelated `claude` from a `docker exec`.
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

# session_count <daemon_pid> — how many sessions this daemon is running (the "X" in
# X/32). Measured TWO independent ways; we take the MAX so we can only ever OVER-count,
# never UNDER-count. Under-counting would mean recycling while a session is live —
# exactly the outcome the safety constraint forbids.
#   (1) `claude agents --json` entries whose pid descends from the daemon. This reads
#       LOCAL state, so it can't hang on a wedged daemon (but could go stale).
#   (2) session-helper processes (`claude.exe --sdk-url .../sessions/`) that descend
#       from the daemon — the backstop if (1) is stale.
session_count() {
  local rc=$1 n_agents=0 n_procs=0 pid
  if command -v python3 >/dev/null 2>&1; then
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
  fi
  for pid in $(pgrep -f -- '--sdk-url .*/sessions/' 2>/dev/null); do
    is_descendant "$pid" "$rc" && n_procs=$((n_procs + 1))
  done
  [ "$n_agents" -ge "$n_procs" ] && echo "$n_agents" || echo "$n_procs"
}

# kill_daemon <pid> — reap the daemon and its whole process group. Only ever called at
# 0 sessions, so there is no live session helper to lose.
kill_daemon() {
  local rc=$1 _
  kill -TERM -"$rc" 2>/dev/null || kill -TERM "$rc" 2>/dev/null || true
  for _ in $(seq 1 10); do kill -0 "$rc" 2>/dev/null || return 0; sleep 1; done
  kill -KILL -"$rc" 2>/dev/null || kill -KILL "$rc" 2>/dev/null || true
}

# Idle until Claude is authenticated (`make dev` / login.sh writes creds into the
# shared ds-claude volume). Poll auth every 10s; do nothing else until then.
until claude auth status >/dev/null 2>&1; do sleep 10; done
log "authenticated; supervising remote-control (poll=${RC_POLL_SECS}s idle-recycle=${RC_IDLE_RECYCLE_SECS}s)"

perm_args=()
[ -n "$RC_PERMISSION_MODE" ] && perm_args=(--permission-mode "$RC_PERMISSION_MODE")

while true; do
  # Drop admin entries for worktrees whose dirs are already gone — combats the
  # orphaned-environment buildup behind #37321. Safe to run at any time.
  git -C "$RC_REPO" worktree prune || true

  # setsid → the daemon leads its own process group (pgid == its pid), so session
  # helpers share that pgid and `kill -TERM -<pid>` reaps the whole tree on recycle.
  setsid claude remote-control --spawn worktree --no-create-session-in-dir \
    "${perm_args[@]}" &
  rc_pid=$!
  log "started remote-control pid=$rc_pid (spawn=worktree, no-create-session-in-dir)"

  used=0        # has this daemon served ≥1 session since it started?
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
      log "idle at 0/32 for ${RC_IDLE_RECYCLE_SECS}s after use → recycling pid=$rc_pid"
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
