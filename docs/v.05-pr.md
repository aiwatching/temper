# Memory Service PRD

> **项目代号**:Memory Service(暂定,待命名)
> **作者**:Jerry
> **版本**:v0.1
> **日期**:2026-05-12
> **状态**:Draft,用于 Claude Code 实现

---

## 1. 项目背景与愿景

### 1.1 一句话定位

**一个独立部署、多租户、可被任意 Agent 调用的中央记忆服务**——本质上是自建版的 Zep Cloud。

### 1.2 设计目标

构建一个语言中立、协议清晰的记忆层,让上层 Agent(英语学习 Agent、Forge Chat、未来的其他 Agent)不需要各自实现记忆系统,而是统一接入这个服务。

### 1.3 核心价值

| 价值 | 说明 |
|------|------|
| **跨会话长期记忆** | Agent 重启、用户跨设备使用都能保持记忆连续 |
| **跨 Agent 共享** | 同一用户的多个 Agent 可以共享记忆(在权限允许范围内) |
| **时序推理** | 知道事实何时发生、何时失效,支持时间旅行查询 |
| **多租户隔离** | 个人 / 组 / 公司 / 公开,四级命名空间 |
| **可扩展** | 加新 Agent 只需调用 HTTP API,不改记忆服务 |

### 1.4 非目标(明确不做的事)

- 不做 Agent 编排(不是 LangChain/LangGraph 的替代)
- 不做 LLM 推理(只用 LLM 做抽取)
- 不做内容生成(只存事实、关系、原始 Episode)
- 不做向量数据库的事(向量检索是 Graphiti 内部行为)
- 不做实时流处理(批量摄入即可)

---

## 2. 关键决策(已确定)

| 编号 | 决策点 | 选择 | 理由 |
|------|--------|------|------|
| **D1** | 定位 | 通用记忆服务(不绑定单一 Agent) | 未来要服务多个 Agent |
| **D2** | 命名空间结构 | 扁平字符串 | 简单,起步够用 |
| **D3** | Agent 写入策略 | 自动写入(前期),后期支持过滤 | 先收集数据,再优化 |
| **D4** | "对外开放"边界 | 登录可见(任何认证用户可读 `public`) | 安全 vs 易用平衡 |
| **D5** | 部署 | 自建服务器 | 控制力强,成本低 |
| **D6** | 核心引擎 | Graphiti(Python)+ FalkorDB | 论证充分,见前期调研 |
| **D7** | 接入协议 | HTTP REST API | 语言中立,易调试 |

---

## 3. 用户与角色

### 3.1 三类主体

```
┌────────────────────────────────────────────────┐
│  User(人类用户)                               │
│  例:Jerry、Jerry 的同事、外部访客              │
└────────────────────────────────────────────────┘
                 │
                 │ 拥有
                 ▼
┌────────────────────────────────────────────────┐
│  Agent(代表 User 调用记忆服务)                │
│  例:英语学习 Agent、Forge Chat、未来其他 Agent │
└────────────────────────────────────────────────┘
                 │
                 │ HTTP API
                 ▼
┌────────────────────────────────────────────────┐
│  Memory Service                                │
└────────────────────────────────────────────────┘
```

### 3.2 Agent 与 User 的关系

**前期方案(MVP)**:**Agent 代表 User** ——

- Agent 启动时需要 User 的 API Key 才能初始化
- Agent 调用记忆服务时,等同于 User 在调用
- 一个 User 可以有多个 Agent
- 一个 Agent 实例只服务一个 User(不跨用户)

**后期演进方向**(不在 MVP 范围):
- Agent 有独立身份,通过 OAuth/授权访问 User 数据
- Agent 跨用户(如客服 Agent 服务多个用户)

---

## 4. 数据模型

### 4.1 用户与权限模型

