#!/usr/bin/env bash
# start.sh — bring Smith up with one command.
#
# Usage:
#   ./start.sh           production: build dist/ then `node dist/index.js`
#   ./start.sh -dev      dev:        `pnpm run dev` (tsx watch, auto-reload)
#   ./start.sh -h        help
#
# What it does:
#   1. Check Node + pnpm versions (Smith needs Node >= 20.6.0, pnpm >= 9).
#   2. Install dependencies if node_modules is missing OR pnpm-lock is
#      newer than the install marker.
#   3. Rebuild better-sqlite3 native binding if needed (pnpm 9+ skips
#      lifecycle scripts by default; the `onlyBuiltDependencies` entry
#      in package.json covers fresh installs but not after a Node-major
#      bump).
#   4. Dev mode → start tsx watch and follow logs.
#      Prod mode → tsc build, then node dist/index.js.
#
# Idempotent — safe to re-run. Long-running step (install) is skipped
# when nothing changed.

set -euo pipefail

# ---- locate self ---------------------------------------------------
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

# ---- ANSI ----------------------------------------------------------
if [[ -t 1 ]]; then
  C_DIM=$'\e[2m' C_BLUE=$'\e[34m' C_GREEN=$'\e[32m' C_YELLOW=$'\e[33m' C_RED=$'\e[31m' C_BOLD=$'\e[1m' C_RST=$'\e[0m'
else
  C_DIM='' C_BLUE='' C_GREEN='' C_YELLOW='' C_RED='' C_BOLD='' C_RST=''
fi
log()  { printf '%s[smith.start]%s %s\n' "${C_BLUE}" "${C_RST}" "$*"; }
warn() { printf '%s[smith.start]%s %s%s%s\n' "${C_YELLOW}" "${C_RST}" "${C_YELLOW}" "$*" "${C_RST}"; }
die()  { printf '%s[smith.start]%s %s%s%s\n' "${C_RED}"   "${C_RST}" "${C_RED}"   "$*" "${C_RST}" >&2; exit 1; }

# ---- args ----------------------------------------------------------
MODE="prod"
for arg in "$@"; do
  case "$arg" in
    -d|--dev|-dev)    MODE="dev" ;;
    -h|--help|-help)
      sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) die "unknown arg: $arg (use -h for help)" ;;
  esac
done

# ---- node version check (Smith requires >= 20.6) -------------------
if ! command -v node >/dev/null 2>&1; then
  die "node not found in PATH. Install Node 20.6+ (https://nodejs.org)."
fi
NODE_FULL="$(node --version)"             # e.g. v22.20.0
NODE_MAJOR="${NODE_FULL#v}"; NODE_MAJOR="${NODE_MAJOR%%.*}"
NODE_MINOR="${NODE_FULL#v[0-9]*.}"; NODE_MINOR="${NODE_MINOR%%.*}"
if (( NODE_MAJOR < 20 )) || (( NODE_MAJOR == 20 && NODE_MINOR < 6 )); then
  die "Smith needs Node >= 20.6.0, got ${NODE_FULL}."
fi

# ---- pnpm check ----------------------------------------------------
if ! command -v pnpm >/dev/null 2>&1; then
  warn "pnpm not found. Trying corepack..."
  if command -v corepack >/dev/null 2>&1; then
    corepack enable
    corepack prepare pnpm@latest --activate
  else
    die "pnpm not installed and corepack unavailable. Install pnpm: npm i -g pnpm"
  fi
fi
PNPM_FULL="$(pnpm --version)"             # e.g. 10.32.1
PNPM_MAJOR="${PNPM_FULL%%.*}"
if (( PNPM_MAJOR < 9 )); then
  warn "pnpm $PNPM_FULL detected. Smith expects pnpm >= 9 (uses onlyBuiltDependencies). Things may misbehave."
fi

# ---- install deps if needed ---------------------------------------
NEED_INSTALL=0
INSTALL_MARK=".data/.last-install-mtime"
if [[ ! -d node_modules ]]; then
  NEED_INSTALL=1
  log "node_modules missing — first install"
elif [[ ! -f "$INSTALL_MARK" ]] || [[ pnpm-lock.yaml -nt "$INSTALL_MARK" ]] || [[ package.json -nt "$INSTALL_MARK" ]]; then
  NEED_INSTALL=1
  log "pnpm-lock.yaml / package.json newer than last install — refreshing"
fi
if (( NEED_INSTALL )); then
  pnpm install
  mkdir -p .data
  touch "$INSTALL_MARK"
fi

# ---- native binding sanity (better-sqlite3 must be rebuilt on Node-major bump)
SQLITE_BINDING="node_modules/.pnpm/better-sqlite3@*/node_modules/better-sqlite3/build/Release/better_sqlite3.node"
# shellcheck disable=SC2086 disable=SC2206
matches=( $SQLITE_BINDING )
if [[ ! -f "${matches[0]}" ]]; then
  warn "better-sqlite3 native binding missing — rebuilding"
  pnpm rebuild better-sqlite3 || die "better-sqlite3 rebuild failed"
fi

# ---- run -----------------------------------------------------------
mkdir -p .data
if [[ "$MODE" == "dev" ]]; then
  log "${C_GREEN}${C_BOLD}dev mode${C_RST} — tsx watch, auto-reload on src/ changes"
  log "open ${C_BOLD}http://127.0.0.1:18099${C_RST} (or /setup if first run)"
  exec pnpm run dev
else
  log "${C_GREEN}${C_BOLD}prod mode${C_RST} — tsc build then node dist/index.js"
  pnpm run build
  log "open ${C_BOLD}http://127.0.0.1:18099${C_RST}"
  exec node dist/index.js
fi
