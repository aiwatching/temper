#!/usr/bin/env bash
# Build Rust binaries for all platforms and prepare npm packages
#
# Prerequisites:
#   - Rust toolchain with cross-compilation targets
#   - For cross-compile: cargo install cross
#
# Usage:
#   ./scripts/build-npm.sh          # build all platforms
#   ./scripts/build-npm.sh local    # build current platform only

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
NPM_DIR="$PROJECT_DIR/npm"
VERSION=$(grep '^version' "$PROJECT_DIR/Cargo.toml" | head -1 | sed 's/.*"\(.*\)".*/\1/')

echo "Building temper v$VERSION"

# Map target to npm package
target_to_pkg() {
  case "$1" in
    aarch64-apple-darwin) echo "temper-darwin-arm64" ;;
    x86_64-apple-darwin)  echo "temper-darwin-x64" ;;
    x86_64-unknown-linux-gnu) echo "temper-linux-x64" ;;
    *) echo "" ;;
  esac
}

ALL_TARGETS="aarch64-apple-darwin x86_64-apple-darwin x86_64-unknown-linux-gnu"
CURRENT_TARGET=$(rustc -vV | grep host | awk '{print $2}')

build_target() {
  local TARGET=$1
  local NPM_PKG=$(target_to_pkg "$TARGET")

  if [ -z "$NPM_PKG" ]; then
    echo "Unknown target: $TARGET"
    return
  fi

  echo ""
  echo "=== Building $TARGET → $NPM_PKG ==="

  mkdir -p "$NPM_DIR/$NPM_PKG/bin"

  if [ "$TARGET" = "$CURRENT_TARGET" ]; then
    echo "Native build..."
    cargo build --release --manifest-path "$PROJECT_DIR/Cargo.toml"
    cp "$PROJECT_DIR/target/release/temper" "$NPM_DIR/$NPM_PKG/bin/temper"
  else
    echo "Cross-compile with cross..."
    if ! command -v cross &>/dev/null; then
      echo "Warning: 'cross' not installed. Run: cargo install cross"
      echo "Skipping $TARGET"
      return
    fi
    cross build --release --target "$TARGET" --manifest-path "$PROJECT_DIR/Cargo.toml"
    cp "$PROJECT_DIR/target/$TARGET/release/temper" "$NPM_DIR/$NPM_PKG/bin/temper"
  fi

  chmod +x "$NPM_DIR/$NPM_PKG/bin/temper"

  cd "$NPM_DIR/$NPM_PKG"
  npm version "$VERSION" --no-git-tag-version --allow-same-version 2>/dev/null || true

  local SIZE=$(wc -c < "$NPM_DIR/$NPM_PKG/bin/temper" | tr -d ' ')
  echo "Done: $NPM_DIR/$NPM_PKG/bin/temper ($SIZE bytes)"
}

if [ "$1" = "local" ]; then
  build_target "$CURRENT_TARGET"
else
  for TARGET in $ALL_TARGETS; do
    build_target "$TARGET"
  done
fi

# Update main package version
cd "$NPM_DIR/temper-cli"
npm version "$VERSION" --no-git-tag-version --allow-same-version 2>/dev/null || true

# Update optionalDependencies versions
node -e "
const pkg = require('./package.json');
for (const dep of Object.keys(pkg.optionalDependencies || {})) {
  pkg.optionalDependencies[dep] = '$VERSION';
}
require('fs').writeFileSync('package.json', JSON.stringify(pkg, null, 2) + '\n');
"

echo ""
echo "=== Build complete ==="
echo "Version: $VERSION"
echo ""
echo "To publish:"
echo "  cd npm/temper-darwin-arm64 && npm publish --access public"
echo "  cd npm/temper-darwin-x64   && npm publish --access public"
echo "  cd npm/temper-linux-x64    && npm publish --access public"
echo "  cd npm/temper-cli          && npm publish"
