# Temper — Implementation Plan

## 目标

让 AI 在大型项目（FortiNAC 2000+ files）中不丢失逻辑，通过模块化记忆提供精准的上下文。

## 架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                      Temper Memory Layer                         │
│                                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ Structural       │  │ Causal           │  │ Experience       │  │
│  │ Memory           │  │ Memory           │  │ Memory           │  │
│  │                  │  │                  │  │                  │  │
│  │ tree-sitter AST  │  │ trigger→cause→   │  │ symptom→cause→   │  │
│  │ graph.json       │  │ effect→constraint│  │ fix              │  │
│  │ 变化频率: 高     │  │ knowledge.db     │  │ knowledge.db     │  │
│  │                  │  │ 变化频率: 中     │  │ 变化频率: 低     │  │
│  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘  │
│           │                     │                      │            │
│  ┌────────▼─────────────────────▼──────────────────────▼────────┐  │
│  │                    Module Registry                           │  │
│  │           modules/*.yaml + _index.yaml                       │  │
│  └──────────────────────────┬───────────────────────────────────┘  │
│                             │                                      │
│  ┌──────────────────────────▼───────────────────────────────────┐  │
│  │               Semantic Search (Embedding)                    │  │
│  │    External API (user-configured) + sqlite-vec               │  │
│  │    Only embeds knowledge entries, NOT all code               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  Storage: <project>/.temper/ (per-project isolation)               │
│  Config:  ~/.temper/config.yaml (global, API keys)                 │
│  API:     MCP tools over stdio (temper serve)                      │
│  CLI:     temper init / serve / scan / modules / search            │
└────────────────────────────────────────────────────────────────────┘
```

## 核心概念

### 与原 Forge Memory Layer 设计的变更

| 原设计 | 新设计 | 原因 |
|--------|--------|------|
| 文件有 "未分配" 状态 | 所有文件属于项目，模块是标签 | 更精准 |
| 文件只属于一个模块 | 文件可属于多个模块 | Legacy 代码中常见 |
| 路径前缀匹配 | glob 模式匹配（ripgrep 风格） | 更灵活 |
| dimensions 手动维护 | 自动推断 + 用户可覆盖 | 减少初始成本 |
| init 只写配置 | init = 注册 + 扫描 + 自动建议模块 | 开箱即用 |
| knowledge.json 扁平存储 | SQLite 因果图 + temporal 历史 | 支持因果链查询和版本追踪 |
| 无语义搜索 | 外部 embedding API + sqlite-vec | 补 tree-sitter 粗粒度 |
| 存储 `.forge/memory/` | 存储 `.temper/` | 独立产品 |
| Graphiti + Neo4j | SQLite (嵌入式) | CLI 工具不能依赖外部数据库 |
| 单项目 | 多项目隔离 (per-project `.temper/`) | 实际使用场景 |

### 三层记忆 vs 竞品

| 竞品 | 索引的内容 | Temper 索引的内容 |
|------|-----------|------------------|
| Augment / Cursor | 代码结构（文件、函数、依赖图） | 设计意图、约束条件、失败历史、因果链 |
| Sourcegraph Cody | 代码语义搜索（"哪里处理认证"） | "为什么这里不能用线程池" |
| Claude Code | 不索引，实时 grep 搜索 | 持久的模块专家知识，跨 session 有效 |
| code-review-graph | 本地结构图, call graph | 因果逻辑、历史决策 |

核心差异：Claude Code 放弃 RAG 是因为**代码文件**的向量会过期。但 Temper 存的不是代码文件，而是**"为什么这样写"**——这不会因为代码变动而过期。

## 存储架构

### 多项目隔离

```
~/.temper/                          # 全局配置（用户级，所有项目共享）
├── config.yaml                     # Embedding API endpoint, API keys, 默认设置
└── projects.json                   # 项目注册表 (path → project_id)

<project-A>/.temper/                # 项目 A 的数据（完全隔离）
├── modules/
│   ├── _index.yaml
│   └── <module>.yaml
├── interfaces/
│   └── <module>.json
├── knowledge.db                    # SQLite: 因果链 + 经验 + temporal + embeddings
├── graph.json                      # AST code graph（内存快照）
└── meta.json                       # 扫描元数据

<project-B>/.temper/                # 项目 B 的数据（完全隔离，互不影响）
├── ...
```

每个项目的数据完全独立。全局 `~/.temper/` 只存：
- **config.yaml**: embedding API 配置、默认设置
- **projects.json**: 哪些项目已初始化，路径映射

### 全局配置 (~/.temper/config.yaml)

```yaml
# ~/.temper/config.yaml
embedding:
  provider: openai           # openai / voyage / custom
  endpoint: https://api.openai.com/v1/embeddings
  model: text-embedding-3-small
  api_key_env: OPENAI_API_KEY  # 从环境变量读取，不直接存 key
  dimensions: 1536

defaults:
  languages: [java]           # 默认扫描语言
  scan_exclude:               # 默认排除目录
    - node_modules
    - target
    - build
    - .git
```

### 项目注册表 (~/.temper/projects.json)

```json
{
  "projects": [
    {
      "id": "fortinac",
      "path": "/home/user/projects/FortiNAC",
      "initialized_at": 1712448000,
      "last_scan_at": 1712534400
    },
    {
      "id": "temper",
      "path": "/home/user/projects/temper",
      "initialized_at": 1712448000,
      "last_scan_at": 1712448000
    }
  ]
}
```

### SQLite Schema (knowledge.db)

```sql
-- 知识条目（当前状态）
CREATE TABLE knowledge (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,                     -- decision/bug/constraint/experience/causal
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  module TEXT,                            -- 锚定到模块
  file TEXT,                              -- 锚定到文件
  function TEXT,                          -- 锚定到函数
  tags TEXT,                              -- JSON array
  status TEXT NOT NULL DEFAULT 'active',  -- active/stale/validated/expired
  current_version INTEGER NOT NULL DEFAULT 1,
  git_commit TEXT,                        -- 创建时的 commit
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

-- 因果关系（知识/代码实体之间的有向边）
CREATE TABLE causal_relations (
  id TEXT PRIMARY KEY,
  from_entity TEXT NOT NULL,              -- knowledge ID 或 code entity (file::function)
  to_entity TEXT NOT NULL,
  relation_type TEXT NOT NULL,            -- triggers/causes/affects/constrains/depends_on
  description TEXT,
  confidence TEXT DEFAULT 'suspected',    -- validated/suspected/stale
  git_commit TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

-- 经验记录（结构化 symptom→cause→fix）
CREATE TABLE experiences (
  id TEXT PRIMARY KEY,
  module TEXT,
  symptom TEXT NOT NULL,
  cause TEXT NOT NULL,
  fix TEXT NOT NULL,
  constraint_note TEXT,                   -- "改这里必须同时..."
  tags TEXT,                              -- JSON array
  status TEXT NOT NULL DEFAULT 'active',
  git_commit TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

-- 完整 Temporal 历史（每次状态变更都记录）
CREATE TABLE history (
  entity_id TEXT NOT NULL,                -- knowledge ID 或 experience ID
  entity_type TEXT NOT NULL,              -- 'knowledge' / 'experience' / 'causal_relation'
  version INTEGER NOT NULL,
  status TEXT NOT NULL,                   -- 变更后的状态
  content TEXT NOT NULL,                  -- 该版本的完整快照 (JSON)
  git_commit TEXT,                        -- 变更时的 commit
  changed_by TEXT,                        -- user / smith / git-hook / rescan
  reason TEXT,                            -- 为什么变更
  timestamp INTEGER NOT NULL,
  PRIMARY KEY (entity_id, version)
);

-- Embeddings（可选，语义搜索）
CREATE TABLE embeddings (
  entity_id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,              -- 'knowledge' / 'experience'
  embedding BLOB,                         -- 向量数据 (sqlite-vec)
  model TEXT,                             -- 哪个模型生成的
  created_at INTEGER NOT NULL
);

-- 索引
CREATE INDEX idx_knowledge_module ON knowledge(module);
CREATE INDEX idx_knowledge_file ON knowledge(file);
CREATE INDEX idx_knowledge_status ON knowledge(status);
CREATE INDEX idx_causal_from ON causal_relations(from_entity);
CREATE INDEX idx_causal_to ON causal_relations(to_entity);
CREATE INDEX idx_experiences_module ON experiences(module);
CREATE INDEX idx_history_entity ON history(entity_id);
```

### 存储空间估算

| 数据 | 2000 文件项目估算 | 说明 |
|------|------------------|------|
| graph.json | 10-50 MB | AST 节点 + 边，全量快照 |
| knowledge (entries) | < 1 MB | 几百到几千条知识 |
| history (temporal) | 1-5 MB | 每条知识平均 ~10 次版本变更 |
| embeddings | 2-10 MB | 取决于模型维度和条目数 |
| modules/*.yaml | < 100 KB | 几十个模块定义 |
| **总计** | **15-70 MB** | 绝大部分是 graph.json |

knowledge.db 本身很小（几 MB），temporal 历史不会成为性能瓶颈。

## 一、Module Registry

### 1.1 模块索引 (_index.yaml)

```yaml
# .temper/modules/_index.yaml
version: 1
project: FortiNAC
updated_at: 2026-04-07

# 多维度分类 — 自动推断，用户可覆盖
# verified: true 的条目 rescan 时保持不变
dimensions:
  by-service:
    - name: web-server
      children: [user, host, system, vlan, network, portal]
      verified: false
    - name: masterloader
      children: [bootstrap, schema, migration]
      verified: true     # 用户确认过，rescan 不覆盖

  by-function:
    - name: authentication
      modules: [web-server/user, agent-server/radius, web-server/portal]
    - name: network-enforcement
      modules: [web-server/vlan, agent-server/snmp, web-server/network]

  by-layer:
    - name: api-layer
      modules: [web-server/user, web-server/host, web-server/system]
    - name: service-layer
      modules: [masterloader, agent-server]
    - name: data-layer
      modules: [database]
```

### 1.2 模块定义 (per-module YAML)

```yaml
# .temper/modules/web-server--user.yaml
name: web-server/user
description: "用户管理模块 — CRUD、认证、权限、角色"
updated_at: 2026-04-07

paths:
  - "src/main/java/com/fortinet/nac/server/user/**/*.java"
  - "src/main/java/com/fortinet/nac/model/User.java"
  - "src/main/java/com/fortinet/nac/model/UserRole.java"
  - "src/main/resources/mapper/UserMapper.xml"

