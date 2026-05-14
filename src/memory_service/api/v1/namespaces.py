"""/v1/namespaces — list namespaces the caller can read.

Used by the graph viewer to populate its chip palette (including agent
sub-namespaces and orgs/groups the user belongs to) without making the
client hit /v1/orgs + /v1/groups + API-key listing separately.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core.namespaces import readable_namespaces_for

router = APIRouter(tags=["namespaces"])


class NamespaceEntry(BaseModel):
    raw: str
    kind: Literal["user", "agent", "group", "org", "public"]
    value: str


class NamespacesResponse(BaseModel):
    namespaces: list[NamespaceEntry]


@router.get("/namespaces", response_model=NamespacesResponse)
async def list_readable_namespaces(
    user: CurrentUser, db: DBDep,
) -> NamespacesResponse:
    """Every namespace the caller can read, in a stable order:

      1. user:<self>           — your flat user namespace
      2. agent:<self>/<slug>×N — your per-agent sub-namespaces (one per
                                 agent_slug ever attached to one of your keys)
      3. group:<slug>×N        — groups you belong to
      4. org:<slug>            — your org (if any)
      5. public                — always last
    """
    ns_list = await readable_namespaces_for(user, db)
    # readable_namespaces_for() already de-dupes implicitly because the
    # underlying queries are distinct; we just preserve its order.
    return NamespacesResponse(
        namespaces=[
            NamespaceEntry(raw=n.raw, kind=n.kind, value=n.value)
            for n in ns_list
        ]
    )
