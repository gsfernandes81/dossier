#!/usr/bin/env bash
# ds-dev's own start-up, baked into the image at /home/dev/child-init.sh and RUN by the
# base image's entrypoint (gsrpi-dev-base's child-init seam). The base runs it after it has
# cloned or pulled /workspace — a volume holding this repo's https clone, made with the
# hub's GitHub token — and before it starts sshd, so a lockfile that pull moved is the one
# installed from, and nothing has arrived yet to meet a half-installed venv. infra's
# `make login C=ds-dev` also runs it, after a clone start-up could not make.
#
# THE GIT IDENTITIES ARE NOT HERE. The base copies every id_* key out of DEV_SECRETS_DIR
# (/run/ds-secrets: the host's ~/.ds-dev-secrets, read-only, once the clone's .dev-ssh/) and
# prepends its ssh_config.fleet to ~/.ssh/config, before the pull. Nothing in the container
# can write there, and GitHub needs none of it: git and gh use the hub's token.
#
# NON-FATAL AT THE OTHER END. A non-zero exit here is printed by the entrypoint as a
# warning and the container still comes up — deliberately, because a container you can ssh
# into and fix beats one that refused to start over its dependencies. So this script
# reports failure honestly rather than swallowing it: an old `uv sync … || true` hid a
# broken sync behind a container that looked fine until the first import failed.
set -u

rc=0

# ── the old name for the identities' ssh config ─────────────────────────────
# The base reads ssh_config.fleet and nothing else. A secrets directory carried over from a
# clone still on the pre-2026-08-24 name holds `config`, which is silently ignored — so say so.
secrets="${DEV_SECRETS_DIR:-/run/ds-secrets}"
if [ -f "$secrets/config" ] && [ ! -f "$secrets/ssh_config.fleet" ]; then
    echo "$secrets/config is ignored: the base reads ssh_config.fleet. On zero, rename it:"
    echo "    mv ~/.ds-dev-secrets/config ~/.ds-dev-secrets/ssh_config.fleet"
fi

# ── the venv ────────────────────────────────────────────────────────────────
# The dependency set is baked into /home/dev/venv at build time with
# --no-install-project; this adds the editable project itself, which cannot happen at
# build time because there is no clone then. It runs on every start because the venv is
# in the image and not on a volume, so a rebuild is a fresh one.
#
# THE FLAGS MUST MATCH Dockerfile.dev's build sync (--all-extras --group driver). uv sync
# makes the environment match what it is asked for, so a start with fewer flags would
# UNINSTALL the extras and the driver group the build put there.
#
# --frozen: install exactly what uv.lock says and never resolve. A start-up that quietly
# re-resolved dependencies would make the container's environment a function of when it
# was last restarted.
if [ -f /workspace/pyproject.toml ]; then
    if (cd /workspace && uv sync --frozen --all-extras --group driver); then
        echo "venv synced from uv.lock (--all-extras --group driver)"
    else
        rc=1
        echo "uv sync FAILED — /home/dev/venv is stale or incomplete."
        echo "    The container is still up: ssh in and run the same command to see why."
    fi
else
    echo "no clone in /workspace yet — venv left as built."
    echo "    The first clone needs the hub's token; 'make login C=ds-dev' on zero clones it."
fi

exit "$rc"
