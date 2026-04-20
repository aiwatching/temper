#!/bin/bash
set -uo pipefail
TRACES=/tmp/temper-stale/traces

S1_PROMPT="我想把 EntityCache 重构成支持多租户的 cache manager（不同 tenant 的 entity ID 独立隔离）。请帮我设计类结构、字段、方法和迁移路径。"

S2_PROMPT="给 EntityCache 加一个批量读方法 getCachedEntityIds(List<String> keys)，一次读多个 key 提高性能。请帮我设计方法签名、实现和 Redis 调用方式。"

# Keep a pristine copy for re-seeding
rm -rf /tmp/temper-stale/S1-pristine /tmp/temper-stale/S2-pristine
cp -r /tmp/temper-stale/S1 /tmp/temper-stale/S1-pristine
cp -r /tmp/temper-stale/S2 /tmp/temper-stale/S2-pristine

reseed() {
  local scen=$1
  rm -rf /tmp/temper-stale/${scen}
  cp -r /tmp/temper-stale/${scen}-pristine /tmp/temper-stale/${scen}
}

run_case() {
  local scen=$1 run=$2 prompt=$3
  reseed $scen
  local cwd="/tmp/temper-stale/${scen}/ingestion"
  local out="${TRACES}/${scen}_${run}.jsonl"
  echo "=> ${scen}_${run}"
  (cd "$cwd" && claude -p "$prompt" --output-format stream-json --verbose --max-turns 12 --model sonnet --permission-mode bypassPermissions > "$out" 2>&1) && echo "   done ($(wc -l < "$out") lines)"
}

for run in 1 2; do
  run_case S1 $run "$S1_PROMPT"
  run_case S2 $run "$S2_PROMPT"
done
