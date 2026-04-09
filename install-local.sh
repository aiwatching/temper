#!/bin/bash
# Build and install temper to local npm global
set -e

export PATH="$HOME/.rustup/toolchains/stable-aarch64-apple-darwin/bin:$PATH"

echo "Building..."
cargo build --release 2>&1 | tail -1

DEST="$HOME/.nvm/versions/node/$(node -v)/lib/node_modules/@aion0/temper/node_modules/@aion0/temper-darwin-arm64/bin/temper"

if [ -f "$DEST" ]; then
  cp target/release/temper "$DEST"
  echo "Installed: $(temper --version)"
else
  echo "npm package not found. Run: npm install -g @aion0/temper"
fi
