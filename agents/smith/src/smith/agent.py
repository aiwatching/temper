"""Agent loop — PLACEHOLDER.

We're deliberately not writing the real LLM tool loop here until we pick
a framework (see docs/framework-comparison.md). The placeholder is just
enough to prove the wiring end-to-end:

  - server.py receives /chat
  - server.py calls run_turn() below
  - run_turn() pulls memory hits from TEMPER and echoes them back

When the framework lands, this file gets the real loop:
  system_prompt + tools(memory.write, memory.search, *mcp_tools)
    → LLM call → tool_use → tool result → loop → final assistant text
"""
from __future__ import annotations

from datetime import datetime

from smith.temper import Temper


async def run_turn(user_message: str) -> dict[str, object]:
    """Stub: search Temper for context, return what we'd hand the LLM.

    Replace with the real loop once the framework is chosen. Keep the
    response shape (`{reply, used_memory, ...}`) stable so the server
    contract doesn't churn while the inner loop changes.
    """
    async with Temper() as t:
        hits = await t.search(user_message, limit=5)
        # In the real loop, the LLM picks WHICH hits matter + paraphrases.
        # For now we surface them raw so we can see the integration is alive.
        preview = [
            {
                "fact": h.get("fact") or h.get("name"),
                "score": h.get("score"),
                "valid_at": h.get("valid_at"),
            }
            for h in hits
        ]
        # Record the turn — one episode per user message. The agent's own
        # writes will happen here too once the loop decides what's durable.
        await t.write(
            user_message,
            source_type="message",
            source_description="user said this in smith chat",
            reference_time=datetime.now().astimezone(),
        )
    return {
        "reply": (
            f"[placeholder] received {len(user_message)} chars. "
            f"{len(preview)} memory hits queued for the LLM loop."
        ),
        "memory_hits": preview,
    }
