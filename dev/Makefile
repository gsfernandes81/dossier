# ds-dev, from a terminal on the host. **This file is the container's management
# interface** — the docker commands live here and nowhere else. The two things too
# long to be a recipe went where they belong instead: the readouts into
# `status.sh` on this side, the interactive logins into `login.sh` in the image.
#
#   make dev         up, then walk through whatever is not logged in yet
#   make up          build the image and (re)create the container
#   make restart     stop + start, NO re-evaluation of the config
#   make status      one-screen health check
#   make verify      status + the toolchain a rebuild should have landed
#   make login       log this container into GitHub and claude.ai (idempotent)
#   make claude      a claude in the container's `claude` abduco session
#   make shell       a fish shell in the container
#   make logs        follow the container log (= sshd's)
#   make boot-log    the entrypoint's start-up lines, from the top
#   make rc-log      the remote-control supervisor's log (when it is enabled)
#   make down        stop and remove the container (volumes stay)
#   make down-volumes  also drop the volumes — logins, host key and caches go
#
# The same targets are `make dev-up`, `make dev-login`, `make dev-status` … from
# the repo root, which forwards into here.
#
# The container is USED over ssh, not from here — `ssh ds-dev` from a PC or from
# Termux, with abduco holding the session. These targets stand it up and tell you
# what state it is in; they are not the daily interface. See dev/README.md.
#
# This is the ONLY make in the repo that does anything: dossier's day-to-day
# workflow is the `uv run …` and `cargo …` commands in CLAUDE.md, not make. There
# are deliberately no lint/test/build targets here.

CONTAINER := ds-dev

# `sudo` unless already root, so both `make up` and `sudo make up` work.
SUDO := $(if $(filter 0,$(shell id -u)),,sudo)

# The repo root, absolute. compose.yaml uses it for BOTH the /workspace bind mount
# and the build context (the image pre-builds the venv from pyproject.toml +
# uv.lock, which are up there). Resolved here rather than written as `..` in the
# compose file — a compose file with `..` in it breaks the moment anything runs it
# from another directory, which is the convention these stacks share on this host.
# `realpath` and not $HOME: HOME is root's under sudo.
DS_SRC := $(realpath $(CURDIR)/..)

# The uid/gid the container's `dev` user is built at, read from the OWNER of the
# checkout — not from `id -u`. Under sudo `id -u` is 0, and a dev user built at 0
# writes root-owned files into the bind-mounted clone (and collides with the base
# image's own root account at build time). The clone's owner is the right answer
# however this was invoked.
HOST_UID := $(shell stat -c '%u' $(DS_SRC))
HOST_GID := $(shell stat -c '%g' $(DS_SRC))

# Claude Code shows the container's hostname as the machine title, so make it say
# which box this is rather than a random container id.
DEV_HOSTNAME ?= $(shell hostname)-ds-dev

# `sudo env VAR=…`, not `VAR=… sudo`: sudo resets the environment, so an
# assignment in front of it never reaches docker and compose would silently build
# at the defaults in compose.yaml. env is inside the sudo, so it survives.
COMPOSE := $(SUDO) env DS_SRC=$(DS_SRC) HOST_UID=$(HOST_UID) HOST_GID=$(HOST_GID) \
	DEV_HOSTNAME=$(DEV_HOSTNAME) \
	docker compose --project-directory $(CURDIR) -f $(CURDIR)/compose.yaml
DOCKER  := $(SUDO) docker

.PHONY: help dev up restart down down-volumes status verify logs boot-log \
	login claude shell rc-log _require-env _require-uid

help:
	@awk '/^#/ { sub(/^# ?/, ""); print; next } { exit }' $(MAKEFILE_LIST)

# ── guards ──────────────────────────────────────────────────────────────────
# Prerequisites rather than checks inside `up`, so each says which thing is
# missing and nothing gets as far as docker with a name it cannot resolve.
_require-env:
	@[ -f $(CURDIR)/.env ] || { \
	  echo "no $(CURDIR)/.env — copy .env.example to .env and edit it." >&2; \
	  echo "At minimum it must set DEV_SSH_AUTHORIZED_KEYS." >&2; exit 1; }

