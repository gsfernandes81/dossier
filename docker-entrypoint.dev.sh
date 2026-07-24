#!/usr/bin/env bash
set -e

# Git identities: keys + SSH config live in the gitignored .dev-ssh/ dir, which
# rides along with the bind-mounted repo clone. Wire them into ~/.ssh on start.
if [ -d /workspace/.dev-ssh ]; then
  mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
  chmod 600 /workspace/.dev-ssh/id_ed25519_* 2>/dev/null || true
  [ -f /workspace/.dev-ssh/config ] && ln -sf /workspace/.dev-ssh/config "$HOME/.ssh/config"
  # Push over SSH with the keys above WITHOUT editing the shared .git/config
  # remote (keeps the host on HTTPS): rewrite GitHub HTTPS->SSH in the
  # container's own ~/.gitconfig only.
  git config --global url."git@github.com:".insteadOf "https://github.com/"
fi

# Deps are baked into /home/dev/venv at build time; add the editable project now
# that /workspace is mounted. Keep these flags in sync with Dockerfile.dev's build
# sync (--all-extras --group driver) so this doesn't uninstall the baked extras.
# Best-effort so the container still comes up if the clone is absent or offline.
[ -f /workspace/pyproject.toml ] && uv sync --frozen --all-extras --group driver || true

# In-container sshd (Zed-remote / direct SSH). Generate the host key once into the
# persisted ds-ssh-host volume so Zed's known_hosts survives rebuilds.
mkdir -p "$HOME/.ssh-host" && chmod 700 "$HOME/.ssh-host"
[ -f "$HOME/.ssh-host/ssh_host_ed25519_key" ] || \
  ssh-keygen -t ed25519 -f "$HOME/.ssh-host/ssh_host_ed25519_key" -N "" -C ds-dev-host

# SSH/Zed sessions don't inherit the entrypoint's env, so publish it (with the venv
# on PATH) to ~/.ssh/environment, which sshd reads via PermitUserEnvironment. Filter
# shell noise; one KEY=value per line, no quotes (PermitUserEnvironment format).
mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
{
  echo "PATH=/home/dev/venv/bin:$PATH"
  env | grep -vE '^(PATH|PWD|SHLVL|_|HOME|OLDPWD|HOSTNAME)='
} > "$HOME/.ssh/environment"
chmod 600 "$HOME/.ssh/environment"

# Pre-seed two headless-hostile first-run flags in ~/.claude.json (moved into the
# persisted ds-claude volume via CLAUDE_CONFIG_DIR). Both default to "unset" on a FRESH
# volume, where each blocks the supervisor with a dialog nobody can answer in a headless
# container. Done idempotently, merging into any existing config, BEFORE the supervisor
# starts (while no claude process is writing the file):
#
#   1. projects["<dir>"].hasTrustDialogAccepted — workspace trust. Absent → `claude
#      remote-control --spawn worktree` aborts with "Workspace not trusted". We seed it
#      for /workspace (which also covers the worktrees spawned beneath it). /workspace is
#      not $HOME, so unlike home-directory trust this record is actually persisted.
#   2. remoteDialogSeen (top-level) — the one-time "Enable Remote Control? [y/N]" consent.
#      When falsy, `claude remote-control` opens a readline prompt on stdin; with no
#      interactive stdin the supervisor's daemon can never answer it and re-prompts on
#      every restart. Seeding it true skips the prompt outright.
python3 - "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/.claude.json" /workspace <<'PY' || true
import json, os, sys

path, project = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        cfg = json.load(f)
    if not isinstance(cfg, dict):
        cfg = {}
except (FileNotFoundError, json.JSONDecodeError):
    cfg = {}

dirty = False

entry = cfg.setdefault("projects", {}).setdefault(project, {})
if entry.get("hasTrustDialogAccepted") is not True:
    entry["hasTrustDialogAccepted"] = True
    dirty = True

if cfg.get("remoteDialogSeen") is not True:
    cfg["remoteDialogSeen"] = True
    dirty = True

if dirty:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)
PY

# In-container sshd (Zed-remote / direct SSH) now runs in the BACKGROUND. -e routes its
# log to `docker logs`; all work is still also reachable via `docker exec`.
/usr/sbin/sshd -D -e -f /home/dev/sshd_config &

# Claude Remote Control: drive this container's sessions from claude.ai/code or the
# Claude mobile app. Now the FOREGROUND process, so the Claude session is what
# `docker logs` shows and it's what keeps the container alive. The supervisor idles
# until Claude is authenticated, then runs (and health-recycles) `claude remote-control
# --spawn worktree`. It loops forever (re-launching the daemon on exit), so it won't
# fall out from under the container; see the script header for the recycle policy — it
# only ever restarts a wedged daemon at 0/32.
exec bash /home/dev/rc-supervisor.sh
