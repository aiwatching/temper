#!/bin/bash
# publish.sh — Bump version, build, commit, tag, push, prompt npm publish
#
# Usage:
#   ./publish.sh          # patch bump (0.1.0 → 0.1.1)
#   ./publish.sh minor    # minor bump (0.1.0 → 0.2.0)
#   ./publish.sh major    # major bump (0.1.0 → 1.0.0)
#   ./publish.sh 0.5.0    # explicit version

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$SCRIPT_DIR"

VERSION_ARG=${1:-patch}
CURRENT=$(grep '^version' Cargo.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')

# Calculate new version
if [[ "$VERSION_ARG" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  NEW_VERSION=$VERSION_ARG
elif [ "$VERSION_ARG" = "patch" ]; then
  IFS='.' read -r major minor patch <<< "$CURRENT"
  NEW_VERSION="$major.$minor.$((patch + 1))"
elif [ "$VERSION_ARG" = "minor" ]; then
  IFS='.' read -r major minor patch <<< "$CURRENT"
  NEW_VERSION="$major.$((minor + 1)).0"
elif [ "$VERSION_ARG" = "major" ]; then
  IFS='.' read -r major minor patch <<< "$CURRENT"
  NEW_VERSION="$((major + 1)).0.0"
else
  echo "Usage: ./publish.sh [patch|minor|major|x.y.z]"
  exit 1
fi

echo "╔══════════════════════════════════╗"
echo "║  Temper publish                   ║"
echo "╚══════════════════════════════════╝"
echo ""
echo "Version: $CURRENT → $NEW_VERSION"
echo ""

# ─── Update versions everywhere ───

echo "Updating versions..."

# Cargo.toml
sed -i '' "s/^version = \"$CURRENT\"/version = \"$NEW_VERSION\"/" Cargo.toml

# npm packages
for PKG in npm/temper/package.json npm/temper-darwin-arm64/package.json; do
  if [ -f "$PKG" ]; then
    sed -i '' "s/\"version\": \"$CURRENT\"/\"version\": \"$NEW_VERSION\"/" "$PKG"
  fi
done

# Update optionalDependencies in main package
node -e "
const pkg = require('./npm/temper/package.json');
for (const dep of Object.keys(pkg.optionalDependencies || {})) {
  pkg.optionalDependencies[dep] = '$NEW_VERSION';
}
require('fs').writeFileSync('./npm/temper/package.json', JSON.stringify(pkg, null, 2) + '\n');
"

echo "  ✓ Cargo.toml → $NEW_VERSION"
echo "  ✓ npm packages → $NEW_VERSION"

# ─── Build ───

echo ""
echo "Building Rust binary..."

export PATH="$HOME/.rustup/toolchains/stable-aarch64-apple-darwin/bin:$PATH"
cargo build --release 2>&1 | tail -1

# Copy binary to npm package
mkdir -p npm/temper-darwin-arm64/bin
cp target/release/temper npm/temper-darwin-arm64/bin/temper
chmod +x npm/temper-darwin-arm64/bin/temper
SIZE=$(wc -c < npm/temper-darwin-arm64/bin/temper | tr -d ' ')
echo "  ✓ Binary built: ${SIZE} bytes"

# ─── Release notes ───

echo ""
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || echo "")
RELEASE_NOTES_FILE="RELEASE_NOTES.md"

# Only auto-generate release notes if the file doesn't exist, or if the user
# passes --regen-notes. Hand-written RELEASE_NOTES.md is preserved by default
# so accidental rerunning doesn't clobber carefully worded release copy.
REGEN_NOTES=false
for arg in "$@"; do
  [ "$arg" = "--regen-notes" ] && REGEN_NOTES=true
done

if [ "$REGEN_NOTES" = "true" ] || [ ! -f "$RELEASE_NOTES_FILE" ]; then
  echo "Generating $RELEASE_NOTES_FILE..."
  {
    echo "# Temper v$NEW_VERSION"
    echo ""
    echo "Released: $(date +%Y-%m-%d)"
    echo ""
    if [ -n "$LAST_TAG" ]; then
      echo "## Changes since $LAST_TAG"
      echo ""
      FEATURES=$(git log --oneline "$LAST_TAG"..HEAD --no-merges --grep="feat:" --format="- %s" 2>/dev/null)
      if [ -n "$FEATURES" ]; then
        echo "### Features"
        echo "$FEATURES"
        echo ""
      fi
      FIXES=$(git log --oneline "$LAST_TAG"..HEAD --no-merges --grep="fix:" --format="- %s" 2>/dev/null)
      if [ -n "$FIXES" ]; then
        echo "### Bug Fixes"
        echo "$FIXES"
        echo ""
      fi
      OTHER=$(git log --oneline "$LAST_TAG"..HEAD --no-merges --format="%s" 2>/dev/null | grep -v -E "^(feat|fix|perf|refactor|docs|chore|test|ci):" | sed 's/^/- /')
      if [ -n "$OTHER" ]; then
        echo "### Other"
        echo "$OTHER"
        echo ""
      fi
    fi
    echo "**Full Changelog**: https://github.com/aiwatching/temper/compare/${LAST_TAG:-main}...v${NEW_VERSION}"
  } > "$RELEASE_NOTES_FILE"
else
  echo "Using existing $RELEASE_NOTES_FILE (pass --regen-notes to overwrite)."
fi

echo "Release notes:"
cat "$RELEASE_NOTES_FILE"
echo ""

# ─── Commit + tag + push ───

echo "Committing..."
git add -A
git commit -m "v$NEW_VERSION"
git tag "v$NEW_VERSION"

echo "Pushing..."
git push origin main
git push origin "v$NEW_VERSION"

# GitHub Release (attach macOS arm64 binary)
if command -v gh &> /dev/null; then
  echo ""
  echo "Creating GitHub Release..."
  gh release create "v$NEW_VERSION" \
    --title "v$NEW_VERSION" \
    --notes-file "$RELEASE_NOTES_FILE" \
    target/release/temper \
    || echo "(release creation skipped)"
fi

# ─── npm publish ───

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Publishing @aion0/temper@$NEW_VERSION to npm         "
echo "╚══════════════════════════════════════════════════════╝"
echo ""

read -p "Publish to npm now? [Y/n] " PUBLISH_NOW
PUBLISH_NOW=${PUBLISH_NOW:-y}

if [ "$PUBLISH_NOW" = "y" ] || [ "$PUBLISH_NOW" = "Y" ]; then
  echo ""
  echo "Publishing @aion0/temper-darwin-arm64..."
  (cd "$PROJECT_DIR/npm/temper-darwin-arm64" && npm publish --access public)

  echo ""
  echo "Publishing @aion0/temper..."
  (cd "$PROJECT_DIR/npm/temper" && npm publish --access public)

  echo ""
  echo "✓ Published @aion0/temper@$NEW_VERSION"
  echo ""
  echo "Install: npm install -g @aion0/temper"
else
  echo "Skipped. Manually publish later:"
  echo "  cd npm/temper-darwin-arm64 && npm publish --access public"
  echo "  cd npm/temper && npm publish --access public"
fi

echo ""
echo "GitHub Actions will also build + publish darwin-x64 and linux-x64."
echo "Check: https://github.com/aiwatching/temper/actions"
