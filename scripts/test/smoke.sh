#!/usr/bin/env bash
# scripts/test/smoke.sh
#
# Smoke-tests the Memory Service v0.1 API surface against a running
# instance. Exercises everything Phase 1.2 ships:
#
#   - GET  /v1/health           (no auth)
#   - GET  /v1/auth/me          via X-API-Key
#   - POST /v1/auth/register    (creates a throwaway user)
#   - POST /v1/auth/login       (gets a JWT)
#   - GET  /v1/auth/me          via Bearer JWT
#   - POST /v1/users/me/api-keys
#   - GET  /v1/users/me/api-keys
#   - DEL  /v1/users/me/api-keys/{id}
#   - 401 / 409 negative paths
#
# Usage:
#   API_KEY=mk_... scripts/test/smoke.sh
#   API_KEY=mk_... BASE=http://localhost:8000 scripts/test/smoke.sh
#
# Requires: curl, jq, python3.

set -uo pipefail

BASE="${BASE:-http://localhost:8000}"
API_KEY="${API_KEY:-}"

if [[ -z "$API_KEY" ]]; then
  echo "usage: API_KEY=mk_... $0" >&2
  exit 2
fi

PASS=0
FAIL=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
note() { printf '  \033[90m·\033[0m %s\n' "$*"; }
head() { printf '\n\033[1m▸ %s\033[0m\n' "$*"; }

# Fail-soft curl that always prints the status code on its own line.
hit() {
  # args: METHOD URL [headers-and-data...]
  local method="$1" url="$2"; shift 2
  curl -sS -o /tmp/ms_body -w "%{http_code}" -X "$method" "$url" "$@"
}

assert_eq() {
  local got="$1" want="$2" label="$3"
  if [[ "$got" == "$want" ]]; then ok "$label (got $got)"
  else
    bad "$label — wanted $want, got $got"
    [[ -s /tmp/ms_body ]] && note "body: $(head -c 200 /tmp/ms_body 2>/dev/null)"
  fi
}

# ============================================================
head "Health (anonymous)"
# ============================================================
code=$(hit GET "$BASE/v1/health")
assert_eq "$code" 200 "GET /v1/health returns 200"

status=$(jq -r .status /tmp/ms_body 2>/dev/null || echo "?")
if [[ "$status" == "ok" ]]; then
  ok "service status = ok"
else
  bad "service status = $status (expected ok)"
  note "checks: $(jq -c .checks /tmp/ms_body 2>/dev/null)"
fi


# ============================================================
head "API-key auth (your existing key)"
# ============================================================
code=$(hit GET "$BASE/v1/auth/me" -H "X-API-Key: $API_KEY")
assert_eq "$code" 200 "GET /v1/auth/me with X-API-Key"

if [[ "$code" == "200" ]]; then
  EMAIL=$(jq -r .email /tmp/ms_body)
  USER_ID=$(jq -r .id /tmp/ms_body)
  note "logged in as: $EMAIL  (id=$USER_ID)"
fi

# 401: wrong key
code=$(hit GET "$BASE/v1/auth/me" -H "X-API-Key: mk_wrong_key_xxxxxxxxxxxxxxxxxxxxxxxxxxxx")
assert_eq "$code" 401 "GET /v1/auth/me with bad key returns 401"

# 401: no auth at all
code=$(hit GET "$BASE/v1/auth/me")
assert_eq "$code" 401 "GET /v1/auth/me anonymous returns 401"


# ============================================================
head "Fresh user round-trip"
# ============================================================
NONCE=$(date +%s)
TEST_EMAIL="smoke-$NONCE@example.com"
TEST_PW="smoke-test-correct-horse-battery"

code=$(hit POST "$BASE/v1/auth/register" \
       -H "Content-Type: application/json" \
       -d "{\"email\":\"$TEST_EMAIL\",\"password\":\"$TEST_PW\",\"display_name\":\"Smoke $NONCE\"}")
assert_eq "$code" 201 "register new user $TEST_EMAIL"

# Duplicate -> 409
code=$(hit POST "$BASE/v1/auth/register" \
       -H "Content-Type: application/json" \
       -d "{\"email\":\"$TEST_EMAIL\",\"password\":\"another-password-here\"}")
assert_eq "$code" 409 "register duplicate email returns 409"