```python
# 用户
class User:
    id: str              # 唯一 ID,例:"jerry"
    email: str           # 唯一邮箱
    password_hash: str   # bcrypt
    org_id: str | None   # 所属组织
    created_at: datetime

# 组(扁平,不嵌套)
class Group:
    id: str              # 例:"fortinac-team"
    name: str            # 显示名:"FortiNAC Team"
    org_id: str          # 所属组织
    created_at: datetime

# 组织
class Organization:
    id: str              # 例:"fortinet"
    name: str            # "Fortinet Inc."
    created_at: datetime

# 用户组成员关系
class UserGroupMembership:
    user_id: str
    group_id: str
    role: str            # "member" | "admin"
    joined_at: datetime

# API Key
class APIKey:
    key_hash: str        # 哈希存储
    user_id: str         # 关联用户
    agent_name: str      # 用户给这个 Key 起的名字,例:"english-agent"
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool
```

### 4.2 命名空间(Namespace)

**扁平字符串**,四种类型:

| 类型 | 格式 | 例子 | 谁能写 | 谁能读 |
|------|------|------|--------|--------|
| 个人 | `user:{user_id}` | `user:jerry` | 用户本人 | 用户本人 |
| 组 | `group:{group_id}` | `group:fortinac-team` | 组成员 | 组成员 |
| 组织 | `org:{org_id}` | `org:fortinet` | 组织 admin | 组织所有成员 |
| 公开 | `public` | `public` | 超级 admin | 所有认证用户 |

**关键设计**:
- 命名空间直接对应 Graphiti 的 `group_id` 字段
- 一个 Episode 只属于一个命名空间(不跨)
- 检索时可以指定多个命名空间,Memory Service 自动检查可读权限

### 4.3 权限矩阵

#### 写入权限

| Actor | user:self | user:other | group:my-group | group:other-group | org:my-org | public |
|-------|-----------|-----------|---------------|------------------|------------|--------|
| 普通用户 | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ |
| 组 admin | ✓ | ✗ | ✓ | ✗ | ✗ | ✗ |
| 组织 admin | ✓ | ✗ | ✓ | ✗ | ✓ | ✗ |
| 超级 admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |

#### 读取权限

| Actor | user:self | user:other | group:my-group | group:other-group | org:my-org | public |
|-------|-----------|-----------|---------------|------------------|------------|--------|
| 匿名 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ |
| 认证用户 | ✓ | ✗ | ✓ | ✗ | ✓ | ✓ |

**重要原则**:**默认拒绝**(deny by default)——任何不在矩阵明确允许的操作都拒绝。

### 4.4 Episode 元数据扩展

在 Graphiti 默认字段基础上,Memory Service 额外维护:

```python
class EpisodeMetadata:
    episode_id: str         # Graphiti UUID
    namespace: str          # 命名空间(即 group_id)
    created_by_user: str    # 创建者 user_id
    created_by_agent: str   # 创建时使用的 agent name
    created_at: datetime
    source_type: str        # "message" | "text" | "json"
    tags: list[str]         # 自由标签,便于过滤
```

存储位置:**应用层数据库(PostgreSQL/SQLite)**,与 Graphiti 的图谱数据分离。

---

## 5. API 设计

### 5.1 协议规范

- **协议**:HTTP REST
- **格式**:JSON
- **认证**:`X-API-Key` header(MVP 阶段)
- **版本**:URL 路径前缀 `/v1/`
- **时间格式**:ISO 8601(UTC)
- **错误格式**:标准 RFC 7807 Problem Details

### 5.2 核心 API 端点

#### 5.2.1 认证与用户管理

```
POST   /v1/auth/register              注册新用户
POST   /v1/auth/login                 用邮箱密码登录,返回 session token
POST   /v1/auth/logout                登出
GET    /v1/auth/me                    返回当前用户信息

POST   /v1/users/me/api-keys          创建 API Key
GET    /v1/users/me/api-keys          列出我的 API Keys
DELETE /v1/users/me/api-keys/{key_id} 撤销 API Key
```

