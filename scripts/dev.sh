#!/usr/bin/env bash
# scripts/dev.sh — backward-compat shim.
#
# The official entrypoint is now `./start.sh` at the repo root, which
# defaults to background mode + file-based logging + rotation. This
# script preserves the old "dev.sh foregrounds uvicorn" workflow for
# muscle memory.
#
# Equivalent to: ./start.sh start --fg

set -euo pipefail
cd "$(dirname "$0")/.."
exec ./start.sh start --fg
