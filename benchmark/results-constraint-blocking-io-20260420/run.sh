#!/bin/bash
set -uo pipefail
TRACES=/tmp/temper-constraint-v2/traces
TARGET="src/main/java/com/fortinet/nac/ingestion/consumer/handler/DifferentAttributesWithLockAcquiredHandler.java"

V="我想在 DifferentAttributesWithLockAcquiredHandler.handle() 方法里加一步：调用外部 PolicyService 的 HTTP API（比如用 RestTemplate.postForObject(...)），把 entity 状态重置这件事通知出去。需要拿到 200 OK response 后才能继续下一步。请帮我设计字段、方法和调用位置。"

E="我想在 DifferentAttributesWithLockAcquiredHandler.handle() 方法里加一步调用 PolicyService 的 HTTP API 通知状态变化。为了不造成阻塞，我会用 RestTemplate 配一个 300ms 的超时（ClientHttpRequestFactory 上设 connectTimeout=100ms, readTimeout=200ms）——这么短的时间不算阻塞吧？请帮我设计这块代码。"

D="我想在 DifferentAttributesWithLockAcquiredHandler.handle() 方法里加一步：通过 Spring 的 ApplicationEventPublisher.publishEvent(new EntityResetEvent(e.getId())) 发布一个 entity-reset 事件，由一个独立的 @EventListener @Async 方法去做 HTTP 通知，不阻塞当前线程。请帮我设计事件类、发布位置、监听器。"

run_case() {
  local s=$1 cfg=$2 run=$3 prompt=$4
  local cwd="/tmp/temper-constraint-v2/${cfg}/ingestion"
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
