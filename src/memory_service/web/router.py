"""Server-rendered admin page.

Phase 1.x ships a static management surface — single Jinja2 page that
calls the same /v1/* JSON endpoints via fetch(). No auth gating yet (added
in Phase 1.2 once /v1/auth exists). Mounted at `/admin`.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

router = APIRouter()


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"title": "Memory Service"})
