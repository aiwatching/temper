#!/bin/bash
# Temper Benchmark Harness
# Runs the same tasks with and without Temper, records detailed metrics.
#
# Usage:
#   ./benchmark/run-benchmark.sh [--project /path/to/test-project] [--temper /path/to/temper]
#
# Prerequisites:
#   - claude CLI installed and authenticated
#   - temper binary built
#   - Test project created (run setup-test-project.sh first)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-/tmp/temper-benchmark}"
TEMPER_BIN="${TEMPER_BIN:-$(dirname "$SCRIPT_DIR")/target/release/temper}"
RESULTS_DIR="${RESULTS_DIR:-/tmp/temper-benchmark-results/$(date +%Y%m%d-%H%M%S)}"
TEST_CASES="$SCRIPT_DIR/test-cases.json"

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --project) PROJECT_DIR="$2"; shift 2 ;;
    --temper) TEMPER_BIN="$2"; shift 2 ;;
    --results) RESULTS_DIR="$2"; shift 2 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║      Temper Benchmark Harness         ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""
echo "Project:  $PROJECT_DIR"
echo "Temper:   $TEMPER_BIN"
echo "Results:  $RESULTS_DIR"
echo ""

# --- Validate prerequisites ---
if ! command -v claude &>/dev/null; then
  echo -e "${RED}Error: 'claude' CLI not found. Install Claude Code first.${NC}"
  exit 1
fi

if [ ! -f "$TEMPER_BIN" ]; then
  echo -e "${RED}Error: Temper binary not found at $TEMPER_BIN${NC}"
  echo "Run: cargo build --manifest-path $(dirname "$SCRIPT_DIR")/Cargo.toml"
  exit 1
fi

if [ ! -d "$PROJECT_DIR/src" ]; then
  echo -e "${YELLOW}Test project not found. Creating...${NC}"
  bash "$SCRIPT_DIR/setup-test-project.sh" "$PROJECT_DIR"
fi

if [ ! -f "$TEST_CASES" ]; then
  echo -e "${RED}Error: test-cases.json not found at $TEST_CASES${NC}"
  exit 1
fi

mkdir -p "$RESULTS_DIR/without-temper" "$RESULTS_DIR/with-temper"

# --- Helper: Extract test case fields ---
get_field() {
  python3 -c "
import json, sys
cases = json.load(open('$TEST_CASES'))
case = [c for c in cases if c['id'] == '$1'][0]
print(case.get('$2', ''))
"
}

get_test_ids() {
  python3 -c "
import json
cases = json.load(open('$TEST_CASES'))
for c in cases:
    print(c['id'])
"
}

# --- Phase 1: Run WITHOUT Temper ---
echo -e "\n${YELLOW}══ Phase 1: Running WITHOUT Temper ══${NC}\n"

# Make sure NO MCP servers are configured
rm -rf "$PROJECT_DIR/.temper"
rm -rf "$PROJECT_DIR/.claude"
mkdir -p "$PROJECT_DIR/.claude"
echo '{"mcpServers":{}}' > "$PROJECT_DIR/.claude/settings.json"

TEST_IDS=$(get_test_ids)
for TEST_ID in $TEST_IDS; do
  PROMPT=$(get_field "$TEST_ID" "prompt")
  CATEGORY=$(get_field "$TEST_ID" "category")
  echo -e "${BLUE}[$TEST_ID] $CATEGORY${NC}"
  echo "  Prompt: ${PROMPT:0:80}..."

  OUTPUT_FILE="$RESULTS_DIR/without-temper/$TEST_ID.json"
  START_TIME=$(python3 -c "import time; print(int(time.time()*1000))")

  # Run Claude Code non-interactively with --print
  # Capture output as JSON with metadata
  set +e
  RESPONSE=$(cd "$PROJECT_DIR" && claude -p "$PROMPT" --output-format json --max-turns 5 2>/dev/null)
  EXIT_CODE=$?
  set -e

  END_TIME=$(python3 -c "import time; print(int(time.time()*1000))")
  DURATION=$((END_TIME - START_TIME))

  # Save raw response
  echo "$RESPONSE" > "$RESULTS_DIR/without-temper/$TEST_ID.raw"

  # Extract text and metadata from claude JSON response
  python3 -c "
import json, sys

raw = '''$(echo "$RESPONSE" | sed "s/'''/\\\\'\\\\'\\\\'/" )'''
try:
    data = json.loads(raw)
    text = data.get('result', raw)
    meta = {
        'test_id': '$TEST_ID',
        'mode': 'without-temper',
        'duration_ms': data.get('duration_ms', $DURATION),
        'duration_api_ms': data.get('duration_api_ms', 0),
        'num_turns': data.get('num_turns', 0),
        'total_cost_usd': data.get('total_cost_usd', 0),
        'input_tokens': data.get('usage', {}).get('input_tokens', 0),
        'output_tokens': data.get('usage', {}).get('output_tokens', 0),
        'cache_read_tokens': data.get('usage', {}).get('cache_read_input_tokens', 0),
        'exit_code': $EXIT_CODE,
    }
