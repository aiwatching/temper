#!/usr/bin/env bash
# update.sh — pull + reinstall + restart.
#
# What it does:
#   1. git pull (--ff-only — refuses if local has divergent commits)
#   2. ./install.sh (idempotent — picks up dep / migration changes)
#   3. ./start.sh restart   (only if service was running)
#
# Re-running is safe. The whole flow is idempotent.
#
# Flags:
#   --no-restart    don't bounce the service (just pull + install)
#   --no-pull       skip git pull (just install + restart)
#   -h, --help

set -euo pipefail
cd "$(dirname "$0")"

if [[ -t 1 ]]; then
  C_BLUE=$'\e[34m' C_GREEN=$'\e[32m' C_RST=$'\e[0m'
else
  C_BLUE='' C_GREEN='' C_RST=''
fi
step() { printf '\n%s━━ %s ━━%s\n' "$C_BLUE" "$*" "$C_RST"; }
ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RST" "$*"; }

DO_PULL=1
DO_RESTART=1
for a in "$@"; do
  case "$a" in
    --no-pull)    DO_PULL=0 ;;
    --no-restart) DO_RESTART=0 ;;
    -h|--help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "unknown arg: $a" >&2; exit 1 ;;
  esac
done

if (( DO_PULL )); then
  step "Pulling latest"
  git pull --ff-only
  ok "git up to date"
fi

step "Re-running install.sh"
./install.sh

if (( DO_RESTART )); then
  # Only bounce the service if it was actually running. Otherwise
  # just print a hint — don't auto-start in case the operator was
  # in the middle of debugging.
  if ./start.sh status >/dev/null 2>&1; then
    step "Restarting TEMPER"
    ./start.sh restart
  else
    step "Service not running"
    ok "skipped restart — start manually with: ./start.sh"
  fi
fi