#### 5.2.2 组织与组管理

```
POST   /v1/orgs                       创建组织(需超级 admin)
GET    /v1/orgs/{org_id}              查看组织信息

POST   /v1/groups                     创建组(在用户所属 org 内)
GET    /v1/groups                     列出我能看到的组
POST   /v1/groups/{group_id}/members  添加成员
DELETE /v1/groups/{group_id}/members/{user_id}  移除成员
```

#### 5.2.3 记忆操作(核心 API)

##### 写入 Episode

```http
POST /v1/episodes
Headers:
  X-API-Key: {key}
  Content-Type: application/json

Body:
{
  "namespace": "user:jerry",
  "content": "Jerry 今天换了英语老师,新老师是 Sarah",
  "type": "message",
  "source_description": "english-agent conversation",
  "reference_time": "2026-05-12T10:00:00Z",
  "tags": ["english-learning"]
}

Response 201:
{
  "episode_id": "uuid-...",
  "namespace": "user:jerry",
  "extracted_entities": [
    {"name": "Jerry", "type": "Person"},
    {"name": "Sarah", "type": "Person"}
  ],
  "extracted_facts": [...],
  "created_at": "2026-05-12T10:00:01Z"
}

Response 403:
{
  "type": "/errors/permission-denied",
  "title": "Cannot write to namespace",
  "detail": "User 'jerry' does not have write permission for 'group:other-team'"
}
```

##### 检索记忆

```http
GET /v1/search?query={query}&namespaces={ns1,ns2}&limit=10
Headers:
  X-API-Key: {key}

可选参数:
  - namespaces:逗号分隔的命名空间列表,默认 = 用户所有可读命名空间
  - limit:返回结果数,默认 10,最大 50
  - search_type:"hybrid" | "node" | "edge",默认 "hybrid"
  - center_node_uuid:中心节点搜索的 UUID(可选)

Response 200:
{
  "facts": [
    {
      "fact": "Jerry has English teacher Sarah",
      "valid_at": "2026-05-12T00:00:00Z",
      "invalid_at": null,
      "namespace": "user:jerry",
      "source_episode_id": "uuid-..."
    }
  ],
  "entities": [
    {
      "name": "Sarah",
      "summary": "English teacher who replaced Randal, focuses on BQ stories",
      "namespace": "user:jerry"
    }
  ],
  "query_time_ms": 234
}
```

##### 查看原始 Episode

```http
GET /v1/episodes/{episode_id}
Headers:
  X-API-Key: {key}

Response 200:
{
  "episode_id": "uuid-...",
  "namespace": "user:jerry",
  "content": "Jerry 今天换了英语老师...",
  "type": "message",
  "reference_time": "2026-05-12T10:00:00Z",
  "extracted_at": "2026-05-12T10:00:01Z",
  "facts": [...],
  "entities": [...]
}
```

##### 列出 Episode(分页)

```http
GET /v1/episodes?namespace=user:jerry&before=2026-05-12T00:00:00Z&limit=20
Headers:
  X-API-Key: {key}

Response 200:
{
  "episodes": [...],
  "next_cursor": "..."
}
```

##### 删除 Episode(管理)

```http
DELETE /v1/episodes/{episode_id}
Headers:
  X-API-Key: {key}

注意:这是物理删除原始 Episode 及其派生的所有 fact/entity 关系。
权限:只有 Episode 的创建者或 namespace 的 admin 可以删除。
```

#### 5.2.4 系统

```
GET    /v1/health                     健康检查
GET    /v1/metrics                    Prometheus 指标
```

### 5.3 错误码规范

| HTTP | 错误类型 | 场景 |
|------|---------|------|
| 400 | invalid-request | 参数错误 |
| 401 | unauthenticated | 未提供 API Key 或无效 |
| 403 | permission-denied | 无权访问该命名空间 |
| 404 | not-found | 资源不存在 |
| 409 | conflict | 资源冲突(如重复创建) |
| 429 | rate-limit-exceeded | 限流 |
| 500 | internal-error | 服务器内部错误 |
| 503 | service-unavailable | LLM 或 DB 不可用 |

