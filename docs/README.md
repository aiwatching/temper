# Temper — Forged Memory for Your Code

## What is Temper

A standalone MCP server that gives Claude Code persistent memory and code understanding.

```
temper init                      → registers MCP server + scans project + suggests modules
temper serve                     → starts MCP server (Claude Code calls this automatically)
temper scan                      → scans project code structure
temper modules                   → lists defined modules (table format)
temper modules <name>            → module detail (files, deps, knowledge)
temper search <query>            → searches code + knowledge (AST + semantic)
temper knowledge                 → lists knowledge entries
temper knowledge --module <name> → filter by module
temper history <id>              → temporal history of a knowledge entry
temper graph --stats             → code graph statistics
temper graph --deps <module>     → ASCII module dependency graph
temper graph --causal <entity>   → ASCII causal chain graph
temper status                    → project overview (files, modules, knowledge, health)
temper export --html             → generate static HTML visualization
temper ui                        → interactive TUI (like lazygit)
temper config set <key> <value>  → set global/project config
temper sync push/pull            → sync with central server (future)
```

## Why "Temper"

In metalworking, **tempering** is the process that gives metal its memory — the ability to remember its shape and strength. Temper does the same for your codebase: it remembers the structure, relationships, design decisions, and lessons learned.

## Core Concepts

### Core Philosophy: Precise Persistent Memory

Temper records **precise, bounded, persistent memory** — NOT a simulation of human memory.

Human memory fades, blurs, and forgets. Temper does none of that. What it records is exact. Knowledge is never automatically forgotten or decayed. It can only be:
- **Marked stale** (code changed, needs verification — a reminder, not forgetting)
- **Validated** (confirmed still accurate after review)
- **Expired** (explicitly by user or confirmed outdated)

This is the fundamental difference from competitors (Mem0, A-MEM, MemGPT) that model memory with decay and forgetting. In code, "this module must not use thread pools" is either true or outdated — there is no "vaguely remember it might be true".

### Three-Layer Memory Architecture

Temper's memory system is organized into three layers, each with different change frequency and storage strategy:

| Layer | Content | Change Frequency | Storage |
|-------|---------|-----------------|---------|
| **Structural Memory** | Files, classes, functions, imports, call chains | High (every commit) | `graph.json` (JSON, in-memory for queries) |
| **Causal Memory** | Why code is this way, what triggers what, constraints | Medium (architecture changes) | `knowledge.db` (SQLite, graph-structured relations) |
| **Experience Memory** | Symptom→cause→fix, known bugs, lessons learned | Low (only accumulates) | `knowledge.db` (SQLite, with full temporal history) |

Key insight from competitive analysis: other tools index "what code is", Temper indexes "why code is this way, and what happens if you change it". The former can be extracted automatically from code; the latter only comes from team experience.

### Files belong to the project, modules are tags

There is no "unassigned" concept. All source files always belong to the project. Modules are labels/groups applied on top of files. A file can belong to **multiple modules** — this is common in legacy codebases where a utility class serves several subsystems.

### Gradual module extraction

Users don't have to define all modules upfront. `temper init` auto-suggests modules based on package/directory structure, and users confirm, adjust, or skip. Over time users can define more modules as they understand the codebase better.

### Glob-based path matching

Module paths use **glob patterns** (ripgrep-style semantics):
```yaml
paths:
  - "src/main/java/com/fortinet/nac/server/user/**/*.java"
  - "src/main/java/com/fortinet/nac/model/User.java"
exclude:
  - "src/main/java/com/fortinet/nac/server/user/legacy/**"
```

## Core Features

