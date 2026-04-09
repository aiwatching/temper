# Temper — Architecture & Benchmark Results

## What is Temper

A Rust MCP server that gives AI coding agents (Claude Code, Forge) persistent memory and code understanding. Claude Code forgets everything between sessions — Temper remembers.

```
Developer asks Claude: "重构 user 模块"

Without Temper:
  Claude greps 8,657 files → 15 turns → misses constraints → breaks things

With Temper:
  Claude calls get_module("user") → gets files + interfaces + constraints + history
  Claude calls search_symptom("stuck entities") → gets exact fix from past incident
  → 11 turns → follows patterns → respects constraints
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                        Temper                            │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │  Code Graph   │  │   Module     │  │  Knowledge   │  │
│  │  (Structural) │  │  Registry    │  │   Store      │  │
│  │              │  │              │  │              │  │
│  │ tree-sitter  │  │ YAML + glob  │  │ SQLite       │  │
│  │ Java/Py/TS   │  │ dimensions   │  │ causal chain │  │
│  │ incremental  │  │ auto-suggest │  │ experiences  │  │
│  │ git refresh  │  │              │  │ temporal     │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                  │          │
│         └────────┬────────┘──────────────────┘          │
│                  │                                      │
│         ┌────────▼────────┐                             │
│         │   MCP Server    │  20 tools, JSON-RPC stdio   │
│         │   + PreToolUse  │  hook: sqlite3 → constraint │
│         │     Hook        │  injection before Edit      │
│         └─────────────────┘                             │
│                                                         │
│  Storage: <project>/.temper/                            │
│  ├── graph.bin     ← AST graph (bincode, auto-refresh) │
│  ├── graph.json    ← AST graph (JSON, human-readable)  │
│  ├── knowledge.db  ← SQLite (knowledge + history)      │
│  ├── modules/*.yaml ← module definitions               │
│  ├── interfaces/*.json ← extracted APIs                 │
│  └── meta.json                                         │
└─────────────────────────────────────────────────────────┘
```

---

## Three Memory Layers

| Layer | What | Storage | Changes |
|-------|------|---------|---------|
| **Structural** | Files, functions, classes, imports, call chains | graph.bin (bincode) | Every code change (auto-refreshed via git diff) |
| **Causal** | Why code is this way, what triggers what, constraints | knowledge.db (SQLite) | When architecture changes |
| **Experience** | Symptom→cause→fix, known bugs, lessons learned | knowledge.db (SQLite) | Only accumulates, never auto-deleted |

---

## MCP Tools (20)

### Code Understanding
| Tool | Purpose |
|------|---------|
| `search_code` | AST graph traversal, returns direct matches + impact chain |
| `get_file_context` | File imports/exports + **auto-injected constraints** |
| `get_patterns` | Code patterns & method signatures for a module (for writing new code) |
| `rescan_code` | Force full graph rebuild |

### Module Management
| Tool | Purpose |
|------|---------|
| `define_module` | Create/update module with glob patterns |
| `list_modules` | List all modules with file counts |
| `get_module` | Full context: files + interfaces + knowledge + constraints |
| `remove_module` | Remove module definition |
| `scan_module_interfaces` | Extract REST endpoints + public methods |
| `refresh_modules` | Suggest new modules from package structure |
| `validate_modules` | Check for issues (empty globs, stale interfaces) |

### Knowledge
| Tool | Purpose |
|------|---------|
| `remember` | Store knowledge with smart dedup (embedding similarity) |
| `recall` | Retrieve by keyword, module, or type |
| `forget` | Expire entry (history preserved) |
| `search_knowledge` | Semantic search (embedding-based when configured) |
| `get_constraints` | All constraints for a module |

### Causal & Experience
| Tool | Purpose |
|------|---------|
| `add_causal_relation` | Record A→triggers→B relationships |
| `find_causal_chain` | BFS traversal of causal graph |
| `record_experience` | Structured symptom→cause→fix |
| `search_symptom` | Find past incidents by symptom |

### Auto-extraction (Mem0-inspired)
| Tool | Purpose |
|------|---------|
| `auto_extract` | LLM extracts constraints/decisions/causal from conversation context |

---

## Key Technical Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Language | Rust | tree-sitter native, single binary distribution |
| AST | tree-sitter (Java, Python, TS/JS) | 300+ languages, incremental parsing |
| Storage | SQLite + bincode | Zero external deps, single-file DB |
| MCP | JSON-RPC over stdio | Claude Code native protocol |
| Graph refresh | git status on-demand (3s throttle) | No file watcher overhead on large projects |
| Constraint delivery | PreToolUse hook (sqlite3 direct) | 0.6s, no temper serve startup |
| Module matching | Glob patterns | Flexible, ripgrep-style |
| Dedup | Embedding similarity > 0.85 (optional) | Smart merge when API key configured |
| Distribution | npm (per-platform binary) | `npm install -g @aion0/temper` |

---

## Benchmark Results

### Test Setup
- **Project**: FortiNAC — 8,657 Java files, 84,906 functions (also 12,090 with Python/TS)
- **Model**: Claude Opus 4.6 via Claude Code CLI
- **Method**: Same prompt, with vs without Temper, 4 rounds

### Overall (v2, most reliable round)

```
                Without Temper    With Temper     Delta
Turns           83               76              -8%
Output tokens   29,694           27,683          -7%
Cost            $3.91            $3.75           -4%
```

### By Scenario

#### Problem Localization — Temper's strongest value