---

## 6. 系统架构

### 6.1 部署架构

```
┌────────────────────────────────────────────────┐
│  外部 Agent(任何语言、任何位置)               │
│  通过 HTTPS 调用                                │
└────────────────────┬───────────────────────────┘
                     │
                     ▼ HTTPS
┌────────────────────────────────────────────────┐
│  Nginx(反向代理 + TLS)                       │
│  - 限流(全局)                                 │
│  - HTTPS 终止                                   │
└────────────────────┬───────────────────────────┘
                     │
                     ▼
┌────────────────────────────────────────────────┐
│  Memory Service(FastAPI 应用)                 │
│  - 认证与授权                                   │
│  - 命名空间路由                                 │
│  - 调用 Graphiti                                │
│  - 写入元数据 DB                                │
│  端口:8000                                     │
└─────┬──────────────────────────────────────┬───┘
      │                                       │
      ▼                                       ▼
┌─────────────────┐                ┌────────────────┐
│  PostgreSQL     │                │  Graphiti +    │
│  - 用户表       │                │  FalkorDB      │
│  - 组织/组表    │                │  - 知识图谱     │
│  - API Key      │                │  - LLM 调用     │
│  - Episode 元数据│                │  端口:6379     │
│  端口:5432     │                └────────────────┘
└─────────────────┘                         │
                                            ▼
                                  ┌────────────────┐
                                  │  OpenAI API    │
                                  │ (LLM 抽取/嵌入)│
                                  └────────────────┘
```

### 6.2 关键组件职责

| 组件 | 职责 |
|------|------|
| **Nginx** | TLS、限流、反向代理 |
| **FastAPI 应用** | 业务逻辑、认证、权限、API |
| **PostgreSQL** | 用户、组织、Episode 元数据 |
| **Graphiti(库)** | 实体抽取、消歧、矛盾检测、检索 |
| **FalkorDB** | 图数据持久化 |
| **OpenAI API** | LLM 抽取 |

### 6.3 为什么 PostgreSQL + FalkorDB 分离

PostgreSQL 存:
- 用户身份、组织结构(关系型数据)
- API Key、Session
- Episode 的元数据(谁写的、什么时候写的、tags)

FalkorDB 存:
- Graphiti 自动管理的知识图谱
- Episode、Entity、Fact、Community
- 时序边

**分离的好处**:
- 用户系统不会因为知识图谱崩了而挂
- 元数据查询用 SQL,知识图谱查询用 Cypher,各自最优
- 备份策略可以分开(用户数据高频备份,图谱低频)

---

## 7. MVP 范围与里程碑

### 7.1 MVP 范围(必做)

| 类别 | 项目 | 说明 |
|------|------|------|
| **认证** | 用户注册/登录 | 邮箱密码 |
| **认证** | API Key 管理 | 创建、列表、撤销 |
| **租户** | 组织、组、成员管理 | 基础 CRUD |
| **租户** | 四种命名空间 | user/group/org/public |
| **租户** | 权限矩阵 | 写入和读取分别校验 |
| **记忆** | Episode 写入 | POST /episodes |
| **记忆** | 记忆检索 | GET /search |
| **记忆** | Episode 查看 | GET /episodes/{id} |
| **运维** | Docker Compose 部署 | 一键启动整套 |
| **运维** | 健康检查 | /health |
| **运维** | 基础日志 | 结构化 JSON 日志 |

### 7.2 MVP 不做(后续迭代)

