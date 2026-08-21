# syntax=docker/dockerfile:1

# ds-dev — the development container for dossier, built to run on `zero`
# (Raspberry Pi 5, 4 GB, arm64) and equally on an ordinary amd64 dev host.
#
# Lineage: destiny-director's Dockerfile.dev, by way of or3's dev/Dockerfile —
# minus everything DB/Railway/Atlas-specific, because dossier is a local,
# file-backed TUI with no database, no migrations and no deploy target. See
# dev/README.md for what this container is and how it is used.
#
# The repo is NOT copied in: compose bind-mounts the host clone at /workspace, so
# a `git pull` in the container and one on the host are the same pull on one
# working tree. Only the toolchain is baked here. Two things live deliberately
# OUTSIDE the bind mount so the mount cannot shadow them and so host and
# container never fight over the same build outputs: the Python venv
# (/home/dev/venv) and the cargo target directory (/home/dev/cargo-target).
#
# The BUILD CONTEXT is the repo root, not this directory, because the venv
# pre-build below needs pyproject.toml and uv.lock. Paths to the files in here
# are therefore `dev/…`, and the .dockerignore that applies is the repo root's.

# ── abduco, built from source ───────────────────────────────────────────────
# Built rather than installed because abduco is NOT in bookworm: Debian has it in
# trixie and sid only, so `apt-get install abduco` fails this build outright. The
# trixie .deb is not a way round that — it is linked against a newer glibc than
# this base carries. It is one C file with no dependency beyond libc, so a
# throwaway stage holding a compiler costs the final image nothing.
#
# Why abduco is here at all: ssh is the way into this container now, and a
# session that is not wrapped in abduco dies with the ssh link that carried it.
# On a phone that is not a corner case — the link ends at the lock screen.
FROM python:3.13-slim-bookworm AS abduco-build
ARG ABDUCO_VERSION=0.6
# Upstream's own release tarball, pinned by hash. A mismatch must FAIL the build
# rather than be retried: this stage compiles what it downloads, and the result
# is then run as the dev user in a container that also holds a git push key.
ARG ABDUCO_SHA256=c90909e13fa95770b5afc3b59f311b3d3d2fdfae23f9569fa4f96a3e192a35f4
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libc6-dev make curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSLo /tmp/abduco.tar.gz \
        "https://www.brain-dump.org/projects/abduco/abduco-${ABDUCO_VERSION}.tar.gz" \
    && echo "${ABDUCO_SHA256}  /tmp/abduco.tar.gz" | sha256sum -c - \
    && tar xzf /tmp/abduco.tar.gz -C /tmp \
    && make -C "/tmp/abduco-${ABDUCO_VERSION}" \
    && install -m 0755 "/tmp/abduco-${ABDUCO_VERSION}/abduco" /usr/local/bin/abduco

# ── the container itself ────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm AS dev
ARG USERNAME=dev
ARG USER_UID=1000
ARG USER_GID=1000

