# FortiOS Experience AI — MCP servers

Source: https://fos-exp-ai.corp.fortinet.com/mcp (corp-internal,
captured 2026-05-14 via user paste because the host was VPN-flaky).

Wire your agent into Fortinet tools. A small set of Model Context
Protocol servers, published to our internal JFrog registry, let
coding agents read and act on Mantis, GitLab, and PMDB using your
Fortinet SSO.

## What ships today

| Package | Purpose | Highlights |
|---|---|---|
| **`@fortios-exp-ai/mantis-mcp`** | Mantis bug tracker | Read and triage bugs end-to-end: pull an issue and its history, comment, assign owners, move tickets through the workflow without leaving the agent's loop. |
| **`@fortios-exp-ai/gitlab-mcp`** | Internal GitLab | Browse issues and merge requests, read diffs, post review comments, check pipeline / job status across the corp GitLab. |
| **`@fortios-exp-ai/pmdb-mcp`** | Product Management Database | Read projects and specs, comment on specs, look up release outlooks, create or update specs. |
| **`@fortios-exp-ai/shared-mcp-auth`** | Shared SSO helper + setup wizard | Runtime dependency of the three above. Handles Fortinet SSO once across servers and installs the `fos-exp-ai-mcp-setup` wizard on PATH. |

## Requirements

- Node.js 22 (LTS) or newer.
- A JFrog API token (User Profile → Identity Tokens in the JFrog UI).

## Install in three steps

### 1. Configure the JFrog registry

Add to `~/.npmrc`:

```ini
@fortios-exp-ai:registry=https://jfrog.corp.fortinet.com/artifactory/api/npm/npm-releases/
//jfrog.corp.fortinet.com/artifactory/api/npm/npm-releases/:_authToken=YOUR_JFROG_API_TOKEN
```

### 2. Install servers + auth helper

```bash
npm install -g \
  @fortios-exp-ai/mantis-mcp \
  @fortios-exp-ai/gitlab-mcp \
  @fortios-exp-ai/pmdb-mcp \
  @fortios-exp-ai/shared-mcp-auth
```

### 3. Run the setup wizard

```bash
fos-exp-ai-mcp-setup
```

Interactive: walks SSO, server selection, transport mode (stdio vs
shared HTTP), client config. Safe to re-run; merges into existing
config.

On first tool call each session: approve the FortiAuthenticator push
on your phone. TOTP works as a fallback.

## What the wizard configures

- Fortinet SSO username + password (saved at `~/.ftnt-mcp/password`, mode 600).
- Which servers to enable (multi-select, defaults to all available).
- Transport mode:
  - **stdio** — one process per agent session, the default for almost everyone.
  - **HTTP** — shared server, useful when running many agents at once.
- Per-server env vars (base URLs, project filters, headless toggle, …).
- Client platforms: OpenCode, Claude Code (`.mcp.json`), Claude Desktop.

Generated config uses `npx --prefer-online -y`, so each launch pulls
newer published versions automatically and falls back to local cache
when offline.

## Where wizard-generated configs end up

| Client | Path |
|---|---|
| OpenCode | `~/.config/opencode/opencode.json` |
| Claude Code CLI | `~/.mcp.json` (or project-level `.mcp.json`) |
| Claude Desktop (macOS) | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Desktop (Linux) | `~/.config/Claude/claude_desktop_config.json` |
| Claude Desktop (Windows) | `%APPDATA%\Claude\claude_desktop_config.json` |

## First-call sign-in

First MCP call each session triggers Fortinet SSO. Browser-automation
layer sends a FortiAuthenticator push to the registered device.
Approve it and the session is good for the rest of the day. If push
isn't available, server falls back to TOTP and prompts for a 6-digit
code.

## Troubleshooting (headings from the page; details TBD)

- "command not found: fos-exp-ai-mcp-setup"
- 401 Unauthorized from JFrog during install
- 401 / 403 from the MCP server at runtime
- First call hangs forever
- No 2FA push arrives
- Slow startup on the first call
- Offline: server fails to start

---

## What this means for Smith — open design points

1. **All three servers are stdio-launched npm packages run via `npx -y …`.**
   Smith's current `mcp-bridge.ts` only supports `stdio:///absolute/path/to/binary`
   with no args. **We need to extend the URL scheme so we can express
   `npx -y @fortios-exp-ai/mantis-mcp`** — either:
     - `stdio:npx?args=-y,@fortios-exp-ai/mantis-mcp`
     - or a JSON config file instead of env-var URLs.
   Suggest the JSON path since the wizard already produces a similar
   shape and we may want to consume / interop with it.

2. **First-call SSO + push.** First `callTool` against any of these
   servers blocks for ~30+s waiting for FortiAuthenticator approval.
   Smith's `/chat` should NOT hold an HTTP request for that whole
   window. Two options:
     - Pre-warm: probe one tool per MCP server at smith startup
       (after `mcp-bridge` connects) so the push happens before any
       user request lands.
     - Streaming `/chat` so we can emit "waiting for FortiAuthenticator
       push…" as a heartbeat.

3. **Tool list per server is dynamic.** Once connected, we get the
   `listTools()` enumeration — that's the canonical source of truth.
   Don't hand-curate; let the bridge enumerate at startup and
   re-enumerate on connection recovery.

4. **HTTP shared mode (`shared-mcp-auth`).** If we run many smith
   instances on one machine, switch to the wizard's HTTP transport so
   SSO/refresh happens once. Out of scope for MVP — stdio is fine.

5. **JFrog auth lives in `~/.npmrc`.** Smith's deploy will need that
   file populated; document the dependency in smith's README.