| 项目 | 推迟原因 |
|------|---------|
| OAuth / SSO | API Key 足够 MVP |
| 复杂权限(细粒度 ACL) | 命名空间级权限足够 |
| Episode 内容过滤(用户标记重要) | 前期自动写入 |
| Web Console | 可以用 curl/Postman |
| Prometheus + Grafana | 等用起来再加 |
| 备份与灾难恢复 | MVP 不考虑生产容灾 |
| 多区域部署 | 单实例足够 |
| 速率限制(用户级) | Nginx 全局限流够 |
| Agent 独立身份 | "代表用户"模式足够 |
| 跨命名空间合并/迁移 | 用得着再说 |

### 7.3 里程碑

| 里程碑 | 通过标准 | 估时 |
|--------|---------|------|
| **M1: 单租户跑通** | 一个用户能写入/检索记忆,无认证 | 1 周 |
| **M2: 多租户基础** | 用户系统 + API Key + 命名空间隔离 | 2 周 |
| **M3: 权限矩阵** | 写入和读取权限正确校验,有单元测试 | 1 周 |
| **M4: 部署就绪** | Docker Compose 一键启动,有部署文档 | 1 周 |
| **M5: Agent 集成** | 一个客户端 SDK + 英语 Agent 真实接入 | 1 周 |

**合计**:6 周(全职);兼职 12 周左右

---

## 8. 技术栈

### 8.1 后端

| 层 | 技术 | 版本 | 选择理由 |
|----|------|------|---------|
| 语言 | Python | 3.11+ | Graphiti 原生支持 |
| Web 框架 | FastAPI | 0.110+ | 异步、类型友好、自动文档 |
| ORM | SQLAlchemy 2.0 + Alembic | 最新 | 类型友好,迁移工具完善 |
| 用户元数据 DB | PostgreSQL | 16 | 关系型数据 |
| 知识图谱引擎 | graphiti-core | 最新 | 项目核心 |
| 图数据库 | FalkorDB | 1.1.2+ | 速度快、Docker 简单 |
| 认证 | passlib + python-jose | 最新 | 密码 hash + JWT |
| 验证 | Pydantic v2 | 最新 | 与 FastAPI 配合 |
| 日志 | structlog | 最新 | 结构化日志 |

### 8.2 部署

| 项目 | 技术 |
|------|------|
| 容器化 | Docker + Docker Compose |
| 反向代理 | Nginx |
| TLS | Let's Encrypt(certbot) |
| 进程管理 | Docker 重启策略 |

### 8.3 LLM Provider

| 用途 | 选择 |
|------|------|
| 实体抽取 | OpenAI gpt-4o-mini(性价比) |
| Embedding | OpenAI text-embedding-3-small |

**注意**:Sajid 文章警告 Graphiti 强依赖 Structured Output。OpenAI 是最稳的选择。

---

## 9. 代码结构(建议)

```
memory-service/
├── pyproject.toml
├── README.md
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env.example
├── Dockerfile
│
├── src/
│   └── memory_service/
│       ├── __init__.py
│       ├── main.py                    # FastAPI 入口
│       ├── config.py                  # 配置管理(Pydantic Settings)
│       │
│       ├── api/                       # API 路由
│       │   ├── __init__.py
│       │   ├── v1/
│       │   │   ├── auth.py
│       │   │   ├── users.py
│       │   │   ├── orgs.py
│       │   │   ├── groups.py
│       │   │   ├── episodes.py
│       │   │   ├── search.py
│       │   │   └── system.py
│       │   └── deps.py                # 公共依赖(认证、DB session)
│       │
│       ├── core/                      # 核心业务逻辑
│       │   ├── __init__.py
│       │   ├── auth.py                # 密码/Token/API Key
│       │   ├── permissions.py         # 权限矩阵
│       │   ├── namespaces.py          # 命名空间解析
│       │   └── memory.py              # 记忆操作的业务逻辑
│       │
│       ├── adapters/                  # 外部依赖适配器
│       │   ├── __init__.py
│       │   ├── graphiti_client.py     # 包装 Graphiti
│       │   ├── falkordb.py            # FalkorDB 连接管理
│       │   └── openai_client.py       # LLM 客户端配置
│       │
│       ├── models/                    # SQLAlchemy 模型
│       │   ├── __init__.py
│       │   ├── user.py
│       │   ├── org.py
│       │   ├── group.py
│       │   ├── api_key.py
│       │   └── episode_meta.py
│       │
│       ├── schemas/                   # Pydantic schemas(API I/O)
│       │   ├── __init__.py
│       │   ├── auth.py
│       │   ├── episode.py
│       │   └── search.py
│       │
│       └── db/                        # 数据库
│           ├── __init__.py
│           ├── session.py
│           └── migrations/            # Alembic
│
├── tests/
│   ├── unit/
│   │   ├── test_permissions.py
│   │   ├── test_namespaces.py
│   │   └── test_auth.py
│   ├── integration/
│   │   ├── test_api_episodes.py
│   │   ├── test_api_search.py
│   │   └── test_graphiti_integration.py
│   └── conftest.py
│
└── scripts/
    ├── init_db.py
    ├── create_admin.py
    └── seed_demo_data.py
```

