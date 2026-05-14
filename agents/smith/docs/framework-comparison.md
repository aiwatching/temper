# Agent framework — comparison for Smith

Pending decision before we replace the placeholder in `smith.agent`.
Our constraints are concrete:

- LLM tool-use loop driving a long-running process.
- **Heavy MCP** — most company internal systems already speak it.
- **TEMPER** for memory; nothing else owns long-term state.
- Hand-roll-friendly team — we don't want a framework whose magic we
  can't read in an afternoon.

## Honest preface

The user mentioned **`pi-agent`** as a starting point. I (Claude) don't
recognise that name in the public ecosystem. If it's an internal
framework, please share the repo / wiki link before we decide —
adopting it makes huge sense if (a) your company already maintains it
and (b) it's MCP-aware. The rest of this doc covers the public-
ecosystem alternatives in case `pi-agent` doesn't fit or doesn't exist.

## Candidates

### A. Hand-roll a minimal loop

What it is: ~150 lines of Python around `anthropic` or `openai` SDK.
Pseudocode:

```python
tools = [memory_write_spec, memory_search_spec, *mcp_tool_specs]
messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": msg}]
while True:
    rsp = client.messages.create(messages, tools=tools)
    if rsp.stop_reason == "end_turn":
        return rsp.content[-1].text
    for tu in rsp.content:
        if tu.type == "tool_use":
            result = await dispatch(tu.name, tu.input)
            messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": tu.id, "content": result}]})
```

- **Pros**: every line is yours; framework upgrade churn = zero; trivial
  to interop with anything; smallest possible surface to debug.
- **Cons**: you write everything yourself — streaming, parallel tool
  calls, retries, traces, MCP plumbing. Not a lot, but it's not
  *nothing*.
- **MCP fit**: bring your own. The official `mcp` Python SDK gives you
  `Client` + `list_tools()` + `call_tool()` — adapting that into our
  tool spec is ~30 lines.

### B. Anthropic Claude Agent SDK (`claude-agent-sdk` / `anthropic-claude-code` core)

What it is: Anthropic's official, MCP-native agent framework. Same
core that powers Claude Code.

- **Pros**: best-in-class MCP support (Anthropic basically maintains
  MCP); built-in tool loop with streaming + parallel calls + auto
  retries; first-class Claude features (extended thinking, prompt
  caching). Stable.
- **Cons**: tied to Anthropic models. If your company is mixed
  (e.g. some Deepseek / OpenAI / Azure), you'd want a thin layer
  for non-Anthropic providers anyway.
- **MCP fit**: ⭐ native. Point it at an MCP server URL, tools show
  up. Probably the lowest-friction option for the MCP-everywhere story.
- **Verdict**: the strongest match for our constraints **if you're
  OK standardising on Claude**.

### C. OpenAI Agents SDK (`openai-agents`)

What it is: OpenAI's official agent loop with handoffs, traces, and
guardrails. ([Repo](https://github.com/openai/openai-agents-python).)

- **Pros**: clean API; traces work out of the box; `Agent`/`Runner`/
  `Handoff` primitives compose well; multiple-LLM support via
  third-party "model adapters."
- **Cons**: OpenAI-first; MCP is supported but feels grafted on
  compared to Anthropic. If your LLM is Claude, you're paying for
  an adapter layer you don't need.
- **MCP fit**: ✓ supported via `MCPServer` config but less native.
- **Verdict**: pick if your company's primary LLM is OpenAI.

### D. Pydantic AI

What it is: type-safe agent framework from the pydantic folks. Multi-
provider out of the box (OpenAI, Anthropic, Gemini, Groq, Ollama,
many more).

- **Pros**: provider-agnostic from day one — easy to swap LLM in/out;
  pydantic-native (good fit since TEMPER is also pydantic-heavy);
  smaller surface than LangGraph; type checking actually helps catch
  tool-spec drift.
- **Cons**: smaller community than the big two; MCP support is solid
  but the docs are sparser than Anthropic's.
- **MCP fit**: ✓ has `MCPServerStdio` / `MCPServerHTTP` adapters.
- **Verdict**: strong choice if you want to keep LLM swappable and
  type-safety matters more than the absolute newest Claude features.

### E. LangGraph

What it is: graph-based orchestration on top of LangChain.

- **Pros**: very flexible for multi-step / branching agents; nice
  observability with LangSmith; mature MCP integration.
- **Cons**: significant cognitive load; graph DSL is a real concept to
  learn; LangChain dependency surface is wide.
- **Verdict**: probably overkill for an MVP. Revisit when Smith grows
  beyond a single chat loop into multi-step workflows.

## Recommendation matrix

| Priority for Smith                  | Pick     |
|-------------------------------------|----------|
| Smallest surface area               | A        |
| MCP-native + Claude-only            | B        |
| Provider-agnostic + type-safe       | D        |
| OpenAI-only company                 | C        |
| Future-proof for multi-step graphs  | E        |

## My lean

Given the user's stated context (heavy MCP, mixed reality on LLM
provider since TEMPER itself does provider switching, "hand-roll
friendly"), the call is between **A** and **D**:

- **A (hand-roll)** if we treat Smith's tool loop as a learning
  exercise and want every line in our control. Lowest dependency
  churn over a year.
- **D (Pydantic AI)** if we want to ship faster and keep LLM
  swappable. The pydantic alignment with TEMPER is a real ergonomic
  win.

If `pi-agent` is an internal company framework and it's MCP-aware,
that probably beats both — please share what it is.

## Decision

(Fill in after discussion.)
