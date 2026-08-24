# dossier — dev-container orchestration.
#
# This Makefile exists SOLELY to drive the remote dev container
# (docker-compose.dev.yml + Dockerfile.dev). dossier's day-to-day workflow is the
# `uv run ...` commands in CLAUDE.md, NOT make — there are no lint/test/build targets
# here on purpose. Everything below is the container lifecycle only.
#
# Ported from the sibling project destiny-director, minus DB/Railway/Atlas (dossier
# is a local, file-backed TUI).

# Build the image and start it detached. It no longer passes HOST_UID/HOST_GID: since
# 2026-08-24 the image is a thin child of gsrpi-dev-base and the `dev` user is built in
# the BASE, at the uid of whoever built THAT — 1001 in the published one. If this host's
# clone owner is a different uid, build the base locally (`make base` in infra's dev/,
# which reads its own clone's owner) and this build picks it up, because docker prefers a
# local image over a pull. DEV_HOSTNAME sets the container's hostname to the docker
# host's name + `-ds-dev`, so Claude Code shows a stable, meaningful machine title
# instead of the random container ID.
dev-up:
	DEV_HOSTNAME=$$(hostname)-ds-dev docker compose -f docker-compose.dev.yml up -d --build
	@$(MAKE) --no-print-directory dev-check-uid

# The guard that the deleted HOST_UID build arg used to make unnecessary. The base image
# bakes its `dev` account at whatever uid built it (1001 in the published one), and if
# that is not this clone's owner the container comes up perfectly and then fails at two
# things at once, neither of which names a uid: /workspace is read-only from inside, and
# sshd refuses the bind-mounted authorized_keys under StrictModes as "bad ownership or
# modes", which reads as a broken key. Checked after the start rather than before the
# build, because the image's uid is a property of the image and this asks the container
# itself. A warning and not a failure: the container is up and `docker exec` still works,
# which is how you would fix it.
dev-check-uid:
	@cuid=$$(docker exec ds-dev id -u 2>/dev/null); \
	owner=$$(stat -c '%u' .); \
	if [ -z "$$cuid" ]; then \
	  echo "note: ds-dev is not running yet — skipping the uid check." >&2; \
	elif [ "$$cuid" != "$$owner" ]; then \
	  echo "" >&2; \
	  echo "WARNING: ds-dev's dev user is uid $$cuid; this clone is owned by uid $$owner." >&2; \
	  echo "         /workspace is not writable from inside the container, and sshd will" >&2; \
	  echo "         refuse every login as 'bad ownership or modes' on the host account's" >&2; \
	  echo "         authorized_keys. Neither message says uid." >&2; \
	  echo "         Fix: build the base at this uid, then re-run make dev-up —" >&2; \
	  echo "             cd ~/infra/dev && make base" >&2; \
	  echo "         It reads its own clone's owner and tags the result under the same" >&2; \
	  echo "         ghcr name, which docker prefers over the pull. If the uid changes," >&2; \
	  echo "         the named volumes must be recreated too: make dev-down-volumes." >&2; \
	  echo "" >&2; \
	fi

# One command to stand the whole thing up: build + start the container, wait for it
# to be running, then walk through any logins that aren't done yet (git SSH, GitHub,
# Claude) interactively. Every login step is idempotent — already-signed-in services
# are skipped — so this is safe to re-run. Once Claude is logged in the supervisor the
# base image's entrypoint started (DEV_REMOTE_CONTROL=1) brings up `claude
# remote-control --spawn worktree` on its own (~10s), so there's nothing to exec by
# hand.
dev: dev-up
	@echo "Waiting for ds-dev to come up (up to 120s)..."
	@for i in $$(seq 1 120); do \
		docker exec ds-dev true 2>/dev/null && break; \
		[ $$i = 120 ] && { echo "ERROR: ds-dev did not become exec-able within 120s — check 'docker compose -f docker-compose.dev.yml logs dev'." >&2; exit 1; }; \
		sleep 1; \
	done
	@$(MAKE) dev-login

# Re-run the interactive login walkthrough against an already-running container.
dev-login:
	docker exec -it ds-dev bash /home/dev/login.sh

dev-down:
	docker compose -f docker-compose.dev.yml down

# Also drops the named volumes (uv cache, claude/gh config, sshd host key, zed server,
# shell history) — use when the BASE image's uid changed and the volumes must be
# recreated under the new owner.
dev-down-volumes:
	docker compose -f docker-compose.dev.yml down -v

.PHONY: dev-up dev-check-uid dev dev-login dev-down dev-down-volumes
