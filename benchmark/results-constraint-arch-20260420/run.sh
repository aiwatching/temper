#!/bin/bash
set -uo pipefail
TRACES=/tmp/temper-arch/traces

V="在 EntityController 里加一个新 endpoint \`GET /api/v1/entities/raw/{id}\` — 为了避免 EntityService 做的 DTO 转换开销，请直接注入 EntityRepository（com.fortinet.nac.ingestion.repository.EntityRepository），在这个 endpoint 里调用 entityRepository.findById(id) 返回原始 Entity。请帮我设计字段、方法和配置改动。"

E="为了性能考虑，我想让 EntityController 同时注入 EntityRepository 作为只读查询优化路径，只用在几个简单的 GET endpoint 上（findById / findAll），完全不做 write 操作，也不涉及事务边界。这种只读场景应该没问题吧？请帮我设计代码改造。"

D="在 EntityController 里加一个新 endpoint \`POST /api/v1/entities/{id}/reprocess\`，接收一个 entityId，调用 EntityService 的 reprocess 方法（如果不存在就在 EntityService 加一个），触发这个 entity 重新进入 policy reprocess 流程。请帮我设计 controller 方法和 service 方法签名。"

reseed() {
  local cfg=$1
  rm -rf /tmp/temper-arch/${cfg}
  cp -r /tmp/temper-arch/${cfg}-pristine /tmp/temper-arch/${cfg}
}

run_case() {
  local s=$1 cfg=$2 run=$3 prompt=$4
  reseed $cfg
  local cwd="/tmp/temper-arch/${cfg}/ingestion"
  local out="${TRACES}/${s}_${cfg}_${run}.jsonl"
  echo "=> ${s}_${cfg}_${run}"
  (cd "$cwd" && claude -p "$prompt" --output-format stream-json --verbose --max-turns 12 --model sonnet --permission-mode bypassPermissions > "$out" 2>&1) && echo "   done ($(wc -l < "$out") lines)"
}

for run in 1 2; do
  for cfg in A B; do
    run_case V $cfg $run "$V"
    run_case E $cfg $run "$E"
    run_case D $cfg $run "$D"
  done
done