---

## 10. Phase 1 实现任务清单(给 Claude Code)

按这个顺序实现,**每个任务完成后停下来确认再继续**。

### Phase 1.0:项目骨架(0.5 天)

- [ ] 创建项目结构(按第 9 节)
- [ ] 配置 pyproject.toml,依赖最小集合
- [ ] 创建 docker-compose.yml(包含 PostgreSQL + FalkorDB)
- [ ] 创建 .env.example
- [ ] 写 README,说明本地启动步骤
- [ ] 配置 ruff + mypy
- [ ] 配置 pre-commit hooks
- [ ] git init,提交骨架

### Phase 1.1:基础设施层(1 天)

- [ ] 实现 `config.py`,用 Pydantic Settings 读取环境变量
- [ ] 实现 `db/session.py`,SQLAlchemy 异步引擎
- [ ] 实现 `adapters/falkordb.py`,FalkorDB 连接管理
- [ ] 实现 `adapters/graphiti_client.py`,初始化 Graphiti 实例
- [ ] 实现 `main.py`,空的 FastAPI 应用
- [ ] 实现 `/v1/health` 端点,返回各依赖的状态
- [ ] 验证:`docker-compose up` 后 `curl /v1/health` 全绿

### Phase 1.2:用户系统(2 天)

- [ ] 定义 User、Organization、Group、APIKey 的 SQLAlchemy 模型
- [ ] 配置 Alembic,生成首版迁移
- [ ] 实现 `core/auth.py`:密码 hash、API Key 生成
- [ ] 实现 `POST /v1/auth/register`
- [ ] 实现 `POST /v1/auth/login`(返回 session token)
- [ ] 实现 `GET /v1/auth/me`
- [ ] 实现 `POST /v1/users/me/api-keys`
- [ ] 实现 `GET /v1/users/me/api-keys`
- [ ] 实现 `DELETE /v1/users/me/api-keys/{key_id}`
- [ ] 实现 `api/deps.py` 中的 `get_current_user`(从 API Key 解析)
- [ ] 单元测试覆盖

### Phase 1.3:组织与组(1 天)

- [ ] 定义 UserGroupMembership 模型
- [ ] 实现 `POST /v1/orgs`(超级 admin)
- [ ] 实现 `POST /v1/groups`
- [ ] 实现 `GET /v1/groups`(列出我能看到的)
- [ ] 实现 `POST /v1/groups/{id}/members`
- [ ] 实现 `DELETE /v1/groups/{id}/members/{user_id}`
- [ ] 单元测试

### Phase 1.4:命名空间与权限(2 天,关键!)

- [ ] 实现 `core/namespaces.py`:
  - `parse_namespace(s: str) -> NamespaceType + ID`
  - `get_readable_namespaces(user: User) -> list[str]`
  - `get_writable_namespaces(user: User) -> list[str]`