_require-uid:
	@[ "$(HOST_UID)" != "0" ] || { \
	  echo "refusing to build the container's dev user at uid 0: this checkout is" >&2; \
	  echo "owned by root, so every file written from the container would be too." >&2; \
	  echo "chown the clone to your own account first." >&2; exit 1; }

# ── lifecycle ───────────────────────────────────────────────────────────────
up: _require-env _require-uid
	$(COMPOSE) up -d --build
	@printf '\n'
	@$(MAKE) --no-print-directory status

# One command from nothing: build, start, wait for the container to answer, then
# walk through the logins that are not done. Every step of login.sh is idempotent,
# so this is also the safe thing to run when you are not sure what state it is in.
dev: up
	@echo; echo "Waiting for $(CONTAINER) to answer (up to 120s)..."
	@for i in $$(seq 1 120); do \
	  $(DOCKER) exec $(CONTAINER) true 2>/dev/null && break; \
	  [ $$i = 120 ] && { echo "ERROR: $(CONTAINER) never became exec-able — make boot-log" >&2; exit 1; }; \
	  sleep 1; \
	done
	@$(MAKE) --no-print-directory login

# stop + start, deliberately NOT `up -d`. `up` re-evaluates compose.yaml and may
# recreate the container, which drops every abduco session in it. The corollary:
# a change to compose.yaml — ports, limits, DS_REMOTE_CONTROL — lands only on `up`.
restart:
	$(DOCKER) stop  $(CONTAINER)
	$(DOCKER) start $(CONTAINER)
	@printf '\n'
	@$(MAKE) --no-print-directory status

down:
	$(COMPOSE) down

# The volumes hold the claude login, the gh login, the sshd HOST key, the caches
# and the cargo target directory. Dropping them means logging in again, rebuilding
# every crate from scratch, and every client being asked to accept a changed host
# key — which is the moment a real key change would be waved through. So it takes
# a word on the command line, not just a target name.
down-volumes:
	@[ "$(CONFIRM)" = yes ] || { \
	  echo "This drops every ds-* volume:" >&2; \
	  echo "  - the claude and gh logins go, and must be done again (make login)" >&2; \
	  echo "  - the sshd host key goes, so every client will be asked to accept a" >&2; \
	  echo "    new one — clear their known_hosts entry yourself" >&2; \
	  echo "  - the uv and cargo caches and the cargo target dir go: the next" >&2; \
	  echo "    build is a cold one, which on a Pi is minutes, not seconds" >&2; \
	  echo "Say you mean it:  make down-volumes CONFIRM=yes   (in dev/)" >&2; \
	  echo "              or: make dev-down-volumes CONFIRM=yes   (from the repo root)" >&2; \
	  exit 1; }
	$(COMPOSE) down -v

# ── readouts ────────────────────────────────────────────────────────────────
# status.sh is one script rather than three recipes because these are long, and a
# health check spelled in Makefile line-continuations is a health check nobody
# will correct. It reads the container and prints; it changes nothing.
status verify boot-log:
	@SUDO="$(SUDO)" CONTAINER=$(CONTAINER) bash $(CURDIR)/status.sh $@

logs:
	$(DOCKER) logs -f --tail 100 $(CONTAINER)

# Only ever has anything in it when DS_REMOTE_CONTROL=1 — `docker logs` shows
# sshd now, and the supervisor keeps its own filtered file.
rc-log:
	$(DOCKER) exec $(CONTAINER) tail -n 60 /home/dev/.local/share/remote-control.log

# ── into the container ──────────────────────────────────────────────────────
# The interactive half. All three are `docker exec -it`, which is the way in from
# the host itself; from a PC or the phone the way in is ssh, and login.sh prints
# those lines at the end.
login:
	$(DOCKER) exec -it $(CONTAINER) bash /home/dev/login.sh

# `abduco -A`: attach the session named `claude`, creating it if it is not there.
# So this is the same command whether you are starting the work or coming back to
# it, and it is the same session `ssh -t ds-dev abduco -A claude claude` reaches —
# one session, not one per way in. Ctrl-\ detaches; what is under it keeps running.
claude:
	$(DOCKER) exec -it $(CONTAINER) abduco -A claude claude

shell:
	$(DOCKER) exec -it $(CONTAINER) fish
