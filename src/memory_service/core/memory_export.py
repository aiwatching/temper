"""build_bundle + apply_bundle — per-user memory export/import.

Powers /v1/me/export and /v1/me/import. Two top-level functions:

  build_bundle(user, db)               -> MemoryBundleV1
  apply_bundle(user, bundle, db, ...)  -> ImportReport

The export is a straight read of the user's blocks + documents +
episodes. Episode original content lives in graphiti (FalkorDB), not
postgres, so we round-trip through `get_episode` for each one.

The import is best-effort idempotent. In `merge` mode (default) it
upserts each row; in `replace` mode it first wipes the user's
existing rows from postgres. **Replace does not wipe FalkorDB** —
that needs a per-namespace `drop_namespace_graph` call which is
heavy enough to leave to the operator (see /v1/admin/...).

Namespace remap: when source user X exports and target user Y imports,
references like `user:X` / `agent:X/<slug>` get rewritten to
`user:Y` / `agent:Y/<slug>`. Group / org / public namespaces are
NOT remapped — they refer to membership identifiers, not user IDs,
and the importing user either has access on the target or doesn't.
"""
from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.core import blocks as block_ops
from memory_service.core import documents as doc_ops
from memory_service.core.memory import (
    WriteRequest,
    add_episode,
    get_episode,
)
from memory_service.models import EpisodeMetadata, MemoryBlock, User
from memory_service.models.document import Document, DocumentRevision
from memory_service.models.memory_block import GLOBAL_AGENT_SLUG
from memory_service.schemas.memory_export import (
    BundleSource,
    ExportedBlock,
    ExportedDocument,
    ExportedEpisode,
    ImportError as BundleImportError,
    ImportReport,
    KindReport,
    MemoryBundleV1,
)

_logger = logging.getLogger(__name__)


# ---------- export ----------


async def build_bundle(
    user: User, db: AsyncSession, *, include_episodes: bool = True,
) -> MemoryBundleV1:
    """Read everything owned by `user` and pack into a bundle.

    The user's own permissions don't gate this — they own the data.
    Caller (the API layer) is responsible for deciding whether to
    expose this to anyone other than the user themselves.

    `include_episodes=False` skips the episode pass entirely. Episode
    export reads each episode's content back from FalkorDB one node at
    a time (single-worker graph DB), so it's the expensive part — the
    daily auto-snapshot omits it and captures only the precisely-
    restorable blocks + documents.
    """

    blocks = await _export_blocks(user, db)
    documents = await _export_documents(user, db)
    episodes = await _export_episodes(user, db) if include_episodes else []

    return MemoryBundleV1(
        exported_at=datetime.now(UTC),
        source=BundleSource(
            user_id=user.id,
            user_email=user.email,
        ),
        blocks=blocks,
        documents=documents,
        episodes=episodes,
    )


async def _export_blocks(user: User, db: AsyncSession) -> list[ExportedBlock]:
    # Pull every block this user owns, across every agent_slug.
    # We don't go through list_blocks() because that applies the
    # caller's agent_slug shadow rules — for export we want the raw
    # truth: one row in postgres = one ExportedBlock in the bundle.
    stmt = select(MemoryBlock).where(MemoryBlock.user_id == user.id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        ExportedBlock(
            agent_slug=b.agent_slug,
            key=b.block_key,
            value=b.block_value,
            pinned=b.pinned,
            priority=b.priority,
            description=b.description,
            created_at=b.created_at,
            updated_at=b.updated_at,
        )
        for b in rows
    ]


