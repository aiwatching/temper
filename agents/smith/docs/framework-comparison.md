# Agent framework — decision

**Decision: pi-coding-agent (TypeScript).**

Recorded 2026-05-14. Replaces an earlier draft that compared the
public agent ecosystem.

## Why pi-coding-agent

- The user's company already builds on it (**openclaw**, **harness** are
  pi-coding-agent based). Sharing the substrate means Smith inherits
  the team's hard-won bug list, extension patterns, and operational
  habits instead of re-discovering them on a parallel stack.
- MIT, self-hostable, ~50k GitHub stars, weekly releases — mature
  enough to bet on but pre-1.0 so expect breaking changes.
- Multi-LLM out of the box (`pi-ai`: Anthropic, OpenAI, DeepSeek,
  Google, Mistral, OpenRouter, local vLLM, etc.). Matches TEMPER's
  multi-provider stance.
- "Primitives, not features" — pi gives us Session / Tool / Extension
  / ResourceLoader and gets out of the way. Less framework magic to
  unlearn than LangGraph.

## Trade-offs we accepted

- **TypeScript only.** Smith's runtime can't import `memory_service`
  directly — has to go through TEMPER's HTTP API. We chose this design
  earlier (Smith as Temper client) so the language switch costs us
  nothing structural; we just rewrote ~200 lines of Python scaffold in
  TS.
- **pi has no built-in MCP.** Author's deliberate stance — see
  https://mariozechner.at/posts/2025-11-02-what-if-you-dont-need-mcp/.
  This is the biggest non-obvious thing. Since we have company-wide
  MCP infrastructure, we wrote `src/extensions/mcp-bridge.ts`: at
  startup it connects to every server in `MCP_SERVERS`, lists each
  server's tools, and registers them as pi tools (`<server>__<tool>`).
  Tools schema comes from MCP as JSON-Schema; we wrap with
  `Type.Unsafe<...>` to satisfy pi's TSchema-typed `parameters`.
- **TypeBox for tool schemas.** Different mental model from Zod / raw
  JSON-Schema. We use `Type.*` builders for first-party tools and
  `Type.Unsafe` for MCP-passthrough.
- **Sub-1.0 SDK.** pi-coding-agent version moves fast. We pin a minor
  range in `package.json` and read CHANGELOG before bumping.

## Options we passed on

Listed for posterity / future revisits.

- **Anthropic Claude Agent SDK** — strongest native MCP support but
  Claude-only. Would need a separate adapter for the team's non-Claude
  models, and we'd lose pi alignment with openclaw/harness.
- **OpenAI Agents SDK** — clean API but OpenAI-first; MCP feels
  grafted on. Pass.
- **Pydantic AI** — solid type-safety and pydantic alignment with
  TEMPER. Strong second choice if pi-coding-agent had been a dead-end.
- **LangGraph** — overkill for an MVP chat loop. Revisit if Smith
  grows into multi-step branching workflows.
- **Hand-roll** — every line yours; zero framework upgrade churn. Pass
  because pi gives us session / compaction / extension primitives we'd
  end up writing ourselves.

## Architectural shape Smith adopts from pi

Direct mapping pi concept → Smith file:

| pi concept | Smith code | Purpose |
|---|---|---|
| `Session` (`createAgentSession`) | `src/session-manager.ts` | one per conversation_id |
| `Tool` (`defineTool` / `pi.registerTool`) | `src/extensions/*.ts` | memory_* + MCP-bridged tools |
| `ExtensionContext` factories | `src/session-manager.ts` `extensionFactories` | wire tools at session create |
| `AuthStorage` + `ModelRegistry` | `src/session-manager.ts` private fields | LLM credentials |
| `getModel(provider, id)` from `pi-ai` | `src/session-manager.ts` `getOrCreate` | resolve config → Model |
| `SessionManager.inMemory()` | `src/session-manager.ts` | session repo (MVP) |
