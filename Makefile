# dossier — this Makefile FORWARDS into dev/, and does nothing else.
#
# The remote dev container's management interface is `dev/Makefile`; everything
# here is a `dev-`prefixed alias for a target in there, so the names CLAUDE.md has
# always documented keep working from the repo root:
#
#   make dev              build + start + walk the logins   (= cd dev && make dev)
#   make dev-up           build + (re)create
#   make dev-restart      stop + start (does NOT re-read compose.yaml)
#   make dev-status       one-screen health check
#   make dev-verify       status + the toolchain a rebuild should have landed
#   make dev-login        re-run the interactive logins
#   make dev-shell        a fish shell in the container
#   make dev-claude       attach (or start) the container's `claude` session
#   make dev-logs         follow the container log (= sshd's)
#   make dev-boot-log     the entrypoint's start-up lines, from the top
#   make dev-rc-log       the remote-control supervisor's log, when enabled
#   make dev-down         stop and remove the container (volumes stay)
#   make dev-down-volumes CONFIRM=yes   also drop the volumes
#
# dossier's day-to-day workflow is the `uv run …` and `cargo …` commands in
# CLAUDE.md, NOT make — there are no lint/test/build targets here on purpose.
# See dev/README.md for what the container is and how it is used.

DEV := $(MAKE) --no-print-directory -C $(CURDIR)/dev

.PHONY: dev dev-up dev-restart dev-status dev-verify dev-login dev-shell \
	dev-claude dev-logs dev-boot-log dev-rc-log dev-down dev-down-volumes

dev:              ; @$(DEV) dev
dev-up:           ; @$(DEV) up
dev-restart:      ; @$(DEV) restart
dev-status:       ; @$(DEV) status
dev-verify:       ; @$(DEV) verify
dev-login:        ; @$(DEV) login
dev-shell:        ; @$(DEV) shell
dev-claude:       ; @$(DEV) claude
dev-logs:         ; @$(DEV) logs
dev-boot-log:     ; @$(DEV) boot-log
dev-rc-log:       ; @$(DEV) rc-log
dev-down:         ; @$(DEV) down
# CONFIRM is forwarded rather than absorbed, so the guard in dev/Makefile is the
# only place that decides whether this is allowed to run.
dev-down-volumes: ; @$(DEV) down-volumes CONFIRM=$(CONFIRM)
