#!/usr/bin/env bash
# Interactive one-shot login walkthrough for the dossier dev container. Baked into the
# image at /home/dev/login.sh; driven from the host by `make dev` (which builds +
# starts the container first) or `make dev-login` to re-run on demand.
#
# Every step is IDEMPOTENT: it checks the current auth state and only prompts when
# you are NOT already signed in, so re-running is safe and near-instant. All three
# credential stores live in persisted named volumes / the clone (see
# docker-compose.dev.yml), so you normally do this exactly once per machine:
#   - git SSH  -> .dev-ssh/    (gitignored, rides with the bind-mounted clone)
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
# entrypoint wires it into ~/.ssh and rewrites GitHub HTTPS->SSH for pushes).
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
      # Point ssh at this key by its absolute path (survives restarts; the entrypoint
      # re-links .dev-ssh/config -> ~/.ssh/config on every start). Don't clobber a
      # config you may already have from a manual multi-account setup.
      [ -f "$DEV_SSH/config" ] || cat > "$DEV_SSH/config" <<EOF
Host github.com
  HostName github.com
  User git
  IdentityFile /workspace/.dev-ssh/id_ed25519_dev
  IdentitiesOnly yes
  StrictHostKeyChecking accept-new
EOF
      mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
      ln -sf "$DEV_SSH/config" "$HOME/.ssh/config"
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
# Logging in here also unblocks the background Remote Control supervisor started by
# the entrypoint — it polls auth every ~10s and comes online on its own once signed in.
bold "3/3  Claude Code"
if claude auth status >/dev/null 2>&1; then
  ok "$(claude auth status --text 2>&1 | grep -m1 -E 'Email|Login method' | sed 's/^[[:space:]]*//')"
else
  warn "Not logged in."
  ask "Run 'claude auth login' now?" && claude auth login
fi

bold "Done."
if claude auth status >/dev/null 2>&1; then
  ok "Claude Remote Control goes live within ~10s — open claude.ai/code or the mobile app."
fi
cat <<'EOF'

  Attach a shell:   docker exec -it ds-dev fish
  Remote control:   auto-started (spawn=worktree); log at ~/.local/share/remote-control.log
  Re-run logins:    make dev-login
EOF
