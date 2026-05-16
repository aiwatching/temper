"""Document operations — CRUD + search + wikilink parsing.

Mirrors core/blocks.py / core/memory.py shape (raise-typed-error,
caller maps to HTTP status). All operations are user-scoped through
the namespace check.

Read patterns:
  - get_by_path(user, ns, path)        single doc
  - list(user, ns, prefix?, tags?)     index for tree view + filters
  - search(user, q, mode='fts')        FTS or vector (vector lands later)
  - backlinks_of(user, ns, path)       reverse lookup

Write patterns:
  - upsert(user, ns, path, payload)    full replace; writes revision
                                       row + reparses wikilinks
  - patch(user, ns, path, patch)       partial; supports append /
                                       prepend / replace verbs
  - delete(user, ns, path)             cascades to links + revisions
                                       (FK ON DELETE CASCADE)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    and_,
    desc,
    func,
    or_,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.core.namespaces import (
    Namespace,
    NamespaceError,
    can_read,
    can_write,
    readable_namespaces_for,
    resolve,
)
from memory_service.models import Document, DocumentLink, DocumentRevision, User

_logger = logging.getLogger(__name__)


class DocumentError(Exception):
    http_status: int = 500


class DocumentNotFoundError(DocumentError):
    http_status = 404


class DocumentBadRequestError(DocumentError):
    http_status = 400


class DocumentPermissionError(DocumentError):
    http_status = 403


# ---------- output shapes (decoupled from FastAPI Pydantic) ----------


@dataclass
class DocumentOut:
    id: str
    user_id: str
    namespace: str
    path: str
    title: str
    content: str
    content_type: str
    source: str | None
    source_url: str | None
    imported_at: datetime | None
    frontmatter: dict[str, Any]
    tags: list[str]
    word_count: int
    created_at: datetime
    updated_at: datetime
    updated_by: str | None


@dataclass
class DocumentSummary:
    id: str
    namespace: str
    path: str
    title: str
    content_type: str
    source: str | None
    source_url: str | None
    tags: list[str]
    word_count: int
    snippet: str | None
    updated_at: datetime


def _to_out(d: Document) -> DocumentOut:
    return DocumentOut(
        id=str(d.id),
        user_id=str(d.user_id),
        namespace=d.namespace,
        path=d.path,
        title=d.title,
        content=d.content,
        content_type=d.content_type,
        source=d.source,
        source_url=d.source_url,
        imported_at=d.imported_at,
        frontmatter=dict(d.frontmatter or {}),
        tags=list(d.tags or []),
        word_count=d.word_count,
        created_at=d.created_at,
        updated_at=d.updated_at,
        updated_by=d.updated_by,
    )


def _to_summary(d: Document, snippet: str | None = None) -> DocumentSummary:
    return DocumentSummary(
        id=str(d.id),
        namespace=d.namespace,
        path=d.path,
        title=d.title,
        content_type=d.content_type,
        source=d.source,
        source_url=d.source_url,
        tags=list(d.tags or []),
        word_count=d.word_count,
        snippet=snippet,
        updated_at=d.updated_at,
    )


# ---------- wikilink parsing ----------
#
# `[[target]]` and `[[target|label]]` forms supported. Targets may
# include a namespace prefix `[[group:engineering/sops/onboarding]]`
# to disambiguate cross-namespace; bare paths inherit the source's
# namespace at read time.

_WIKILINK_RE = re.compile(
    r"\[\[(?P<target>[^\]\|]+?)(?:\|(?P<label>[^\]]+))?\]\]"
)


@dataclass
class ParsedLink:
    target_path: str
    target_namespace: str | None
    label: str | None


def parse_wikilinks(content: str) -> list[ParsedLink]:
    """Extract every `[[wikilink]]` from the content.

    Targets may have a namespace prefix: `[[group:foo/bar]]` →
    target_namespace="group:foo/bar" — wait, the colon is what
    separates kind from value, but our namespace strings also use
    "/" inside the value. We adopt the convention: a prefix is only
    a namespace if it matches a known kind ("user:", "agent:",
    "group:", "public") — otherwise treat the whole thing as a
    bare path.

    Deduplicated within a single content (each (path, ns, label)
    appears once).
    """
    out: list[ParsedLink] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for m in _WIKILINK_RE.finditer(content):
        raw_target = (m.group("target") or "").strip()
        label = (m.group("label") or "").strip() or None
        if not raw_target:
            continue
        ns: str | None = None
        path = raw_target
        # Namespace prefix detection. Cheap heuristic — full
        # resolution against the namespaces module would over-couple.
        for prefix in ("user:", "agent:", "group:", "public"):
            if raw_target.startswith(prefix):
                head, _, rest = raw_target.partition("/")
                # "user:me" alone has no /; treat the whole thing
                # as namespace + empty path → skip.
                if rest:
                    ns = head
                    path = rest
                break
        key = (path, ns, label)
        if key in seen:
            continue
        seen.add(key)
        out.append(ParsedLink(target_path=path, target_namespace=ns, label=label))
    return out


# ---------- namespace resolution ----------


def _resolve_ns(raw: str | None, user: User) -> Namespace:
    """Standard TEMPER namespace resolution with documents' default:
    when no explicit value, use the caller's default scope (api-key
    agent slug if any, otherwise user:<id>)."""
    try:
        return resolve(raw, user)
    except NamespaceError as exc:
        raise DocumentBadRequestError(str(exc)) from exc


# ---------- CRUD ----------


async def get_by_path(
    user: User,
    db: AsyncSession,
    namespace: str | None,
    path: str,
) -> DocumentOut | None:
    ns = _resolve_ns(namespace, user)
    if not await can_read(user, ns, db):
        raise DocumentPermissionError(f"no read access to {ns.raw}")

    stmt = select(Document).where(
        Document.user_id == user.id,
        Document.namespace == ns.raw,
        Document.path == path,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    return _to_out(row) if row else None


async def list_documents(
    user: User,
    db: AsyncSession,
    namespace: str | None = None,
    prefix: str | None = None,
    tags: list[str] | None = None,
    source: str | None = None,
    limit: int = 100,
    before: datetime | None = None,
) -> tuple[list[DocumentSummary], str | None]:
    """Newest-first list. `before` is a cursor on updated_at."""
    if namespace:
        ns_filter: list[str] = [_resolve_ns(namespace, user).raw]
    else:
        # Default: all namespaces the caller can read. Documents
        # under group: and public: are visible if the user is a
        # member; that's what readable_namespaces_for already enforces.
        nss = await readable_namespaces_for(user, db)
        ns_filter = [n.raw for n in nss]

    if not ns_filter:
        return [], None

    stmt = (
        select(Document)
        .where(Document.user_id == user.id, Document.namespace.in_(ns_filter))
        .order_by(desc(Document.updated_at))
        .limit(limit + 1)
    )
    if prefix:
        stmt = stmt.where(Document.path.like(f"{prefix}%"))
    if source:
        stmt = stmt.where(Document.source == source)
    if before:
        stmt = stmt.where(Document.updated_at < before)
    if tags:
        # ALL tags must match — `&&` is ANY, `<@` is contained-by.
        # We want the row's tags to contain every supplied tag, so
        # use `@>` (contains).
        stmt = stmt.where(Document.tags.contains(tags))

    rows = (await db.execute(stmt)).scalars().all()
    next_cursor: str | None = None
    if len(rows) > limit:
        next_cursor = rows[limit - 1].updated_at.isoformat()
        rows = rows[:limit]
    return [_to_summary(r) for r in rows], next_cursor


async def upsert(
    user: User,
    db: AsyncSession,
    path: str,
    *,
    title: str,
    content: str,
    content_type: str = "markdown",
    source: str | None = None,
    source_url: str | None = None,
    imported_at: datetime | None = None,
    frontmatter: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    namespace: str | None = None,
    updated_by: str | None = None,
    reason: str | None = None,
) -> DocumentOut:
    if not path.strip():
        raise DocumentBadRequestError("path is required")
    if not title.strip():
        raise DocumentBadRequestError("title is required")

    ns = _resolve_ns(namespace, user)
    if not await can_write(user, ns, db):
        raise DocumentPermissionError(f"no write access to {ns.raw}")

    stmt = select(Document).where(
        Document.user_id == user.id,
        Document.namespace == ns.raw,
        Document.path == path,
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing is None:
        doc = Document(
            user_id=user.id,
            namespace=ns.raw,
            path=path,
            title=title,
            content=content,
            content_type=content_type,
            source=source,
            source_url=source_url,
            imported_at=imported_at,
            frontmatter=frontmatter or {},
            tags=tags or [],
            updated_by=updated_by,
        )
        db.add(doc)
    else:
        # Snapshot the prior version BEFORE we overwrite. Done
        # synchronously in the same transaction so a crash
        # mid-update leaves both rows or neither.
        db.add(DocumentRevision(
            document_id=existing.id,
            title=existing.title,
            content=existing.content,
            frontmatter=existing.frontmatter,
            revised_at=existing.updated_at,
            revised_by=existing.updated_by,
            reason=reason,
        ))
        existing.title = title
        existing.content = content
        existing.content_type = content_type
        if source is not None:
            existing.source = source
        if source_url is not None:
            existing.source_url = source_url
        if imported_at is not None:
            existing.imported_at = imported_at
        if frontmatter is not None:
            existing.frontmatter = frontmatter
        if tags is not None:
            existing.tags = tags
        if updated_by is not None:
            existing.updated_by = updated_by
        doc = existing

    await db.flush()
    await _refresh_links(db, doc)
    await db.commit()
    await db.refresh(doc)
    return _to_out(doc)


async def patch(
    user: User,
    db: AsyncSession,
    path: str,
    *,
    namespace: str | None = None,
    title: str | None = None,
    content: str | None = None,
    content_type: str | None = None,
    source: str | None = None,
    source_url: str | None = None,
    imported_at: datetime | None = None,
    frontmatter: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    append: str | None = None,
    prepend: str | None = None,
    replace: dict[str, str] | None = None,
    updated_by: str | None = None,
    reason: str | None = None,
) -> DocumentOut:
    ns = _resolve_ns(namespace, user)
    if not await can_write(user, ns, db):
        raise DocumentPermissionError(f"no write access to {ns.raw}")

    stmt = select(Document).where(
        Document.user_id == user.id,
        Document.namespace == ns.raw,
        Document.path == path,
    )
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if doc is None:
        raise DocumentNotFoundError(f"document {ns.raw}/{path} not found")

    # Snapshot before mutating.
    db.add(DocumentRevision(
        document_id=doc.id,
        title=doc.title,
        content=doc.content,
        frontmatter=doc.frontmatter,
        revised_at=doc.updated_at,
        revised_by=doc.updated_by,
        reason=reason,
    ))

    # Effective content. Explicit `content` wins over verb-based edits.
    if content is not None:
        doc.content = content
    else:
        body = doc.content
        if replace and isinstance(replace, dict):
            find = replace.get("find") or ""
            rep = replace.get("replace") or ""
            if find:
                body = body.replace(find, rep, 1)
        if append is not None:
            body = (body.rstrip() + "\n\n" + append) if body else append
        if prepend is not None:
            body = (prepend + "\n\n" + body.lstrip()) if body else prepend
        doc.content = body

    if title is not None:
        doc.title = title
    if content_type is not None:
        doc.content_type = content_type
    if source is not None:
        doc.source = source
    if source_url is not None:
        doc.source_url = source_url
    if imported_at is not None:
        doc.imported_at = imported_at
    if frontmatter is not None:
        doc.frontmatter = frontmatter
    if tags is not None:
        doc.tags = tags
    if updated_by is not None:
        doc.updated_by = updated_by

    await db.flush()
    await _refresh_links(db, doc)
    await db.commit()
    await db.refresh(doc)
    return _to_out(doc)


async def delete(
    user: User,
    db: AsyncSession,
    namespace: str | None,
    path: str,
) -> bool:
    ns = _resolve_ns(namespace, user)
    if not await can_write(user, ns, db):
        raise DocumentPermissionError(f"no write access to {ns.raw}")
    stmt = select(Document).where(
        Document.user_id == user.id,
        Document.namespace == ns.raw,
        Document.path == path,
    )
    doc = (await db.execute(stmt)).scalar_one_or_none()
    if doc is None:
        return False
    await db.delete(doc)   # cascades to links + revisions via FK
    await db.commit()
    return True


# ---------- wikilink maintenance ----------


async def _refresh_links(db: AsyncSession, doc: Document) -> None:
    """Replace all rows in document_links for this source. Called on
    every upsert / patch after the content settles."""
    # Wipe existing.
    from sqlalchemy import delete as sa_delete
    await db.execute(
        sa_delete(DocumentLink).where(DocumentLink.source_document_id == doc.id)
    )
    # Insert parsed.
    links = parse_wikilinks(doc.content)
    for link in links:
        db.add(DocumentLink(
            source_document_id=doc.id,
            target_path=link.target_path,
            target_namespace=link.target_namespace,
            label=link.label,
            created_at=datetime.now(UTC),
        ))
    await db.flush()


# ---------- backlinks ----------


@dataclass
class Backlink:
    source_namespace: str
    source_path: str
    source_title: str
    label: str | None


async def backlinks_of(
    user: User,
    db: AsyncSession,
    namespace: str | None,
    path: str,
) -> list[Backlink]:
    ns = _resolve_ns(namespace, user)
    if not await can_read(user, ns, db):
        raise DocumentPermissionError(f"no read access to {ns.raw}")
    # A link with target_namespace IS NULL inherits the source's
    # namespace — match those when source.namespace == target.namespace.
    # A link with target_namespace set must match explicitly.
    stmt = (
        select(Document, DocumentLink.label)
        .join(DocumentLink, DocumentLink.source_document_id == Document.id)
        .where(
            DocumentLink.target_path == path,
            Document.user_id == user.id,
            or_(
                and_(
                    DocumentLink.target_namespace.is_(None),
                    Document.namespace == ns.raw,
                ),
                DocumentLink.target_namespace == ns.raw,
            ),
        )
        .order_by(Document.namespace, Document.path)
    )
    rows = (await db.execute(stmt)).all()
    return [
        Backlink(
            source_namespace=d.namespace,
            source_path=d.path,
            source_title=d.title,
            label=label,
        )
        for d, label in rows
    ]


# ---------- search ----------


async def search_fts(
    user: User,
    db: AsyncSession,
    query: str,
    namespace: str | None = None,
    limit: int = 10,
) -> list[DocumentSummary]:
    """Postgres FTS over title (weight A) + content (weight B).
    Snippet returned for the UI's "blurb" rendering."""
    if not query.strip():
        return []

    if namespace:
        ns_filter = [_resolve_ns(namespace, user).raw]
    else:
        nss = await readable_namespaces_for(user, db)
        ns_filter = [n.raw for n in nss]
    if not ns_filter:
        return []

    # Simple plainto_tsquery for now — accepts free-text. websearch_to_tsquery
    # would let users use quotes / OR; defer until we have a complaint.
    tsq = func.plainto_tsquery("simple", query)
    rank = func.ts_rank_cd(Document.content_tsv, tsq)
    snippet = func.ts_headline(
        "simple",
        Document.content,
        tsq,
        "MaxFragments=2,MinWords=8,MaxWords=24,StartSel=«,StopSel=»",
    )

    stmt = (
        select(Document, rank.label("rank"), snippet.label("snippet"))
        .where(
            Document.user_id == user.id,
            Document.namespace.in_(ns_filter),
            Document.content_tsv.op("@@")(tsq),
        )
        .order_by(desc("rank"))
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()
    out: list[DocumentSummary] = []
    for d, _rk, snip in rows:
        s = _to_summary(d, snippet=snip)
        # Tuck the rank into the snippet for inspection; callers
        # that care can re-rank.
        s.snippet = snip
        out.append(s)
    return out
