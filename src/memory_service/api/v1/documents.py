"""/v1/documents — markdown documents (notes, wiki, SOPs).

CRUD + search + backlinks + revisions + bulk import. Path-as-id
addressing: `/v1/documents/projects/auth/refactor` resolves to
path="projects/auth/refactor". FastAPI's `path` converter swallows
the slashes.

Endpoints:

    GET    /v1/documents                            list
    GET    /v1/documents/search?q=                  FTS
    POST   /v1/documents/import                     bulk
    GET    /v1/documents/{path:path}                one
    PUT    /v1/documents/{path:path}                upsert
    PATCH  /v1/documents/{path:path}                partial
    DELETE /v1/documents/{path:path}                drop
    GET    /v1/documents/{path:path}/backlinks      reverse links
    GET    /v1/documents/{path:path}/revisions      history
    GET    /v1/documents/{path:path}/revisions/{rid}  one revision
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, select

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import documents as doc_ops
from memory_service.models import DocumentRevision
from memory_service.schemas.document import (
    BacklinkResponse,
    BacklinkRow,
    DocumentListResponse,
    DocumentOut,
    DocumentSummary,
    ImportRequest,
    ImportResponse,
    PatchDocumentRequest,
    RevisionDetail,
    RevisionListResponse,
    RevisionSummary,
    SearchHit,
    SearchResponse,
    UpsertDocumentRequest,
)

router = APIRouter(prefix="/documents", tags=["documents"])


def _to_http(exc: doc_ops.DocumentError) -> HTTPException:
    return HTTPException(status_code=exc.http_status, detail=str(exc))


def _out(d: doc_ops.DocumentOut) -> DocumentOut:
    return DocumentOut(**d.__dict__)


def _sum(d: doc_ops.DocumentSummary) -> DocumentSummary:
    return DocumentSummary(**d.__dict__)


def _updated_by(user: CurrentUser) -> str:
    slug = getattr(user, "_default_agent_slug", None)
    return f"agent:{slug}" if slug else f"user:{user.email}"


# ----- list / search -----


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
    prefix: Annotated[str | None, Query()] = None,
    tags: Annotated[str | None, Query(description="Comma-separated tags (ALL must match)")] = None,
    source: Annotated[str | None, Query(description="e.g. 'mantis' / 'confluence'")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[datetime | None, Query()] = None,
) -> DocumentListResponse:
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    try:
        rows, cursor = await doc_ops.list_documents(
            user, db,
            namespace=namespace, prefix=prefix, tags=tag_list,
            source=source, limit=limit, before=before,
        )
    except doc_ops.DocumentError as exc:
        raise _to_http(exc) from exc
    return DocumentListResponse(
        documents=[_sum(r) for r in rows], next_cursor=cursor,
    )


@router.get("/search", response_model=SearchResponse)
async def search(
    user: CurrentUser,
    db: DBDep,
    q: Annotated[str, Query(min_length=1, max_length=512)],
    namespace: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> SearchResponse:
    try:
        rows = await doc_ops.search_fts(
            user, db, q, namespace=namespace, limit=limit,
        )
    except doc_ops.DocumentError as exc:
        raise _to_http(exc) from exc
    hits = [
        SearchHit(
            path=r.path, namespace=r.namespace, title=r.title,
            snippet=r.snippet or "", score=0.0,
            tags=r.tags, source=r.source, source_url=r.source_url,
        )
        for r in rows
    ]
    return SearchResponse(hits=hits, kind="fts", query=q)


@router.post("/import", response_model=ImportResponse, status_code=201)
async def bulk_import(
    payload: ImportRequest,
    user: CurrentUser,
    db: DBDep,
) -> ImportResponse:
    imported = 0
    skipped: list[str] = []
    for it in payload.items:
        try:
            await doc_ops.upsert(
                user, db, it.path,
                title=(it.title or it.path.split("/")[-1].replace("-", " ")),
                content=it.content,
                content_type=it.content_type,
                source=it.source or "import",
                source_url=it.source_url,
                imported_at=it.imported_at,
                frontmatter=it.frontmatter or {},
                tags=it.tags or [],
                namespace=payload.namespace,
                updated_by=_updated_by(user),
                reason="bulk-import",
            )
            imported += 1
        except doc_ops.DocumentError:
            skipped.append(it.path)
            # Best-effort: keep going on per-item errors so a bad
            # row doesn't tank the whole batch.
            continue
    return ImportResponse(
        imported=imported, skipped=len(skipped), skipped_paths=skipped,
    )


# ----- single-doc CRUD -----


@router.get("/{path:path}/backlinks", response_model=BacklinkResponse)
async def get_backlinks(
    path: str,
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
) -> BacklinkResponse:
    try:
        rows = await doc_ops.backlinks_of(user, db, namespace, path)
    except doc_ops.DocumentError as exc:
        raise _to_http(exc) from exc
    ns_resolved = namespace or "default"
    return BacklinkResponse(
        target_path=path,
        target_namespace=ns_resolved,
        backlinks=[
            BacklinkRow(
                source_namespace=b.source_namespace,
                source_path=b.source_path,
                source_title=b.source_title,
                label=b.label,
            )
            for b in rows
        ],
    )


@router.get("/{path:path}/revisions", response_model=RevisionListResponse)
async def list_revisions(
    path: str,
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> RevisionListResponse:
    try:
        doc = await doc_ops.get_by_path(user, db, namespace, path)
    except doc_ops.DocumentError as exc:
        raise _to_http(exc) from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    stmt = (
        select(DocumentRevision)
        .where(DocumentRevision.document_id == doc.id)
        .order_by(desc(DocumentRevision.revised_at))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return RevisionListResponse(
        revisions=[
            RevisionSummary(
                id=str(r.id),
                revised_at=r.revised_at,
                revised_by=r.revised_by,
                reason=r.reason,
                title=r.title,
            )
            for r in rows
        ]
    )


@router.get("/{path:path}/revisions/{revision_id}", response_model=RevisionDetail)
async def get_revision(
    path: str,
    revision_id: str,
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
) -> RevisionDetail:
    try:
        doc = await doc_ops.get_by_path(user, db, namespace, path)
    except doc_ops.DocumentError as exc:
        raise _to_http(exc) from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    stmt = select(DocumentRevision).where(
        DocumentRevision.id == revision_id,
        DocumentRevision.document_id == doc.id,
    )
    rev = (await db.execute(stmt)).scalar_one_or_none()
    if rev is None:
        raise HTTPException(status_code=404, detail="revision not found")
    return RevisionDetail(
        id=str(rev.id),
        revised_at=rev.revised_at,
        revised_by=rev.revised_by,
        reason=rev.reason,
        title=rev.title,
        content=rev.content,
        frontmatter=rev.frontmatter,
    )


@router.get("/{path:path}", response_model=DocumentOut)
async def get_one(
    path: str,
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
) -> DocumentOut:
    try:
        d = await doc_ops.get_by_path(user, db, namespace, path)
    except doc_ops.DocumentError as exc:
        raise _to_http(exc) from exc
    if d is None:
        raise HTTPException(status_code=404, detail="document not found")
    return _out(d)


@router.put("/{path:path}", response_model=DocumentOut)
async def upsert_doc(
    path: str,
    payload: UpsertDocumentRequest,
    user: CurrentUser,
    db: DBDep,
) -> DocumentOut:
    try:
        d = await doc_ops.upsert(
            user, db, path,
            title=payload.title,
            content=payload.content,
            content_type=payload.content_type,
            source=payload.source,
            source_url=payload.source_url,
            imported_at=payload.imported_at,
            frontmatter=payload.frontmatter or {},
            tags=payload.tags,
            namespace=payload.namespace,
            updated_by=_updated_by(user),
            reason=payload.reason,
        )
    except doc_ops.DocumentError as exc:
        raise _to_http(exc) from exc
    return _out(d)


@router.patch("/{path:path}", response_model=DocumentOut)
async def patch_doc(
    path: str,
    payload: PatchDocumentRequest,
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
) -> DocumentOut:
    try:
        d = await doc_ops.patch(
            user, db, path,
            namespace=namespace,
            title=payload.title,
            content=payload.content,
            content_type=payload.content_type,
            source=payload.source,
            source_url=payload.source_url,
            imported_at=payload.imported_at,
            frontmatter=payload.frontmatter,
            tags=payload.tags,
            append=payload.append,
            prepend=payload.prepend,
            replace=payload.replace,
            updated_by=_updated_by(user),
            reason=payload.reason,
        )
    except doc_ops.DocumentError as exc:
        raise _to_http(exc) from exc
    return _out(d)


@router.delete("/{path:path}", status_code=204)
async def delete_doc(
    path: str,
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
) -> None:
    try:
        ok = await doc_ops.delete(user, db, namespace, path)
    except doc_ops.DocumentError as exc:
        raise _to_http(exc) from exc
    if not ok:
        raise HTTPException(status_code=404, detail="document not found")
