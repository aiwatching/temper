# Smith — pending work

Living todo. Stuff that's been designed / discussed but not built.
Ordered roughly by "日常用得到 × 工作量小 = 越靠前".

`docs/roadmap.md` covers the phased pi-primitive rollout (Phase A
items, all done). This file is for everything that landed on the
backlog AFTER v0.5 (typed memory + scheduler + tasks UI + main/branch
chat + nav rail + timezone + start.sh).

---

## Tier 1 — quick wins, immediate utility

### plugin kinds: `http` + `builtin`

Currently `plugins/manager.ts:284` is a one-line case stub for both.

- **http**: thin `fetch()`-based plugin that exposes a tool whose
  body fires an HTTP request. Useful for "ad-hoc API I don't want to
  wrap as a full MCP server". ~3h. No sandbox concerns.
- **builtin**: wrap Smith's own typed tool packs (temper-memory,
  typed-memory, scheduled-jobs, conv-waiting) as visible "plugins"
  in `/plugins` so the UI shows them with the same enable/disable
  affordance as MCP plugins. ~2h. Pure UI layer over what already
  runs.

`shell` plugin kind is **out** until we have a sandbox design — too
much foot-gun.

### Settings tab "工具与安全" (per-tool policy editor)

Design `screen-settings.jsx` calls for it; today's `approval-store.ts`
hardcodes the dangerous regex. UI should list every registered tool
with a 3-segment toggle: `ask each time / allow this session / always
allow`. Policy persists per-tool to settings table.

This is the cleanest improvement to the approval flow — power users
who keep approving the same tool can stop being asked. ~1 day.

### Slash-command popover in the composer

`.smith/prompts/*.md` already loaded by pi. Composer should:

1. When user types `/`, popover with matching command list
2. ↑↓ to navigate, ⏎ to expand the template into the textarea
3. Slash command body can have `$1`/`$2` parameter slots; popover
   prompts for them inline before expanding

~half day. Design lives in `docs/design/src/shared.jsx` SlashPopover.

### Interrupt button in chat

Current "Agent is already processing" handler queues with
`streamingBehavior: "followUp"` (commit `a7621aa`). Need the
opposite affordance: explicit "stop this turn" button that aborts
the in-flight `session.prompt()`. UI: red X next to the streaming
indicator, server route `POST /chat/abort?conv=<id>`. ~half day.

### Back-link badge on forked replies

H2 (`179e33d`) left the back-link badge out because anchor_turn
indices in `forkedFrom` may not align with chat.jsx's React turn
array (thinking + tool turns can shift the count). Fix: give each
JSONL message a stable id (pi already does — see jsonl peek in
H1's commit message), use THAT in forkedFrom + the UI lookup
instead of an array index. ~1h once we standardize on the id.

### waiting auto-detection

Today `set_waiting` is a tool the model has to call explicitly.
Heuristic upgrade: in `after_agent_end` (or similar pi hook), if
the assistant reply contains phrases like "waiting on", "等",
"等待", "blocked on", auto-fire `set_waiting(external=<inferred>)`.
Surfaces conv in `/tasks` 等待中 column automatically. ~1-2h.

---

## Tier 2 — bigger but high-value

### Notes / Vault subsystem (Obsidian-style)

**Design discussion 2026-05-16. Decision pending on scope.**

What it is: give Smith first-class read/write access to markdown
notes, both its own scratch vault and the user's existing
Obsidian vault. Concretely: tools (note_search / note_open /
note_create / note_append / note_patch), inline `[[wikilink]]`
chips in chat with hover preview, and a `/notes` screen.

Data model: file-based markdown vaults. Notes do NOT live in
TEMPER (graphiti is bad at long-form documents). TEMPER gets only
the path + a summary as an episode so recall can surface "you
have a note about X".

Vault policy (asymmetric read vs write):
  * Smith's own vault — default `agents/smith/.data/notes/` (or
    `~/Documents/smith-notes/`, configurable). Smith reads + writes
    freely.
  * User's Obsidian vault — path from settings. Smith READS freely
    via fs (search, open). WRITE only via a separate
    `vault_write_obsidian` tool, gated by the approval flow.