exclude:
  - "src/main/java/com/fortinet/nac/server/user/legacy/**"

entry_points:
  - UserController.java
  - UserService.java

tags: [user, auth, rbac, login, session]
```

### 1.3 模块定义要素

| 字段 | 必须 | 说明 |
|------|------|------|
| name | 是 | 模块名，建议 `service/component` 格式 |
| description | 是 | 模块职责描述 |
| paths | 推荐 | glob 模式。如提供 package name 自动转 glob |
| tags | 推荐 | 用于搜索和 dimensions 自动推断 |
| exclude | 可选 | 排除 paths 中的部分文件 |
| entry_points | 可选 | 模块入口文件 |

## 二、Interface Map

### 2.1 自动生成逻辑

```
扫描模块内所有 Java 文件（tree-sitter AST）：
1. public class → 记录类名
2. public method → 记录方法签名
3. @RestController/@GetMapping/@PostMapping → 记录 REST API
4. @Autowired/@Inject 的其他模块类 → 记录依赖

输出: interfaces/<module>.json
```

### 2.2 接口文件格式

```json
{
  "module": "web-server/user",
  "generated_at": "2026-04-07",
  "verified": false,

  "exposes": {
    "rest": [
      { "method": "GET", "path": "/api/user", "handler": "UserController.getUser", "line": 45 },
      { "method": "POST", "path": "/api/user", "handler": "UserController.createUser", "line": 62 }
    ],
    "java": [
      { "class": "UserService", "method": "getUser(Long id)", "visibility": "public", "line": 30 },
      { "class": "UserService", "method": "createUser(UserDTO dto)", "visibility": "public", "line": 55 }
    ]
  },

  "depends_on": [
    { "module": "web-server/system", "class": "AuthService", "method": "authenticate", "usage": "鉴权" },
    { "module": "database", "class": "UserDAO", "method": "findById", "usage": "数据访问" }
  ],

  "depended_by": [
    { "module": "web-server/host", "class": "HostController", "method": "getHostUsers", "usage": "查询关联用户" }
  ]
}
```

## 三、MCP 工具设计

### 3.1 模块管理

```
define_module(name, description, paths?, tags?, exclude?, entry_points?)
  → 创建/更新模块 YAML
  → 触发接口扫描
  → 增量推断 dimensions（不覆盖已验证的）

