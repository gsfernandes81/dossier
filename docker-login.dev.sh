#!/usr/bin/env bash
# The interactive login walkthrough for ds-dev. Baked into the image at /home/dev/login.sh;
# run from a terminal on zero with:
#
#     cd ~/infra/dev && make login C=ds-dev
#
# Every step is IDEMPOTENT — it reads the current state and only prompts when something
# is NOT already set up — so re-running it is safe and near-instant. Nothing here is fatal:
# a step that cannot finish says why and the next one runs. Where each thing lives:
#
#   GitHub          -> /run/github, the hub's token   (nothing to do in here; checked)
#   ssh identities  -> /run/ds-secrets, read-only     (added on zero in ~/.ds-dev-secrets)
#   Claude Code     -> the login volume infra's compose names (shared by default)
#   /workspace      -> ds-workspace, cloned from DEV_REPO (here, if start-up could not)
set -u

bold() { printf '\n\033[1m%s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
info() { printf '    %s\n' "$1"; }
ask()  { # ask "question" -> exit 0 on yes (default yes on empty)
  local reply
  read -r -p "  $1 [Y/n] " reply || return 1
  [ -z "$reply" ] || [ "$reply" = y ] || [ "$reply" = Y ]
}

REPO="${DEV_REPO:-gsfernandes81/dossier}"
SECRETS="${DEV_SECRETS_DIR:-/run/ds-secrets}"

# ── 1/3  GitHub: the hub's token ─────────────────────────────────────────────
# Nothing to log into: the hub mints this container an hourly token for this repo alone,
# and git (its credential helper) and gh (its wrapper) both use it. So this only reports —
# and NOT through `gh auth status`, which fails for an installation token (it has no user)
# while every repo command works.
bold "1/3  GitHub (the hub's token)"
if [ -s /run/github/token ]; then
  if git ls-remote "https://github.com/${REPO}.git" HEAD >/dev/null 2>&1; then
    ok "the hub's token for ${REPO} works — git and gh need nothing else."
  else
    warn "a token is here but GitHub refuses it for ${REPO}."
    info "Is the App installed on that repo? On zero: cd ~/infra/dev && make status C=hub"
  fi
else
  warn "no token from the hub yet."
  info "The hub writes one at its start and every 30 min; on zero, make status C=hub"
  info "says why it has not."
fi

# ── 2/3  ssh identities ──────────────────────────────────────────────────────
# Read-only in here: the base copies the id_* keys into ~/.ssh and prepends ssh_config.fleet
# to ~/.ssh/config at every start. Nothing in this container can add to them.
bold "2/3  ssh identities ($SECRETS)"
if [ ! -d "$SECRETS" ]; then
  warn "$SECRETS is not mounted — see ds-dev's DS_SECRETS line in infra's dev/.env."
elif ! ls "$SECRETS" >/dev/null 2>&1; then
  warn "$SECRETS is there but uid $(id -u) cannot read it — its owner on zero must be"
  info "this container's dev uid."
else
  keys=$(cd "$SECRETS" && ls -d id_* 2>/dev/null | grep -v '\.pub$' | tr '\n' ' ')
  if [ -n "$keys" ]; then
    ok "keys: ${keys% }"
  else
    ok "no keys — none are needed for GitHub, which is the hub's token."
  fi
  if [ -f "$SECRETS/ssh_config.fleet" ]; then
    ok "ssh_config.fleet is prepended to ~/.ssh/config at start."
    if grep -qiE '^[[:space:]]*Host[[:space:]].*github\.com' "$SECRETS/ssh_config.fleet"; then
      info "Its github.com block is unused: git reaches GitHub over https with the token."
    fi
  fi
  info "To add or change them: on zero, put them in ~/.ds-dev-secrets, then"
  info "cd ~/infra/dev && make restart C=ds-dev (the base copies them in at start)."
fi

# ── 3/3  Claude Code ─────────────────────────────────────────────────────────
# Logging in here is what makes `claude` work in the container at all. The login is shared
# across the dev containers by default (infra's CLAUDE_UNDOCUMENTED_SHARED_LOGINS switch), so
# a login made in any of them is already this one's. An OAuth login belongs to the device
# that made it: never copy a credentials file in from elsewhere.
bold "3/3  Claude Code"
if claude auth status 2>/dev/null | grep -q '"loggedIn": *true'; then
  ok "logged in — \`claude\` will start without a login flow."
else
  warn "Not logged in."
  info "No browser in here: it prints a URL, and you paste back the code."
  ask "Run 'claude auth login' now?" \
    && claude auth login --claudeai --email "${GIT_USER_EMAIL:-gavinfernandes2012@pm.me}"
fi

# ── the workspace ────────────────────────────────────────────────────────────
# /workspace is this container's own volume, cloned at first start from DEV_REPO with the
# hub's token. A start before the token arrived leaves it empty; this is where it gets
# filled, and child-init.sh then adds the project to the venv as a start would have.
bold "the workspace (/workspace)"
if [ -d /workspace/.git ]; then
  ok "$(git -C /workspace remote get-url origin 2>/dev/null) at $(git -C /workspace log --oneline -1 2>/dev/null)"
elif [ -n "$(ls -A /workspace 2>/dev/null)" ]; then
  warn "/workspace holds files but no checkout; not cloning ${REPO} over them."
elif ask "Clone ${REPO} into /workspace now?"; then
  if git clone "https://github.com/${REPO}.git" /workspace; then
    bash /home/dev/child-init.sh || warn "child-init.sh failed — its output is above."
  else
    warn "the clone failed — step 1 above says whether the token is the reason."
  fi
fi

bold "Done."
cat <<'EOF'

  ds-dev is reached through the hub. From the phone or a PC:

      ssh dev                 every dev container; Enter on ds-dev for its sessions,
                              s for a shell in it

  Every session the menu opens is a zmx session: Ctrl-\ detaches and the menu
  comes back, and a dropped connection costs nothing — ssh in again and press Enter.
  Idle sessions are offloaded and stay resumable from the menu.

  From a terminal on zero, in ~/infra/dev:
      make up S=ds-dev        start it (it rests stopped)
      make shell C=ds-dev     a shell in it, without the hub
      make status C=ds-dev    what it is running, and which logins it has
      make login C=ds-dev     these steps again
EOF
