"""/v1/graph — full node+edge dump of a namespace's FalkorDB graph.

Used by the admin graph viewer (`/admin/graph`) and any other tool that
wants to inspect the knowledge graph without writing Cypher. Read-only.
"""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import memory

router = APIRouter(prefix="/graph", tags=["graph"])


class GraphNodeOut(BaseModel):
    id: str
    kind: str
    name: str
    summary: str | None
    content: str | None


class GraphEdgeOut(BaseModel):
    source: str
    target: str
    type: str
    name: str | None = None
    fact: str | None = None


class GraphResponse(BaseModel):
    namespace: str
    nodes: list[GraphNodeOut]
    edges: list[GraphEdgeOut]


@router.get("", response_model=GraphResponse)
async def get_graph(
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[
        str | None, Query(description="user:me | user:<id> | group:<slug> | org:<slug> | public")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> GraphResponse:
    try:
        view = await memory.get_graph(user, namespace, db, limit=limit)
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    return GraphResponse(
        namespace=view.namespace,
        nodes=[
            GraphNodeOut(
                id=n.id, kind=n.kind, name=n.name, summary=n.summary, content=n.content
            )
            for n in view.nodes
        ],
        edges=[
            GraphEdgeOut(
                source=e.source, target=e.target, type=e.type, name=e.name, fact=e.fact
            )
            for e in view.edges
        ],
    )
