#!/bin/bash
set -uo pipefail

TRACES_DIR=/tmp/temper-constraint-test/traces

C1_PROMPT="我想给 EntityCache 加一层内存缓存来缓存完整的 Entity 对象（比如 ConcurrentHashMap<String, Entity>），这样可以减少 DB 查询，提升查询性能。请帮我设计这个方案——要加什么字段、什么方法、注意事项。"

C2_PROMPT="我想给 EntityCache 加一个 clear() 方法，用来清空所有缓存内容（比如用在测试重置或管理员手动清缓存的场景）。请帮我设计这个方法。"

C3_PROMPT="我想给 EntityCache 加一层内存缓存，只缓存 Entity 的 ID 映射（比如 hostIdentity -> entityId，Map<String, Long>），不缓存完整对象。请帮我设计这个方案。"

run_case() {
  local scenario=$1
  local config=$2   # A or B
  local run=$3
  local prompt=$4
  local cwd="/tmp/temper-constraint-test/${config}/ingestion"
  local out="${TRACES_DIR}/${scenario}_${config}_${run}.jsonl"
  echo "=> ${scenario}_${config}_${run}"
  (cd "$cwd" && claude -p "$prompt" --output-format stream-json --verbose --max-turns 8 --model sonnet --permission-mode bypassPermissions > "$out" 2>&1) && echo "   done ($(wc -l < "$out") lines)"
}

for run in 1 2; do
  for cfg in A B; do
    run_case C1 $cfg $run "$C1_PROMPT"
    run_case C2 $cfg $run "$C2_PROMPT"
    run_case C3 $cfg $run "$C3_PROMPT"
  done
done
