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

# Claude Remote Control: drive this container's sessions from claude.ai/code or the
# Claude mobile app. Launched in the BACKGROUND so it never blocks container start or
# Zed's sshd (the resilient foreground process below) — if the supervisor dies, the
# container and SSH stay up. The supervisor idles until Claude is authenticated, then
# runs (and health-recycles) `claude remote-control --spawn worktree`. See the script
# header for the recycle policy — it only ever restarts a wedged daemon at 0/32.
bash /home/dev/rc-supervisor.sh &

# sshd becomes the foreground process, keeps the container alive, and serves SSH; -e
# routes its log to `docker logs`. All work still also reachable via `docker exec`.
exec /usr/sbin/sshd -D -e -f /home/dev/sshd_config