remove_module(name)
  → 删除模块定义
  → 更新 _index.yaml

list_modules(dimension?)
  → 列出所有模块（可按维度过滤）
```

### 3.2 模块查询

```
get_module(name)
  → 完整上下文：文件 + 接口 + 依赖 + 知识 + 入口点

get_module_interfaces(name)
  → 只返回接口（轻量）

get_module_deps(name)
  → 双向依赖关系
```

### 3.3 知识管理（因果 + 经验）

```
remember(title, content, type, module?, file?, function?, tags?)
  → 写入 knowledge 表
  → 如果有因果关系，同时写入 causal_relations
  → 记录 temporal history
  → 异步生成 embedding（如果配置了 API）

recall(query?, module?, type?, include_stale?)
  → 关键词搜索 + 模块过滤
  → 如果配置了 embedding，同时做语义搜索，合并结果

forget(id)
  → 标记为 expired（不物理删除）
  → 记录 temporal history

find_causal_chain(change_description)
  → 从 causal_relations 图做 BFS
  → 返回影响路径：A → triggers → B → affects → C

search_symptom(symptom)
  → 搜索 experiences 表
  → 关键词匹配 + 语义搜索（如果有 embedding）
  → 返回匹配的 symptom→cause→fix

get_constraints(module)
  → 查询该模块的所有 constraint 类型知识 + causal_relations 中的 constrains 边
```

### 3.4 代码搜索

```
search_code(query)
  → AST 图搜索（tree-sitter，camelCase 匹配 + BFS 影响链）

search_knowledge(query)
  → 语义搜索（embedding 相似度）
  → 补充 tree-sitter 搜不到的概念级匹配

get_file_context(file)
  → 文件的 imports/exports + 关联知识 + 因果链
```

### 3.5 自动化

```
rescan_code(force?)
  → 增量更新 graph.json
  → 检测变更文件，标记相关知识为 stale
  → 记录 temporal history

scan_module_interfaces(name)
  → tree-sitter 重新扫描接口

refresh_modules()
  → 扫描项目结构，建议新模块（不自动创建）
  → 已有模块和 verified dimensions 保持不变

validate_modules()
  → glob 路径是否匹配到文件
  → 接口是否过期
```

## 四、`temper init` 工作流

```
Step 1: 全局配置检查
  → 检查 ~/.temper/config.yaml 是否存在
  → 如果没有，引导用户配置 embedding API（可跳过）
  → 在 ~/.temper/projects.json 注册当前项目

Step 2: 注册 MCP server
  → 写入 Claude Code 的 settings.json
  → 配置 temper serve <project-path> 为 MCP server (stdio)

Step 3: 全量扫描
  → tree-sitter 解析所有源文件
  → 生成 .temper/graph.json + meta.json