Per-stage breakdown (size estimates assume one focused dev day):

  N1 (2-3h) — Read-only + chat references
    * Tools: note_search / note_open / note_recent
    * Renderer: chat assistant output containing `[[path/to/note.md]]`
      gets parsed → clickable chip → opens right-side preview
      drawer + has "open in Obsidian" button (uses obsidian://
      URL scheme)
    * Decision needed before starting: vault path config (one
      setting? both vaults always-on? auto-discover?)

  N2 (half day) — Smith writes its own vault
    * Tools: note_create / note_append (only target Smith vault)
    * Daily note template ('YYYY-MM-DD.md', schema TBD)
    * "Write a note about X" use case works

  N3 (1-2 days) — /notes screen
    * AppShell entry, file-tree on left, preview/edit on right
    * Markdown preview, inline edit, save button
    * Backlinks panel (auto-generated reverse links across vault)
    * TEMPER index integration: on note save, write a summary
      episode tagged `note-update` for recall

  N4 (half day) — Obsidian vault WRITE (gated)
    * Separate `vault_write_obsidian` tool that goes through the
      approval gate every time
    * Big warning in the modal — "this edits your real Obsidian
      vault at <path>"

  N5 — Cross-link integrations
    * Pinned blocks can reference notes:
      state.current_project = { notes: ["[[projects/foo]]"] }
    * Tasks can link to a note
    * Forks can attach to a note for context

Decisions needed before N1 starts:
  1. Vault paths — one source of truth or two? Auto-discover
     Obsidian vault from system or require setting?
  2. Smith own vault location: `agents/smith/.data/notes/` (lives
     with installation) or `~/Documents/smith-notes/` (user home,
     survives reinstall)?
  3. Daily note schema: `YYYY-MM-DD.md` flat? `Daily/YYYY-MM-DD.md`?
     Match the user's existing Obsidian setup if they have one.

### MCP screen overhaul (5 tabs per design)

`screen-mcp.jsx` design calls for: 工具 / 认证 / 配置 / 调用审计 /
健康. Today's `/plugins` is a simple list + CRUD modal.

Tabs needed:
  * 工具 — per-tool stats (call count, p50/p95 latency, policy)
  * 认证 — SSO state, 2FA prefs, registry creds
  * 配置 — transport mode + JSON cmd display + lifecycle toggle
  * 调用审计 — read `.data/audit.log`, table view, filterable
  * 健康 — 4 stats + latency histogram + recent errors

Per-tool stats need a new `tool_metrics` table. Audit log needs
parsing helper. ~2 days.

### Dashboard / Briefs real data

`/briefs` page is built (chat-style with brief strip top) but uses
MOCK_BRIEFS. Should:
  1. Scan `.smith/briefs/*.md` on startup
  2. Parse frontmatter (id, icon, title, tint, cmd, source, refresh,
     big.tool/args, sub.template)
  3. Run each brief's big.tool on schedule (`refresh: 5m`),
     compute big number + sub string, cache
  4. Brief click → expand into `/cmd` in composer

Design lives in `docs/design/src/layout-brief.jsx`. ~1 day.

### ⌘K command palette

Global keyboard shortcut → fullscreen modal with fuzzy search
across: slash commands, skills, recent conversations, MCP tool
names. ~half day if the design's `CommandPalette` JSX in
`docs/design/src/shared.jsx` is reused.

---

## Tier 3 — defer, lower urgency

### cron triggers for scheduled jobs

P9 ships interval + once. cron requires the `cron-parser` dep —
pnpm install was hanging during the P9 commit. Trivial swap once
the install cooperates.

### Footer audit bar in chat (per design)

