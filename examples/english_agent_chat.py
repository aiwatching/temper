"""Interactive English-learning agent backed by Memory Service + forti-k2.

This is the live counterpart to `english_agent_minimal.py`:

  - Same memory pattern (recall → prompt → reply → remember).
  - But `call_my_llm()` is real: it hits any OpenAI-compatible chat
    endpoint (the nac-ai gateway hosts forti-k2 there) so you can
    actually have a conversation.

Run as a REPL:

    export MS_BASE_URL=http://localhost:18088
    export MS_API_KEY=mk_yourkeyhere
    export LLM_BASE_URL=http://nac-ai.fortinet-us.com:7001/v1
    export LLM_API_KEY=sk-...
    export LLM_MODEL=forti-k2

    python3 examples/english_agent_chat.py

Type a sentence, hit enter. Ctrl-C exits.

Only dep: `httpx`. The file is single-file on purpose; copy + paste it
into another project as a starting skeleton.
"""
from __future__ import annotations

import os
import sys
import textwrap
from datetime import UTC, datetime

import httpx

# ---- config (env-driven) ------------------------------------------------

MS_BASE_URL = os.environ.get("MS_BASE_URL", "http://localhost:18088").rstrip("/")
MS_API_KEY = os.environ.get("MS_API_KEY") or sys.exit("MS_API_KEY env var required")

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "forti-k2")

AGENT_NAME = os.environ.get("AGENT_NAME", "english-agent")

# ---- HTTP clients --------------------------------------------------------

_ms = httpx.Client(
    base_url=MS_BASE_URL,
    headers={"X-API-Key": MS_API_KEY, "Content-Type": "application/json"},
    timeout=120.0,
)

_llm = httpx.Client(
    base_url=LLM_BASE_URL,
    headers={
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    },
    timeout=120.0,
)


# ---- Memory I/O ---------------------------------------------------------


def remember(text: str, *, tags: list[str] | None = None) -> str | None:
    """Persist a single observation. Returns episode_id or None on failure.

    Failures here shouldn't kill the conversation — we log and move on.
    """
    try:
        r = _ms.post(
            "/v1/episodes",
            json={
                "content": text,
                "source_type": "message",
                "source_description": AGENT_NAME,
                "reference_time": datetime.now(UTC).isoformat(),
                "tags": tags or [],
            },
        )
        r.raise_for_status()
        return r.json()["episode_id"]
    except httpx.HTTPError as exc:
        sys.stderr.write(f"  [memory write failed: {exc}]\n")
        return None


def recall(query: str, *, limit: int = 5) -> list[dict]:
    """Return up to `limit` relevant facts. Empty list on failure."""
    try:
        r = _ms.get("/v1/search", params={"query": query, "limit": limit})
        r.raise_for_status()
        return r.json()["facts"]
    except httpx.HTTPError as exc:
        sys.stderr.write(f"  [memory search failed: {exc}]\n")
        return []


# ---- LLM call ------------------------------------------------------------


def call_llm(messages: list[dict]) -> str:
    """OpenAI-compatible chat completion. Works against nac-ai's
    `/v1/chat/completions` for forti-k2; against vanilla OpenAI; against
    Ollama; against any other OpenAI-shaped endpoint.
    """
    r = _llm.post(
        "/chat/completions",
        json={
            "model": LLM_MODEL,
            "messages": messages,
            "temperature": 0.3,
        },
    )
    r.raise_for_status()
    body = r.json()
    return body["choices"][0]["message"]["content"]


# ---- Prompt assembly -----------------------------------------------------

SYSTEM_PROMPT = textwrap.dedent("""\
    You are a patient English-learning coach. The user is practicing.

    Coaching rules:
      - Reply briefly. One or two sentences plus a follow-up question.
      - If they make a grammar mistake, note it once gently before
        continuing the conversation.
      - Use only the background facts listed if they're relevant; never
        say "according to my memory" — just use them naturally.
      - Don't restate background that was just established this turn.
""")


def build_messages(user_message: str, facts: list[dict]) -> list[dict]:
    if facts:
        fact_lines = "\n".join(f"- {f['fact']}" for f in facts)
        bg = f"\n\nBackground you've established with this user:\n{fact_lines}"
    else:
        bg = ""
    return [
        {"role": "system", "content": SYSTEM_PROMPT + bg},
        {"role": "user", "content": user_message},
    ]


# ---- main loop -----------------------------------------------------------


def turn(user_message: str) -> str:
    facts = recall(user_message)
    if facts:
        print(f"  ↳ recalled {len(facts)} fact(s) from memory")
    messages = build_messages(user_message, facts)
    reply = call_llm(messages)
    # Persist the *user's* utterance for future turns. Reply is also worth
    # saving in a fuller agent — skipped here so writes stay obvious.
    remember(user_message, tags=["chat-turn"])
    return reply


def repl() -> None:
    print(f"English coach (memory={MS_BASE_URL}, llm={LLM_MODEL}). Ctrl-C to quit.")
    while True:
        try:
            line = input("\nyou> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            return
        if not line:
            continue
        try:
            reply = turn(line)
        except httpx.HTTPError as exc:
            print(f"  [llm call failed: {exc}]")
            continue
        print(f"\ncoach> {reply}")


if __name__ == "__main__":
    repl()
