# ds-dev — dossier's development container

Runs the dossier toolchain — Rust *and* Python — on `zero` (Raspberry Pi 5, 4 GB,
arm64), so the work can be driven from a phone or a laptop without either of them
holding a toolchain. It also builds and runs on an ordinary amd64 dev host; the
image is arch-agnostic and both `gh` and the Rust toolchain resolve their own
architecture.

It lives here, in the app repo, rather than in a host-services repo, on the same
rule its siblings follow: **Claude dev containers stay in their own app repos —
they are developer tooling, not host services.** `dd-dev` and `or3-dev` on the
same box are the siblings, and this container is their descendant: destiny-director
wrote the original, or3 has done most of the fixing since, and this is the port of
that fixing plus what dossier needs and they do not.

## Layout

| | |
|---|---|
| `Makefile` | **the management interface** — `make up`, `make login`, `make status`, … |
| `compose.yaml` | the stack — `name: ds-dev`, one service, loopback-only port |
| `Dockerfile` | Debian slim + Rust + uv/Python + Node 22 + Claude Code + gh + screen/abduco + sshd; fish as `dev`'s login shell |
| `entrypoint.sh` | ssh material → `git pull` → venv → claude state → **sshd(fg)** |
| `login.sh` | in the image: the interactive logins, idempotent — `make login` |
| `status.sh` | on the host: the readouts — `make status`, `verify`, `boot-log` |
| `rc-supervisor.sh` | keeps `claude remote-control` alive — **only under `DS_REMOTE_CONTROL=1`** |
| `sshd_config` | the in-container daemon |
| `config.fish` | fish's config, baked in — puts every login shell in `/workspace` |
| `screenrc` | screen's, for ordinary shell work |
| `.env.example` | copy to `.env` on the host and edit |

The same targets are available from the repo root as `make dev-up`,
`make dev-login`, `make dev-status` and so on; the root `Makefile` does nothing
but forward into here.

## How this container is used

**You ssh into it and work in an `abduco` session.** From a PC, or from Termux on
the phone:

```sh
ssh ds-dev                                   # a shell
ssh -t ds-dev abduco -A claude claude        # a claude that survives the link
abduco                                       # (inside) list sessions
```

`abduco -A NAME CMD` attaches the session called `NAME`, creating it if it is not
there — so the same command starts the work and comes back to it. **Ctrl-\\**
detaches; what is under it keeps running, and a dropped connection costs nothing.
A claude started *outside* a session dies with the ssh link that carried it, which
on a phone means dies at the first lock screen. The same is true of a long
`cargo test --release`, and of dossier's own TUI.

`screen` is also in the image and is better for ordinary shell work — it has
scrollback and a status line. abduco is what you want under a full-screen program,
because it is detach/attach and nothing else: no key handling beyond Ctrl-\\, so
every key goes through to what is underneath. For the TUI driver tests in `tools/`
that matters — a multiplexer that swallowed keys would be testing itself.

**The shell is fish**, and it is fish because that is `dev`'s login shell in
`/etc/passwd`: sshd reads the shell from there and consults nothing else, so this
is the one setting that reaches an ssh session. `config.fish` cds a *login* shell
to `/workspace`, since sshd starts you in the home directory whatever the image's
`WORKDIR` says. Scripts in here still run under bash, by explicit invocation.

> If Zed-remote ever misbehaves against this container, the login shell is the
> first thing to suspect and a one-line fix:
> `docker exec -u 0 ds-dev usermod --shell /bin/bash dev`. Everything else in the
> image is shell-agnostic.

The Makefile is how the container is stood up and looked at, not how it is used.
`make claude` and `make shell` are the same two sessions reached from a terminal
on the host instead — `make claude` attaches the *same* `claude` session the ssh
line above does, not a second one.

Claude Remote Control — the claude.ai/code and mobile-app daemon — is **off by
default**. See below; it is a thing to turn on for a spell, not the way in.

## Bring-up, from nothing

```sh
git clone git@github.com:gsfernandes81/dossier.git ~/dossier
cp ~/dossier/dev/.env.example ~/dossier/dev/.env && $EDITOR ~/dossier/dev/.env
cd ~/dossier/dev && make dev
```

`.env` needs one thing set for certain: `DEV_SSH_AUTHORIZED_KEYS`, the **host**
user's `.ssh` directory. It is bind-mounted read-only and the in-container sshd
reads `authorized_keys` straight out of it, so the keys that reach the host reach
this container and there is no second list to maintain. Adding a client later
means appending to that file on the host — nothing in here needs restarting.