`agent ok · last tool · token usage · pending confirms · version`
strip at bottom of /chat. Mostly cosmetic; the data exists but
isn't surfaced. ~2h.

### ConvPicker grouping (Recent / Older / Branches)

Current picker is a flat list with main pinned at top. Design
groups by 3 days (recent) / older / branches. ~1h, purely UX.

### Job tool_call + shell actions

`jobs-engine.ts` only runs `llm_prompt` actions. tool_call would
let a job directly invoke a registered tool (no LLM in the loop —
cheaper for "every hour, just run mantis__list_bugs"). shell is
the big one and needs sandbox design. Defer.

### Plugin `shell` kind

Same sandbox question. Defer.

### Fortinet SSO (OAuth)

Roadmap A4. Needs Fortinet SSO spec from the team. Not blocked on
us. Defer.

### Mobile / iPad client

Out of scope. Smith's HTTP API is the integration point; any
client (mobile, Telegram, etc.) is a separate project.

---

## Inventory — current state of `.smith/` resources (skills + prompts + briefs)

What works today and what's missing, so we don't lose track of
this corner.

### Skills (`.smith/skills/*.md`)

- **Wired** via pi's `DefaultResourceLoader` in
  `session-manager.ts:158-167`:
    additionalSkillPaths: [<cwd>/.smith/skills]
- Directory auto-created on first session if absent
- pi reads each `.md`, parses YAML frontmatter (name, description,
  triggers), and decides on-the-fly each turn whether to "open" a
  skill based on description matching the turn's intent
- Ships with `example-mantis-bug-format.md` as a reference

What's NOT done:
- No UI listing / editing — design's SkillsScreen tab in
  `docs/design/src/app-shell.jsx` is unimplemented; current /briefs
  has a placeholder "接口未上,先空着"
- No HTTP endpoint to list loaded skills (e.g. `GET /skills`)
- No npm distribution path — roadmap B6 mentions
  `@fortinet/smith-skills` but no scaffolding yet
- No frontmatter schema validation — a malformed skill silently
  fails to load
- Hot-reload status unclear — pi may or may not pick up `.md`
  changes mid-session; not verified

### Prompt templates (`.smith/prompts/*.md`)

- **Wired** identically via `additionalPromptTemplatePaths`
- User types `/<name>` in the composer, pi expands the template
  into the message
- Ships with `standup.md`

What's NOT done:
- No composer popover when typing `/` — listed in Tier 1 above
  ("Slash-command popover in the composer")
- No UI listing / editing
- Same hot-reload uncertainty as skills

### Briefs (`.smith/briefs/*.md`)

- **Path planned but NOT wired** — the directory isn't read by any
  code today
- `/briefs` page renders MOCK data from `briefs.jsx`
- Design (`docs/design/src/layout-brief.jsx`) defines the
  frontmatter schema (id/icon/title/group/tint/cmd/source/refresh/
  big.tool/sub.template) but Smith doesn't parse or run it
- Covered separately in Tier 2 "Dashboard / Briefs real data"

### Distribution (`@fortinet/smith-skills` npm package)

- Mentioned in roadmap B6
- Concept: skills + prompts + briefs ship as one npm package teams
  can `npm install` to layer corp-wide content on top of personal
  `.smith/`
- Loader needs to know to walk `node_modules/@fortinet/smith-skills/`
  alongside `.smith/`
- Not started — need someone to:
    1. Define the package shape (folder structure, peer deps, version
       pinning vs Smith version)
    2. Add npm-path discovery to the ResourceLoader call
    3. Set up the actual npm package + publish flow

### Validation + lint

- No tooling to sanity-check a new `.md` before commit:
    * Frontmatter required fields present
    * `triggers` regex valid
    * `description` not empty
    * (for briefs) `big.tool` resolves to a registered tool
- Currently failures are silent — bad skill just doesn't load
- Would be ~half day of work + a `pnpm run lint:resources` script