Step 4: 自动建议模块
  → 分析 package 层级结构
  → 找到最佳切分层级（hierarchy 分叉最多的层级）
  → 输出建议列表

Step 5: 用户确认
  → 确认的写入 modules/*.yaml
  → 自动推断 dimensions 写入 _index.yaml

Step 6: 初始化 knowledge.db
  → 创建 SQLite 数据库 + 表结构
  → 如果配置了 embedding API，验证连通性
```

## 五、Temporal 历史机制

### 5.1 状态流转

```
active → stale → validated → active    (文件变了，验证后确认仍然有效)
active → stale → expired               (文件变了，确认已过期)
active → expired                        (用户手动 forget)
```

### 5.2 每次状态变更记录

```json
// history 表的一行
{
  "entity_id": "k-1712448000-abc",
  "entity_type": "knowledge",
  "version": 3,
  "status": "validated",
  "content": "{...完整快照...}",
  "git_commit": "a1b2c3d",
  "changed_by": "user",
  "reason": "confirmed still valid after HA module refactor",
  "timestamp": 1712534400
}
```

### 5.3 触发场景

| 触发 | 动作 | changed_by |
|------|------|-----------|
| git commit 改了模块文件 | 相关知识 → stale | git-hook |
| rescan_code 检测到变更 | 相关知识 → stale | rescan |
| 用户/Smith 确认知识仍有效 | stale → validated | user / smith |
| 用户 forget | → expired | user |
| Smith 发现知识与代码不符 | 更新 content，version++ | smith |

## 六、借鉴的论文和竞品技术

### 来自 Codified Context 论文
- **三层加载策略**：始终加载(Tier 1) / 按需路由(Tier 2) / MCP 检索(Tier 3)
- **知识嵌入 > 行为指令**：agent spec 中 >50% 是领域知识，不是指令
- **Context drift detector**：git hook 检测代码变更，标记过期文档 → Temper 的 stale 检测
- **Trigger Table**：自动路由到正确的专家 → Temper 的模块关联

### 来自 SemanticForge
- **增量 KG 维护 O(|ΔR|·log n)**：不需要全量重建，只更新变化部分
- **Lazy resolution**：跨文件引用延迟到查询时计算 → Temper 的 causal_relations 可以做类似优化

### 来自 Graphiti
- **Temporal tracking**：事实有时间有效期 → Temper 的 history 表
- **结构化三元组**：不是 key-value 而是 entity-relation-entity → Temper 的 causal_relations

### 来自 Mem0
- **双存储架构**：向量 + 图谱并行 → Temper 的 SQLite 同时存关系图和 embedding

### 来自 iText2KG
- **语义去重**：新实体与已有图谱做匹配 → Temper 的 remember 去重逻辑

### 设计取舍
| 原方案 | Temper 选择 | 原因 |
|--------|------------|------|
| Graphiti + Neo4j | SQLite 嵌入式 | CLI 工具不能依赖外部数据库 |
| 本地 embedding 模型 | 外部 API | 用户选择自己的模型，binary 体积小 |
| Branch Delta 中央服务器 | Phase 1 本地，预留中央模式接口 | 先跑通本地，schema 兼容未来迁移 |
| 完整 KG 查询语言 (Cypher) | SQLite JOIN + 应用层 BFS | 够用，无额外依赖 |

## 七、实现步骤

### Phase 1: 基础框架 + Code Graph
```
1. Rust 项目初始化（Cargo.toml, clap CLI）
2. tree-sitter Java 解析（函数/类/import/export）
3. Code Graph 数据结构 + JSON 存储
4. 增量更新逻辑（git2 检测变更）
5. search_code 查询（camelCase 搜索 + BFS 影响链）
6. MCP server (JSON-RPC stdio): search_code, get_file_context, rescan_code
7. 多项目支持: ~/.temper/config.yaml + projects.json
```

### Phase 2: Module Registry
```
1. YAML 读写（modules/*.yaml, _index.yaml）
2. glob 路径匹配
3. MCP 工具: define_module, list_modules, get_module, remove_module
4. dimensions 自动推断算法
5. temper init 自动建议模块流程
```

### Phase 3: Knowledge Store (SQLite)
```
1. SQLite schema 创建 (knowledge, causal_relations, experiences, history)
2. MCP 工具: remember, recall, forget
3. 因果链: find_causal_chain, get_constraints
4. 经验记录: search_symptom
5. Temporal 历史: 每次变更自动记录
6. git-aware stale 检测 + 状态流转
```

### Phase 4: Semantic Search
```
1. 外部 embedding API 集成（可配置 provider）
2. sqlite-vec 集成
3. 知识条目入库时异步生成 embedding
4. search_knowledge 语义搜索
5. recall 合并关键词 + 语义结果
```

### Phase 5: Interface Map + 联动
```
1. tree-sitter Java 接口扫描（public methods + annotations）
2. 跨模块依赖推断
3. get_module 返回完整上下文
4. refresh_modules + validate_modules
5. npm 包打包（per-platform native binary）
6. temper init 写 Claude Code settings.json
```

## 八、关键决策

| 决策 | 选择 | 原因 |
|------|------|------|
| 语言 | Rust | 高性能，tree-sitter 原生支持 |
| 知识存储 | SQLite (rusqlite) | 嵌入式单文件，支持关系查询 + sqlite-vec |
| AST 图存储 | JSON (内存加载) | 全量 BFS，不需要 SQL 查询 |
| 模块定义 | YAML | 人工可编辑 |
| 向量搜索 | sqlite-vec | 零外部依赖，知识条目少暴力搜够用 |
| Embedding | 外部 API (用户配置) | binary 体积小，用户选择模型 |
| 因果图查询 | SQLite JOIN + 应用层 BFS | 够用，不需要 Cypher |
| Temporal | 完整版本历史 (history 表) | 追踪每次状态变更，空间开销小 |
| 多项目 | per-project .temper/ + global ~/.temper/ | 完全隔离 |
| 分发 | npm (Rust binary wrapper) | 用户生态 |
| 外部数据库依赖 | 无（本地模式）/ 可选（中央模式） | CLI 工具本地自包含，团队可选中央 server |
| 存储抽象层 | trait StorageBackend | 本地 SQLite 和远程 API 共用同一接口 |

## 九、中央模式（Central Memory Server）— 设计预留

### 9.1 动机

本地模式下每个开发者独立维护 `.temper/knowledge.db`，存在以下问题：
- **知识孤岛**：A 记录的经验 B 看不到
- **重复劳动**：每个人的 Smith 都要重新积累相同模块的知识
- **资源浪费**：大型项目的 graph.json 每个人都要本地计算和存储
- **一致性**：同一条知识在不同人的本地可能有不同版本

中央模式让团队**共享一个 knowledge server**，本地只做轻量缓存。

### 9.2 架构演进路径

```
Phase 1 (当前):  纯本地
  每个开发者: <project>/.temper/ (完整数据)
  无网络依赖

Phase 2 (未来):  本地 + 中央同步
  中央 Server:   维护 main branch 的完整知识库 (graph + knowledge + modules)
  本地开发者:    只维护分支 delta（本次分支新增/修改的知识）
  同步时机:      PR merge → push delta 到中央; pull/checkout → 拉取中央最新

Phase 3 (远期):  薄客户端
  中央 Server:   完整计算和存储
  本地开发者:    只做缓存，不持久化 knowledge.db
                 所有查询走 HTTP API → 中央 server
                 离线时用缓存兜底
```

### 9.3 当前需要预留的接口

为了让 Phase 1 的代码能平滑演进到 Phase 2/3，需要在 Rust 实现中预留：

#### Storage Trait（存储抽象层）

```rust
/// 所有知识存储操作通过这个 trait
/// Phase 1: 实现 LocalStorage (SQLite)
/// Phase 2: 实现 RemoteStorage (HTTP API → Central Server)
/// Phase 3: 实现 CachedRemoteStorage (HTTP + local cache)
trait KnowledgeStore {
    // 知识 CRUD
    fn remember(&self, entry: KnowledgeEntry) -> Result<String>;
    fn recall(&self, query: RecallQuery) -> Result<Vec<KnowledgeEntry>>;
    fn forget(&self, id: &str) -> Result<()>;

    // 因果链
    fn add_causal_relation(&self, relation: CausalRelation) -> Result<String>;
    fn find_causal_chain(&self, entity: &str) -> Result<Vec<CausalChain>>;

    // 经验
    fn record_experience(&self, exp: Experience) -> Result<String>;
    fn search_symptom(&self, symptom: &str) -> Result<Vec<Experience>>;

    // 语义搜索
    fn search_semantic(&self, query: &str) -> Result<Vec<SearchResult>>;

    // 同步（Phase 2+）
    fn get_changes_since(&self, version: u64) -> Result<Vec<ChangeRecord>>;
    fn apply_changes(&self, changes: Vec<ChangeRecord>) -> Result<()>;
}
```

#### 每条记录的同步元数据

当前 SQLite schema 已经包含了中央模式需要的字段，无需额外改动：

| 字段 | 本地模式用途 | 中央模式用途 |
|------|------------|------------|
| `id` (全局唯一) | 本地去重 | 中央 ↔ 本地 去重和合并 |
| `history.version` | Temporal 追踪 | 乐观锁 + 冲突检测 |
| `history.changed_by` | 区分 user/smith/hook | 区分**哪个用户**改的 |
| `history.git_commit` | Stale 检测 | Branch delta 关联 |
| `history.timestamp` | 时间排序 | 同步顺序 |

#### ID 生成策略

当前 `k-{timestamp}-{random}` 在单机够用，但多人场景可能冲突。预留兼容方案：

```
本地模式:  k-{timestamp}-{random_5}          (当前方案，简单)
中央模式:  k-{user_id}-{timestamp}-{random}  (加 user 前缀避免冲突)
```

Phase 1 用简单方案，Phase 2 迁移时做一次 ID 重映射即可。冲突概率极低，不需要提前复杂化。

### 9.4 中央 Server 技术预判

Phase 2 的中央 server 不需要现在实现，但预判技术选型：

| 组件 | 选择 | 原因 |
|------|------|------|
| Server 框架 | Rust (axum) 或 Python (FastAPI) | Rust 复用 Temper 核心逻辑；Python 快速迭代 |
| 知识存储 | PostgreSQL + pgvector | 团队级别，需要并发写入 + 向量搜索 |
| 图查询 | PostgreSQL recursive CTE 或 FalkorDB | 因果链 BFS |
| 同步协议 | 基于 version 的增量同步 | 类似 CouchDB replication |
| 认证 | API key per user | 简单，on-premise 友好 |

#### 同步协议草案

```
Pull (本地 ← 中央):
  GET /api/changes?since_version=42&modules=web-server/user,database
  → 返回 version 42 之后的所有变更记录
  → 本地 apply_changes()

Push (本地 → 中央):
  POST /api/changes
  body: [{ entity_id, version, content, git_commit, changed_by, ... }]
  → 中央做语义去重（iText2KG 方法）
  → 冲突时保留两个版本，标记 conflict，人工解决

Branch Delta:
  本地在 feature branch 上的知识变更，打上 branch tag
  PR merge 时触发: POST /api/merge?branch=feature-xyz
  中央合并 delta，去重，更新 main 的知识库
```

### 9.5 迁移策略

从 Phase 1 → Phase 2 的迁移对用户透明：

```
temper config set server.url https://temper.internal:8080
temper config set server.api_key xxx
temper sync push    # 首次：本地全量 push 到中央
temper sync pull    # 之后：拉取团队其他人的知识
```

之后 `remember` / `recall` 等 MCP 工具自动走中央，本地做缓存。用户不需要改任何工作流。

### 9.6 与 Forge 多 Agent 平台的关系

Temper 中央模式是独立的知识服务，不依赖 Forge。但 Forge 可以利用它：

```
Forge Multi-Agent Platform (编排层)
  │
  ├── Smith A (HA 模块专家)  ──┐
  ├── Smith B (Auth 专家)     ──┼── 都通过 MCP 查询 Temper Central
  ├── Smith C (DB 专家)       ──┤
  └── Memory Smith (知识管理) ──┘    Temper Central Server
                                      │
                                      └── PostgreSQL + pgvector
```

Temper 单独用：个人开发者，本地 `.temper/` + SQLite
Temper + 中央 server：团队共享知识
Temper + Forge：多 Agent 协作 + 共享知识

## 十、可视化

Temper 提供多层次可视化，从 CLI 纯文本到 Web UI，逐步实现：

### 10.1 CLI 可视化（Phase 1 — 所有用户可用，不依赖 Forge）

#### 纯文本表格

```
temper status
  → 项目概览：文件数、模块数、知识条目数、上次扫描时间、stale 知识数

temper modules
  → 列出所有模块 + 维度分类（表格格式）
  → 示例：
    Module                Files  Knowledge  Interfaces  Status
    web-server/user         23         5           3   ok
    web-server/host         18         2           2   ok
    database/dao            45         8           6   stale (3 entries)

temper modules web-server/user
  → 模块详情：描述、文件列表、入口点、tags
  → 关联知识条目摘要
  → 依赖关系（depends_on / depended_by）

temper knowledge
  → 列出知识条目（表格：id, type, title, module, status, updated_at）

temper knowledge --module web-server/user
  → 按模块过滤

temper knowledge --type experience
  → 按类型过滤（decision/bug/constraint/experience/causal）

temper history <id>
  → 某条知识的完整 temporal 历史
  → 示例：
    Version  Status     Changed By  Git Commit  Reason                           Time
    1        active     user        a1b2c3d     initial creation                 2024-01-15
    2        stale      git-hook    d4e5f6a     HA module files changed          2025-06-01
    3        validated  user        d4e5f6a     confirmed still valid            2025-06-02
    4        expired    smith       g7h8i9j     architecture changed to MQ       2026-01-10

temper graph --stats
  → Code graph 统计：文件数、函数数、类数、边数

temper search <query>
  → 搜索代码 + 知识（合并 AST 搜索和语义搜索结果）
```

#### ASCII 依赖图

```
temper graph --deps web-server/user
  → 模块依赖关系（ASCII 渲染）
  → 示例：
    web-server/user
    ├── depends on
    │   ├── web-server/system (AuthService.authenticate)
    │   ├── database (UserDAO.findById)
    │   └── web-server/host (HostResolver.resolve)
    └── depended by
        ├── web-server/host (HostController.getHostUsers)
        └── masterloader (MasterLoader.initServices)

temper graph --causal <entity>
  → 因果链（ASCII 渲染）
  → 示例：
    HA failover 切换
    ├── triggers: disk sync (X 模块假设 shared storage)
    │   └── affects: Y 模块认证超时 reset
    │       └── constraint: 必须同时更新 Y 模块 timeout
    └── fix: HA 切换前 pre-warm session cache
```

### 10.2 HTML 导出（Phase 2 — 本地浏览器查看，不依赖 Forge）

```
temper export --html [output_dir]
  → 生成静态 HTML 页面到 output_dir（默认 .temper/export/）
  → 浏览器打开即可查看，无需启动 server

temper export --html --open
  → 生成并自动打开浏览器
```

生成的页面包含：
- **Dashboard**：项目概览、模块列表、知识统计
- **Module Graph**：交互式模块依赖图（vis.js，复用 prototype 的 graph.html 方案）
- **Causal Graph**：因果链可视化（节点 + 有向边）
- **Knowledge Timeline**：知识条目的 temporal 历史时间线
- **Search**：客户端搜索（纯静态 JS，数据内嵌 JSON）

技术：纯静态 HTML + JS（vis.js / D3.js），不需要后端。Rust 侧只负责把数据导出为 JSON，HTML 模板内嵌在 binary 中。

### 10.3 TUI 交互式（Phase 3 — 终端 power user）

```
temper ui
  → 启动终端交互界面（类似 lazygit）
  → 左侧：模块树 / 知识列表
  → 右侧：详情面板
  → 支持搜索、过滤、展开因果链
  → 键盘导航
```

Rust crate: `ratatui`

### 10.4 Forge Web UI 集成（有 Forge 时）

Forge 通过 Temper 的 MCP 工具（或未来的 HTTP API）读取数据，在 Web UI 中渲染：
- Project Detail → Memory tab：模块列表、知识条目、因果链
- 交互式依赖图（拖拽、缩放、点击展开）
- 知识编辑器（直接在 UI 中 remember/forget）
- 模块定义向导（可视化选择文件/目录）

Forge 不复制 Temper 的数据，只做渲染层。数据源始终是 `.temper/` 或 Temper Central Server。

### 10.5 可视化实现优先级

| 阶段 | 功能 | 依赖 |
|------|------|------|
| Phase 1 | CLI 纯文本表格 (`temper status/modules/knowledge/history/search`) | clap + 基础数据层 |
| Phase 1 | CLI ASCII 依赖图 (`temper graph --deps/--causal`) | Code Graph + 因果链 |
| Phase 2 | HTML 静态导出 (`temper export --html`) | vis.js 模板 + JSON 导出 |
| Phase 3 | TUI 交互式 (`temper ui`) | ratatui |
| Phase 4 | Forge Web UI 集成 | Forge 平台 + MCP/HTTP API |

## 十一、多级模块 Dimensions（方案 B）

### 11.1 问题

FortiNAC (8657 files) 的模块划分需要多个维度，且每个维度需要多级嵌套：

```
按部署: backend/masterloader/plugin/radius
按功能: 认证/radius
按层次: plugin层/radius
```

当前 _index.yaml 的 DimensionGroup 只支持一层 children，不够。

### 11.2 设计：递归嵌套 DimensionGroup

```yaml
# _index.yaml — dimensions 支持任意层级嵌套
dimensions:
  by-deployment:
    - name: backend
      children:
        - name: masterloader
          children:
            - name: plugin
              modules: [plugin/radius, plugin/ldap, plugin/snmp, plugin/kea]
            - name: api
              modules: [api/persistence, api/selection, api/database]
          modules: [dao/impl]    # 直接属于 masterloader 但不属于任何子组
        - name: web-server
          children:
            - name: rest
              modules: [rest/user, rest/host, rest/system, rest/policy]
            - name: servlet
              modules: [servlet/system, servlet/ncm, servlet/settings]
        - name: campusMgr
          modules: [campusMgr/api, campusMgr/service]
    - name: frontend
      modules: [gui]

  by-function:
    - name: 认证
      modules: [plugin/radius, plugin/ldap, api/authenticate, rest/portal, api/fradius]
    - name: 网络管控
      modules: [plugin/forwarding, plugin/snmp, api/forwarding, forwarding/fortilan]
    - name: 设备管理
      modules: [api/device, api/fingerprint, plugin/agent, rest/host]

  by-layer:
    - name: api
      modules: [rest/*, servlet/*]     # 支持通配符
    - name: service
      modules: [api/*]
    - name: plugin
      modules: [plugin/*]
    - name: data
      modules: [dao/*, cache/*]
```

### 11.3 数据结构

```rust
struct DimensionGroup {
    name: String,
    modules: Vec<String>,           // 直接属于此组的模块
    children: Vec<DimensionGroup>,  // 子组（递归）
    verified: bool,                 // 用户确认过的不被 rescan 覆盖
}
```

### 11.4 查询方式

```
list_modules(dimension="by-deployment")
  → 展示整棵树

list_modules(dimension="by-deployment", group="backend/web-server")
  → 只展示 web-server 子树

list_modules(dimension="by-function", group="认证")
  → 返回所有认证相关模块

get_module("plugin/radius")
  → 返回结果中包含它属于哪些 dimension 路径：
    - by-deployment: backend/masterloader/plugin
    - by-function: 认证
    - by-layer: plugin
```

### 11.5 自动推断增强

当前推断只看模块名的 `/` 分隔。增强后：
- **by-deployment**：从文件的目录结构推断（`core_business_v2/ingestion/` → deployment=core_business_v2, sub=ingestion）
- **by-function**：从模块的 tags 推断（tags 含 `auth/radius/ldap` → function=认证）
- **by-layer**：从包名推断（`com/bsc/plugin/*` → layer=plugin, `com/bsc/rest/*` → layer=api）

用户修改过的（verified=true）不覆盖。

## 十二、Graph 实时刷新策略

### 12.1 问题

```
Claude Code 直接读代码文件 → 永远是最新的
Temper 的 graph.json → 是某个时间点的 AST 快照，可能过期
```

Claude 改了代码但没 commit，下一次调用 search_code 时 graph 和实际代码不一致。

### 12.2 各方案对比

| 方案 | 机制 | 延迟 | 开销 | 适合大项目？ |
|------|------|------|------|------------|
| ❌ File watcher | fs event 触发 parse | 实时 | 高（8657 watches，频繁 event） | 不适合 |
| ❌ Git hook | commit 时触发 | 只有 commit 才刷新 | 低 | 检测不到未 commit 变更 |
| ❌ 定期轮询 | 每 N 秒检查 | N 秒 | 浪费 CPU | 不需要 |
| ✅ **Git diff on-demand** | MCP 调用时检查 | ~20ms | 极低 | **适合** |

### 12.3 采用方案：Git diff on-demand（策略 2）

**不用 file watcher。不用 git hook。只在 MCP 工具调用时按需检查。**

```
temper serve 启动：
  1. 加载 graph.json（冷缓存）
  2. 记录 last_check_time = now

每次 MCP 工具调用（search_code, get_file_context, get_module）：
  1. now - last_check_time < 3s？
     → 跳过检查，用缓存（避免连续调用重复检查）
  2. 执行 git status --porcelain （~10-20ms，哪怕万级文件）
  3. 有变更文件？
     → 无：用缓存
     → 有且 < 50 个文件：增量 tree-sitter 解析（~50-200ms）
     → 有且 >= 50 个文件：标记 graph 为 stale，返回结果带 ⚠️ 警告
  4. 更新 last_check_time
```

### 12.4 性能预算

| 操作 | FortiNAC (8657 files) | 说明 |
|------|----------------------|------|
| git status --porcelain | ~10-20ms | 比较 index，不读文件内容 |
| 增量 parse 5 文件 | ~50-100ms | tree-sitter 单文件 <20ms |
| 增量 parse 20 文件 | ~200-400ms | 仍然可接受 |
| 全量 rescan | ~5-10s | 仅在 >50 文件变更时提示 |
| 3 秒 throttle | 每 session ~2-3 次检查 | 不会频繁触发 |
| **总额外开销** | **< 300ms / session** | **可忽略** |

### 12.5 检测范围

`git status --porcelain` 覆盖所有场景：

```
M  file.java    ← staged 修改
 M file.java    ← unstaged 修改  
?? file.java    ← 新文件（未 tracked）
D  file.java    ← 已删除
```

Claude Code 改文件但不 commit → `M` 状态 → 检测到 → 增量更新。

### 12.6 各数据类型的刷新策略

| 数据 | 刷新方式 | 原因 |
|------|---------|------|
| graph.json (AST) | git diff on-demand | 代码变就要刷新 |
| modules/*.yaml | 不自动刷新 | 人工定义，稳定 |
| knowledge.db (约束) | 不自动刷新，stale 标记 | 精准记忆，人工管理 |
| interfaces/*.json | rescan_module_interfaces 手动触发 | API 不经常变 |

### 12.7 与 Benchmark 发现的关联

v3 Benchmark 发现的冷启动问题（M1 With Temper 比 Without 慢）：

**根因**：`temper serve` 启动时立刻加载完整 graph.json（FortiNAC ~50MB）

**解决方案**：延迟加载 + on-demand refresh 结合

```
temper serve 启动：
  1. 不加载 graph.json
  2. 第一次 MCP 调用时：
     a. graph.json 存在？加载它（~500ms for 50MB）
     b. 不存在？提示 run temper scan
     c. 加载后检查 git status → 增量更新
  3. 后续调用：用缓存 + throttled git diff 检查
```

这样 M1 的冷启动开销从"serve 启动时"延迟到"第一次查询时"，且只加载一次。

## 十三、主动约束注入（Benchmark 发现）

### 13.1 问题

v3 Benchmark 证明 CLAUDE.md 的被动规则（"修改前请检查约束"）无法阻止 Claude 违反约束。Claude 面对直接指令（"remove it", "refactor to X"）时倾向于执行而非质疑。

### 13.2 方案：get_file_context 主动注入约束

当 Claude 调用 `get_file_context(file)` 时，Temper 自动在返回结果中附带该文件关联的约束：

```
## src/main/java/com/fortinet/nac/ingestion/util/EntityCache.java

### Imports (2)
- ...

### Exports (3)
- ...

### ⚠️ CONSTRAINTS (from project memory — do NOT violate)
- **Do NOT cache entity objects, only cache entity IDs**
  Caching full objects caused stale state issues because entity processing
  stage changes frequently during state machine progression.
  (id: k-xxx, status: active, confidence: validated)

- **EntityCache fault tolerance — write failures are non-fatal**
  Do NOT make cache writes throw exceptions. Redis OOM caused ingestion
  service to reject all requests.
  (id: k-yyy, status: active, confidence: validated)
```

Claude 无法忽略这些约束——它们直接出现在 Claude 正在阅读的文件上下文中。

### 13.3 同样应用于 get_module

`get_module(name)` 返回模块上下文时，自动附带该模块的所有约束和经验。

### 13.4 不需要 CLAUDE.md 规则

主动注入替代了 CLAUDE.md 的被动规则。Claude 不需要"记得去查约束"，因为约束已经在它读到的每个上下文中了。
