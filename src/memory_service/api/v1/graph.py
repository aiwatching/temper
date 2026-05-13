"""/v1/graph — full node+edge dump of a namespace's FalkorDB graph
plus a sandboxed read-only Cypher endpoint.

Used by the admin graph viewer (`/admin/graph`) and any other tool that
wants to inspect the knowledge graph without writing Cypher. Read-only.
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

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


class CypherRequest(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)
    namespace: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=10_000, ge=100, le=60_000)


class CypherResponse(BaseModel):
    namespace: str
    rows: list[dict[str, Any]]


@router.post("/cypher", response_model=CypherResponse)
async def run_cypher(
    payload: CypherRequest, user: CurrentUser, db: DBDep
) -> CypherResponse:
    """Run a read-only Cypher query against ONE namespace's graph.

    Differs from `memctl graph cypher` (which talks directly to FalkorDB)
    in that this path:
      - enforces the read-permission matrix on `namespace`,
      - clones the FalkorDB driver to the right per-namespace graph so
        the caller can't reach data outside their own namespace,
      - uses `ro_query` (writes rejected server-side),
      - enforces a TIMEOUT_MS so a runaway query can't pin a worker.

    Returns rows as `[{column: value}, ...]`. Node/edge values are
    flattened to their property dict.
    """
    try:
        rows = await memory.run_cypher(
            user, payload.namespace, payload.query, payload.params, db,
            timeout_ms=payload.timeout_ms,
        )
    except memory.MemoryError as exc:
        raise HTTPException(status_code=exc.http_status, detail=str(exc)) from exc

    # Resolve the namespace one more time so the response echoes the
    # canonical form (e.g. "user:me" → "user:<uuid>").
    from memory_service.core.namespaces import resolve

    canonical = resolve(payload.namespace, user).raw
    return CypherResponse(namespace=canonical, rows=rows)
