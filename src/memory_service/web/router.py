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
    return templates.TemplateResponse(
        request, "login.html", {"title": "Sign in", "bare": True},
    )


@router.get("/admin/setup", response_class=HTMLResponse, include_in_schema=False)
async def admin_setup(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "setup.html", {"title": "Initial setup", "bare": True}
    )


@router.get("/admin/accept-invite", response_class=HTMLResponse, include_in_schema=False)
async def admin_accept_invite(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "accept_invite.html", {"title": "Accept invite", "bare": True}
    )


@router.get("/admin/forgot", response_class=HTMLResponse, include_in_schema=False)
async def admin_forgot(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "forgot.html", {"title": "Forgot password", "bare": True}
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
    return templates.TemplateResponse(request, "me.html", {"title": "Account & API keys"})


@router.get("/admin/api-keys", response_class=HTMLResponse, include_in_schema=False)
async def admin_api_keys(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "api_keys_admin.html", {"title": "All API keys", "wide": True}
    )


@router.get("/admin/integrate", response_class=HTMLResponse, include_in_schema=False)
async def admin_integrate(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "integrate.html", {"title": "Connect an agent", "wide": True}
    )


@router.get("/admin/stats", response_class=HTMLResponse, include_in_schema=False)
async def admin_stats(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "stats.html", {"title": "Memory stats", "wide": True}
    )


@router.get("/admin/consolidate", response_class=HTMLResponse, include_in_schema=False)
async def admin_consolidate(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "consolidate.html", {"title": "Consolidate memory", "wide": True}
    )


@router.get("/admin/blocks", response_class=HTMLResponse, include_in_schema=False)
async def admin_blocks(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "blocks.html", {"title": "Memory blocks", "wide": True}
    )


@router.get("/admin/documents", response_class=HTMLResponse, include_in_schema=False)
async def admin_documents(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "documents.html", {"title": "Documents", "wide": True}
    )


@router.get("/admin/snapshots", response_class=HTMLResponse, include_in_schema=False)
async def admin_snapshots(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "snapshots.html", {"title": "Memory snapshots", "wide": True}
    )


@router.get("/admin/backups", response_class=HTMLResponse, include_in_schema=False)
async def admin_backups(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "backups.html", {"title": "Full backups", "wide": True}
    )


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
