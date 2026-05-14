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
    from memory_service.config import get_settings

    return templates.TemplateResponse(
        request, "login.html",
        {"title": "Login", "allow_self_registration": get_settings().allow_self_registration},
    )


@router.get("/admin/setup", response_class=HTMLResponse, include_in_schema=False)
async def admin_setup(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "setup.html", {"title": "Setup"})


@router.get("/admin/accept-invite", response_class=HTMLResponse, include_in_schema=False)
async def admin_accept_invite(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "accept_invite.html", {"title": "Accept invite"}
    )


@router.get("/admin/users", response_class=HTMLResponse, include_in_schema=False)
async def admin_users(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "users.html", {"title": "Users", "wide": True})


@router.get("/admin/change-password", response_class=HTMLResponse, include_in_schema=False)
async def admin_change_password(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "change_password.html", {"title": "Change password"}
    )


@router.get("/admin/me", response_class=HTMLResponse, include_in_schema=False)
async def admin_me(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "me.html", {"title": "Account"})


@router.get("/admin/episodes", response_class=HTMLResponse, include_in_schema=False)
async def admin_episodes(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "episodes.html", {"title": "Episodes"})


@router.get("/admin/graph", response_class=HTMLResponse, include_in_schema=False)
async def admin_graph(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "graph.html", {"title": "Graph"})


@router.get("/admin/search", response_class=HTMLResponse, include_in_schema=False)
async def admin_search(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "search.html", {"title": "Search", "wide": True}
    )


@router.get("/admin/sagas", response_class=HTMLResponse, include_in_schema=False)
async def admin_sagas(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "sagas.html", {"title": "Sagas"})


@router.get("/admin/schemas", response_class=HTMLResponse, include_in_schema=False)
async def admin_schemas(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "schemas.html", {"title": "Schemas"})


@router.get("/admin/communities", response_class=HTMLResponse, include_in_schema=False)
async def admin_communities(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "communities.html", {"title": "Communities"}
    )


@router.get("/admin/orgs", response_class=HTMLResponse, include_in_schema=False)
async def admin_orgs(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "orgs.html", {"title": "Orgs"})


@router.get("/admin/groups", response_class=HTMLResponse, include_in_schema=False)
async def admin_groups(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "groups.html", {"title": "Groups"})


@router.get("/admin/cypher", response_class=HTMLResponse, include_in_schema=False)
async def admin_cypher(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "cypher.html", {"title": "Cypher", "wide": True}
    )


@router.get("/admin/import", response_class=HTMLResponse, include_in_schema=False)
async def admin_import(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "import.html", {"title": "Bulk import", "wide": True}
    )
