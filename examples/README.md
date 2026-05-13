# Examples

Working agent-side integrations against the Memory Service v0.1 API.
All examples use the REST API directly — no SDK — so the pattern ports
to any language you care to.

## english_agent_minimal.py

The smallest useful pattern in ~100 lines of Python:

- `recall(query)` before your LLM call to fetch relevant background.
- `call_my_llm(...)` is a stub — wire your own LLM.
- `remember(text)` after the user turn to persist for future calls.

Run:

```bash
# 1. Get an API key (one-time, via the admin page or curl)
open http://localhost:8000/admin/me

# 2. Point the example at your service + key
export MS_BASE_URL=http://localhost:8000
export MS_API_KEY=mk_yourkeyhere

python3 examples/english_agent_minimal.py
```

The first run will produce stub coach replies — that's expected; the
LLM call is left for you to wire. Watch the live admin episodes page
(`/admin/episodes`) and you'll see four episodes appear and Sarah +
Toronto + past-tense show up as extracted entities/facts.

The fourth turn (`What's the teacher's name again?`) is the actual
test: `recall(...)` should pull "Sarah is the user's English teacher"
out of memory and your LLM should answer with Sarah's name.

## english_agent_chat.py

Same recall→prompt→reply→remember pattern, but the LLM call is
**real** (any OpenAI-compatible endpoint) and the loop is an
interactive REPL — so you can actually chat with it.

```bash
export MS_BASE_URL=http://localhost:8000
export MS_API_KEY=mk_yourkeyhere
export LLM_BASE_URL=http://nac-ai.fortinet-us.com:7001/v1   # forti-k2 gateway
export LLM_API_KEY=sk-yourtokenhere
export LLM_MODEL=forti-k2

python3 examples/english_agent_chat.py
```

Type a sentence, hit enter. After the first few turns you'll see
recalled facts surface in subsequent replies. Ctrl-C to quit.

Each turn:

1. `recall(user_input)` — semantic search across your readable namespaces
2. Build a chat message list with the facts as system-prompt background
3. POST `/chat/completions` to the configured LLM endpoint
4. `remember(user_input)` — async write to memory for future turns