`make dev` is `make up` followed by `make login`, which is the walkthrough in
`login.sh`: git over ssh, `gh auth login`, `claude auth login`. Every step reads
the current state first and only prompts for what is not done, so re-running it is
safe and usually silent.

Then `make status` should read `sshd: running in the foreground` and `auth: claude
logged in`, and `make verify` should show the whole toolchain — including
`rustfmt`, `clippy` and the `aarch64-unknown-linux-musl` target, which is the part
a rebuild is most likely to have quietly dropped.

### If you already had the old container

The files used to be `Dockerfile.dev`, `docker-compose.dev.yml`, `docker-*.dev.sh` and
`sshd_config.dev` at the repo root, with no `name:` in the compose file — so Compose
derived the project name from the directory and the volumes were called
`dossier_ds-claude`, `dossier_ds-uv-cache` and so on. This file declares `name: ds-dev`,
so the new volumes are `ds-dev_ds-claude` and friends: **the old ones are orphaned, not
migrated.** In practice that costs one `make login` and one changed host key, which is
the cheapest possible time to pay it. Clean up the old set by hand when you are happy:

```sh
docker volume ls | grep '^local *dossier_'
```

The host-side ssh port also moved, from 2222 to **2225** — update the client's
`~/.ssh/config`, and clear its `known_hosts` entry for the old one.

## The targets

```sh
cd ~/dossier/dev
make            # the header of the Makefile: every target, one line each
make up         # build + (re)create — drops every session in the container
make restart    # stop + start; does NOT re-read compose.yaml
make status     # one screen: container, sshd, sessions, logins
make verify     # status + the toolchain a rebuild should have landed
make login      # the logins, again
make claude     # attach (or start) the `claude` abduco session
make shell      # a fish shell in the container
make logs       # follow the container log (= sshd's)
make boot-log   # the entrypoint's lines, from the top, ANSI stripped
make rc-log     # the remote-control supervisor's log, when it is enabled
```

They work with or without `sudo` — the Makefile adds one when it is not already
root.

The uid the container's `dev` user is built at comes from the **owner of the
checkout**, read with `stat`, not from `id -u`: under `sudo` that is 0, and a dev
user at uid 0 writes root-owned files into the bind-mounted clone. It refuses to
build at 0 rather than doing it.

`restart` is `stop`/`start` deliberately. `up -d` re-evaluates the config and may
recreate the container, which would throw away every live abduco session in it;
cycling it must not be able to do that by accident. The corollary: a change to
`compose.yaml` or `.env` — ports, limits, `DS_REMOTE_CONTROL` — only lands on `up`.

`make down-volumes` takes `CONFIRM=yes`, because the volumes hold the claude and
gh logins, the sshd **host key**, and the cargo caches and target directory.
Dropping them asks every client to accept a changed host key — which is the moment
a real one would be waved through — and makes the next build a cold one.

`logs` follows sshd's output. When the question is why a start went wrong, use
`boot-log`: the top of the log, where the entrypoint's lines are.

## The toolchain in here

The whole of CLAUDE.md's **Rust local gate** has to run in this container, and
`make verify` exists to prove the pieces arrived:

```sh
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace --release -- --nocapture
cargo build --workspace --release --target aarch64-unknown-linux-musl
(cd spike && cargo fmt --check && cargo clippy --all-targets -- -D warnings && cargo test --release)
```

Rust itself needs nothing but `rustup target add` for the phone target — rustup
ships musl's libc and crt objects and `rust-lld` links them. The **clang** in the
image is for `ring`, which arrives via rustls via the Syncthing REST check and
compiles C and assembly: `cc` looks for an `aarch64-linux-musl-gcc` that does not
exist, so `.cargo/config.toml` points `CC`/`AR` at clang and `llvm-ar`. That is
the same pair CI's `phone` job installs, on purpose.

Two things live **outside** the `/workspace` bind mount, and both matter:

- **`/home/dev/venv`** — the Python virtualenv, pre-built at image build time with
  `--all-extras --group driver`. Outside the mount so the mount cannot shadow it;
  `VIRTUAL_ENV` is set so Zed and ty find it over ssh. `entrypoint.sh` adds the
  editable project on every start, and its flags must stay in step with the
  Dockerfile's or that sync will uninstall the baked extras.
