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


def _type_filter(
    *, edge_types: list[str] | None, node_labels: list[str] | None
):  # type: ignore[no-untyped-def]
    """Build SearchFilters for the relation/label string filters.

    Returns None when both inputs are empty, so we don't pay for a noop
    WHERE clause. Unlike date filters these lower to plain equality
    predicates and FalkorDB handles them correctly.
    """
    if not edge_types and not node_labels:
        return None
    from graphiti_core.search.search_filters import SearchFilters

    return SearchFilters(
        edge_types=list(edge_types) if edge_types else None,
        node_labels=list(node_labels) if node_labels else None,
    )


def _as_of_filter(as_of: datetime):  # type: ignore[no-untyped-def]
    """Build a SearchFilters that returns only facts active at `as_of`.

    Not currently used: FalkorDB compares ISO-8601 date strings at year
    granularity in WHERE clauses, so a `valid_at <= 2026-04-28` predicate
    matches every 2026-something row regardless of month. We post-filter
    in Python (see `_active_at`) until FalkorDB fixes the comparison
    semantics or we add a Neo4j driver that handles native datetimes.

    Kept here so the equivalent Cypher-side filter is one switch away.

    Active = valid_at <= as_of AND (invalid_at > as_of OR invalid_at IS NULL).

    Graphiti's shape is counter-intuitive: the **outer** list is OR'd,
    the **inner** list is AND'd.
    """
    from graphiti_core.search.search_filters import (
        ComparisonOperator,
        DateFilter,
        SearchFilters,
    )

    return SearchFilters(
        valid_at=[
            [DateFilter(date=as_of, comparison_operator=ComparisonOperator.less_than_equal)]
        ],
        invalid_at=[
            [DateFilter(date=as_of, comparison_operator=ComparisonOperator.greater_than)],
            [DateFilter(date=as_of, comparison_operator=ComparisonOperator.is_null)],
        ],
    )


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


@dataclass
class BulkWriteItem:
    content: str
    source_type: str = "text"
    source_description: str = ""
    reference_time: datetime | None = None
    tags: list[str] | None = None


@dataclass
class BulkWriteResult:
    episode_ids: list[str]
    namespace: str
    total_entities: int
    total_facts: int


async def add_episodes_bulk(
    user: User,
    agent_name: str,
    namespace: str | None,
    items: list[BulkWriteItem],
    db: AsyncSession,
) -> BulkWriteResult:
    """Write many episodes in one Graphiti pass — faster than looping
    add_episode for the same N items because entity/edge dedup happens
    once across the whole batch.

    All items land in the SAME namespace (one Graphiti group_id per
    bulk call). Permission check runs once.
    """
    if not items:
        raise InvalidRequestError("items must be non-empty")
    try:
        ns = resolve(namespace, user)
    except NamespaceError as exc:
        raise InvalidRequestError(str(exc)) from exc
    if not await can_write(user, ns, db):
        raise PermissionDeniedError(_write_denied_hint(user, ns))

    client = _require_client()
    from graphiti_core.utils.bulk_utils import RawEpisode

    now = datetime.now(UTC)
    raws: list[RawEpisode] = []
    metadatas: list[EpisodeMetadata] = []
    for item in items:
        ref_t = item.reference_time or now
        raws.append(
            RawEpisode(
                name=f"{agent_name}-{int(ref_t.timestamp() * 1000)}-{len(raws)}",
                content=item.content,
                source=_episode_type(item.source_type),
                source_description=item.source_description or agent_name,
                reference_time=ref_t,
            )
        )

    try:
        result = await client.add_episode_bulk(
            bulk_episodes=raws, group_id=ns.as_graphiti_group_id()
        )
    except Exception as exc:
        _logger.exception("add_episode_bulk failed")
        raise BackendUnavailableError(f"bulk write failed: {exc}") from exc

    episode_ids: list[str] = []
    for episodic_node, item in zip(result.episodes, items):
        episode_ids.append(episodic_node.uuid)
        metadatas.append(
            EpisodeMetadata(
                id=episodic_node.uuid,
                namespace=ns.raw,
                created_by_user_id=user.id,
                created_by_agent=agent_name,
                source_type=item.source_type,
                tags=item.tags or [],
                reference_time=item.reference_time or now,
            )
        )
    db.add_all(metadatas)
    await db.commit()

    return BulkWriteResult(
        episode_ids=episode_ids,
        namespace=ns.raw,
        total_entities=len(result.nodes),
        total_facts=len(result.edges),
    )