- [ ] 实现 `core/permissions.py`:
  - `can_read(user: User, namespace: str) -> bool`
  - `can_write(user: User, namespace: str) -> bool`
- [ ] **彻底的单元测试**:对每种角色 × 每种命名空间组合验证
- [ ] 实现 `api/deps.py` 中的 `require_can_write(namespace)` 装饰器/依赖
- [ ] 实现 `api/deps.py` 中的 `require_can_read(namespace)` 装饰器/依赖

### Phase 1.5:记忆操作 - 写入(1.5 天)

- [ ] 定义 EpisodeMetadata 模型
- [ ] 实现 `core/memory.py:add_episode(...)`:
  - 检查写入权限
  - 调用 Graphiti `add_episode`(传入 `group_id = namespace`)
  - 把元数据写入 PostgreSQL
- [ ] 实现 `POST /v1/episodes` 端点
- [ ] 处理错误(权限拒绝、Graphiti 异常、超时)
- [ ] 集成测试:写入后能从 FalkorDB 查到

### Phase 1.6:记忆操作 - 检索(1.5 天)

- [ ] 实现 `core/memory.py:search(...)`:
  - 如果指定 namespaces,检查每个的读取权限
  - 如果未指定,自动用 `get_readable_namespaces(user)`
  - 调用 Graphiti `search`(传入 `group_ids = namespaces`)
  - 附加 namespace 信息到每条结果
- [ ] 实现 `GET /v1/search` 端点
- [ ] 实现 `GET /v1/episodes/{id}`
- [ ] 实现 `GET /v1/episodes`(列表 + 分页)
- [ ] 实现 `DELETE /v1/episodes/{id}`(权限严格控制)
- [ ] 集成测试

### Phase 1.7:文档与示例(1 天)

- [ ] FastAPI 自动文档(Swagger)调通
- [ ] 写 `docs/api-guide.md`,包含 curl 示例
- [ ] 写 `docs/permissions.md`,详细解释权限模型
- [ ] 写 `examples/english_agent_minimal.py`,演示如何接入
- [ ] 写 `scripts/seed_demo_data.py`,创建演示数据

### Phase 1.8:部署(1 天)

- [ ] 完善 docker-compose.prod.yml
- [ ] 配置 Nginx 反向代理
- [ ] 配置 Let's Encrypt 自动续签
- [ ] 写部署文档 `docs/deployment.md`
- [ ] 写 systemd 单元文件(如果不用 Docker 重启)

**总计**:约 11-12 天(全职);兼职翻倍

---

## 11. 验收标准(Acceptance Criteria)

完成 MVP 后,以下场景必须全部通过:

### 11.1 基础功能

- [ ] 新用户能注册、登录、创建 API Key
- [ ] 用户能创建组,邀请其他用户加入
- [ ] 用户能写入 `user:{self}` 命名空间
- [ ] 用户能查询 `user:{self}` 命名空间

### 11.2 多租户隔离

- [ ] 用户 A 不能读取用户 B 的 `user:B` 命名空间
- [ ] 用户 A 不能写入 `user:B` 命名空间
- [ ] 不在组 X 的用户不能读取 `group:X`
- [ ] 不在组织 Y 的用户不能读取 `org:Y`

### 11.3 跨命名空间检索

- [ ] 用户在自己的命名空间 + 所在组 + 所在组织 + public 中检索,能合并结果
- [ ] 检索结果中每条 fact 标明所属命名空间

### 11.4 时序行为

- [ ] 先写入"Jerry 的老师是 Randal",再写"Jerry 换成了 Sarah"
- [ ] 查询"Jerry 现在的老师"返回 Sarah
- [ ] 查询能区分历史和当前(invalid_at 字段)

### 11.5 部署与运维