async def _export_documents(user: User, db: AsyncSession) -> list[ExportedDocument]:
    stmt = select(Document).where(Document.user_id == user.id)
    rows = (await db.execute(stmt)).scalars().all()
    return [
        ExportedDocument(
            namespace=d.namespace,
            path=d.path,
            title=d.title,
            content=d.content,
            content_type=d.content_type,
            source=d.source,
            source_url=d.source_url,
            imported_at=d.imported_at,
            frontmatter=d.frontmatter or {},
            tags=list(d.tags or []),
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in rows
    ]


async def _export_episodes(user: User, db: AsyncSession) -> list[ExportedEpisode]:
    stmt = select(EpisodeMetadata).where(
        EpisodeMetadata.created_by_user_id == user.id
    )
    metas = (await db.execute(stmt)).scalars().all()

    out: list[ExportedEpisode] = []
    for meta in metas:
        # Fetch original content from graphiti. get_episode has a
        # built-in perm check that's a no-op here (the user owns the
        # episode by definition). It also handles the
        # extraction_status=pending case by returning content=None.
        try:
            ep = await get_episode(user, meta.id, db)
        except Exception as exc:
            # Don't let one missing graphiti episode tank the whole
            # export — skip it with a warning and keep going.
            _logger.warning(
                "skipping episode %s in export: %s", meta.id, exc,
            )
            continue
        content = ep.get("content")
        if not content:
            # Async-write that never finished extracting → nothing
            # useful to round-trip. Skip silently.
            continue
        out.append(ExportedEpisode(
            original_episode_id=meta.id,
            namespace=meta.namespace,
            content=content,
            source_type=meta.source_type,
            source_description="",  # not stored on the meta row
            tags=list(meta.tags or []),
            reference_time=meta.reference_time,
            created_by_agent=meta.created_by_agent,
            created_at=meta.created_at,
        ))
    return out


# ---------- import ----------


async def apply_bundle(
    user: User,
    bundle: MemoryBundleV1,
    db: AsyncSession,
    *,
    mode: str = "merge",
    background_extraction: bool = False,
) -> ImportReport:
    """Write a bundle into `user`'s namespace.

    mode='merge' (default): upsert. Existing rows with the same key /
                            path / agent_slug are overwritten; rows
                            not in the bundle are left alone.
    mode='replace':         wipe user's blocks + documents +
                            episode_metadata first, THEN merge. Does
                            NOT touch FalkorDB graph.

    background_extraction=True: episode writes return immediately and
                                graphiti extraction happens in the
                                background. Trades correctness-at-
                                response-time for fast import. Each
                                episode still costs LLM tokens — just
                                not synchronously.
    """
    if mode not in ("merge", "replace"):
        raise ValueError(f"unknown import mode: {mode!r}")
    if bundle.format_version != "1":
        raise ValueError(
            f"unsupported bundle format_version: {bundle.format_version!r}; "
            "this build only knows '1'"
        )

    started_at = time.monotonic()
    report = ImportReport(mode=mode, skip_extraction=background_extraction)

    if mode == "replace":
        await _clear_user_data(user, db)

    src_user_id = bundle.source.user_id

    await _import_blocks(user, bundle, db, report)
    await _import_documents(user, bundle, db, report, src_user_id)
    await _import_episodes(user, bundle, db, report, src_user_id, background_extraction)

    report.duration_seconds = round(time.monotonic() - started_at, 3)
    return report


async def _clear_user_data(user: User, db: AsyncSession) -> None:
    """Replace-mode prelude. Postgres only — FalkorDB graph is left
    in place (operator's choice to wipe via drop_namespace_graph).

    Order matters: revisions before documents (FK), then everything
    else."""
    # DocumentRevision points at Document; cascade is set up but we
    # delete explicitly to make intent obvious.
    await db.execute(
        delete(DocumentRevision)
        .where(DocumentRevision.document_id.in_(
            select(Document.id).where(Document.user_id == user.id)
        ))
    )
    await db.execute(delete(Document).where(Document.user_id == user.id))
    await db.execute(delete(MemoryBlock).where(MemoryBlock.user_id == user.id))
    await db.execute(
        delete(EpisodeMetadata).where(
            EpisodeMetadata.created_by_user_id == user.id
        )
    )
    await db.commit()


async def _import_blocks(
    user: User,
    bundle: MemoryBundleV1,
    db: AsyncSession,
    report: ImportReport,
) -> None:
    for b in bundle.blocks:
        try:
            # upsert_block's scope= is "own" / "global". We pass the
            # agent_slug verbatim so global blocks ('*') stay global
            # and per-agent blocks land under the right slug.
            scope = "global" if b.agent_slug == GLOBAL_AGENT_SLUG else "own"
            agent_slug = None if b.agent_slug == GLOBAL_AGENT_SLUG else b.agent_slug
            await block_ops.upsert_block(
                user, db, b.key, b.value,
                scope=scope,
                pinned=b.pinned,
                priority=b.priority,
                description=b.description,
                agent_slug=agent_slug,
                updated_by="import",
            )
            report.blocks.inserted += 1
        except Exception as exc:
            report.errors.append(BundleImportError(
                kind="block", target=b.key, error=str(exc),
            ))
            report.blocks.errored += 1


async def _import_documents(
    user: User,
    bundle: MemoryBundleV1,
    db: AsyncSession,
    report: ImportReport,
    src_user_id: str | None,
) -> None:
    for d in bundle.documents:
        try:
            ns = _remap_namespace(d.namespace, src_user_id, user.id)
            await doc_ops.upsert(
                user, db, d.path,
                title=d.title,
                content=d.content,
                content_type=d.content_type,
                source=d.source,
                source_url=d.source_url,
                imported_at=d.imported_at,
                frontmatter=d.frontmatter,
                tags=d.tags,
                namespace=ns,
                updated_by="import",
                reason="imported from bundle",
            )
            report.documents.inserted += 1
        except Exception as exc:
            report.errors.append(BundleImportError(
                kind="document", target=d.path, error=str(exc),
            ))
            report.documents.errored += 1


async def _import_episodes(
    user: User,
    bundle: MemoryBundleV1,
    db: AsyncSession,
    report: ImportReport,
    src_user_id: str | None,
    background_extraction: bool,
) -> None:
    for i, e in enumerate(bundle.episodes):
        try:
            ns = _remap_namespace(e.namespace, src_user_id, user.id)
            await add_episode(
                user=user,
                agent_name=e.created_by_agent or "imported",
                req=WriteRequest(
                    namespace=ns,
                    content=e.content,
                    source_type=e.source_type,
                    source_description=e.source_description or "imported",
                    reference_time=e.reference_time,
                    tags=e.tags or None,
                ),
                db=db,
                async_extract=background_extraction,
            )
            report.episodes.inserted += 1
        except Exception as exc:
            report.errors.append(BundleImportError(
                kind="episode",
                target=f"episode #{i}" + (
                    f" ({e.original_episode_id})"
                    if e.original_episode_id else ""
                ),
                error=str(exc),
            ))
            report.episodes.errored += 1


def _remap_namespace(
    raw_ns: str, src_user_id: str | None, dst_user_id: str,
) -> str:
    """Rewrite user-scoped namespaces from source user_id to dest.

    `user:<src>`           -> `user:<dst>`
    `agent:<src>/<slug>`   -> `agent:<dst>/<slug>`
    everything else        -> unchanged

    Returns the raw namespace string ready to pass back to
    `resolve()` on the API side."""
    if not src_user_id or src_user_id == dst_user_id:
        return raw_ns
    if raw_ns == f"user:{src_user_id}":
        return f"user:{dst_user_id}"
    agent_prefix = f"agent:{src_user_id}/"
    if raw_ns.startswith(agent_prefix):
        return f"agent:{dst_user_id}/{raw_ns[len(agent_prefix):]}"
    return raw_ns
