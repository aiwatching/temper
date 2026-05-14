#!/usr/bin/env bash
# Walk through every feature added this session.
#
# Assumes:
#   - scripts/dev.sh started cleanly (service on :18088, FalkorDB on :6380,
#     ollama embedding on :11434)
#   - MS_API_KEY is exported in the current shell, OR the script will
#     create a throwaway one against the running service
#
# Run from repo root:
#   ./scripts/test/walkthrough.sh
#
# Pass --keep to skip cleanup at the end.

set -uo pipefail
cd "$(dirname "$0")/../.."

BASE="http://localhost:18088"
NS="user:me"   # all writes/reads use the caller's own namespace by default

say()  { printf "\n\033[1;36m▸ %s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
note() { printf "  \033[33m·\033[0m %s\n" "$*"; }

if [[ -z "${MS_API_KEY:-}" ]]; then
  note "MS_API_KEY not set — visit ${BASE}/admin/login to get one."
  exit 1
fi
export MS_API_KEY

run_cmd() { printf "    \033[2m$ %s\033[0m\n" "$*"; "$@"; }
jq_id() { python3 -c "import json,sys; print(json.load(sys.stdin).get('episode_id', ''))"; }

# ---------- 0. health -----------------------------------------------------
say "0. Health: postgres + falkordb + graphiti all green"
curl -s "$BASE/v1/health" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'  status: {d[\"status\"]}')
for k, v in (d.get('checks') or {}).items():
  print(f'  {k}: ok={v[\"ok\"]}')
"

# ---------- 1. memctl basic ops -------------------------------------------
say "1. memctl basics — whoami / write / search / ls / show / rm"
run_cmd memctl whoami
EID=$(memctl --json write "Walkthrough: Sarah teaches me English on Tuesdays." | jq_id)
ok "wrote episode $EID"
run_cmd memctl search "teacher" --limit 3
run_cmd memctl ls --limit 3
run_cmd memctl show $EID
run_cmd memctl rm $EID

# ---------- 2. search dimensions ------------------------------------------
say "2. Search dimensions (filters + bias + walk + rerank)"
# seed some data
SETUP=$(memctl --json write "Anna lives in Lisbon and teaches Portuguese.")
ANNA_EID=$(echo "$SETUP" | jq_id)
SETUP2=$(memctl --json write "Bruno is Anna's student; he lives in Lyon.")
BRUNO_EID=$(echo "$SETUP2" | jq_id)
sleep 1
note "edge_types — only LIVES_IN"
memctl search "city" --edge-types LIVES_IN --limit 5
note "as_of — time-travel (5 days ago, before our writes)"
PAST=$(python3 -c "import datetime; print((datetime.datetime.now(datetime.UTC)-datetime.timedelta(days=5)).isoformat())")
memctl search "Anna" --as-of "$PAST" --limit 5
note "center — bias toward Anna"
ANNA_UUID=$(memctl --json graph cypher 'MATCH (n:Entity) WHERE n.name="Anna" RETURN n.uuid AS uuid LIMIT 1' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['uuid'] if d else '')")
[[ -n "$ANNA_UUID" ]] && memctl search "lives" --center "$ANNA_UUID" --limit 3 || note "(skipped: Anna entity not extracted)"
note "reranker — cross_encoder (one LLM call per query, slower)"
time memctl search "teacher" --reranker cross_encoder --limit 3 >/dev/null
note "BFS — pull neighborhood of Anna"
[[ -n "$ANNA_UUID" ]] && memctl search "weather" --bfs-origins "$ANNA_UUID" --bfs-max-depth 2 --limit 5

# ---------- 3. node/edge lookup + invalidation ----------------------------
say "3. Entity / fact lookups + explicit invalidation"
if [[ -n "$ANNA_UUID" ]]; then
  run_cmd memctl entity "$ANNA_UUID"
fi
FACT_UUID=$(memctl --json graph cypher 'MATCH ()-[r:RELATES_TO]->() WHERE r.fact CONTAINS "Anna" RETURN r.uuid AS uuid LIMIT 1' \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d[0]['uuid'] if d else '')")
if [[ -n "$FACT_UUID" ]]; then
  run_cmd memctl fact show "$FACT_UUID"
  note "invalidate then reactivate"
  memctl fact invalidate "$FACT_UUID" | head -3
  memctl fact invalidate "$FACT_UUID" --reactivate | head -3
fi

# ---------- 4. bulk + saga ------------------------------------------------
say "4. Bulk write + saga"
cat > /tmp/walk-bulk.txt <<'EOF'
Standup: Mira shipped the OAuth migration.
Standup: Reza is blocked on the staging environment.
Standup: Pat finished the search recall review.
EOF
memctl write-bulk -f /tmp/walk-bulk.txt --saga walkthrough-standup --tags walk-demo
sleep 1
run_cmd memctl saga ls
run_cmd memctl saga show walkthrough-standup

# ---------- 5. custom schemas + node_labels filter -----------------------
say "5. Custom entity schemas"
memctl schema create Project priority:integer deadline:datetime --description "An engineering project"
memctl write "We launched Project Eclipse with priority 1 and deadline 2026-09-30." | head -4
sleep 2
note "graph now should show Project label"
memctl graph cypher 'MATCH (n:Entity) WHERE n.name CONTAINS "Eclipse" RETURN n.name AS name, labels(n) AS labels'
note "node_labels filter works"
memctl search "project" --node-labels Project --limit 3

# ---------- 6. async extraction -------------------------------------------
say "6. Async write (returns immediately, polls status)"
ASYNC_EID=$(memctl --json write --async "Async demo: Eve joined the platform team." | jq_id)
ok "async returned: $ASYNC_EID"
note "status @ 0s:"
memctl status "$ASYNC_EID"
sleep 6
note "status @ 6s:"
memctl status "$ASYNC_EID"

# ---------- 7. communities + reindex --------------------------------------
say "7. Build communities + re-embed everything"
memctl admin communities build
note "after build, communities live in the graph:"
memctl graph cypher 'MATCH (n:Community) RETURN n.name AS name LIMIT 5'
note "reindex embeddings (sanity-check post-model-swap path)"
time memctl admin embeddings reindex --include-communities

# ---------- 8. server-side cypher ----------------------------------------
say "8. Server-side cypher (ACL'd, read-only)"
run_cmd memctl cypher 'MATCH (n:Entity) RETURN n.name AS name LIMIT 5'

# ---------- 9. /admin/graph (just a sanity GET) ---------------------------
say "9. /admin/graph viewer page loads"
code=$(curl -s -o /dev/null -w "%{http_code}" "$BASE/admin/graph")
[[ "$code" = "200" ]] && ok "open in browser: $BASE/admin/graph"

# ---------- cleanup --------------------------------------------------------
if [[ "${1:-}" != "--keep" ]]; then
  say "Cleanup"
  memctl schema rm Project 2>/dev/null || true
  for id in $(memctl --json ls --limit 100 \
    | python3 -c "
import json, sys
d = json.load(sys.stdin)
for e in d['episodes']:
    c = (e.get('source_description') or '') + ' ' + ' '.join(e.get('tags') or [])
    if any(t in c for t in ['walk-demo']):
        print(e['episode_id'])
"); do
    memctl rm "$id" >/dev/null
  done
  memctl rm "$ANNA_EID" >/dev/null 2>&1 || true
  memctl rm "$BRUNO_EID" >/dev/null 2>&1 || true
  memctl rm "$ASYNC_EID" >/dev/null 2>&1 || true
  ok "removed demo episodes (pass --keep to skip)"
fi

say "Done."