- **`/home/dev/cargo-target`** — `CARGO_TARGET_DIR`, on a named volume. Outside the
  mount because the host may have its own cargo, and a target directory shared
  between two toolchains is invalidated and rebuilt on every alternation. On a
  volume so an incremental `cargo test --release` survives a rebuild, which on a
  Pi is the difference between seconds and minutes.

  The consequence to know: **the phone binary is not under `/workspace/target`.**
  It is at
  `/home/dev/cargo-target/aarch64-unknown-linux-musl/release/ds`, and `make verify`
  prints the `docker cp` line for getting it out.

The cargo **registry** and **git** caches are separate volumes mounted at
`~/.cargo/registry` and `~/.cargo/git` rather than one over all of `~/.cargo` —
`CARGO_HOME` also holds the rustup-installed binaries, and a volume over the whole
directory would shadow the toolchain a rebuild just installed with whatever the
old volume held.

## The three things that are load-bearing

**`sshd` is the foreground process, so the container's lifetime is the ssh
endpoint's.** That is the arrangement this thing is for: you get in by ssh, and
nothing you type can end the container — `exit` closes an ssh session, `/exit`
closes a claude, and PID 1 has not moved.

It was the other way round until recently, with the Remote Control supervisor on
PID 1 so that `docker logs` would show the Claude session, and sshd backgrounded
under it. That made an **optional** component the thing keeping the container
alive: a wedged or crashed supervisor took the ssh door down with it. Now
`docker logs ds-dev` is sshd's `-e` output, the supervisor is opt-in and
backgrounded with a filtered log of its own, and neither can affect the other.

**A session that is not in `abduco` dies with the link that carried it.** This is
the whole reason abduco is in the image, and on a phone it is not a corner case:
the ssh session ends at the lock screen, and an unwrapped claude — or an unwrapped
`cargo test --release` — ends with it, part-way through. `abduco -A claude claude`
is the habit; `make claude` and the ssh line above deliberately name the *same*
session, so it does not matter which way you came in.

**Nothing of dossier's data is in here.** dossier is a local, file-backed TUI, and
the container is deliberately missing everything its ancestors have for services:
no database, no Railway, no migrations, no deploy target — and **no data store
mounted**. Tests use pytest's `tmp_path` and Rust's `tempfile`; real documents and
a real `.dossier/` stay off the dev box. Only the repo clone is bind-mounted.

## Remote Control, when you want it

`rc-supervisor.sh` runs `claude remote-control --spawn worktree`, which makes the
container drivable from claude.ai/code and the mobile app. **It does not run
unless `DS_REMOTE_CONTROL=1` is in `dev/.env`**, and then only from `make up` —
the entrypoint reads it at start, so `make restart` will not pick up the change.
`make rc-log` shows what it is doing.

That default is the point of the change: the way in is ssh, and a remote-control
daemon nobody is driving is a live claude in a container with a permission
classifier for company. Turn it on for a spell away from a terminal; turn it off
again by setting the value back and running `make up`.

It runs with `--permission-mode auto` — Claude's classifier judges each tool call
rather than prompting. Deliberate, and a change from this container's earlier
default: nobody is at this container's terminal, and a prompt waiting on a keypress
is itself one of the ways a live session wedges. Set `RC_PERMISSION_MODE=` (empty)
to restore prompting, or `acceptEdits`.

Four first-run dialogs would otherwise block it, all seeded by the entrypoint on
every start whether or not the daemon is enabled — because an interactive `claude`
over ssh meets three of them too, and nothing in a headless container can answer
any: `hasTrustDialogAccepted` for `/workspace` (without it `--spawn worktree`
aborts with "Workspace not trusted"), `remoteDialogSeen` (the one-time "Enable
Remote Control?" consent — when falsy it opens a readline prompt on stdin and
re-prompts on every restart), `theme`, and `hasCompletedOnboarding`.

The supervisor **polls for a login rather than failing without one**, so enabling
it before `make login` is harmless — it comes up within a minute of the login with
no restart.

Its recycle rule is the part not to loosen: it restarts a wedged daemon **only at
literally zero sessions**, and only after it has served at least one. A wedged
daemon still holding a live session is left alone until that session ends. Live
work is never traded away to unstick the server.

That policy depends on `ps` and `pgrep` — from `procps`, which the image now
installs explicitly. Without them every session count reads zero, the "has served
at least one" flag never sets, and the recycle silently never fires. If you ever
slim the package list, that is the trap.

> **Never run `git worktree prune` against the host's clone from the host.** The
> worktrees `--spawn worktree` creates are registered under `/workspace` paths,
> which do not resolve outside the container, so every one of them would read as
> prunable and be unregistered. The supervisor runs it *inside* the container,
> where those paths are real.

