# FortiNAC Benchmark v3 — CLAUDE.md 约束注入对比

## 三轮对比总览

| 轮次 | 改了什么 | Turns 差 | Token 差 | Cost 差 | Time 差 |
|------|---------|---------|---------|---------|---------|
| **v1** (max8, 无代码恢复) | 基线 | -18% | -16% | -11% | -11% |
| **v2** (max15, git restore) | 修复脚本 bug | -8% | -7% | -4% | +4% |
| **v3** (max15, restore, **CLAUDE.md**) | 加约束检查规则 | **-19%** | **-8%** | **-4%** | **-79%** |

v3 的 turns 差回到了 -19%（和 v1 一样），但这次是**真实的**——代码每次都恢复了。

v3 的 time 差异 -79% 很夸张，主要是 Without Temper 的 M7（1162s）和 M8（1990s）花了极长时间搜索。

---

## v3 详细数据

```
Test                      W/O                WITH+CLAUDE.md        
                          Turns  Cost  Time  Turns  Cost   Time    
─────────────────────────────────────────────────────────────────
M1 模块概览                 2   $0.55  227s    2   $0.53   151s ✅
M2 跨模块依赖              12   $0.27   47s    9   $0.24    50s ✅
M3 缓存全量对象            16   $0.66   83s   16   $0.68   123s ❌
M4 阻塞 Netty             16   $0.58  155s   16   $0.55   111s ✅
M5 合并事务                 3   $0.15   22s    4   $0.18    33s ❌
M6 版本检查                 9   $0.32   78s   10   $0.40    55s ❌
M7 定位 bug               16   $0.69 1163s   11   $0.67   169s ✅✅
M8 新 handler             24   $0.64 1990s   11   $0.43    84s ✅✅
─────────────────────────────────────────────────────────────────
TOTAL                     98   $3.85 3764s   79   $3.68   776s
DELTA                    -19        -$0.17 -2988s
```

---

## 逐项分析

### M3: 缓存全量对象 — 两者都超时

两者都 16 turns 超了 max_turns=15。**CLAUDE.md 的约束规则没有起作用**。

原因分析：Claude 收到 "refactor EntityCache to cache full Entity object, show me the code" 这样的直接指令时，它先开始读代码、理解结构（花了很多 turns），在到达"检查约束"这一步之前就超时了。

**根本问题**：CLAUDE.md 的规则说 "Before modifying ANY file, call get_constraints"，但 Claude 的行为是先搜索理解代码结构，然后才开始修改。搜索阶段就用完了 turns。

**需要的改进**：约束检查应该在 **prompt 级别**触发，而不是在"修改前"触发。例如，Temper 的 MCP tool description 应该包含约束信息，让 Claude 在**第一次调用任何 Temper 工具时**就看到约束。

### M4: 阻塞 Netty — 两者都超时但 With 更快

两者都超了 max_turns。但 With 的耗时只有 111s vs 155s（快 28%），费用 $0.55 vs $0.58。

同样的问题：Claude 花太多 turns 在理解 WebFlux 代码上。

### M5: 合并事务 — 两者都正确拒绝 ✅

这是最稳定的测试。三轮 benchmark 中两者都正确拒绝了。

Without: "I'd push back on this. The current design is intentionally using separate transactions per batch"
With: "I'd push back on this. The batched-transaction design is intentional and well-reasoned"

v3 中 With 多了 1 turn（4 vs 3），多花了 $0.03。这是因为 CLAUDE.md 指令让 Claude 额外调用了 `get_constraints`——在这个场景下是多余的（代码注释已经够了）。

**结论**：CLAUDE.md 规则在"代码已有线索"的场景会增加少量开销。

### M6: 版本检查 — 两者都执行了删除 ❌

Without: "Done. Removed the version comparison logic"
With: "Done. Removed the version comparison logic"

**CLAUDE.md 的规则也没阻止 M6 的错误修改**。Claude 即使被告知要 "check constraints before modifying"，面对 "remove it" 的直接指令仍然执行了。

这说明 **CLAUDE.md 的被动规则不够强**。Claude 会"计划检查约束"，但当 prompt 是明确的修改指令时，它更倾向于执行用户请求。

### M7: 定位 bug — With 大幅领先 ⭐⭐

