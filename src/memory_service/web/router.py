"""Server-rendered admin page.

Pages are static HTML; the JS in each template calls /v1/* with the
Bearer token (or no token, for /admin which is public).
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


@router.get("/admin/login", response_class=HTMLResponse, include_in_schema=False)
async def admin_login(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"title": "Login"})


@router.get("/admin/me", response_class=HTMLResponse, include_in_schema=False)
async def admin_me(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "me.html", {"title": "Account"})


@router.get("/admin/episodes", response_class=HTMLResponse, include_in_schema=False)
async def admin_episodes(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "episodes.html", {"title": "Episodes"})


@router.get("/admin/graph", response_class=HTMLResponse, include_in_schema=False)
async def admin_graph(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "graph.html", {"title": "Graph"})
