#!/usr/bin/env bash
# Interactive one-shot login walkthrough for the dossier dev container. Baked into the
# image at /home/dev/login.sh; driven from the host by `make dev` (which builds +
# starts the container first) or `make dev-login` to re-run on demand.
#
# Every step is IDEMPOTENT: it checks the current auth state and only prompts when
# you are NOT already signed in, so re-running is safe and near-instant. All three
# credential stores live in persisted named volumes / the clone (see
# docker-compose.dev.yml), so you normally do this exactly once per machine:
#   - git SSH  -> .dev-ssh/    (gitignored, rides with the bind-mounted clone; the
#                              identities file in it is ssh_config.fleet, the name the
#                              base image prepends to ~/.ssh/config at every start)
#   - GitHub   -> ds-gh        (~/.config/gh)
#   - Claude   -> ds-claude    (~/.claude)
set -u

bold() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
ask()  { # ask "question" -> exit 0 on yes (default yes on empty)
  local reply
  read -r -p "  $1 [Y/n] " reply || return 1
  [ -z "$reply" ] || [ "$reply" = y ] || [ "$reply" = Y ]
}

DEV_SSH=/workspace/.dev-ssh

# ── 1/3  Git SSH keys ────────────────────────────────────────────────────────
# Reuse a preexisting key if one already authenticates; otherwise offer to
# generate one into the gitignored .dev-ssh/ (persists with the clone; the
# child-init.sh wires it into ~/.ssh at start, and the base image's entrypoint
# rewrites GitHub HTTPS->SSH for pushes).
bold "1/3  Git SSH keys"
if ssh -o StrictHostKeyChecking=accept-new -o BatchMode=yes -T git@github.com 2>&1 \
     | grep -q "successfully authenticated"; then
  ok "GitHub SSH already authenticates — using the existing key."
else
  existing=$(find "$DEV_SSH" -maxdepth 1 -name 'id_*' ! -name '*.pub' 2>/dev/null | head -1)
  if [ -n "$existing" ]; then
    warn "Found $existing but it does not authenticate to GitHub yet."
  else
    warn "No git SSH key found in .dev-ssh/."
    if ask "Generate a new ed25519 key there?"; then
      mkdir -p "$DEV_SSH" && chmod 700 "$DEV_SSH"
      ssh-keygen -t ed25519 -f "$DEV_SSH/id_ed25519_dev" -N "" -C "ds-dev-$(hostname)"
      # ssh_config.fleet is the BASE IMAGE's name for the host-specific half of
      # ~/.ssh/config: it prepends this file to the baked defaults at every start, and
      # it does so before it pulls the clone, which is why the identities live here
      # rather than in something this container assembles later. Don't clobber a file
      # you may already have.
      #
      # The key is named by the path the BASE copies it to, not by its path in the
      # mount: the base copies every .dev-ssh/id_* into ~/.ssh at 0600, and a copy it
      # owns cannot be refused for the mount's modes.
      [ -f "$DEV_SSH/ssh_config.fleet" ] || cat > "$DEV_SSH/ssh_config.fleet" <<EOF
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_dev
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
EOF
      # In force now rather than at the next start — the same two-part assembly the
      # base does. NOT a symlink: the base's own write would follow it and truncate
      # the host's file on the next boot.
      mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
      cp -f "$DEV_SSH/id_ed25519_dev" "$HOME/.ssh/id_ed25519_dev" 2>/dev/null || true
      chmod 600 "$HOME/.ssh/id_ed25519_dev" 2>/dev/null || true
      rm -f "$HOME/.ssh/config"
      cat "$DEV_SSH/ssh_config.fleet" /home/dev/ssh_config > "$HOME/.ssh/config"
      chmod 600 "$HOME/.ssh/config"
      existing="$DEV_SSH/id_ed25519_dev"
    fi
  fi
  # Get the public key onto GitHub: upload via gh if it's already authed (re-run
  # this script after step 2 to use the shortcut), otherwise print it to add by hand.
  if [ -n "${existing:-}" ] && [ -f "$existing.pub" ]; then
    if gh auth status >/dev/null 2>&1 && ask "Upload this key to GitHub via gh?"; then
      gh ssh-key add "$existing.pub" --title "ds-dev-$(hostname)" && ok "Key uploaded to GitHub."
    else
      warn "Add this public key at https://github.com/settings/keys , then re-run:"
      printf '\n'; cat "$existing.pub"; printf '\n'
    fi
  fi
fi

# ── 2/3  GitHub CLI (gh) ─────────────────────────────────────────────────────
bold "2/3  GitHub CLI (gh)"
if gh auth status >/dev/null 2>&1; then
  ok "$(gh auth status 2>&1 | grep -m1 'Logged in' | sed 's/^[[:space:]]*//')"
else
  warn "Not logged in."
  ask "Run 'gh auth login' now?" && gh auth login
fi

# ── 3/3  Claude Code ─────────────────────────────────────────────────────────
# Logging in here is what makes `claude` work in the container at all: ssh in and run
# it inside an abduco session, which is how every dev container on this host is used.
bold "3/3  Claude Code"
if claude auth status >/dev/null 2>&1; then
  ok "$(claude auth status --text 2>&1 | grep -m1 -E 'Email|Login method' | sed 's/^[[:space:]]*//')"
else
  warn "Not logged in."
  ask "Run 'claude auth login' now?" && claude auth login
fi

bold "Done."
if claude auth status >/dev/null 2>&1; then
  ok "Work in it over ssh: 'ssh -p <port> dev@<host>' then 'abduco -A claude claude'."
fi
cat <<'EOF'

  Attach a shell:   docker exec -it ds-dev fish
  Work in it:       ssh -p <port> dev@<host>  then  abduco -A claude claude
  Idle sessions:    stopped after 90m and left resumable — ~/.local/share/claude-offload.log
  Re-run logins:    make dev-login
EOF