### 1. Code Graph (Structural Memory)
- Tree-sitter multi-language parsing (Java first, others added as needed)
- Function/class/method level dependency tracking
- Import/call chain traversal
- CamelCase-aware search with prefix matching
- Incremental updates (only rescan changed files)
- Stored as JSON, loaded fully into memory for BFS traversal — no temporal history needed (it's a point-in-time snapshot)

### 2. Module Registry
- Define module boundaries with glob patterns
- A file can belong to multiple modules
- Multi-dimension classification (by-service, by-function, by-layer)
- Dimensions auto-inferred from module paths + tags; user adjustments are persistent
- Auto-suggest modules on `temper init` based on package structure analysis

### 3. Interface Map
- Auto-extract public APIs (REST endpoints, public methods)
- Cross-module dependency tracking (who depends on whom)
- Change impact analysis ("if I change this API, what breaks?")

### 4. Knowledge Store (Causal + Experience Memory)

Replaces the flat `knowledge.json` with a structured SQLite database supporting:

- **Causal chains**: `(trigger → cause → effect → constraint → fix)` — not flat key-value
- **Experience records**: structured `symptom → cause → fix` triples
- **Full temporal history**: every state change is tracked with timestamp, git commit, who changed it, and why
- **Semantic search**: embedding-based similarity search via external API (user-configured model endpoint)
- **Module anchoring**: knowledge entries linked to modules, files, and functions

### 5. Semantic Search (Embedding Layer)

Tree-sitter finds code by name/structure. Semantic search finds code by **meaning**:
- "authentication" matches `TokenValidator.checkExpiry()` even though the name doesn't contain "auth"
- Embeddings are generated for **knowledge entries only** (hundreds, not thousands) — not for all code
- Uses external embedding API configured by the user (OpenAI, Voyage, or any compatible endpoint)
- Stored in SQLite via `sqlite-vec` extension — zero external dependencies
- For small knowledge bases (< 1000 entries), brute-force cosine similarity is fast enough

## `temper init` Flow

```
temper init
  1. Register MCP server to Claude Code settings
  2. Full code scan (tree-sitter AST analysis)
  3. Auto-analyze project structure → suggest module breakdown
     Example output:
       "Detected package structure, suggested modules:"
       1. web-server/user  (com.fortinet.nac.server.user, 23 files)
       2. web-server/host  (com.fortinet.nac.server.host, 18 files)
       3. database/dao     (com.fortinet.nac.dao, 45 files)
       ...
  4. User confirms / adjusts / skips each suggestion
  5. Write modules/*.yaml + _index.yaml
```

For Java projects, the auto-suggest algorithm analyzes the package hierarchy and finds the optimal split level — where the hierarchy diverges most.

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_code` | Find related code via AST graph traversal |
| `search_knowledge` | Semantic search across knowledge entries (embedding-based) |
| `get_file_context` | File dependencies + knowledge + **auto-injected constraints** |
| `get_module` | Complete module context (files, APIs, deps, knowledge, **constraints**) |
| `define_module` | Create/update module boundary |
| `remove_module` | Remove a module definition |
| `list_modules` | List all modules |
| `remember` | Store knowledge with causal structure (anchored to file/function/module) |
| `recall` | Retrieve knowledge by keyword, module, type, or semantic similarity |
| `forget` | Delete knowledge (keeps temporal history) |
| `find_causal_chain` | Given a change, trace what modules/code will be affected via causal links |
| `search_symptom` | Match a symptom to known symptom→cause→fix records |
| `get_constraints` | Get all constraints for a module |
| `rescan_code` | Incremental code graph update |
| `scan_module_interfaces` | Auto-extract module APIs |
| `refresh_modules` | Rescan for new files, suggest updates for user confirmation |
| `validate_modules` | Check module definitions for issues |

## Tech Stack

- **Language**: Rust
- **AST Parsing**: tree-sitter (native Rust, Java first, others added as needed)
- **MCP Protocol**: JSON-RPC over stdio
- **Knowledge Storage**: SQLite + sqlite-vec (single file, embedded, causal graph + temporal history + embeddings)
- **Module Storage**: YAML (human-editable module definitions)
- **Code Graph Storage**: JSON (AST snapshot, loaded into memory)
- **Embedding**: External API (user-configured endpoint — OpenAI, Voyage, etc.)
- **Git Integration**: git2 (libgit2)
- **CLI**: clap
- **Storage Abstraction**: `KnowledgeStore` trait (LocalStorage now, RemoteStorage future)
- **Distribution**: npm (native Rust binary wrapped per-platform, like esbuild/turbo)

## Storage Layout

```
~/.temper/                          # Global config (user-level, shared across projects)
├── config.yaml                     # Embedding API endpoint, API keys, default settings
└── projects.json                   # Project registry (path → project_id mapping)

<project>/.temper/                  # Project-level data (fully isolated per project)
├── modules/
│   ├── _index.yaml                 # Module list + multi-dimension classification
│   └── <module>.yaml               # Module definition (paths, tags, entry points)
├── interfaces/
│   └── <module>.json               # Auto-generated API surface per module
├── knowledge.db                    # SQLite: causal chains + experiences + temporal history + embeddings
├── graph.json                      # AST code graph cache (auto-refreshed via git diff on-demand)
└── meta.json                       # Scan metadata
```

### Multi-Project Isolation

Each project's data lives entirely within its own `<project>/.temper/` directory. Projects never share data.

Global `~/.temper/` only stores:
- **config.yaml**: embedding API keys, default settings, optional central server URL — shared across all projects
- **projects.json**: registry mapping project paths to IDs, so `temper` CLI knows which projects are initialized

### Deployment Modes

Temper supports two modes, with a smooth migration path from local to central:

```
Mode 1 — Local (default):
  All data in <project>/.temper/
  Zero network dependency, fully self-contained

Mode 2 — Central (team, future):
  Central Temper Server holds the shared knowledge base
  Local .temper/ becomes a cache + branch delta
  temper sync push/pull to synchronize
  Enable: temper config set server.url https://temper.internal:8080
```

The Rust implementation uses a `KnowledgeStore` trait abstraction — Phase 1 implements `LocalStorage` (SQLite), future phases add `RemoteStorage` (HTTP API) and `CachedRemoteStorage` (HTTP + local cache) without changing MCP tools or CLI commands.

See `Forge_Memory_Implementation_Plan.md` §九 for the full central mode design.

### SQLite Schema (knowledge.db)

```sql
-- Knowledge entries (current state)
CREATE TABLE knowledge (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,              -- decision/bug/constraint/experience/causal
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  module TEXT,                     -- anchored to module
  file TEXT,                       -- anchored to file
  function TEXT,                   -- anchored to function
  tags TEXT,                       -- JSON array
  status TEXT NOT NULL DEFAULT 'active',  -- active/stale/validated/expired
  current_version INTEGER NOT NULL DEFAULT 1,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

-- Causal relations (graph edges between knowledge/code entities)
CREATE TABLE causal_relations (
  id TEXT PRIMARY KEY,
  from_entity TEXT NOT NULL,       -- knowledge ID or code entity
  to_entity TEXT NOT NULL,
  relation_type TEXT NOT NULL,     -- triggers/causes/affects/constrains/depends_on
  description TEXT,
  confidence TEXT DEFAULT 'suspected',  -- validated/suspected/stale
  created_at INTEGER NOT NULL
);

-- Experience records (structured symptom→cause→fix)
CREATE TABLE experiences (
  id TEXT PRIMARY KEY,
  module TEXT,
  symptom TEXT NOT NULL,
  cause TEXT NOT NULL,
  fix TEXT NOT NULL,
  constraint_note TEXT,            -- "改这里必须同时..."
  status TEXT NOT NULL DEFAULT 'active',
  git_commit TEXT,
  created_at INTEGER NOT NULL
);

-- Temporal history (full version tracking for knowledge + experiences)
CREATE TABLE history (
  entity_id TEXT NOT NULL,         -- knowledge ID or experience ID
  entity_type TEXT NOT NULL,       -- 'knowledge' or 'experience'
  version INTEGER NOT NULL,
  status TEXT NOT NULL,
  content TEXT NOT NULL,           -- full snapshot at this version
  git_commit TEXT,
  changed_by TEXT,                 -- user/smith/git-hook/rescan
  reason TEXT,                     -- why this change happened
  timestamp INTEGER NOT NULL,
  PRIMARY KEY (entity_id, version)
);

-- Embeddings (optional, for semantic search)
CREATE TABLE embeddings (
  entity_id TEXT PRIMARY KEY,      -- knowledge ID or experience ID
  entity_type TEXT NOT NULL,
  embedding BLOB,                  -- vector via sqlite-vec
  model TEXT,                      -- which model generated this
  created_at INTEGER NOT NULL
);
```

## Multi-level Dimensions

Module dimensions support recursive nesting for large projects:

```yaml
dimensions:
  by-deployment:
    - name: backend
      children:
        - name: masterloader
          children:
            - name: plugin
              modules: [plugin/radius, plugin/ldap]
        - name: web-server
          children:
            - name: rest
              modules: [rest/user, rest/host]
  by-function:
    - name: authentication
      modules: [plugin/radius, plugin/ldap, api/authenticate]
```

Query: `list_modules(dimension="by-deployment", group="backend/web-server")` → returns only web-server subtree.

## Graph Real-time Refresh

Graph is **auto-refreshed on-demand** via git diff — no file watcher, no git hook:

```
Each MCP tool call:
  1. Throttle check (skip if < 3s since last check)
  2. git status --porcelain (~10-20ms even on 8000+ files)
  3. Changed files < 50 → incremental tree-sitter re-parse (~50-200ms)
  4. Changed files >= 50 → mark stale, suggest rescan
```

Total overhead: **< 300ms per session**. Works with uncommitted changes.

## Proactive Constraint Injection

`get_file_context` and `get_module` **automatically inject constraints** into their responses. Claude sees constraints while reading code context — no need to manually call `recall` or `get_constraints`.

```
## EntityCache.java (module: ingestion)
### Imports (2) ...
### Exports (3) ...
### ⚠️ CONSTRAINTS (from project memory)
- Do NOT cache entity objects, only cache entity IDs
  Caching full objects caused stale state during state machine progression.
```

## Rescan Behavior

- **Scan is independent of module config**. Module definitions are labels on scan results, not inputs to scanning.
- **Incremental scan**: only re-parse files changed since last scan (via git diff).
- **Module dimensions**: rescan respects user edits. Only new/deleted modules trigger incremental inference. User-modified classifications are preserved.
- **New module suggestions**: if rescan detects new package structures, it suggests (not auto-applies) for user confirmation.
- **Stale detection**: when source files change, related knowledge entries are automatically marked as `stale`. Knowledge history records the state transition.

## Documents

| File | Contents |
|------|----------|
| `Forge_Strategy_Research_2026.docx` | Market research, competitive analysis, positioning, referenced papers |
| `Forge_Memory_Layer_Design.docx` | Detailed architecture design (three-layer memory, causal chains) |
| `Forge_Memory_Implementation_Plan.md` | Full implementation plan with phases and MCP tool specs |
| `prototype/` | TypeScript prototype code (working, reference for Rust rewrite) |

## Prototype Reference

The `prototype/` directory contains a working TypeScript implementation:
- `code-graph.ts` — AST parsing with TypeScript compiler, camelCase search, incremental update
- `memory-mcp-server.ts` — Standalone MCP server (stdio) with 6 tools
- `graph-worker.ts` — Worker thread for async scanning
- `graph-server.cjs` + `graph.html` — Debug visualization page (vis.js)

These should be rewritten in Rust but the logic and storage format should be kept compatible.
