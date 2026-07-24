# dossier — dev-container orchestration.
#
# This Makefile exists SOLELY to drive the remote dev container
# (docker-compose.dev.yml + Dockerfile.dev). dossier's day-to-day workflow is the
# `uv run ...` commands in CLAUDE.md, NOT make — there are no lint/test/build targets
# here on purpose. Everything below is the container lifecycle only.
#
# Ported from the sibling project destiny-director, minus DB/Railway/Atlas (dossier
# is a local, file-backed TUI).

# Build the image with the uid/gid that OWN this clone so the bind-mounted /workspace
# stays writable, then start it detached. We read the owner with `stat`, NOT `id -u`:
# when docker is run via sudo/root, `id -u` is 0 and the build then collides with the
# root account (`groupadd: GID '0' already exists`). The clone owner is the right uid
# whoever launches the build. DEV_HOSTNAME sets the container's hostname to the docker
# host's name + `-ds-dev`, so Claude Code shows a stable, meaningful machine title
# instead of the random container ID.
dev-up:
	HOST_UID=$$(stat -c '%u' .) HOST_GID=$$(stat -c '%g' .) DEV_HOSTNAME=$$(hostname)-ds-dev docker compose -f docker-compose.dev.yml up -d --build

# One command to stand the whole thing up: build + start the container, wait for it
# to be running, then walk through any logins that aren't done yet (git SSH, GitHub,
# Claude) interactively. Every login step is idempotent — already-signed-in services
# are skipped — so this is safe to re-run. Once Claude is logged in the entrypoint's
# background supervisor brings up `claude remote-control --spawn worktree` on its own
# (~10s), so there's nothing to exec by hand.
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
# shell history) — use when the baked uid changed and the volumes must be recreated
# under the new owner.
dev-down-volumes:
	docker compose -f docker-compose.dev.yml down -v

.PHONY: dev-up dev dev-login dev-down dev-down-volumes
