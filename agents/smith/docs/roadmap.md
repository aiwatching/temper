# Smith — roadmap

Two phases:

**Phase A — Complete the agent foundation.** Wire up pi's primitives
Smith hasn't touched yet. These are reusable across every use case
that follows; without them the company-specific work is glued on
half-built scaffolding.

**Phase B — Company use cases.** Once the foundation is solid, deliver
the integrations the team actually needs day-to-day. Most should ride
on top of Skills + MCP, not bespoke code per service.

The "MCP接入" task itself sits between A and B — see
`fortinet-mcp-servers.md`. Out of scope here; we'll handle it last.

---

## Phase A — Foundation (pi primitives not yet used)

### A1. Skills (markdown bundles, on-demand load)

**What pi gives:** Markdown packages under `~/.pi/agent/skills/` or
`.pi/skills/`. Each file has frontmatter (`name`, `description`,
`triggers`). The LLM decides at runtime whether to "open" a skill
based on the description matching what it needs to do.

**Why it's load-bearing for Smith:** Skills are the natural way to
ship company-internal knowledge — "how to write a Mantis ticket
template", "how to read a forti-* log", "how to summarise a CR
review". Cheaper than baking into the system prompt (token cost),
cheaper than custom tools (no extension code).

**Implementation in Smith:**
- Point `DefaultResourceLoader` at `<cwd>/.smith/skills/` instead of
  `~/.pi/agent/`. Skills become repo-local + portable.
- Document the skill frontmatter format in `README.md`.
- Add `/admin/skills` endpoint? — maybe later.

**Open question:** how teammates DISTRIBUTE skills. Options:
- Drop into a git repo, `git pull` keeps everyone in sync.
- npm package: `npm install -g @fortinet/smith-skills`. Setup wizard
  symlinks into `~/.smith/skills/`.
- Pull from a curated URL at startup, cache locally.

---

### A2. Prompt Templates (slash commands)

**What pi gives:** `~/.pi/agent/prompts/<name>.md` + frontmatter. User
types `/name` and pi expands it into the message.

**Examples for Smith:**
- `/standup` → "Summarise what I did yesterday from my git activity
  and yesterday's Mantis updates. Format as standup bullets."
- `/triage <bug-id>` → "Pull Mantis bug $1, summarise root cause +
  next action, suggest assignee."
- `/cr <mr-url>` → "Read MR $1, summarise diff + risk + reviewers
  to ping."

**Implementation in Smith:**
- Same `ResourceLoader` config as Skills — `.smith/prompts/`.
- UI: autocompleter when the textarea starts with `/`. (pi handles
  expansion server-side; UI just needs to surface available names.)

---

### A3. Compaction policy (currently default)

**What pi gives:** Auto-summarises old turns when context fills.
Default threshold is some % of model context window.

**What we should override:**
- For an enterprise agent the compaction LLM call matters — at
  forti-k2 cost it's not free, and a bad summary loses durable
  facts. Hook `before_compact` to:
  - Force critical turns (memory writes, tool decisions) into the
    keep list rather than the summary.
  - Write the COMPACTION SUMMARY as a Temper episode in `agent:me/<slug>`
    with tag `compaction` — so we can recover the lost detail by
    searching memory.

**Implementation:**
- `smith-personality.ts` adds `pi.on("session_before_compact", ...)`.
- Inspect the compaction plan; reject if it'd drop tool-call results
  unread; on confirm, post-process to write episode.

---

### A4. OAuth (Fortinet SSO via `registerProvider`)

**What pi gives:** `pi.registerProvider({ oauth: { login, refreshToken, getApiKey, ... }})`. pi handles the `/login` UX and credential refresh.

**Why important for Smith:**
- Today `LLM_API_KEY` is a plaintext env var. For deploys (Smith as
  a per-user process, or shared internal service), Fortinet SSO →
  short-lived token → auto-refresh is the right shape.
- Same pattern will plug `shared-mcp-auth` in once we get to MCP.