## Copying a Claude login does not work

or3 tried it and it failed in a way worth recording rather than repeating. A
phone's `~/.claude/.credentials.json` was copied into the container's config
volume; `claude` started and **rewrote the file one second later with zero-length
`accessToken` and `refreshToken`** — logged out before anyone could use it. The
rewritten file also carried a different `refreshTokenExpiresAt`, so the container
had reached the auth server and put the shared token through a refresh the phone
did not initiate.

Two conclusions: an **OAuth login belongs to the device that performed it**, and
copying one can **disturb the source device**. So this container never seeds
credentials — the durable path is one interactive login, which persists in the
`ds-claude` volume and survives rebuilds:

```sh
cd ~/dossier/dev && make login
```

The entrypoint does keep one piece of that lesson: it deletes a `.credentials.json`
holding empty tokens, because a husk is worse than no file at all — `claude auth
status` reports logged out either way, but the login flow can trip over it.

## Reaching the container's sshd

`sshd` listens on 2222 inside and is published on **`${DEV_SSH_BIND}:${DEV_SSH_PORT}`**
of the host — `127.0.0.1:2225` by default. 2222 is `dd-dev` and 2224 is `or3-dev`
on this same box; the number is this container's identity there, so leave it alone
unless you have a real collision.

Three things decide whether a given client can use it, and they are in three
different places:

- **The address.** Loopback is the default because it is the whole of this port's
  protection: sshd behind it is key-only, but what a key gets you is a shell in a
  container holding a GitHub push key, a `gh` token and a Claude login. A
  `cloudflared` running on the host itself already reaches `127.0.0.1:2225` and
  needs nothing changed here. Set `DEV_SSH_BIND` to the docker bridge gateway only
  if cloudflared runs in a container of its own and therefore cannot see the host's
  loopback — or to `0.0.0.0` if you deliberately want the home LAN to reach it.
  Publishing is create-time: it lands on `make up`, never on `make restart`.
- **The key.** `AuthorizedKeysFile` is the read-only bind mount of the host user's
  `.ssh` directory, so a key added there is live immediately with no restart.
  Password auth is off, the daemon is non-root and can only ever serve `dev`, and
  there is no other door.
- **The host key.** Generated once and kept in the `ds-ssh-host` volume rather than
  per build, so a client's `known_hosts` entry survives every rebuild. That is the
  point of it: a host key that changed on each `make up` would train you to accept
  a changed key, which is the one habit that makes the check worthless.

Outbound is pinned rather than accepted: the entrypoint writes `~/.ssh/known_hosts`
from scratch on every start with the literal `github.com` keys, so a changed key is
an edit to `entrypoint.sh` and never a first-contact `yes`.

## What was deliberately NOT taken from upstream

- **or3's `seed-secrets.sh` and `/run/or3-secrets` mount.** They exist to move a
  vessel-LAN ssh key and a per-repo deploy key from a phone onto the host. dossier
  has neither: its git identity is a key pair in the gitignored `.dev-ssh/` that
  rides with the clone, and its authorized_keys is the host user's own.
- **or3's `ssh_config`.** It is the `or3ecr` tunnel and the deploy key, both
  or3-only. The one useful half — pinned `github.com` host keys — was ported into
  `entrypoint.sh` instead.
- **destiny-director's `docker-run-devbot.sh`, `postgres` service, Railway CLI,
  `mariadb-client`/`postgresql-client`, and `env_file`.** dossier has no service to
  run, no database, and no deploy target. This is the divergence the container was
  forked for and it has not changed.

## Why not alpine

`dd-dev` moved to alpine. This did not follow it, and the reason is not the obvious
one. **It is not that Claude Code would not run** — its npm installer resolves
`linux-arm64-musl` as a first-class target. What musl actually costs is **Python**:
no manylinux wheel matches it, so anything carrying a C extension becomes a source
build, on a Pi 5 sharing 4 GB with whatever else the box runs. dossier's Python
side is a satellite now, but it is a satellite with `--all-extras` in it.

The low-risk move, if this base is ever changed, is `trixie` — still glibc, current
stable, and bookworm is oldstable now. It does not remove the abduco build stage:
abduco is absent from bookworm and trixie both, and is back in Debian only in
forky. Either way, do it as its own change — a base image change is a rebuild, and
a rebuild is `make up`, which drops every abduco session that is live.
