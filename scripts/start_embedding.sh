#!/usr/bin/env bash
# scripts/start_embedding.sh
#
# Idempotent local embedding-server bootstrap.
#
# Reads EMBEDDING_PROVIDER from .env and acts accordingly:
#
#   EMBEDDING_PROVIDER=ollama
#       1. Installs ollama (via brew) if missing.
#       2. Pulls EMBEDDING_MODEL (default: nomic-embed-text) if not present.
#       3. Starts `ollama serve` in the background if not already running.
#       4. Verifies the /v1/embeddings endpoint responds.
#
#   EMBEDDING_PROVIDER=openai  (with localhost in EMBEDDING_BASE_URL)
#       Treated as a self-hosted OpenAI-compatible endpoint.
#       Caller is responsible for starting it. Script just verifies reachability.
#
#   EMBEDDING_PROVIDER=openai  (remote)
#       External endpoint — nothing to start. Verifies reachability if curl-able.
#
# Safe to re-run. Won't reinstall, redownload, or restart anything that's
# already in the desired state.

set -euo pipefail

cd "$(dirname "$0")/.."

# ---- load .env -----------------------------------------------------------

if [[ ! -f .env ]]; then
  echo "✗ .env not found. cp .env.example .env first." >&2
  exit 1
fi

# Read just the keys we care about, ignoring comments / blanks.
get() { grep -E "^$1=" .env 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '"' | tr -d "'"; }

EMBEDDING_PROVIDER="$(get EMBEDDING_PROVIDER || true)"
EMBEDDING_MODEL="$(get EMBEDDING_MODEL || true)"
EMBEDDING_BASE_URL="$(get EMBEDDING_BASE_URL || true)"

# ---- helpers -------------------------------------------------------------

say()   { printf '  %s\n' "$*"; }
ok()    { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn()  { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

ollama_running() {
  curl -sf --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1
}

# ---- branches ------------------------------------------------------------

case "${EMBEDDING_PROVIDER:-ollama}" in

  ollama)
    MODEL="${EMBEDDING_MODEL:-nomic-embed-text}"
    echo "Embedding provider: ollama (model=$MODEL)"

    # 1. ensure ollama binary
    if ! command -v ollama >/dev/null 2>&1; then
      if command -v brew >/dev/null 2>&1; then
        say "ollama not found, installing via brew..."
        brew install ollama
      else
        fail "ollama not found and brew unavailable. Install manually: https://ollama.com/download"
        exit 1
      fi
    fi
    ok "ollama installed: $(ollama --version | head -n1)"

    # 2. ensure ollama serve is running
    if ollama_running; then
      ok "ollama serve already running on :11434"
      # Don't write the pid marker — we didn't start it, dev.sh shouldn't
      # stop it on exit either (might belong to another app).
    else
      say "starting ollama serve in background..."
      nohup ollama serve >/tmp/ollama.log 2>&1 &
      OLLAMA_PID=$!
      echo "$OLLAMA_PID" > /tmp/temper-ollama.pid
      # wait up to 5s for it to come up
      for i in 1 2 3 4 5; do
        sleep 1
        if ollama_running; then break; fi
      done
      if ollama_running; then
        ok "ollama serve started (pid=$OLLAMA_PID, logs: /tmp/ollama.log)"
      else
        fail "ollama serve did not become ready within 5s. Check /tmp/ollama.log"
        rm -f /tmp/temper-ollama.pid
        exit 1
      fi
    fi

    # 3. ensure model pulled
    if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$MODEL"; then
      ok "model $MODEL already pulled"
    else
      say "pulling model $MODEL (one-time download)..."
      ollama pull "$MODEL"
      ok "model $MODEL ready"
    fi

    # 4. functional smoke test
    say "smoke-testing /v1/embeddings..."
    DIM=$(curl -sf --max-time 30 http://localhost:11434/v1/embeddings \
            -H "Content-Type: application/json" \
            -d "{\"model\":\"$MODEL\",\"input\":\"hello\"}" \
          | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["data"][0]["embedding"]))' 2>/dev/null || echo 0)
    if [[ "$DIM" -gt 0 ]]; then
      ok "embedding endpoint working — vector dimension = $DIM"
      echo
      echo "Hint: set EMBEDDING_DIMENSIONS=$DIM in .env (if not already set)."
    else
      fail "embedding endpoint smoke test failed"
      exit 1
    fi
    ;;

  openai)
    URL="${EMBEDDING_BASE_URL:-https://api.openai.com/v1}"
    echo "Embedding provider: openai-compatible ($URL)"
    if [[ "$URL" == *localhost* || "$URL" == *127.0.0.1* ]]; then
      warn "local OpenAI-compatible endpoint configured — not started by this script"
      warn "(start your TEI / vLLM / custom server separately, then re-run to verify)"
    fi
    if curl -sf --max-time 5 "${URL%/}/models" -H "Authorization: Bearer ${EMBEDDING_API_KEY:-}" >/dev/null 2>&1; then
      ok "endpoint reachable"
    else
      warn "endpoint not reachable yet (this is normal if you haven't started it)"
    fi
    ;;

  *)
    fail "unknown EMBEDDING_PROVIDER='$EMBEDDING_PROVIDER'"
    exit 1
    ;;
esac

echo
ok "embedding backend ready"