**Implementation:**
- Branch the existing custom-provider registration: if
  `LLM_OAUTH=fortinet`, register an `oauth` block instead of static
  `apiKey`.
- Persist tokens via `AuthStorage` at `<cwd>/.smith/auth.json`.

---

### A5. `beforeToolCall` / `afterToolCall` hooks

**What pi gives:** Per-tool gate. Return `{ block: true, reason }` to
veto a call before it runs.

**Why important for Smith:**
- Confirmation prompts for destructive tools (`gitlab__merge_mr`,
  `mantis__close_bug`, etc).
- Rate limit / quota: pi-ai cost tracking is per-model; tool side
  needs its own (e.g. don't call gitlab more than N times/min).
- Audit logging: write every tool call + result hash to Temper as
  an episode with tag `tool-audit`.

**Implementation:**
- Per-tool config: read `dangerous: true` field from tool registration.
- Smith maintains a small allowlist: tools marked `dangerous` need
  an explicit user `/approve <toolCallId>` reply before running.
- /chat UI surfaces a confirm button when an SSE event reports a
  blocked tool.

---

### A6. 对话记忆(beyond just JSONL replay)

**Today:** `<conv_id>.jsonl` resumes exact turns in the same thread.

**Gap:** No way to recall things from OTHER conversations. If user
told Smith something in conv A and a week later opens conv B, Smith
relies on Temper's auto-recall for it. Two improvements:

- **Conversation index** — Smith maintains a manifest of all conv
  files with `(id, title, first_message, last_used_at)`. Powers a
  conversation picker in the UI.
- **Auto-summary into Temper** — at session_end (or `/end` command),
  Smith summarises the conversation into 1–3 episodes and writes
  them to `agent:me/<slug>` so future Temper search picks them up.

**Implementation:**
- Manifest: `<cwd>/.data/smith-sessions/_index.json`. Update on
  session create / message_end.
- Auto-summary extension: hook `session_shutdown` (we already see
  this event but ignore it).

---

### A7. Authentication on Smith's own HTTP

**Today:** Anyone with TCP access to 18099 can POST `/chat`.

**Implementation:**
- Simple bearer token: `SMITH_SECRET` env, required on `/chat` and
  `/healthz` if set.
- Or reuse the Temper API key the user already has: Smith verifies
  by calling Temper's `/v1/auth/me` with the bearer.

---

### A8. Markdown + thinking-block rendering in chat UI

**Today:** UI shows plaintext. Code blocks like ` ```python ` render
as raw backticks. Reasoning models' thinking is dropped (we don't
even forward `thinking_delta` events).

**Implementation:**
- Inline a small Markdown renderer (`marked.js` ~30KB) for the smith
  bubble's final text.
- Add a "thinking" event in SSE; render as a collapsible block above
  the main reply.

---

## Phase B — Company use cases

Each item below answers: **what pi/Smith mechanism do we use?**

The answer is usually "MCP server (for the data plane) + Skill (for
the prompt knowledge) + Prompt Template (for the slash command)".

### B1. 会议纪要总结

**Data:** transcripts (Teams / Zoom / Webex export, or paste).

**Mechanism:**
- **Skill** `meeting-summary.md` — describes the company's standard
  meeting-notes format (sections, action-item style, tagging
  convention). On-demand load when user says "summarise this".
- **Prompt template** `/notes` — wraps the user's paste in a system
  prompt that loads the skill and outputs the canonical format.
- **memory_write**: action items written as one episode each, tagged
  `action-item`, `saga=<meeting-name>`.

**MCP only if:** we want Smith to fetch the transcript itself instead
of the user pasting (Teams / Zoom MCP server).

---

### B2. 邮件读取 (Exchange / Outlook)

**Data:** Outlook / Exchange via MCP.

**Mechanism:**
- **MCP server** that exposes `email_list / email_read / email_search /
  email_reply`. Microsoft has a Graph MCP server; or write a thin
  Fortinet-internal one (probably easier than wrestling with Graph
  permissions in production).
- **Skill** `email-triage.md` — when to archive vs reply vs forward;
  what counts as "needs human review".
- **Prompt template** `/inbox` — "summarise inbox since
  last_check, flag the ones needing action, draft replies for the
  obvious ones".
- **`beforeToolCall` confirmation** on `email_send` / `email_reply`.

---

### B3. 文档管理

**Data:** depending on the team: SharePoint, Confluence, internal
wiki, shared drives.

**Mechanism:**
- **MCP server(s)** per source (sharepoint-mcp, confluence-mcp).
- **Skill** `document-conventions.md` — what counts as canonical,
  where specs live, how to title things.
- **Prompt template** `/find <topic>`.
- Smith's auto-recall already picks up document references it has
  written about; otherwise it calls the MCP search tool.

---

### B4. Bug 系统 + 功能需求 (Mantis / PMDB)

Already covered by the `@fortios-exp-ai/mantis-mcp` + `pmdb-mcp` packages.

**Mechanism:**
- MCP servers from the FortiOS Experience AI registry (see
  `fortinet-mcp-servers.md`).
- **Skill** `mantis-conventions.md` — required fields per bug type,
  severity rubric, who to assign by component.
- **Skill** `pmdb-spec-format.md` — what a good spec looks like.
- **Prompt templates**:
  - `/bug <id>` — read + summarise
  - `/triage` — work through open bugs assigned to me
  - `/spec <area>` — read recent specs in $1, summarise progress
- **`beforeToolCall`** on `mantis__close_bug`, `mantis__assign`,
  `pmdb__update_spec`.

---

### B5. 代码库 / MR 跟踪 (GitLab)

Covered by `@fortios-exp-ai/gitlab-mcp`.

**Mechanism:**
- MCP server already provides `mr_list / mr_read / mr_comment /
  pipeline_status / job_status`.
- **Skill** `cr-conventions.md` — what to look for in a CR (test
  coverage, security review, performance), how to phrase a review
  comment.
- **Prompt template** `/cr <url>` — full CR walkthrough.
- **`beforeToolCall`** on `mr_merge`, `mr_close`, `mr_approve`.

---

## Distributable skills (B6)

The pattern emerging from B1-B5: **what's company-specific isn't
"another tool"; it's a markdown bundle with conventions**. So one
of the most leveraged things we can build is:

- **`@fortinet/smith-skills` npm package** — versioned, internal-
  published. Drops a folder of `.md` files into `~/.smith/skills/`
  on install.
- Skill examples to ship in v1:
  - `mantis-conventions.md`
  - `pmdb-spec-format.md`
  - `cr-conventions.md`
  - `log-analysis.md` (forti-* log formats, what's noise vs signal)
  - `commit-message-style.md`
  - `release-notes-format.md`
- Anyone who finds their team's pattern useful can PR a new skill.
- The skills package itself uses semver — Smith pulls the latest at
  startup if the env says so.

---

## 优先级 / 顺序

Foundation first (A) — if we build B without A, we'll rebuild B
when A lands and discover half the assumptions were wrong.

Within A, recommended order:

1. **A8 Markdown + thinking rendering** (zero behavior change, big
   UX leap)
2. **A1 Skills** (everything downstream depends on this)
3. **A2 Prompt Templates** (small, makes UX nicer immediately)
4. **A5 `beforeToolCall` hooks** (required before any destructive
   tool ships)
5. **A7 Authentication** (required before Smith leaves localhost)
6. **A6 Conversation index + auto-summary**
7. **A3 Compaction policy**
8. **A4 OAuth** (only when we deploy Smith as shared infra)

Then for B, do **B4 (Mantis/PMDB)** first since the MCP server is
already public + most of the "internal agent" daily value lives
there.

---

## Out of scope here

- MCP接入 itself — picks up in `fortinet-mcp-servers.md` and the
  `mcp-bridge.ts` extension once Phase A's `beforeToolCall` lands.
- Production hosting (multi-tenant, k8s, nginx, logs) — separate
  doc when we get there.
- Cost / token tracking dashboard.
- Mobile / Telegram client.
