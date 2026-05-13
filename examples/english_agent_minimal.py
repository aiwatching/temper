"""Minimal English-learning agent — shows how any Python agent integrates.

This is the smallest useful pattern:

  1. On each user message, write what they said into memory.
  2. Before generating a reply, ask memory for relevant context.
  3. Pass that context to your own LLM.

The agent's own LLM call is left as a stub — you plug in your favourite.
The memory side is what this file exists to demonstrate.

Usage:
    export MS_BASE_URL=http://localhost:8000
    export MS_API_KEY=mk_...                  # from /v1/users/me/api-keys
    python3 examples/english_agent_minimal.py

Dependencies: just `httpx`. No SDK — this hits the REST API directly so
you can port the same pattern to any language.
"""
from __future__ import annotations

import os
import sys
import textwrap
from datetime import UTC, datetime

import httpx

BASE = os.environ.get("MS_BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("MS_API_KEY") or sys.exit("MS_API_KEY env var is required")
AGENT_NAME = "english-agent"

_client = httpx.Client(
    base_url=BASE,
    headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
    timeout=120.0,
)


def remember(text: str, *, tags: list[str] | None = None) -> str:
    """Persist a single observation into the caller's own memory.

    Returns the new episode_id; raises on HTTP error so the agent's
    caller can decide whether to swallow or escalate.
    """
    r = _client.post(
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


def recall(query: str, *, limit: int = 5) -> list[dict]:
    """Return up to `limit` facts most relevant to `query`.

    `namespaces=` is omitted, so the service searches every namespace
    the caller can read: user:<self> + public + any groups/orgs.
    """
    r = _client.get("/v1/search", params={"query": query, "limit": limit})
    r.raise_for_status()
    return r.json()["facts"]


def build_prompt(user_message: str, facts: list[dict]) -> str:
    """Assemble the prompt your own LLM call will receive.

    The cheap-and-effective pattern: drop the recalled facts in as a
    bulleted "what we know" block, then the user's actual turn.
    """
    if facts:
        context = "\n".join(f"- {f['fact']}" for f in facts)
        context_block = textwrap.dedent(f"""\
            Background you've established with this user:
            {context}

            """)
    else:
        context_block = ""

    return textwrap.dedent(f"""\
        You are an English-learning coach. Use the background below if
        relevant — don't repeat questions already answered there.

        {context_block}User: {user_message}
        Coach:""")


def call_my_llm(prompt: str) -> str:
    """Replace this with your real LLM client.

    For this example we just echo the prompt back so you can see what
    the assembly looks like.
    """
    return f"(stub coach reply)\n--- prompt was ---\n{prompt}"


def turn(user_message: str) -> str:
    """One full conversational turn — what you'd wire into a chat UI."""
    facts = recall(user_message)
    prompt = build_prompt(user_message, facts)
    reply = call_my_llm(prompt)
    # Persist the *user's* utterance so future turns can recall it. The
    # agent's reply is also worth storing in a richer app — it's left
    # out here to keep the example minimal.
    remember(user_message, tags=["chat-turn"])
    return reply


def main() -> None:
    transcript = [
        "I just started English lessons this week.",
        "My teacher's name is Sarah, she's from Toronto.",
        "I want to practice describing my day in the past tense.",
        "What's the teacher's name again?",
    ]
    for i, line in enumerate(transcript, 1):
        print(f"\n=== turn {i} — user: {line}")
        reply = turn(line)
        print(reply)


if __name__ == "__main__":
    main()
