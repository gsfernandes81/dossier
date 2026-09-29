# dossier — cargo shortcuts for the Rust workspace (crates/*).
#
# Thin wrappers spelled exactly as the Rust local gate in CLAUDE.md and the `rust` CI
# workflow spell them, so `make rust-gate` is that gate and not an approximation of
# it. The Python side is still the `uv run ...` commands in CLAUDE.md, and the
# throwaway spike/ tree keeps its own commands. The remote dev container's targets
# live in Makefile.dev (`make -f Makefile.dev dev`), not here.

CARGO ?= cargo
PHONE_TARGET := aarch64-unknown-linux-musl

# Desktop release build. Release rather than debug because that is the profile every
# measurement and perf gate in this repo is taken in.
build:
	$(CARGO) build --workspace --release

# The phone binary: static aarch64 musl. Needs clang and llvm-ar on PATH for ring's C
# (.cargo/config.toml). The path is printed because it moves: the dev container sets
# CARGO_TARGET_DIR outside the bind mount, so there it is not under target/.
phone:
	$(CARGO) build --workspace --release --target $(PHONE_TARGET)
	@echo "phone binary: $${CARGO_TARGET_DIR:-target}/$(PHONE_TARGET)/release/ds"

fmt:
	$(CARGO) fmt --all

fmt-check:
	$(CARGO) fmt --all --check

# Pedantic is on in the crates; triage its findings, never silence them.
clippy:
	$(CARGO) clippy --workspace --all-targets -- -D warnings

# Not called `test`, so nobody reaches for it expecting pytest. Release, because the
# perf gates only assert there.
rust-test:
	$(CARGO) test --workspace --release -- --nocapture

# The whole Rust local gate from CLAUDE.md, in its order. Mirror it before pushing.
# Sequenced by recursion rather than prerequisites so `make -j` cannot interleave it.
rust-gate:
	@$(MAKE) --no-print-directory fmt-check
	@$(MAKE) --no-print-directory clippy
	@$(MAKE) --no-print-directory rust-test
	@$(MAKE) --no-print-directory phone

# Run the app on this machine. Arguments go through ARGS: make run ARGS="--help".
run:
	$(CARGO) run -p ds --release -- $(ARGS)

clean:
	$(CARGO) clean

# Install ds for this user. cargo's bin dir (~/.cargo/bin, %USERPROFILE%\.cargo\bin) is
# put on PATH once by rustup, so nothing here edits PATH. Termux's rust package does no
# such thing, so there it goes to $PREFIX/bin, which is on PATH already.
ifdef TERMUX_VERSION
INSTALL_ROOT := --root $(PREFIX)
endif
install:
	$(CARGO) install --locked --path crates/ds $(INSTALL_ROOT)

.PHONY: build phone fmt fmt-check clippy rust-test rust-gate run clean install
