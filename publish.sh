#!/bin/bash
# publish.sh — Bump version, build, commit, tag, push, prompt npm publish
#
# Usage:
#   ./publish.sh          # patch bump (0.1.0 → 0.1.1)
#   ./publish.sh minor    # minor bump (0.1.0 → 0.2.0)
#   ./publish.sh major    # major bump (0.1.0 → 1.0.0)
#   ./publish.sh 0.5.0    # explicit version

set -e

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
for PKG in npm/temper-cli/package.json npm/temper-darwin-arm64/package.json npm/temper-darwin-x64/package.json npm/temper-linux-x64/package.json; do
  if [ -f "$PKG" ]; then
    sed -i '' "s/\"version\": \"$CURRENT\"/\"version\": \"$NEW_VERSION\"/" "$PKG"
  fi
done

# Update optionalDependencies in main package
node -e "
const pkg = require('./npm/temper-cli/package.json');
for (const dep of Object.keys(pkg.optionalDependencies || {})) {
  pkg.optionalDependencies[dep] = '$NEW_VERSION';
}
require('fs').writeFileSync('./npm/temper-cli/package.json', JSON.stringify(pkg, null, 2) + '\n');
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

echo "# Temper v$NEW_VERSION" > "$RELEASE_NOTES_FILE"
echo "" >> "$RELEASE_NOTES_FILE"
echo "Released: $(date +%Y-%m-%d)" >> "$RELEASE_NOTES_FILE"
echo "" >> "$RELEASE_NOTES_FILE"

if [ -n "$LAST_TAG" ]; then
  echo "## Changes since $LAST_TAG" >> "$RELEASE_NOTES_FILE"
  echo "" >> "$RELEASE_NOTES_FILE"

  FEATURES=$(git log --oneline "$LAST_TAG"..HEAD --no-merges --grep="feat:" --format="- %s" 2>/dev/null)
  if [ -n "$FEATURES" ]; then
    echo "### Features" >> "$RELEASE_NOTES_FILE"
    echo "$FEATURES" >> "$RELEASE_NOTES_FILE"
    echo "" >> "$RELEASE_NOTES_FILE"
  fi

  FIXES=$(git log --oneline "$LAST_TAG"..HEAD --no-merges --grep="fix:" --format="- %s" 2>/dev/null)
  if [ -n "$FIXES" ]; then
    echo "### Bug Fixes" >> "$RELEASE_NOTES_FILE"
    echo "$FIXES" >> "$RELEASE_NOTES_FILE"
    echo "" >> "$RELEASE_NOTES_FILE"
  fi

  OTHER=$(git log --oneline "$LAST_TAG"..HEAD --no-merges --format="%s" 2>/dev/null | grep -v -E "^(feat|fix|perf|refactor|docs|chore|test|ci):" | sed 's/^/- /')
  if [ -n "$OTHER" ]; then
    echo "### Other" >> "$RELEASE_NOTES_FILE"
    echo "$OTHER" >> "$RELEASE_NOTES_FILE"
    echo "" >> "$RELEASE_NOTES_FILE"
  fi
else
  echo "Initial release" >> "$RELEASE_NOTES_FILE"
  echo "" >> "$RELEASE_NOTES_FILE"
  echo "### Features" >> "$RELEASE_NOTES_FILE"
  echo "- Code Graph: tree-sitter Java AST analysis with incremental updates" >> "$RELEASE_NOTES_FILE"
  echo "- Module Registry: define module boundaries with glob patterns, auto-suggest on init" >> "$RELEASE_NOTES_FILE"
  echo "- Knowledge Store: SQLite with causal chains, experiences, full temporal history" >> "$RELEASE_NOTES_FILE"
  echo "- 17 MCP Tools: search_code, get_module, remember, recall, find_causal_chain, etc." >> "$RELEASE_NOTES_FILE"
  echo "- Semantic Search: external embedding API + cosine similarity" >> "$RELEASE_NOTES_FILE"
  echo "- Interface Map: REST endpoint + public method extraction" >> "$RELEASE_NOTES_FILE"
  echo "- HTML Dashboard: temper export for visualization" >> "$RELEASE_NOTES_FILE"
  echo "- On-demand graph refresh via git status" >> "$RELEASE_NOTES_FILE"
  echo "- Proactive constraint injection in get_file_context" >> "$RELEASE_NOTES_FILE"
fi

echo "" >> "$RELEASE_NOTES_FILE"
echo "**Full Changelog**: https://github.com/AiON0/temper/compare/${LAST_TAG:-main}...v${NEW_VERSION}" >> "$RELEASE_NOTES_FILE"

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

# GitHub Release
if command -v gh &> /dev/null; then
  echo ""
  echo "Creating GitHub Release..."
  gh release create "v$NEW_VERSION" --title "v$NEW_VERSION" --notes-file "$RELEASE_NOTES_FILE" || echo "(release creation skipped)"
fi

# ─── npm publish instructions ───

echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  Ready to publish @aion0/temper@$NEW_VERSION"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Run these commands (will prompt for npm 2FA):"
echo ""
echo "  # 1. Platform binary"
echo "  cd npm/temper-darwin-arm64 && npm publish --access public"
echo ""
echo "  # 2. Main package"
echo "  cd ../temper-cli && npm publish --access public"
echo ""
echo "After publish, users install with:"
echo "  npm install -g @aion0/temper"
