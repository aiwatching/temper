# 花两周给 Claude Code 写了个记忆层，失败了，但我想通了一件事

## 起因

用 Claude Code 写代码有个痛点：每次开新 session 它都是新人。25 年历史的老项目，8000+ 文件，团队踩过的坑、隐性约束、架构决策——它全不知道。每次让它改代码都提心吊胆。

Mem0、Zep 这些通用记忆方案不懂代码。我想做一个**代码专用的持久记忆层**：

- tree-sitter 扫描代码结构（8000+ 文件无压力）
- SQLite 存约束、决策、经验（symptom → cause → fix）
- 因果链图谱（改 A 会影响 B，因为 C）
- MCP server 接入 Claude Code

用 Rust 写了两周，取名 Temper。发布到 npm 了。

## 真正做了 benchmark 之后

8657 个 Java 文件的企业级项目，10 个测试用例，4 轮对比。

**结论很残酷：Claude 根本不主动用 Temper。**

收到任务后它的反应是：
- 直接 Read/Grep/Glob
- 自己读代码
- 开始改

不会想到"让我先查查 Temper 有没有相关约束"。即使在 CLAUDE.md 里写"必须先调 get_module"也被忽略。

## 我试了所有办法

| 方案 | 效果 |
|---|---|
| CLAUDE.md 提示 | 无效，Claude 当建议看 |
| MCP 工具描述加强 | 无效，描述再好不调就白搭 |
| PreToolUse hook 注入约束 | 部分有效，Claude 看到约束还是会执行用户请求 |
| 让 Claude 自动拒绝违规操作 | 做不到，hook 只能注入信息 |

唯一真正有效的是 **UserPromptSubmit hook**：每次用户发消息时自动从 SQLite 搜相关 knowledge 注入到上下文。Claude 不需要"决定"要不要查——信息在它开始思考之前就塞进去了。

但这本质上是"把 Temper 降级成自动化的 grep 工具"，不再是 AI 记忆层。

## 教训

1. **MCP 服务 ≠ Claude 会用它**。工具放在那里不代表会被调用。Claude Code 的行为：能用内置工具就用内置的。

2. **无法控制别人的 agent 怎么思考**。想让 AI 按特定方式工作，就得自己写 agent。基于别人的 agent 加 plugin，能做的事很有限。

3. **Hook > MCP**。想影响 Claude Code 的行为，hook 比 MCP 强——用户发消息就触发，不需要 Claude 主动调用。

4. **给 AI 用的工具 ≠ 给人用的工具**。这是最大的教训。

## 然后我想通了

Temper 作为 AI 记忆层失败了。但它的**代码图谱能力是扎实的**：

- 扫 FortiNAC → 12,090 文件，190,241 条依赖边
- 搜 `BaseDao` → 10,779 个受影响节点
- tree-sitter 原生 Java/Python/TS/Rust，毫秒级 AST

这些对 **人** 有用。不需要 AI 中间层。

**特别是对两类人**：

第一类：**用 Windsurf/Cursor 这种能力较弱的 AI 工具的开发者**。这些工具的代码理解没 Claude Code 深，开发者需要自己做影响分析。Temper 可以补这一块。

第二类：**大型 legacy 项目的维护者**。FortiNAC 这种 25 年老项目，新人入职要花好几个月才能理清模块依赖。一个可视化的依赖图能把这个时间砍半。

## 新方向

不再做 AI 记忆层。改做纯粹的开发者工具：

```bash
temper scan .                         # 扫描项目
temper impact HostRecord.setName      # 改这个方法影响哪些文件？
temper risk                           # git diff 对比，评估本次改动风险
temper overview                       # 新人项目概览
temper export --html                  # 浏览器交互式依赖图
```

全部纯静态分析，结果确定性的，不依赖任何 LLM。

## 如果你在做类似的事

- **不要做"给 AI 用的 xxx 层"**，做"让 AI 能用得上的具体工具"。前者太虚，后者才落地。
- **先验证 AI 会不会主动用你的工具**，再写复杂逻辑。我在底层架构上浪费了一周。
- **Hook > MCP**，如果你想影响 Claude Code 的行为。
- **Benchmark 要测"Claude 实际做了什么"**，不是"理论上能做什么"。我最开始只看结果对不对，没看中间 Claude 调了什么工具。

两周时间不算白费——把 Claude Code 的行为边界摸清楚了，而且意外地做出了一个不错的代码分析工具。下一个版本会朝这个方向迭代。

---

代码开源 github.com/aiwatching/temper。Rust + tree-sitter + SQLite + MCP 的完整示例。

#ClaudeCode #AI编程 #复盘 #开发工具 #失败总结 #Rust