**M7: "Entities stuck in LABELED state, what's wrong?"**

| Round | Without | With Temper | Speedup |
|-------|---------|-------------|---------|
| v1 | $0.91, 257s | $0.38, 75s | **3.4x** |
| v2 | $0.80, 216s | $0.73, 191s | 1.1x |
| v3 | $0.69, 1163s | $0.67, 169s | **6.9x** |
| v4 | $0.77, 186s | $0.50, 105s | **1.8x** |

Why it works: `search_symptom("stuck LABELED")` returns the exact experience record (symptom→cause→fix). Claude doesn't need to search 8,657 files.

#### New Feature Implementation — Variable (LLM randomness)

**M8: "Add a DeviceTypeChangedHandler following existing patterns"**

| Round | Without | With Temper |
|-------|---------|-------------|
| v1 | 9t, $0.36, 35s | 9t, $0.42, 64s |
| v2 | 16t, $0.44, 70s | 13t, $0.42, 79s |
| v3 | 24t, $0.64, **1990s** | 11t, $0.43, 84s |
| v4 | 19t, $0.53, 96s | 17t, $0.73, 192s |

High variance because Claude sometimes calls Temper tools (fast), sometimes greps directly (random speed).

#### Constraint Awareness — Temper provides info, can't enforce

**M3: "Cache full Entity objects in EntityCache"** (should refuse — stale state risk)
- Both with/without: Claude ignores constraint and does it
- PreToolUse hook injects constraint, but Claude still proceeds

**Key learning**: Temper is an **information provider**, not a behavior blocker. Claude decides whether to follow constraints.

### Cost Distribution (v3, 8 tests)

| Test Won By | Count | Explanation |
|-------------|-------|-------------|
| With Temper cheaper | 5/8 | Less searching, direct answers |
| Without cheaper | 3/8 | Cold start overhead, MCP tool call cost |

---

## What Temper Is Good At

1. **Locating known problems** — `search_symptom` gives exact symptom→cause→fix from team history
2. **Understanding module context** — `get_module` gives complete picture in one call vs 15 rounds of grep
3. **Stabilizing search** — Without Temper, M7 time ranged 186s-1163s. With Temper: 75s-191s (much less variance)
4. **Preserving team knowledge** — Constraints, decisions, causal chains survive across sessions

## What Temper Is NOT

1. **Not a behavior enforcer** — Can't stop Claude from making bad changes
2. **Not a code generator** — Doesn't write code, provides context for better decisions
3. **Not RAG over code** — Stores knowledge *about* code, not code itself
4. **Not a replacement for reading code** — Claude still reads files, Temper helps it know *which* files matter

---

## How Temper Learns

**Temper does NOT auto-learn from interactions.** All knowledge is explicitly stored:

```
Human decides:   "这个约束很重要，记下来"
Claude calls:    remember("不能在 DAO 加缓存", ...)
Temper stores:   → knowledge.db, with temporal history
```

### Why not auto-learn?

Claude edits hundreds of files per session. 99% are routine changes not worth remembering. The 1% that matter — constraints, architectural decisions, production incidents — only the developer knows which is which.

**Automatic extraction produces noise. Manual curation produces precision.**

Temper has an `auto_extract` tool (LLM-based) that can extract facts from conversation context, but it still requires Claude to explicitly call it. It's an assistance tool, not a background learner.

### What triggers knowledge updates

| Trigger | Action | Who initiates |
|---------|--------|--------------|
| Developer tells Claude a constraint | Claude calls `remember` | Human → Claude → Temper |
| Bug is debugged and fixed | Claude calls `record_experience` | Human → Claude → Temper |
| Architecture decision is made | Claude calls `remember` type=decision | Human → Claude → Temper |
| Code file is changed | `mark_stale` flags anchored knowledge | Automatic (git diff) |
| `temper upgrade` is run | Graph rescanned, stale entries flagged | Human |

### What Temper DOES do automatically

- **Graph refresh**: Detects code changes via `git status`, incrementally re-parses affected files
- **Stale detection**: When a file changes, knowledge anchored to that file is marked `stale` (needs human review)
- **Constraint injection**: PreToolUse hook auto-injects relevant constraints before Claude edits a file

### Design philosophy

```
Mem0 approach:   Auto-extract everything → deduplicate → hope quality is ok
Temper approach:  Human curates what matters → store precisely → never lose it
```

A project with 20 precise constraints beats 2,000 auto-extracted noisy facts.

---

## Distribution

```bash
# Install
npm install -g @aion0/temper

# Initialize project
cd your-project
temper init

# Configure with Claude Code
claude mcp add temper -- temper serve .

# PreToolUse hook (auto-inject constraints before Edit)
# Add to .claude/settings.json:
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Edit",
      "hooks": [{"type": "command", "command": ".claude/hooks/temper-pre-edit.sh"}]
    }]
  }
}
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Core | Rust |
| AST Parsing | tree-sitter (Java, Python, TypeScript, JavaScript) |
| Knowledge DB | SQLite (rusqlite) |
| Graph Storage | bincode (binary) + JSON (human-readable) |
| MCP Protocol | JSON-RPC over stdio |
| CLI | clap |
| Module Matching | globset |
| Git Integration | git2 + git status --porcelain |
| Embedding | External API (OpenAI/Voyage/Ollama) — optional |
| Distribution | npm per-platform binary (@aion0/temper) |
| CI/CD | GitHub Actions (3 platforms) |