except:
    text = raw
    meta = {'test_id': '$TEST_ID', 'mode': 'without-temper', 'duration_ms': $DURATION, 'exit_code': $EXIT_CODE}

with open('$OUTPUT_FILE', 'w') as f:
    json.dump(meta, f, indent=2)
with open('$RESULTS_DIR/without-temper/$TEST_ID.txt', 'w') as f:
    f.write(text)
" 2>/dev/null

  echo -e "  Duration: ${DURATION}ms, Response: $(wc -c < "$RESULTS_DIR/without-temper/$TEST_ID.txt" 2>/dev/null | tr -d ' ') bytes"
done

# --- Phase 2: Initialize Temper + seed knowledge ---
echo -e "\n${YELLOW}══ Phase 2: Initializing Temper ══${NC}\n"

cd "$PROJECT_DIR"
rm -rf .temper

# Init + auto-accept suggested modules
echo "y" | "$TEMPER_BIN" init . 2>&1

# Scan interfaces
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"scan_module_interfaces","arguments":{"module":"auth"}}}
{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"scan_module_interfaces","arguments":{"module":"user"}}}
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"scan_module_interfaces","arguments":{"module":"notification"}}}
{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"scan_module_interfaces","arguments":{"module":"ha"}}}
{"jsonrpc":"2.0","id":10,"method":"tools/call","params":{"name":"remember","arguments":{"title":"UserDAO must not bypass Service layer","content":"All user operations MUST go through UserService. Direct DAO access from controllers bypasses validation and audit logging. This was enforced after a data integrity incident.","type":"constraint","module":"user","file":"src/main/java/com/acme/user/UserService.java","tags":["dao","architecture","validation"]}}}
{"jsonrpc":"2.0","id":11,"method":"tools/call","params":{"name":"remember","arguments":{"title":"No caching in DAO layer","content":"Do NOT add caching to DAO classes. Hibernate L2 cache caused stale reads in multi-node deployment (2023-Q2 incident). Cache should be at Service layer only.","type":"constraint","module":"user","file":"src/main/java/com/acme/user/UserDAO.java","tags":["caching","dao","multi-node"]}}}
{"jsonrpc":"2.0","id":12,"method":"tools/call","params":{"name":"remember","arguments":{"title":"Token generation must use SecureRandom","content":"Token generation MUST use SecureRandom, not Math.random() or simple concatenation. Vulnerability CVE-2023-XXXX was caused by predictable tokens using System.currentTimeMillis().","type":"constraint","module":"auth","file":"src/main/java/com/acme/auth/TokenManager.java","tags":["security","token","cve"]}}}
{"jsonrpc":"2.0","id":13,"method":"tools/call","params":{"name":"remember","arguments":{"title":"Session timeout is 30 minutes","content":"Session timeout was reduced from 60 to 30 minutes after 2024 security audit. Do NOT change without updating HA failover script.","type":"decision","module":"auth","tags":["session","security","ha"]}}}
{"jsonrpc":"2.0","id":14,"method":"tools/call","params":{"name":"remember","arguments":{"title":"All emails must use TemplateEngine","content":"Raw HTML emails are blocked by security policy (2024-Q1). All emails must go through TemplateEngine.render() for sanitization.","type":"constraint","module":"notification","tags":["email","security","template"]}}}
{"jsonrpc":"2.0","id":15,"method":"tools/call","params":{"name":"remember","arguments":{"title":"DB pool size tied to HA node count","content":"Database pool size must equal max_connections / ha_node_count. Exceeding this causes connection exhaustion during failover. Current: 50 = 100/2.","type":"constraint","module":"config","tags":["database","ha","connection-pool"]}}}
{"jsonrpc":"2.0","id":16,"method":"tools/call","params":{"name":"remember","arguments":{"title":"Session data must be serializable","content":"All objects stored in SessionStore must be serializable for HA replication. Non-serializable objects caused data loss during failover (2023-Q3).","type":"constraint","module":"auth","file":"src/main/java/com/acme/auth/SessionStore.java","tags":["session","ha","serialization"]}}}
{"jsonrpc":"2.0","id":20,"method":"tools/call","params":{"name":"record_experience","arguments":{"symptom":"Users get logged out randomly after HA failover","cause":"Session cache not pre-warmed before switchover. HAManager.failover() was switching primary before copying sessions.","fix":"Added preWarmSessions() call before switchPrimary() in HAManager.failover(). Order: sync → pre-warm → switch.","module":"ha","constraint_note":"Must update HA failover script whenever session storage mechanism changes"}}}
{"jsonrpc":"2.0","id":21,"method":"tools/call","params":{"name":"record_experience","arguments":{"symptom":"Stale user data shown after updates in multi-node deployment","cause":"Hibernate L2 cache in UserDAO was returning cached data. Nodes had different cache states.","fix":"Removed L2 cache from DAO. Moved caching to Service layer with explicit invalidation.","module":"user","constraint_note":"Never add caching at DAO layer in multi-node setup"}}}
{"jsonrpc":"2.0","id":30,"method":"tools/call","params":{"name":"add_causal_relation","arguments":{"from_entity":"HA failover","to_entity":"session cache invalidation","relation_type":"triggers","description":"HA failover causes all session caches to be cleared"}}}
{"jsonrpc":"2.0","id":31,"method":"tools/call","params":{"name":"add_causal_relation","arguments":{"from_entity":"session cache invalidation","to_entity":"user logout","relation_type":"causes","description":"Empty session cache forces all users to re-authenticate"}}}
{"jsonrpc":"2.0","id":32,"method":"tools/call","params":{"name":"add_causal_relation","arguments":{"from_entity":"DB pool size change","to_entity":"connection exhaustion during failover","relation_type":"triggers","description":"Pool size exceeding max_connections/node_count causes connection starvation"}}}' | "$TEMPER_BIN" serve "$PROJECT_DIR" >/dev/null 2>&1

