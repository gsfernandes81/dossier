#!/usr/bin/env bash
# ds-dev's own start-up, baked into the image at /home/dev/child-init.sh and RUN by the
# base image's entrypoint — the fifth seam of gsrpi-dev-base, added 2026-08-24 for this
# container and dd-dev. The base runs it after its `git pull --ff-only` of /workspace and
# before it starts the remote-control supervisor and sshd, which is the only window where
# both halves are true: the lockfile in the mount is current, and nothing has arrived yet
# to meet a half-installed venv.
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
# Keys and the ssh config live in the gitignored .dev-ssh/, which rides along with the
# bind-mounted clone. They are host-side because they are credentials, and the config
# beside them is host-side because `make dev-login` writes it.
#
# The symlink OVERWRITES the file the base assembled from /home/dev/ssh_config a moment
# ago, which is intended: that baked file carries the defaults that have to be right when
# .dev-ssh/config is absent, and this one is the real thing when it is present.
if [ -d /workspace/.dev-ssh ]; then
    chmod 700 /workspace/.dev-ssh 2>/dev/null || true
    # ssh refuses a key it considers group- or world-readable, and these live in a bind
    # mount whose modes came from the host. Failures are ignored: a read-only clone is a
    # thing somebody may try, and it is not a reason to fail the start.
    chmod 600 /workspace/.dev-ssh/id_* 2>/dev/null || true
    if [ -f /workspace/.dev-ssh/config ]; then
        mkdir -p "$HOME/.ssh" && chmod 700 "$HOME/.ssh"
        ln -sf /workspace/.dev-ssh/config "$HOME/.ssh/config"
        echo "~/.ssh/config -> /workspace/.dev-ssh/config"
    else
        echo "no /workspace/.dev-ssh/config — ssh has the baked defaults only."
        echo "    'make dev-login' on the host writes one, key and all."
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
