#!/usr/bin/env bash
#
# Temper npm publish script
#
# Usage:
#   ./scripts/publish-npm.sh              # dry-run (验证，不实际发布)
#   ./scripts/publish-npm.sh --publish    # 实际发布到 npm
#
# Prerequisites:
#   - npm login (已登录 npm 账号)
#   - npm org: @temper (已创建 org)
#   - ./scripts/build-npm.sh local 已执行（或 GitHub Actions 已构建）
#
# 发布顺序: platform packages → main package
# (main package 的 optionalDependencies 引用 platform packages，必须先发布 platform)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
NPM_DIR="$PROJECT_DIR/npm"
VERSION=$(grep '^version' "$PROJECT_DIR/Cargo.toml" | head -1 | sed 's/.*"\(.*\)".*/\1/')
DRY_RUN=true

if [ "$1" = "--publish" ]; then
  DRY_RUN=false
fi

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}╔═══════════════════════════════════╗${NC}"
echo -e "${BLUE}║   Temper npm publish v$VERSION       ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════╝${NC}"
echo ""

if $DRY_RUN; then
  echo -e "${YELLOW}DRY RUN mode — run with --publish to actually publish${NC}"
  echo ""
fi

# ─── Pre-flight checks ───

echo "Pre-flight checks..."

# 1. npm login
NPM_USER=$(npm whoami 2>/dev/null || echo "")
if [ -z "$NPM_USER" ]; then
  echo -e "${RED}Error: not logged in to npm. Run: npm login${NC}"
  exit 1
fi
echo -e "  npm user: ${GREEN}$NPM_USER${NC}"

# 2. Check all platform binaries exist
PLATFORMS="temper-darwin-arm64 temper-darwin-x64 temper-linux-x64"
MISSING=0
for PKG in $PLATFORMS; do
  BIN="$NPM_DIR/$PKG/bin/temper"
  if [ -f "$BIN" ]; then
    SIZE=$(wc -c < "$BIN" | tr -d ' ')
    echo -e "  $PKG: ${GREEN}✓${NC} ($SIZE bytes)"
  else
    echo -e "  $PKG: ${RED}✗ binary not found${NC}"
    MISSING=$((MISSING + 1))
  fi
done

if [ $MISSING -gt 0 ]; then
  echo ""
  echo -e "${YELLOW}Warning: $MISSING platform binaries missing.${NC}"
  echo -e "${YELLOW}Run ./scripts/build-npm.sh to build all platforms.${NC}"
  echo -e "${YELLOW}Or ./scripts/build-npm.sh local to build current platform only.${NC}"
  echo ""
  read -p "Continue with available platforms? [y/N] " CONTINUE
  if [ "$CONTINUE" != "y" ] && [ "$CONTINUE" != "Y" ]; then
    exit 1
  fi
fi

# 3. Version consistency
echo ""
echo "Version check..."
for PKG_DIR in temper temper-darwin-arm64 temper-darwin-x64 temper-linux-x64; do
  PKG_JSON="$NPM_DIR/$PKG_DIR/package.json"
  if [ -f "$PKG_JSON" ]; then
    PKG_VER=$(node -e "console.log(require('$PKG_JSON').version)")
    MATCH=""
    if [ "$PKG_VER" = "$VERSION" ]; then
      MATCH="${GREEN}✓${NC}"
    else
      MATCH="${RED}✗ (expected $VERSION)${NC}"
    fi
    echo -e "  $PKG_DIR: $PKG_VER $MATCH"
  fi
done

# 4. Check if versions already published
echo ""
echo "Registry check..."
for PKG_DIR in temper temper-darwin-arm64 temper-darwin-x64 temper-linux-x64; do
  PKG_JSON="$NPM_DIR/$PKG_DIR/package.json"
  if [ -f "$PKG_JSON" ]; then
    PKG_NAME=$(node -e "console.log(require('$PKG_JSON').name)")
    PUBLISHED=$(npm view "$PKG_NAME@$VERSION" version 2>/dev/null || echo "")
    if [ -n "$PUBLISHED" ]; then
      echo -e "  $PKG_NAME@$VERSION: ${YELLOW}already published${NC}"
    else
      echo -e "  $PKG_NAME@$VERSION: ${GREEN}not yet published${NC}"
    fi
  fi
done

# ─── Publish ───

echo ""
echo "═══════════════════════════════════"

publish_package() {
  local PKG_DIR=$1
  local PKG_JSON="$NPM_DIR/$PKG_DIR/package.json"
  local PKG_NAME=$(node -e "console.log(require('$PKG_JSON').name)")

  # Skip if no binary (platform package)
  if [[ "$PKG_DIR" == temper-darwin-* ]] || [[ "$PKG_DIR" == temper-linux-* ]]; then
    if [ ! -f "$NPM_DIR/$PKG_DIR/bin/temper" ]; then
      echo -e "  ${YELLOW}Skipping $PKG_NAME (no binary)${NC}"
      return
    fi
  fi

  # Check if already published
  local PUBLISHED=$(npm view "$PKG_NAME@$VERSION" version 2>/dev/null || echo "")
  if [ -n "$PUBLISHED" ]; then
    echo -e "  ${YELLOW}$PKG_NAME@$VERSION already published, skipping${NC}"
    return
  fi

  echo -e "  Publishing ${GREEN}$PKG_NAME@$VERSION${NC}..."

  if $DRY_RUN; then
    echo "    (dry-run) cd $NPM_DIR/$PKG_DIR && npm publish --access public --dry-run"
    (cd "$NPM_DIR/$PKG_DIR" && npm publish --access public --dry-run 2>&1 | sed 's/^/    /')
  else
    (cd "$NPM_DIR/$PKG_DIR" && npm publish --access public 2>&1 | sed 's/^/    /')
  fi
  echo ""
}

# Publish platform packages first
echo "Step 1: Publishing platform packages..."
echo ""
for PKG in temper-darwin-arm64 temper-darwin-x64 temper-linux-x64; do
  publish_package "$PKG"
done

# Then main package
echo "Step 2: Publishing main package..."
echo ""
publish_package "temper"

# ─── Done ───

echo "═══════════════════════════════════"
echo ""
if $DRY_RUN; then
  echo -e "${YELLOW}Dry run complete. To actually publish:${NC}"
  echo -e "${YELLOW}  ./scripts/publish-npm.sh --publish${NC}"
else
  echo -e "${GREEN}Published temper@$VERSION${NC}"
  echo ""
  echo "Users can install with:"
  echo "  npm install -g temper"
  echo ""
  echo "Configure with Claude Code:"
  echo "  claude mcp add temper -- temper serve ."
  echo ""
  echo "Configure with Forge (.forge/mcp.json):"
  echo '  {"mcpServers":{"temper":{"command":"temper","args":["serve","."]}}}'
fi