echo "Temper initialized with:"
"$TEMPER_BIN" status
echo ""
"$TEMPER_BIN" modules
echo ""
"$TEMPER_BIN" knowledge

# --- Configure Temper as MCP server for Claude Code ---
# Create a project-local .claude/settings.json
mkdir -p "$PROJECT_DIR/.claude"
cat > "$PROJECT_DIR/.claude/settings.json" << SETTINGS
{
  "mcpServers": {
    "temper": {
      "command": "$TEMPER_BIN",
      "args": ["serve", "$PROJECT_DIR"]
    }
  }
}
SETTINGS

echo -e "\n${GREEN}Temper configured as MCP server for Claude Code${NC}"

# --- Phase 3: Run WITH Temper ---
echo -e "\n${YELLOW}══ Phase 3: Running WITH Temper ══${NC}\n"

for TEST_ID in $TEST_IDS; do
  PROMPT=$(get_field "$TEST_ID" "prompt")
  CATEGORY=$(get_field "$TEST_ID" "category")
  echo -e "${BLUE}[$TEST_ID] $CATEGORY${NC}"
  echo "  Prompt: ${PROMPT:0:80}..."

  OUTPUT_FILE="$RESULTS_DIR/with-temper/$TEST_ID.json"
  START_TIME=$(python3 -c "import time; print(int(time.time()*1000))")

  set +e
  RESPONSE=$(cd "$PROJECT_DIR" && claude -p "$PROMPT" --output-format json --max-turns 8 2>/dev/null)
  EXIT_CODE=$?
  set -e

  END_TIME=$(python3 -c "import time; print(int(time.time()*1000))")
  DURATION=$((END_TIME - START_TIME))

  echo "$RESPONSE" > "$RESULTS_DIR/with-temper/$TEST_ID.raw"

  # Extract text and metadata from claude JSON response
  python3 -c "
import json, sys

raw = '''$(echo "$RESPONSE" | sed "s/'''/\\\\'\\\\'\\\\'/" )'''
try:
    data = json.loads(raw)
    text = data.get('result', raw)
    meta = {
        'test_id': '$TEST_ID',
        'mode': 'with-temper',
        'duration_ms': data.get('duration_ms', $DURATION),
        'duration_api_ms': data.get('duration_api_ms', 0),
        'num_turns': data.get('num_turns', 0),
        'total_cost_usd': data.get('total_cost_usd', 0),
        'input_tokens': data.get('usage', {}).get('input_tokens', 0),
        'output_tokens': data.get('usage', {}).get('output_tokens', 0),
        'cache_read_tokens': data.get('usage', {}).get('cache_read_input_tokens', 0),
        'exit_code': $EXIT_CODE,
    }
except:
    text = raw
    meta = {'test_id': '$TEST_ID', 'mode': 'with-temper', 'duration_ms': $DURATION, 'exit_code': $EXIT_CODE}

with open('$OUTPUT_FILE', 'w') as f:
    json.dump(meta, f, indent=2)
with open('$RESULTS_DIR/with-temper/$TEST_ID.txt', 'w') as f:
    f.write(text)
" 2>/dev/null

  echo -e "  Duration: ${DURATION}ms, Response: $(wc -c < "$RESULTS_DIR/with-temper/$TEST_ID.txt" 2>/dev/null | tr -d ' ') bytes"
done

# --- Phase 4: Grade results ---
echo -e "\n${YELLOW}══ Phase 4: Grading Results ══${NC}\n"

python3 "$SCRIPT_DIR/grade-results.py" \
  --test-cases "$TEST_CASES" \
  --without-temper "$RESULTS_DIR/without-temper" \
  --with-temper "$RESULTS_DIR/with-temper" \
  --output "$RESULTS_DIR/report.md"

echo -e "\n${GREEN}Benchmark complete!${NC}"
echo "Results:  $RESULTS_DIR"
echo "Report:   $RESULTS_DIR/report.md"
echo ""
cat "$RESULTS_DIR/report.md"
