#!/usr/bin/env bash
# ds-dev's own start-up, baked into the image at /home/dev/child-init.sh and RUN by the
# base image's entrypoint — the fifth seam of gsrpi-dev-base, added 2026-08-24 for this
# container and dd-dev. The base runs it after its `git pull --ff-only` of /workspace and
# before it starts the remote-control supervisor and sshd, so a lockfile that pull moved
# is the one installed from, and nothing has arrived yet to meet a half-installed venv.
# (That pull only authenticates once .dev-ssh/ssh_config.fleet exists — see below.)
#
# WHAT USED TO BE HERE AND IS NOT ANY MORE. This replaces docker-entrypoint.dev.sh, and
# most of that file was not lost but inherited — the base does all of it, for four
# containers instead of one:
#   the sshd host key into ~/.ssh-host        the ~/.ssh/environment publish for ssh
#   the ~/.claude.json first-run seeding      the GitHub HTTPS->SSH insteadOf rewrite
#   starting sshd, and the RC supervisor      git user.name / user.email
# What is left is the two things that are this repo's: its git identities, and its venv.
#
# NON-FATAL AT THE OTHER END. A non-zero exit here is printed by the entrypoint as a
# warning and the container still comes up — deliberately, because a container you can ssh
# into and fix beats one that refused to start over its dependencies. So this script
# reports failure honestly rather than swallowing it: the old `uv sync … || true` hid a
# broken sync behind a container that looked fine until the first import failed.
set -u

rc=0

# ── git identities ──────────────────────────────────────────────────────────
# The keys and the ssh config live in the gitignored .dev-ssh/, which rides along with the
# bind-mounted clone. DEV_SECRETS_DIR points the base at that directory, and the base does
# both halves BEFORE it pulls: it copies every private key out of it into ~/.ssh at 0600,
# and it PREPENDS .dev-ssh/ssh_config.fleet to ~/.ssh/config.
#
# THAT FILENAME IS THE BASE'S, and it is why this section is four lines rather than forty.
# `ssh_config.fleet` is an odd name in this repo — it is infra-dev's word for "the
# host-specific half of ~/.ssh/config" — and it is worth it: the identities are then in
# force in time for the start-up `git pull`, which is not true of anything this script
# could do, because this script runs after it.
#
# NOTHING IS SYMLINKED ANY MORE, and that is a correctness fix rather than tidying. Until
# 2026-08-24 ~/.ssh/config was a symlink to .dev-ssh/config; under the base that destroys
# the host's file. The base rewrites ~/.ssh/config with a redirection at every start, a
# redirection follows a symlink, and ~/.ssh is not a volume — so the FIRST boot writes a
# regular file and replaces it with the link, and the SECOND boot (any docker stop/start)
# truncates /workspace/.dev-ssh/config and fills it with the baked defaults. The base now
# `rm -f`s that path first; this script no longer gives it a link to find either way.
if [ -d /workspace/.dev-ssh ]; then
    chmod 700 /workspace/.dev-ssh 2>/dev/null || true
    # ssh refuses a key it considers group- or world-readable, and these live in a bind
    # mount whose modes came from the host. Failures are ignored: a read-only clone is a
    # thing somebody may try, and it is not a reason to fail the start.
    chmod 600 /workspace/.dev-ssh/id_* 2>/dev/null || true

    if [ -f /workspace/.dev-ssh/ssh_config.fleet ]; then
        echo "ssh identities: .dev-ssh/ssh_config.fleet, prepended by the base at start"
    elif [ -f /workspace/.dev-ssh/config ]; then
        # The pre-base layout, still on disk. COPIED, never linked, so this boot works —
        # and said loudly, because until it is renamed the identities arrive after the
        # pull and that pull reports itself as offline every start.
        cp -f /workspace/.dev-ssh/config "$HOME/.ssh/config"
        chmod 600 "$HOME/.ssh/config"
        echo "LEGACY LAYOUT: .dev-ssh/config is the pre-base name for this file."
        echo "    Copied into ~/.ssh/config for this boot. On the host, rename it:"
        echo "        mv .dev-ssh/config .dev-ssh/ssh_config.fleet"
        echo "    Until then the start-up 'git pull' has no identity to offer and says"
        echo "    'pull skipped (not fast-forward, or offline)' whatever the truth is."
    else
        echo "no ssh identities in .dev-ssh — 'make dev-login' on the host writes them."
    fi
else
    echo "no /workspace/.dev-ssh — this container cannot push to GitHub over ssh yet."
    echo "    'make dev-login' on the host creates it."
fi

# ── the venv ────────────────────────────────────────────────────────────────
# The dependency set is baked into /home/dev/venv at build time with
# --no-install-project; this adds the editable project itself, which cannot happen at
# build time because /workspace is not mounted then. It runs on every start because the
# venv is in the image and not on a volume, so a rebuild is a fresh one.
#
# THE FLAGS MUST MATCH Dockerfile.dev's build sync (--all-extras --group driver). uv sync
# makes the environment match what it is asked for, so a start with fewer flags would
# UNINSTALL the extras and the driver group the build put there.
#
# --frozen: install exactly what uv.lock says and never resolve. A start-up that quietly
# re-resolved dependencies would make the container's environment a function of when it
# was last restarted.
if [ -f /workspace/pyproject.toml ]; then
    if uv sync --frozen --all-extras --group driver; then
        echo "venv synced from uv.lock (--all-extras --group driver)"
    else
        rc=1
        echo "uv sync FAILED — /home/dev/venv is stale or incomplete."
        echo "    The container is still up: ssh in and run the same command to see why."
    fi
else
    echo "no /workspace/pyproject.toml — the clone is not mounted; venv left as built."
fi

exit "$rc"