- [ ] `docker-compose up` 后服务全部健康
- [ ] /v1/health 返回所有依赖状态
- [ ] 服务重启后数据不丢
- [ ] 至少有 80% 的代码被单元测试覆盖

---

## 12. 风险与开放问题

### 12.1 已知风险

| 风险 | 缓解 |
|------|------|
| Graphiti 抽取质量不稳定 | Phase 1 灌真实数据验证;后期支持手动修正 API |
| LLM 成本失控 | 加 per-user 配额(后续);加监控 |
| FalkorDB 故障 | 定期备份;数据可从 Episode 重新抽取 |
| 中文/混合语言抽取效果差 | Phase 1 测试中文场景;必要时改 Prompt |
| 多租户数据泄露 | 严格的权限单元测试;默认拒绝原则 |

### 12.2 开放问题(实施中再决定)

| 问题 | 当前倾向 |
|------|---------|
| 是否需要 Episode 内容加密? | MVP 不加密,后期看监管要求 |
| 删除用户时如何处理其记忆? | 用户记忆物理删除;组/组织内的需要标记孤儿 |
| Embedding 模型是否要可换? | MVP 锁定 OpenAI,后期再抽象 |
| 是否需要 Audit Log? | MVP 用结构化日志覆盖,后期独立表 |

---

## 13. 后续迭代方向(Post-MVP)

### v0.2:可观测性

- Prometheus 指标(摄入延迟、LLM 成本、错误率)
- Grafana 仪表盘
- 用户级配额与限流

### v0.3:Web Console

- 用户登录、查看自己的记忆
- 浏览图谱可视化
- 手动修正/删除错误的 fact

### v0.4:Agent 独立身份

- OAuth 2.0 风格的 Agent 授权
- Agent 可服务多用户
- Agent 行为审计

### v0.5:进阶功能

- 自定义实体类型(Pydantic 模型)
- 自定义抽取 Prompt
- 时间旅行查询(显式 API)
- Episode 重抽取工具

---

## 14. 参考资料

- Graphiti GitHub: https://github.com/getzep/graphiti
- Zep 论文:Rasmussen et al., "Zep: A Temporal Knowledge Graph Architecture for Agent Memory", arxiv:2501.13956
- FalkorDB: https://www.falkordb.com/
- 前期调研笔记:本项目 docs/research/ 目录

---

## 附录 A:Claude Code 实施备忘

**给 Claude Code 的提示**:

1. **不要一次性把全部代码生成完**——按 Phase 1.0 → 1.8 顺序,每完成一段停下来确认
2. **每个 API 端点都要有对应的测试**,不要"先实现再补测试"
3. **权限相关的代码格外谨慎**:第 1.4 节(命名空间与权限)是项目核心,务必先写测试再实现
4. **不要自创 Graphiti 的封装**——直接用 `graphiti-core` 提供的 API,我们的服务层只做权限和元数据
5. **遇到 Graphiti 行为不明确的地方**(比如 search 的具体参数语义),先写一个 spike script 验证,不要猜
6. **环境配置全部走 .env**,不要硬编码 API Key
7. **PostgreSQL schema 用 Alembic 迁移管理**,所有 schema 变更都生成迁移文件
8. **错误处理统一走 FastAPI 的 exception handler**,业务代码只抛领域异常
9. **日志使用 structlog**,所有请求带 request_id
10. **如果某个 Phase 1.x 任务超过预估时间 50% 仍未完成**,停下来跟我讨论是否要简化

**禁止的事项**:

- ❌ 不要引入额外的中间件(Redis、Kafka、ElasticSearch),MVP 用不到
- ❌ 不要做"为了完美的抽象"——简单胜过精巧
- ❌ 不要假设 Graphiti 的内部行为,有疑问就读源码或写 spike
- ❌ 不要绕过权限检查"为了方便测试"——测试代码也要走完整路径
- ❌ 不要在 main 分支直接推代码,每个 Phase 一个 PR

---

**End of PRD v0.1**