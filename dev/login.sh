#!/usr/bin/env bash
# The interactive login walkthrough for ds-dev. Baked into the image at
# /home/dev/login.sh; run from a terminal on the host with:
#
#     cd dossier/dev && make login        # or: make dev, which is up + this
#
# Every step is IDEMPOTENT — it reads the current state and only prompts when
# something is NOT already set up — so re-running it is safe and usually silent.
# Everything it fills lives in a persisted volume or in the bind-mounted clone, so
# this is normally done once per machine and not again after a rebuild:
#
#   git over ssh -> /workspace/.dev-ssh  (gitignored; rides with the clone)
#   GitHub CLI   -> ds-gh      volume    ($GH_CONFIG_DIR)
#   Claude Code  -> ds-claude  volume    ($CLAUDE_CONFIG_DIR)
#
# It is a script in the image rather than a Makefile recipe because every step of
# it needs a tty and every credential store it touches is in here, not on the host.
set -u

bold() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
info() { printf '    %s\n' "$1"; }
ask()  { # ask "question" -> 0 on yes; an empty answer means yes
  local reply
  read -r -p "  $1 [Y/n] " reply || return 1
  [ -z "$reply" ] || [ "$reply" = y ] || [ "$reply" = Y ]
}

DEV_SSH=/workspace/.dev-ssh

# ── 1/3  git over ssh ───────────────────────────────────────────────────────
# Reuse a key that already authenticates; otherwise offer to generate one into the
# gitignored .dev-ssh/ (it persists with the clone, and the entrypoint wires it
# into ~/.ssh and rewrites GitHub HTTPS->SSH for pushes on every start).
bold "1/3  git over ssh"
if ssh -o BatchMode=yes -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
  ok "github.com accepts the key — commits from here can be pushed."
else
  existing=$(find "$DEV_SSH" -maxdepth 1 -name 'id_*' ! -name '*.pub' 2>/dev/null | head -1)
  if [ -n "$existing" ]; then
    warn "Found $existing but github.com does not accept it yet."
  else
    warn "No git ssh key in .dev-ssh/."
    if ask "Generate a new ed25519 key there?"; then
      mkdir -p "$DEV_SSH" && chmod 700 "$DEV_SSH"
      ssh-keygen -t ed25519 -f "$DEV_SSH/id_ed25519_dev" -N "" -C "ds-dev-$(hostname)"
      # Point ssh at this key by absolute path (it survives restarts; the
      # entrypoint re-links .dev-ssh/config -> ~/.ssh/config on every start).
      # Deliberately NO StrictHostKeyChecking here: the entrypoint writes
      # github.com's host keys into ~/.ssh/known_hosts from scratch on every start,
      # so a changed key must be an edit to entrypoint.sh and never a first-contact
      # accept. Don't clobber a config you may already have.
      [ -f "$DEV_SSH/config" ] || cat > "$DEV_SSH/config" <<EOF
Host github.com
  HostName github.com
  User git
  IdentityFile /workspace/.dev-ssh/id_ed25519_dev
  IdentitiesOnly yes
EOF
      mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
      ln -sf "$DEV_SSH/config" "$HOME/.ssh/config"
      existing="$DEV_SSH/id_ed25519_dev"
    fi
  fi
  # Get the public half onto GitHub: via gh if it is already authed (re-run this
  # script after step 2 to use that shortcut), otherwise print it to add by hand.
  if [ -n "${existing:-}" ] && [ -f "$existing.pub" ]; then
    if gh auth status >/dev/null 2>&1 && ask "Upload this key to GitHub via gh?"; then
      gh ssh-key add "$existing.pub" --title "ds-dev-$(hostname)" && ok "Key uploaded to GitHub."
    else
      warn "Add this public key at https://github.com/settings/keys , then re-run:"
      printf '\n'; cat "$existing.pub"; printf '\n'
    fi
  fi
fi

# ── 2/3  GitHub CLI (gh) ────────────────────────────────────────────────────
# The key above covers git. gh covers what git does not — PRs, issues, releases,
# `gh api` — and it is a login rather than a mounted key, which is why it is a
# step here at all.
bold "2/3  GitHub CLI (gh)"
if gh auth status >/dev/null 2>&1; then
  ok "$(gh auth status 2>&1 | grep -m1 'Logged in' | sed 's/^[[:space:]]*//')"
else
  warn "Not logged in."
  info "gh's token is an account-wide credential in a container that also holds a"
  info "git push key, so give it the narrowest token that does the job — 'repo'"
  info "alone, usually. A pasted token is easier to scope than the browser flow's"
  info "default set, and there is no browser in here: the web flow prints a code"
  info "to paste elsewhere. \`make down-volumes\` is how you revoke it locally."
  ask "Run 'gh auth login' now?" && gh auth login
fi

# ── 3/3  Claude Code ────────────────────────────────────────────────────────
# The grep is not decoration: `claude auth status` EXITS 0 WHEN LOGGED OUT, so
# testing its exit status reports every fresh container as already signed in.
bold "3/3  Claude Code"
if claude auth status 2>/dev/null | grep -q '"loggedIn": *true'; then
  ok "logged in — \`claude\` will start without a login flow."
else
  warn "Not logged in."
  info "No browser in here: it prints a URL, and you paste the code back."
  info "This is the one credential that cannot be seeded by copying a file — an"
  info "OAuth login belongs to the device that performed it, and a copied"
  info "credentials.json gets blanked seconds after first contact with the auth"
  info "server. One login here persists in the ds-claude volume across rebuilds."
  ask "Run 'claude auth login' now?" && claude auth login
fi

bold "Done."
cat <<'EOF'

  This container is used over ssh. From a PC, or from Termux on the phone:

      ssh ds-dev                               a shell (fish, in /workspace)
      ssh -t ds-dev abduco -A claude claude    a claude that survives the link

  abduco detaches with Ctrl-\ and re-attaches with the same command, so a dropped
  connection costs nothing — and on a phone the link ends at the lock screen, so
  this is the ordinary case. `abduco` on its own lists the sessions. Use `screen`
  instead for ordinary shell work; it has scrollback and a status line.

  From a terminal on the host:  cd dossier/dev && make shell | make claude | make status
  Re-run these logins:          make login

  Claude Remote Control (claude.ai/code and the mobile app) is OFF by default.
  Set DS_REMOTE_CONTROL=1 in dev/.env and `make up` to turn it on for a spell.
EOF