async def search(
    user: User,
    query: str,
    namespaces: list[str] | None,
    limit: int,
    db: AsyncSession,
    as_of: datetime | None = None,
    edge_types: list[str] | None = None,
    node_labels: list[str] | None = None,
    center_node_uuid: str | None = None,
    bfs_origin_node_uuids: list[str] | None = None,
    bfs_max_depth: int = 3,
    reranker: str | None = None,
) -> list[SearchHit]:
    """Search across the caller's readable namespaces.

    Filters / bias / traversal:
      - `as_of`: only facts active at that moment — `valid_at <= as_of` AND
        (`invalid_at IS NULL` OR `invalid_at > as_of`). Time-travel.
      - `edge_types`: only RELATES_TO edges whose `name` is in this list
        (e.g. ["LIVES_IN", "TEACHES"]). Pushed down to Graphiti.
      - `node_labels`: only entity nodes with these labels (e.g.
        ["Person", "Place"]). Applied to entity-hit search results too.
      - `center_node_uuid`: bias ranking toward facts/entities connected
        to this node ("relevant to Sarah" rather than globally relevant).
      - `bfs_origin_node_uuids` + `bfs_max_depth`: walk the graph from
        these nodes up to N hops and include any facts/entities reached.
        Complements semantic search rather than replacing it.
    """
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
    from graphiti_core.search.search_config_recipes import (
        COMBINED_HYBRID_SEARCH_CROSS_ENCODER,
        COMBINED_HYBRID_SEARCH_MMR,
        COMBINED_HYBRID_SEARCH_RRF,
    )

    # Pick the recipe — request override > settings default. cross_encoder
    # is the only one that pays for an LLM call per search; document that.
    from memory_service.config import get_settings

    chosen = reranker or get_settings().search_reranker
    recipe = {
        "rrf": COMBINED_HYBRID_SEARCH_RRF,
        "mmr": COMBINED_HYBRID_SEARCH_MMR,
        "cross_encoder": COMBINED_HYBRID_SEARCH_CROSS_ENCODER,
    }.get(chosen, COMBINED_HYBRID_SEARCH_RRF)
    config = recipe.model_copy(deep=True)
    config.limit = limit

    # `center_node_uuid` only biases the result set when the reranker
    # consults graph distance. When the caller asks for centering AND
    # didn't pick a different reranker explicitly, swap to node-distance.
    # If they DID pick (e.g. cross_encoder), respect that choice — they're
    # opting into a different ranking criterion entirely.
    if center_node_uuid is not None and reranker is None and chosen == "rrf":
        from graphiti_core.search.search_config import EdgeReranker, NodeReranker

        config.edge_config.reranker = EdgeReranker.node_distance
        if config.node_config is not None:
            config.node_config.reranker = NodeReranker.node_distance

    # BFS isn't part of the default RRF recipe's search_methods, so just
    # passing bfs_origin_node_uuids would do nothing. Enable BFS on both
    # the edge and node searches when origins are supplied, and override
    # the depth to what the caller asked for.
    if bfs_origin_node_uuids:
        from graphiti_core.search.search_config import (
            EdgeSearchMethod,
            NodeSearchMethod,
        )

        if EdgeSearchMethod.bfs not in config.edge_config.search_methods:
            config.edge_config.search_methods.append(EdgeSearchMethod.bfs)
        config.edge_config.bfs_max_depth = bfs_max_depth
        if config.node_config is not None:
            if NodeSearchMethod.bfs not in config.node_config.search_methods:
                config.node_config.search_methods.append(NodeSearchMethod.bfs)
            config.node_config.bfs_max_depth = bfs_max_depth

    # Push the type-based filters into Graphiti's SearchFilters; they
    # lower to plain string-equality Cypher predicates and work reliably.
    # Time filters (as_of) stay in Python post-processing because FalkorDB
    # compares date strings at year granularity only — see _as_of_filter.
    search_filter = _type_filter(edge_types=edge_types, node_labels=node_labels)
    over_fetch = limit * 4 if as_of is not None else limit
    config.limit = over_fetch

    try:
        results = await client.search_(
            query=query,
            config=config,
            group_ids=group_ids,
            search_filter=search_filter,
            center_node_uuid=center_node_uuid,
            bfs_origin_node_uuids=bfs_origin_node_uuids,
        )
    except Exception as exc:
        _logger.exception("Graphiti search failed")
        raise BackendUnavailableError(f"search failed: {exc}") from exc

    def _to_raw(group_id: str) -> str:
        return group_id_to_raw.get(group_id, group_id)

    edge_hits: list[SearchHit] = []
    for edge in results.edges:
        if as_of is not None and not _active_at(edge, as_of):
            continue
        edge_hits.append(
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

    # When time-travel is requested, entity-node summaries (which have no
    # validity semantics) would mix "what we believed then" with "everything
    # we ever knew about this entity" — confusingly. Drop them in that mode.
    node_hits: list[SearchHit] = []
    community_hits: list[SearchHit] = []
    if as_of is None:
        for node in results.nodes:
            text = (getattr(node, "summary", None) or node.name or "").strip()
            if not text:
                continue
            node_hits.append(
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
        for comm in getattr(results, "communities", None) or []:
            text = (getattr(comm, "summary", None) or comm.name or "").strip()
            if not text:
                continue
            community_hits.append(
                SearchHit(
                    kind="community",
                    fact=text,
                    namespace=_to_raw(comm.group_id),
                    source_episode_ids=[],
                    valid_at=None,
                    invalid_at=None,
                    score=None,
                )
            )

    # Round-robin merge across the three result streams: each kind's
    # internal rank is preserved, but no kind monopolizes the slice
    # when the caller's `limit` is smaller than the total candidate
    # count. Edges first so a single-kind query (no entities, no
    # communities yet) still degrades to "all facts."
    merged: list[SearchHit] = []
    iters = [iter(edge_hits), iter(node_hits), iter(community_hits)]
    while iters and len(merged) < limit:
        for it in iters[:]:
            try:
                merged.append(next(it))
                if len(merged) >= limit:
                    break
            except StopIteration:
                iters.remove(it)
    return merged


def _active_at(edge: Any, as_of: datetime) -> bool:
    """True iff the edge's validity window covers `as_of`."""
    valid_at = getattr(edge, "valid_at", None)
    if valid_at is None:
        # Graphiti sometimes leaves valid_at null on edges produced for
        # statements without temporal information. Treating them as
        # "always valid" prevents losing them entirely under time filters.
        pass
    elif valid_at > as_of:
        return False
    invalid_at = getattr(edge, "invalid_at", None)
    if invalid_at is not None and invalid_at <= as_of:
        return False
    return True


@dataclass
class GraphNode:
    id: str
    kind: str  # "Episodic" | "Entity" | "Community"
    name: str
    summary: str | None
    content: str | None


@dataclass
class GraphEdge:
    source: str
    target: str
    type: str  # "MENTIONS" | "RELATES_TO" | ...
    name: str | None  # only on RELATES_TO
    fact: str | None  # only on RELATES_TO


@dataclass
class GraphView:
    namespace: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]


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


async def get_graph(
    user: User,
    raw_namespace: str | None,
    db: AsyncSession,
    limit: int = 200,
) -> GraphView:
    """Return all nodes + edges in one namespace's FalkorDB graph.

    Used by the admin graph viewer and any other tool that needs the
    full graph picture. Respects the read-permission matrix; raises
    PermissionDeniedError if the caller can't read `raw_namespace`.
    """
    try:
        ns = resolve(raw_namespace, user)
    except NamespaceError as exc:
        raise InvalidRequestError(str(exc)) from exc
    if not await can_read(user, ns, db):
        raise PermissionDeniedError(
            f"User '{user.email}' has no read access to namespace '{ns.raw}'"
        )

    client = _require_client()
    driver = _driver_for_namespace(client, ns)
    if driver is None:
        return GraphView(namespace=ns.raw, nodes=[], edges=[])

    # The driver clone exposes execute_query() which underpins both
    # node-by-uuid lookups and arbitrary Cypher.
    try:
        node_records, _, _ = await driver.execute_query(
            "MATCH (n) "
            "RETURN n.uuid AS uuid, labels(n)[0] AS kind, "
            "       n.name AS name, n.summary AS summary, "
            "       n.content AS content "
            f"LIMIT {int(limit)}"
        )
        edge_records, _, _ = await driver.execute_query(
            "MATCH (a)-[r]->(b) "
            "RETURN a.uuid AS source, b.uuid AS target, type(r) AS type, "
            "       r.name AS name, r.fact AS fact "
            f"LIMIT {int(limit) * 4}"
        )
    except Exception as exc:
        _logger.exception("Graph dump failed for %s", ns.raw)
        raise BackendUnavailableError(f"graph read failed: {exc}") from exc

    nodes = [
        GraphNode(
            id=_rec_get(r, "uuid") or "",
            kind=_rec_get(r, "kind") or "Unknown",
            name=_rec_get(r, "name") or "",
            summary=_rec_get(r, "summary"),
            content=_rec_get(r, "content"),
        )
        for r in node_records
    ]
    valid_ids = {n.id for n in nodes if n.id}
    edges = [
        GraphEdge(
            source=_rec_get(r, "source") or "",
            target=_rec_get(r, "target") or "",
            type=_rec_get(r, "type") or "",
            name=_rec_get(r, "name"),
            fact=_rec_get(r, "fact"),
        )
        for r in edge_records
    ]
    # Drop dangling edges so vis-network doesn't create implicit phantom
    # nodes for endpoints we trimmed by `limit`.
    edges = [e for e in edges if e.source in valid_ids and e.target in valid_ids]
    return GraphView(namespace=ns.raw, nodes=nodes, edges=edges)


def _rec_get(rec: Any, key: str) -> Any:
    """FalkorDB/Neo4j drivers return Record-like objects; some support
    item access, some only `.get()`. Try both."""
    try:
        return rec[key]
    except Exception:
        pass
    try:
        return rec.get(key)
    except Exception:
        return None


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


async def build_communities(
    user: User, raw_namespace: str | None, db: AsyncSession
) -> dict[str, Any]:
    """Run Graphiti's clustering pass on a namespace, creating Community
    nodes that summarize related-entity neighborhoods.

    Requires write permission on the target namespace (Communities mutate
    the graph). Returns the count of created nodes + edges; the actual
    nodes show up under `kind="Community"` in subsequent /v1/graph and
    /v1/search calls.
    """
    try:
        ns = resolve(raw_namespace, user)
    except NamespaceError as exc:
        raise InvalidRequestError(str(exc)) from exc
    if not await can_write(user, ns, db):
        raise PermissionDeniedError(_write_denied_hint(user, ns))

    client = _require_client()
    encoded = ns.as_graphiti_group_id()
    # Graphiti's handle_multiple_group_ids decorator only clones the driver
    # when len(group_ids) > 1; with a single id it falls through to
    # self.driver (pinned to `default_db`, empty). Pass the cloned driver
    # explicitly so we hit the right per-namespace graph.
    driver = _driver_for_namespace(client, ns)
    try:
        nodes, edges = await client.build_communities(
            group_ids=[encoded], driver=driver
        )
    except Exception as exc:
        _logger.exception("build_communities failed for %s", ns.raw)
        raise BackendUnavailableError(f"build_communities failed: {exc}") from exc

    return {
        "namespace": ns.raw,
        "communities_created": len(nodes),
        "community_edges_created": len(edges),
    }


async def run_cypher(
    user: User,
    raw_namespace: str | None,
    query: str,
    params: dict[str, Any] | None,
    db: AsyncSession,
    *,
    timeout_ms: int = 10_000,
) -> list[dict[str, Any]]:
    """Run a *read-only* Cypher query against one namespace's graph.

    Permission: caller must have read access to `raw_namespace`. The
    driver is cloned to the per-namespace FalkorDB graph so callers
    can never reach data outside their own namespace through this API.

    Safety: passes the query through the falkordb client's `ro_query`
    which rejects writes server-side, and a TIMEOUT clause keeps a
    malformed query from monopolizing the FalkorDB worker.
    """
    try:
        ns = resolve(raw_namespace, user)
    except NamespaceError as exc:
        raise InvalidRequestError(str(exc)) from exc
    if not await can_read(user, ns, db):
        raise PermissionDeniedError(
            f"User '{user.email}' has no read access to namespace '{ns.raw}'"
        )

    encoded = ns.as_graphiti_group_id()
    settings = __import__("memory_service.config", fromlist=["get_settings"]).get_settings()
    try:
        import falkordb

        client = falkordb.FalkorDB(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            password=settings.falkordb_password,
        )
        graph = client.select_graph(encoded)
        result = graph.ro_query(query, params=params or {}, timeout=timeout_ms)
    except Exception as exc:
        _logger.exception("Cypher query failed in %s", ns.raw)
        raise InvalidRequestError(f"cypher failed: {exc}") from exc

    headers = [h[1] for h in result.header]
    rows: list[dict[str, Any]] = []
    for row in result.result_set:
        rec: dict[str, Any] = {}
        for h, v in zip(headers, row):
            if hasattr(v, "properties"):  # Node / Edge → flatten props
                rec[h] = dict(v.properties)
            else:
                rec[h] = v
        rows.append(rec)
    return rows


async def get_entity(
    user: User, entity_uuid: str, db: AsyncSession
) -> dict[str, Any] | None:
    """Find an entity node by UUID across the caller's readable namespaces.

    Returns None when no readable namespace contains it. Inside each
    namespace we clone the Graphiti driver before calling get_by_uuid so
    the per-graph routing works (same trick as get_episode).
    """
    client = _require_client()
    readable = await readable_namespaces_for(user, db)
    from graphiti_core.nodes import EntityNode

    for ns in readable:
        driver = _driver_for_namespace(client, ns)
        if driver is None:
            continue
        try:
            node = await EntityNode.get_by_uuid(driver, entity_uuid)
        except Exception:
            continue
        return {
            "id": node.uuid,
            "namespace": ns.raw,
            "name": getattr(node, "name", None),
            "summary": getattr(node, "summary", None),
            "labels": list(getattr(node, "labels", []) or []),
            "created_at": getattr(node, "created_at", None),
            "attributes": dict(getattr(node, "attributes", {}) or {}),
        }
    return None


async def get_fact(
    user: User, edge_uuid: str, db: AsyncSession
) -> dict[str, Any] | None:
    """Find an EntityEdge (RELATES_TO fact) by UUID across readable namespaces."""
    client = _require_client()
    readable = await readable_namespaces_for(user, db)
    from graphiti_core.edges import EntityEdge
    from graphiti_core.nodes import EntityNode

    for ns in readable:
        driver = _driver_for_namespace(client, ns)
        if driver is None:
            continue
        try:
            edge = await EntityEdge.get_by_uuid(driver, edge_uuid)
        except Exception:
            continue
        # Resolve endpoint names so the response is self-describing.
        source_name = target_name = None
        try:
            source = await EntityNode.get_by_uuid(driver, edge.source_node_uuid)
            source_name = source.name
        except Exception:
            pass
        try:
            target = await EntityNode.get_by_uuid(driver, edge.target_node_uuid)
            target_name = target.name
        except Exception:
            pass
        return {
            "id": edge.uuid,
            "namespace": ns.raw,
            "fact": edge.fact,
            "name": getattr(edge, "name", None),
            "source_uuid": edge.source_node_uuid,
            "target_uuid": edge.target_node_uuid,
            "source_name": source_name,
            "target_name": target_name,
            "valid_at": edge.valid_at,
            "invalid_at": edge.invalid_at,
            "created_at": getattr(edge, "created_at", None),
            "episodes": list(getattr(edge, "episodes", []) or []),
        }
    return None


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