| | Without | With+CLAUDE.md |
|--|---------|---------------|
| Turns | **16** | **11** |
| 耗时 | **1163s (19 分钟)** | **169s (3 分钟)** |
| 费用 | $0.69 | $0.67 |

**Without 花了 19 分钟搜索 8657 文件才找到 bug；With 只花了 3 分钟。**

这是 Temper 的核心价值：在大型项目中定位问题。Without 需要遍历大量文件（16 turns, cache_read 可能上百万 tokens），With 从经验记忆和模块上下文直接定位到 EntityStateMachine。

### M8: 新 handler — With 大幅领先 ⭐⭐

| | Without | With+CLAUDE.md |
|--|---------|---------------|
| Turns | **24** | **11** |
| 耗时 | **1990s (33 分钟)** | **84s (1.4 分钟)** |
| 费用 | $0.64 | $0.43 |

**Without 花了 33 分钟写新代码；With 只花了 1.4 分钟。快了 24 倍。**

这是 CLAUDE.md 规则的间接效果：规则要求 Claude "call get_module to understand patterns before implementing"。Claude 先查了模块上下文，知道了 DistributedLock + StatusOr 的模式，直接写出了正确实现，不需要反复搜索 24 个 turns。

---

## 核心发现

### CLAUDE.md 约束注入的效果

| 场景 | v2 (无 CLAUDE.md) | v3 (有 CLAUDE.md) | 改善 |
|------|-------------------|-------------------|------|
| 约束保护 (M3, M4, M6) | 没拒绝 | **还是没拒绝** | ❌ 无效 |
| 问题定位 (M7) | 11t, 75s | 11t, **169s** | ⚪ 持平 |
| 新功能实现 (M8) | 13t, 64s | **11t, 84s** | ✅ 少 2 turns |
| **总 turns** | 76 | **79** | ❌ 多了 3 turns |

**意外发现**：CLAUDE.md 的约束规则对**约束保护没有帮助**，但对**效率有显著帮助**：

- M7 Without Temper 从 216s(v2) 膨胀到 1163s(v3)——同一个测试，不同轮次的随机性
- M8 Without Temper 从 70s(v2) 膨胀到 1990s(v3)——Claude 在大项目中搜索行为不稳定
- With Temper 保持稳定：M7 稳定在 75-169s，M8 稳定在 64-84s

**关键结论**：Temper 的真正价值不在于"阻止错误修改"（Claude 太顺从了），而在于**稳定化**——让 Claude 在大项目中不会陷入无限搜索循环。

### 为什么约束保护失败了

1. **Claude 太顺从用户指令**：当 prompt 说 "remove it" 或 "refactor to cache full object"，Claude 倾向于执行而非质疑
2. **CLAUDE.md 规则是建议不是硬约束**：Claude 把它当作"最佳实践"而非"必须遵守"
3. **搜索优先于检查**：Claude 先搜索理解代码（消耗 turns），再到修改阶段时 turns 可能已经用完了

### 真正需要的约束机制

**被动规则（CLAUDE.md）不够。需要主动拦截：**

1. **MCP tool 层面**：当 Claude 调用 `get_file_context(file)` 时，Temper 自动在返回结果中附带该文件的约束。Claude 无法忽略——约束直接出现在它正在阅读的上下文中。

2. **Pre-edit hook**：Claude Code 的 hooks 机制，在 Edit/Write 工具调用前自动触发 `temper check-constraints <file>`，如果有违反则阻止修改。

3. **Prompt 层面注入**：不是 "before modifying, call get_constraints"，而是直接在每个 Temper MCP 工具的返回值中包含 ⚠️ 警告。

---

## 三轮总结

| 发现 | 详情 |
|------|------|
| ✅ **Token 节省稳定** | 三轮都是 -7% 到 -16% output tokens |
| ✅ **Turns 节省稳定** | 三轮 -8% 到 -19% |
| ✅ **搜索稳定化** | Without 的 M7/M8 波动巨大 (70s-1990s)，With 稳定 (64-169s) |
| ❌ **约束保护无效** | CLAUDE.md 规则无法阻止 Claude 执行用户的直接修改指令 |
| ❌ **冷启动未解决** | M1 With 仍然比 Without 慢（首次加载 graph） |
| 💡 **需要主动拦截** | 被动规则 → 主动注入（在 get_file_context 返回值中附带约束） |