# fish matches the shell on the host and is the dev account's LOGIN shell (see the
# useradd/usermod below) — the /etc/shells line is added rather than assumed,
# because that is what `chsh` and anything else consulting it read. Scripts in
# here still run under bash, which is not going anywhere.
#
# openssh-server runs the in-container sshd — the foreground process and the way
# in. openssh-client is what git needs for git@github.com (Debian's git does not
# pull an ssh client under --no-install-recommends).
#
# screen and abduco are what an ssh session's work lives in. abduco is the one to
# reach for under a full-screen program — claude, or dossier's own TUI — because
# it is detach/attach and nothing else: no key handling beyond Ctrl-\, so every
# key reaches the program underneath. screen keeps its scrollback and status line
# for ordinary shell work.
#
# procps is NOT decoration and was missing from this image before: `ps` and
# `pgrep` are what rc-supervisor.sh counts live Remote Control sessions with, and
# without them session_count() silently returns 0 forever — which disables the
# whole "recycle only when genuinely idle" policy and makes the supervisor's
# safety constraint unverifiable. iproute2 gives status.sh its `ss`. less is
# git's and gh's pager.
#
# build-essential is the host-native linker rustc calls `cc`, plus anything
# Claude Code or the TUI driver needs to build from source. clang and llvm are
# the CROSS C compiler for the phone target — see the rustup block below.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential clang llvm git make screen fish \
        openssh-client openssh-server curl gnupg ca-certificates tzdata \
        procps iproute2 less \
    && rm -rf /var/lib/apt/lists/* \
    && if ! grep -qx /usr/bin/fish /etc/shells; then echo /usr/bin/fish >> /etc/shells; fi

# Debian's `llvm` provides /usr/bin/llvm-ar through the versioned package. If a
# future base image drops that unversioned symlink, make one here rather than
# letting the phone cross-build fail months later with an unfindable `ar`:
# .cargo/config.toml names `llvm-ar` unversioned, so that is the name that has to
# exist. Both tools are checked in the same layer, so a base that stops shipping
# either fails the BUILD instead of the gate.
RUN set -eux; \
    if ! command -v llvm-ar >/dev/null 2>&1; then \
      ln -s "$(ls -1 /usr/bin/llvm-ar-* | head -1)" /usr/local/bin/llvm-ar; \
    fi; \
    clang --version | head -1; \
    llvm-ar --version | head -1

COPY --from=abduco-build /usr/local/bin/abduco /usr/local/bin/abduco

# Node 22 LTS + Claude Code. The npm cache is cleaned in the same layer — it is
# ~100 MB of nothing this image will ever read again.
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g @anthropic-ai/claude-code \
    && npm cache clean --force

# GitHub CLI (`gh`): the release tarball directly (Debian's apt repo lags), which
# is also one fewer signing key and sources.list entry in an image that already
# carries NodeSource's. GitHub publishes linux arm64 + amd64 assets whose names
# match `dpkg --print-architecture` verbatim, so no arch remapping is needed.
# Pinned rather than `latest` — an unpinned download is a different binary on
# every rebuild with nothing to compare it against. Bump GH_VERSION to upgrade.
ARG GH_VERSION=2.96.0
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      arm64|amd64) : ;; \
      *) echo "unsupported arch: $arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_${arch}.tar.gz" \
      | tar -xz -C /usr/local/bin --strip-components=2 "gh_${GH_VERSION}_linux_${arch}/bin/gh"; \
    gh --version

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Create the dev user at the HOST owner's uid/gid, passed by the Makefile as build
# args, so the bind-mounted /workspace stays writable from both sides. Guard
# against a uid/gid the base image already uses so the build cannot hard-fail on a
# collision.
#
# The login shell is fish, and that is the setting that makes it the default for
# `ssh ds-dev`: sshd runs the shell out of /etc/passwd and consults nothing else,
# so setting $SHELL or aliasing something would never reach an ssh session. It is
# set with usermod as well as at useradd so the collision branch — where the uid
# already exists and useradd is skipped — cannot quietly leave you on /bin/sh.
#
# (This supersedes the older "bash stays the login shell" note inherited from
# destiny-director. That mattered when the container was driven by `docker exec
# -it … bash`; ssh is the front door now. Everything scripted still runs under
# bash by explicit invocation — the CMD below, login.sh, rc-supervisor.sh.)
RUN if ! getent group "${USER_GID}" >/dev/null; then \
        groupadd --gid "${USER_GID}" "${USERNAME}"; \
    fi \
    && if ! getent passwd "${USER_UID}" >/dev/null; then \
        useradd --uid "${USER_UID}" --gid "${USER_GID}" --shell /usr/bin/fish -m "${USERNAME}"; \
    fi \
    && usermod --shell /usr/bin/fish "$(getent passwd "${USER_UID}" | cut -d: -f1)" \
    && mkdir -p /workspace && chown "${USER_UID}:${USER_GID}" /workspace

# Venv lives OUTSIDE /workspace so the bind mount cannot shadow the pre-built venv.
# VIRTUAL_ENV mirrors UV_PROJECT_ENVIRONMENT so ty/ruff/Zed toolchain detection find
# it over SSH (they key off VIRTUAL_ENV, and this repo has no project-local .venv).
#
# CARGO_TARGET_DIR is outside the bind mount for the same reason plus one more:
# `zero` may have its own cargo, and a target directory shared between a host
# toolchain and this image's would be invalidated and rebuilt from scratch on
# every alternation. It is on a named volume (compose.yaml), so an incremental
# `cargo test --release` survives a rebuild — which on a Pi is the difference
# between seconds and minutes. Consequence to know: the phone binary the Rust
# gate cross-builds lands at
#   /home/dev/cargo-target/aarch64-unknown-linux-musl/release/ds
# and NOT under /workspace/target, so `make dev-verify` prints that path.
#
# CARGO_HOME holds the rustup-installed cargo binaries as well as the registry
# and git caches, so the VOLUMES in compose.yaml cover .cargo/registry and
# .cargo/git only. Mounting a volume over all of ~/.cargo would shadow the
# toolchain the next rebuild installs with whatever the old volume held.
#
# CLAUDE_CONFIG_DIR moves ~/.claude.json INTO the persisted .claude volume so
# Claude's onboarding/trust survives a rebuild; without it every rebuild re-runs
# onboarding on a container with nobody to answer it. GH_CONFIG_DIR does the same
# for `gh auth login` (gh writes hosts.yml there and, with no keyring in a slim
# image, the token goes in that file). HISTFILE points bash history at the
# persisted .local/share volume; fish's own history is already in there.
#
# SCREENDIR: screen keeps its sockets in /run/screen, which a container image does
# not have and a non-root user cannot create, so an interactive `screen` over ssh
# would die with "Directory '/run/screen' must have mode 777". Point it at the dev
# user's home. Deliberately NOT on a volume — a socket that outlived its container
# is a stale session `screen -r` would try, and fail, to attach to.
# ABDUCO_CMD is set for a blunter reason: abduco's compiled-in default command is
# `dvtm`, which is not installed here, so `abduco -c work` with no command would
# fail rather than hand you a shell.
#
# PATH is set here rather than assembled in the entrypoint so one definition
# covers docker exec, ssh (the entrypoint copies $PATH into ~/.ssh/environment)
# and every RUN below.
ENV TZ=Etc/UTC \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/home/dev/venv \
    VIRTUAL_ENV=/home/dev/venv \
    RUSTUP_HOME=/home/dev/.rustup \
    CARGO_HOME=/home/dev/.cargo \
    CARGO_TARGET_DIR=/home/dev/cargo-target \
    ABDUCO_CMD=/usr/bin/fish \
    CLAUDE_CONFIG_DIR=/home/dev/.claude \
    GH_CONFIG_DIR=/home/dev/.config/gh \
    HISTFILE=/home/dev/.local/share/.bash_history \
    SCREENRC=/home/dev/.screenrc \
    SCREENDIR=/home/dev/.screen \
    PATH=/home/dev/.cargo/bin:/home/dev/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

USER ${USERNAME}
WORKDIR /workspace

# Pre-create every directory compose mounts a named volume onto, AS the dev user.
# A volume that is empty on first use inherits the ownership of the directory it
# covers; created by root here, uv/claude/gh/cargo could not write to their own
# state. .host-ssh needs no pre-create — it is a directory bind mount that
# inherits the mounted .ssh dir's ownership. .screen and .abduco are not volumes;
# they are here so the 700 both daemons insist on is set once rather than left to
# the umask of whoever runs one first.
RUN mkdir -p /home/dev/.cache/uv /home/dev/.claude /home/dev/.config/gh \
        /home/dev/.config/fish /home/dev/.ssh-host /home/dev/.zed_server \
        /home/dev/.local/share /home/dev/.screen /home/dev/.abduco \
        /home/dev/cargo-target /home/dev/.cargo/registry /home/dev/.cargo/git \
    && chmod 700 /home/dev/.ssh-host /home/dev/.screen /home/dev/.abduco

# ── the Rust toolchain ──────────────────────────────────────────────────────
# The whole of CLAUDE.md's "Rust local gate" has to be runnable in here:
#
#   cargo fmt --all --check
#   cargo clippy --workspace --all-targets -- -D warnings
#   cargo test --workspace --release -- --nocapture
#   cargo build --workspace --release --target aarch64-unknown-linux-musl
#   (cd spike && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test --release)
#
# so: stable with rustfmt and clippy, and the aarch64-musl target — the same
# minimal-profile-plus-components shape .github/workflows/rust.yml installs, on
# purpose, because a local gate that installs a different component set from CI
# is a local gate that lies. Components and target are added in separate `rustup`
# calls rather than as rustup-init flags: `rustup component add a b` and `rustup
# target add …` are the stable, unambiguous spellings, and they are also what the
# CI workflow uses, so this block reads the same as the one it mirrors.
#
# Pin RUST_TOOLCHAIN to a version if `stable` ever moves under you; the
# workspace's own floor is rust-version = "1.89" in Cargo.toml.
#
# Rust needs no external toolchain for the musl target — rustup ships musl's libc
# and crt objects and rust-lld links them (.cargo/config.toml, and the R0.2 spike
# that settled it). The clang/llvm installed above are for `ring`, which arrives
# via rustls via the Syncthing REST check and compiles C and assembly: `cc` looks
# for an `aarch64-linux-musl-gcc` that does not exist, so .cargo/config.toml
# points CC/AR at clang and llvm-ar, which cross-compile by construction. This is
# the same pair CI's `phone` job installs.
#
# NOT run with a cache mount: the registry it would populate is exactly what the
# ds-cargo-registry volume is for at runtime, and a build-time cache mount is
# invisible to the container.
ARG RUST_TOOLCHAIN=stable
RUN curl -fsSL https://sh.rustup.rs \
      | sh -s -- -y --no-modify-path --profile minimal \
          --default-toolchain "${RUST_TOOLCHAIN}" \
    && rustup component add rustfmt clippy \
    && rustup target add aarch64-unknown-linux-musl \
    && rustc --version && cargo --version \
    && cargo fmt --version && cargo clippy --version \
    && rustup target list --installed

# ── the Python satellite ────────────────────────────────────────────────────
# Pre-build the full dependency set into /home/dev/venv: the dev group (default),
# all extras (dedup + scan), and the opt-in `driver` group for the real-terminal
# TUI harness in tools/. The editable project is added at container start, once
# /workspace is mounted. Keep these flags in sync with entrypoint.sh's sync, or
# that sync will uninstall the extras baked here.
#
# These two paths are why the build context is the repo root.
COPY --chown=${USER_UID}:${USER_GID} pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/home/dev/.cache/uv,uid=${USER_UID},gid=${USER_GID} \
    uv sync --frozen --no-install-project --all-extras --group driver

# ── the container's own files ───────────────────────────────────────────────
# The in-container sshd: port 2222, non-root, key-only. It is the FOREGROUND
# process — see entrypoint.sh — so this is the container's payload, not a
# side-channel.
COPY --chown=${USER_UID}:${USER_GID} dev/sshd_config /home/dev/sshd_config
EXPOSE 2222

# fish is the login shell, and this is the half of that which puts every login
# shell in /workspace rather than in the home directory. Not on a volume, so a
# rebuild is what updates it.
COPY --chown=${USER_UID}:${USER_GID} dev/config.fish /home/dev/.config/fish/config.fish
COPY --chown=${USER_UID}:${USER_GID} dev/screenrc /home/dev/.screenrc

# The interactive login walkthrough (git SSH / GitHub / Claude), run by
# `make dev-login`. It lives in the IMAGE rather than in the Makefile because
# every step of it needs a tty and every credential store it fills is in here.
COPY --chown=${USER_UID}:${USER_GID} dev/login.sh /home/dev/login.sh

# Keeps `claude remote-control` alive and recycles it only when wedged AND idle.
# Started by the entrypoint IN THE BACKGROUND, and only under
# DS_REMOTE_CONTROL=1 — see compose.yaml and the script header.
COPY --chown=${USER_UID}:${USER_GID} dev/rc-supervisor.sh /home/dev/rc-supervisor.sh
COPY --chown=${USER_UID}:${USER_GID} dev/entrypoint.sh /home/dev/entrypoint.sh

CMD ["bash", "/home/dev/entrypoint.sh"]
