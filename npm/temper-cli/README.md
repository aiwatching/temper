# temper-cli

Forged memory for your code — persistent memory and code understanding for AI coding agents.

## Install

```bash
npm install -g temper-cli
```

## Quick Start

```bash
cd your-project
temper init          # Scan project + suggest modules
temper serve         # Start MCP server (Claude Code calls this automatically)
```

## Configure with Claude Code

```bash
claude mcp add temper -- temper serve .
```

## Configure with Forge

Add to your project's `.forge/mcp.json`:

```json
{
  "mcpServers": {
    "temper": {
      "command": "temper",
      "args": ["serve", "."]
    }
  }
}
```

## What it does

Temper gives AI coding agents persistent memory:

- **Code Graph**: tree-sitter AST analysis with incremental updates
- **Module Registry**: define module boundaries, auto-suggest on init
- **Knowledge Store**: constraints, design decisions, causal chains, experiences
- **17 MCP Tools**: search_code, get_module, remember, recall, find_causal_chain, etc.

## CLI Commands

```
temper init       Scan project + suggest modules
temper serve      Start MCP server (stdio)
temper scan       Rescan code graph
temper modules    List modules
temper search     Search code
temper knowledge  List knowledge entries
temper history    View temporal history
temper status     Project overview
temper export     Export HTML dashboard
```

## Philosophy

Precise persistent memory, not simulated human memory. What's recorded is exact — no decay, no forgetting. Knowledge can only be marked stale or expired, never automatically deleted.
