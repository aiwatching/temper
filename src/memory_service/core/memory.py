"""Memory operations — wraps Graphiti calls + permission checks.

This module is the only place that touches `graphiti.add_episode` /
`graphiti.search`. The API layer above only sees domain objects; the
adapter layer below only sees Graphiti-shaped data.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.adapters.graphiti_client import get_graphiti
from memory_service.core.namespaces import (
    Namespace,
    NamespaceError,
    can_read,
    can_write,
    default_namespace_for,
    parse,
    readable_namespaces_for,
    resolve,
)
from memory_service.models import EpisodeMetadata, User

_logger = logging.getLogger(__name__)


class MemoryError(Exception):
    """Base class for user-visible memory errors. Each subclass maps to an HTTP code."""

    http_status: int = 500


class PermissionDeniedError(MemoryError):
    http_status = 403


class NotFoundError(MemoryError):
    http_status = 404


class BackendUnavailableError(MemoryError):
    http_status = 503


class InvalidRequestError(MemoryError):
    http_status = 400


@dataclass
class WriteRequest:
    namespace: str
    content: str
    source_type: str = "text"  # message | text | json
    source_description: str = ""
    reference_time: datetime | None = None
    tags: list[str] | None = None


@dataclass
class ExtractedEntity:
    uuid: str
    name: str
    labels: list[str]
    summary: str | None


@dataclass
class ExtractedFact:
    uuid: str
    fact: str
    source_entity_uuid: str
    target_entity_uuid: str
    valid_at: datetime | None
    invalid_at: datetime | None


@dataclass
class WriteResult:
    episode_id: str
    namespace: str
    extracted_entities: list[ExtractedEntity]
    extracted_facts: list[ExtractedFact]
    created_at: datetime


@dataclass
class SearchHit:
    fact: str
    namespace: str
    source_episode_ids: list[str]
    valid_at: datetime | None
    invalid_at: datetime | None
    score: float | None
    # "fact" = relationship edge between entities (Graphiti's primary output).
    # "entity" = entity-node summary. We surface both because Graphiti's
    # extractor sometimes produces a rich entity summary but never derives a
    # relation from it (especially on terse one-liners), so edge-only search
    # would miss the answer that's clearly in the graph.
    kind: str = "fact"


# ---------- adapters ----------


def _require_client():  # type: ignore[no-untyped-def]
    client = get_graphiti()
    if client is None:
        raise BackendUnavailableError(
            "Graphiti is not initialised — check /v1/health for which provider is failing"
        )
    return client


def _episode_type(raw: str):  # type: ignore[no-untyped-def]
    from graphiti_core.nodes import EpisodeType

    return {
        "message": EpisodeType.message,
        "text": EpisodeType.text,
        "json": EpisodeType.json,
    }.get(raw, EpisodeType.text)


def _write_denied_hint(user: User, ns) -> str:  # type: ignore[no-untyped-def]
    """Human-readable explanation of why a write was rejected.

    Each namespace kind has its own typical fix; a one-size message would
    just send users back to the docs.
    """
    base = f"User '{user.email}' cannot write to namespace '{ns.raw}'."
    if ns.kind == "user":
        return (
            f"{base} You can only write to your own namespace "
            f"'user:{user.id}' (or just leave it blank / use 'user:me')."
        )
    if ns.kind == "group":
        return (
            f"{base} You must be a member of group '{ns.value}'. "
            "Ask a group admin to add you via "
            f"POST /v1/groups/{ns.value}/members."
        )
    if ns.kind == "org":
        return (
            f"{base} Writing to an org namespace requires org_admin role in "
            f"that org. Ask a super_admin or another org_admin to promote you "
            f"via PATCH /v1/orgs/{ns.value}/members/{{user_id}}."
        )
    if ns.kind == "public":
        return (
            f"{base} Only super_admin may write to 'public'. Pick your own "
            "user/group/org namespace instead."
        )
    return base


# ---------- public ops ----------


async def add_episode(
    user: User,
    agent_name: str,
    req: WriteRequest,
    db: AsyncSession,
) -> WriteResult:
    try:
        ns = resolve(req.namespace, user)
    except NamespaceError as exc:
        raise InvalidRequestError(str(exc)) from exc
    if not await can_write(user, ns, db):
        raise PermissionDeniedError(_write_denied_hint(user, ns))

    client = _require_client()
    reference_time = req.reference_time or datetime.now(UTC)

    try:
        result = await client.add_episode(
            name=f"{agent_name}-{int(reference_time.timestamp() * 1000)}",
            episode_body=req.content,
            source=_episode_type(req.source_type),
            source_description=req.source_description or agent_name,
            reference_time=reference_time,
            group_id=ns.as_graphiti_group_id(),
        )
    except Exception as exc:
        _logger.exception("Graphiti add_episode failed")
        raise BackendUnavailableError(f"add_episode failed: {exc}") from exc

    # Persist application-layer metadata.
    meta = EpisodeMetadata(
        id=result.episode.uuid,
        namespace=ns.raw,
        created_by_user_id=user.id,
        created_by_agent=agent_name,
        source_type=req.source_type,
        tags=req.tags or [],
        reference_time=reference_time,
    )
    db.add(meta)
    await db.commit()

    return WriteResult(
        episode_id=result.episode.uuid,
        namespace=ns.raw,
        extracted_entities=[
            ExtractedEntity(
                uuid=n.uuid,
                name=n.name,
                labels=list(n.labels) if hasattr(n, "labels") else [],
                summary=getattr(n, "summary", None),
            )
            for n in result.nodes
        ],
        extracted_facts=[
            ExtractedFact(
                uuid=e.uuid,
                fact=e.fact,
                source_entity_uuid=e.source_node_uuid,
                target_entity_uuid=e.target_node_uuid,
                valid_at=e.valid_at,
                invalid_at=e.invalid_at,
            )
            for e in result.edges
        ],
        created_at=result.episode.created_at,
    )


async def search(
    user: User,
    query: str,
    namespaces: list[str] | None,
    limit: int,
    db: AsyncSession,
) -> list[SearchHit]:
    if not query.strip():
        return []

    if namespaces:
        try:
            parsed = [resolve(n, user) for n in namespaces]
        except NamespaceError as exc:
            raise InvalidRequestError(str(exc)) from exc
        # Drop any the caller can't read.
        readable = [n for n in parsed if await can_read(user, n, db)]
        if not readable:
            raise PermissionDeniedError(
                f"User '{user.email}' has no read access to namespaces: {namespaces}"
            )
    else:
        readable = await readable_namespaces_for(user, db)

    # Build encoded→raw lookup so we can return the API-surface namespace
    # ("user:<uuid>") instead of the Graphiti-internal group_id ("user__...").
    # The encoding is lossy (slugs containing '_' can't be reversed), but every
    # group_id Graphiti returns came from a namespace we passed in here, so
    # the table covers it.
    group_id_to_raw = {n.as_graphiti_group_id(): n.raw for n in readable}
    group_ids = list(group_id_to_raw.keys())

    client = _require_client()
    # RRF recipe: no cross-encoder, no MMR — works with our noop reranker.
    from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF

    config = COMBINED_HYBRID_SEARCH_RRF.model_copy(deep=True)
    config.limit = limit
    try:
        results = await client.search_(query=query, config=config, group_ids=group_ids)
    except Exception as exc:
        _logger.exception("Graphiti search failed")
        raise BackendUnavailableError(f"search failed: {exc}") from exc

    def _to_raw(group_id: str) -> str:
        return group_id_to_raw.get(group_id, group_id)

    hits: list[SearchHit] = []
    for edge in results.edges:
        hits.append(
            SearchHit(
                kind="fact",
                fact=edge.fact,
                namespace=_to_raw(edge.group_id),
                source_episode_ids=list(edge.episodes or []),
                valid_at=edge.valid_at,
                invalid_at=edge.invalid_at,
                score=None,
            )
        )
    for node in results.nodes:
        text = (getattr(node, "summary", None) or node.name or "").strip()
        if not text:
            continue
        hits.append(
            SearchHit(
                kind="entity",
                fact=text,
                namespace=_to_raw(node.group_id),
                source_episode_ids=[],
                valid_at=None,
                invalid_at=None,
                score=None,
            )
        )
    return hits[:limit]


def _driver_for_namespace(client: Any, ns: Namespace):  # type: ignore[no-untyped-def]
    """Return a Graphiti driver bound to the FalkorDB graph backing `ns`.

    FalkorDB stores each Graphiti `group_id` as a separate graph; the
    connection-level default ("default_db") never holds real data. Any op
    that goes through `driver.execute_query` (get_by_uuid, delete, ...)
    must be issued against the per-namespace graph or it sees nothing.

    We `clone()` the existing driver so the underlying socket/auth is
    reused — only the target graph name changes.
    """
    if getattr(client, "driver", None) is None:
        return None
    return client.driver.clone(database=ns.as_graphiti_group_id())


async def get_episode(user: User, episode_id: str, db: AsyncSession) -> dict[str, Any]:
    meta = await db.get(EpisodeMetadata, episode_id)
    if meta is None:
        raise NotFoundError(f"Episode {episode_id} not found")
    ns = parse(meta.namespace)
    if not await can_read(user, ns, db):
        # Don't leak existence to unprivileged callers — 404 not 403.
        raise NotFoundError(f"Episode {episode_id} not found")

    client = _require_client()
    driver = _driver_for_namespace(client, ns)
    try:
        # Inlined Graphiti.get_nodes_and_edges_by_episode using the cloned
        # driver — the canned method is a thin wrapper that uses
        # `self.driver`, which is pinned to the wrong graph.
        from graphiti_core.edges import EntityEdge
        from graphiti_core.nodes import EpisodicNode
        from graphiti_core.search.search_utils import get_mentioned_nodes

        episode_node = await EpisodicNode.get_by_uuid(driver, episode_id)
        edge_uuids = list(getattr(episode_node, "entity_edges", []) or [])
        edges = await EntityEdge.get_by_uuids(driver, edge_uuids) if edge_uuids else []
        nodes = await get_mentioned_nodes(driver, [episode_node])
    except Exception as exc:
        _logger.exception("Graphiti get_episode failed")
        raise BackendUnavailableError(f"get_episode failed: {exc}") from exc

    return {
        "episode_id": episode_id,
        "namespace": meta.namespace,
        "created_by_user_id": meta.created_by_user_id,
        "created_by_agent": meta.created_by_agent,
        "source_type": meta.source_type,
        "tags": meta.tags or [],
        "reference_time": meta.reference_time,
        "created_at": meta.created_at,
        "content": getattr(episode_node, "content", None),
        "entities": [
            {"uuid": n.uuid, "name": n.name, "summary": getattr(n, "summary", None)}
            for n in nodes
        ],
        "facts": [
            {
                "uuid": e.uuid,
                "fact": e.fact,
                "valid_at": e.valid_at,
                "invalid_at": e.invalid_at,
            }
            for e in edges
        ],
    }


async def list_episodes(
    user: User,
    namespace: str | None,
    limit: int,
    before_cursor: datetime | None,
    db: AsyncSession,
) -> tuple[list[EpisodeMetadata], datetime | None]:
    """Returns (rows, next_cursor). Cursor is the created_at of the last row."""
    stmt = select(EpisodeMetadata).order_by(EpisodeMetadata.created_at.desc())

    if namespace:
        try:
            ns = resolve(namespace, user)
        except NamespaceError as exc:
            raise InvalidRequestError(str(exc)) from exc
        if not await can_read(user, ns, db):
            raise PermissionDeniedError(
                f"User '{user.email}' cannot read namespace '{ns.raw}'"
            )
        stmt = stmt.where(EpisodeMetadata.namespace == ns.raw)
    else:
        readable = await readable_namespaces_for(user, db)
        stmt = stmt.where(EpisodeMetadata.namespace.in_([n.raw for n in readable]))

    if before_cursor is not None:
        stmt = stmt.where(EpisodeMetadata.created_at < before_cursor)

    stmt = stmt.limit(limit + 1)
    rows = list((await db.execute(stmt)).scalars().all())
    next_cursor: datetime | None = None
    if len(rows) > limit:
        # We over-fetched by one so we know if there's a next page.
        next_cursor = rows[-1].created_at
        rows = rows[:limit]
    return rows, next_cursor


async def delete_episode(user: User, episode_id: str, db: AsyncSession) -> None:
    meta = await db.get(EpisodeMetadata, episode_id)
    if meta is None:
        raise NotFoundError(f"Episode {episode_id} not found")
    # Only the creator or super_admin can delete. (Namespace-admin support
    # arrives with Phase 1.3/1.4.)
    if not user.is_super_admin and meta.created_by_user_id != user.id:
        raise NotFoundError(f"Episode {episode_id} not found")

    client = _require_client()
    ns = parse(meta.namespace)
    driver = _driver_for_namespace(client, ns)
    try:
        from graphiti_core.nodes import EpisodicNode

        node = await EpisodicNode.get_by_uuid(driver, episode_id)
        # Graphiti exposes Node.delete() that also detaches related entities.
        await node.delete(driver)
    except Exception as exc:
        _logger.exception("Graphiti delete_episode failed")
        raise BackendUnavailableError(f"delete_episode failed: {exc}") from exc

    await db.delete(meta)
    await db.commit()
