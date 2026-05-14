"""Smith's local HTTP control plane.

Smaller cousin of TEMPER's FastAPI app. Exposes:

  GET  /healthz   — liveness; checks Temper reachability + LLM creds
  POST /chat      — single turn. Body: {"message": str}. Response:
                    whatever agent.run_turn() returns.

Kept deliberately small so anything that wants to drive Smith — a CLI
client, a future web UI, an IDE plugin, an iOS shortcut — can speak
plain JSON over HTTP.
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from smith.agent import run_turn
from smith.config import get_settings
from smith.temper import Temper, TemperError

app = FastAPI(title="Smith", version="0.0.1")


class ChatRequest(BaseModel):
    message: str


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    s = get_settings()
    out: dict[str, object] = {
        "status": "ok",
        "temper_base_url": s.temper_base_url,
        "llm_provider": s.llm_provider,
    }
    try:
        async with Temper() as t:
            await t.health()
            me = await t.whoami()
            out["temper_user"] = me.get("email")
    except TemperError as e:
        out["status"] = "degraded"
        out["temper_error"] = e.detail
    return out


@app.post("/chat")
async def chat(payload: ChatRequest) -> dict[str, object]:
    return await run_turn(payload.message)