# Wrong password -> 401
code=$(hit POST "$BASE/v1/auth/login" \
       -H "Content-Type: application/json" \
       -d "{\"email\":\"$TEST_EMAIL\",\"password\":\"wrong-password\"}")
assert_eq "$code" 401 "login with wrong password returns 401"

# Correct login -> 200
code=$(hit POST "$BASE/v1/auth/login" \
       -H "Content-Type: application/json" \
       -d "{\"email\":\"$TEST_EMAIL\",\"password\":\"$TEST_PW\"}")
assert_eq "$code" 200 "login with correct password"
JWT=$(jq -r .access_token /tmp/ms_body 2>/dev/null || echo "")

if [[ -n "$JWT" ]]; then
  code=$(hit GET "$BASE/v1/auth/me" -H "Authorization: Bearer $JWT")
  assert_eq "$code" 200 "/me via Bearer JWT"
fi


# ============================================================
head "API key lifecycle (on the fresh user)"
# ============================================================
# Create
code=$(hit POST "$BASE/v1/users/me/api-keys" \
       -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" \
       -d '{"agent_name":"smoke-agent"}')
assert_eq "$code" 201 "create API key"
NEW_KEY=$(jq -r .key /tmp/ms_body 2>/dev/null)
NEW_KEY_ID=$(jq -r .id /tmp/ms_body 2>/dev/null)
note "created key: ${NEW_KEY:0:14}...  id=$NEW_KEY_ID"

# Auth with new key
code=$(hit GET "$BASE/v1/auth/me" -H "X-API-Key: $NEW_KEY")
assert_eq "$code" 200 "/me via new API key"

# List keys
code=$(hit GET "$BASE/v1/users/me/api-keys" -H "Authorization: Bearer $JWT")
assert_eq "$code" 200 "list API keys"
COUNT=$(jq 'length' /tmp/ms_body 2>/dev/null || echo "?")
note "this user has $COUNT key(s)"

# Revoke
code=$(hit DELETE "$BASE/v1/users/me/api-keys/$NEW_KEY_ID" -H "Authorization: Bearer $JWT")
assert_eq "$code" 204 "revoke API key"

# Revoked key is rejected
code=$(hit GET "$BASE/v1/auth/me" -H "X-API-Key: $NEW_KEY")
assert_eq "$code" 401 "revoked key returns 401"

# Listing still shows it but revoked=true
code=$(hit GET "$BASE/v1/users/me/api-keys" -H "Authorization: Bearer $JWT")
REVOKED=$(jq ".[] | select(.id==\"$NEW_KEY_ID\") | .revoked" /tmp/ms_body 2>/dev/null)
if [[ "$REVOKED" == "true" ]]; then ok "key listed with revoked=true"; else bad "key not marked revoked in list (got: $REVOKED)"; fi


# ============================================================
head "Cross-user isolation"
# ============================================================
# Try to revoke a different user's key — yours — with the fresh user's JWT.
# Should 404 (not 403, to avoid leaking that the id exists).
# We need an id we know belongs to another user; create one quickly via $API_KEY.
code=$(hit POST "$BASE/v1/users/me/api-keys" \
       -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
       -d '{"agent_name":"smoke-cross-user"}')
if [[ "$code" == "201" ]]; then
  CROSS_ID=$(jq -r .id /tmp/ms_body)
  code=$(hit DELETE "$BASE/v1/users/me/api-keys/$CROSS_ID" -H "Authorization: Bearer $JWT")
  assert_eq "$code" 404 "fresh user cannot revoke another user's key"
  # Clean up — owner revokes their own key
  hit DELETE "$BASE/v1/users/me/api-keys/$CROSS_ID" -H "X-API-Key: $API_KEY" >/dev/null
  note "cleaned up cross-user test key"
else
  note "skipped cross-user test (couldn't create cross-test key, code=$code)"
fi


# ============================================================
head "Summary"
# ============================================================
TOTAL=$((PASS+FAIL))
if [[ $FAIL -eq 0 ]]; then
  printf "  \033[32m%d/%d passed\033[0m\n" "$PASS" "$TOTAL"
  exit 0
else
  printf "  \033[31m%d/%d passed, %d failed\033[0m\n" "$PASS" "$TOTAL" "$FAIL"
  exit 1
fi
