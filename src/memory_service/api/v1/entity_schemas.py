"""/v1/schemas/entity-types — per-namespace entity schema CRUD.

Each schema constrains entity extraction for that namespace: when the
LLM extracts a Customer, it has to match the registered Customer fields
(email, signup_date, etc.) instead of inventing arbitrary attributes.

Requires WRITE permission on the target namespace — adding a schema is
a config change that affects all future extractions there.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import schemas as schema_core
from memory_service.core.namespaces import (
    NamespaceError,
    can_read,
    can_write,
    resolve,
)
from memory_service.models import EntitySchema

router = APIRouter(prefix="/schemas/entity-types", tags=["schemas"])


# ---- Pydantic shapes ---------------------------------------------------


FieldType = Literal["string", "integer", "number", "boolean", "datetime"]


class SchemaField(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    type: FieldType
    description: str | None = None
    required: bool = False


class SchemaIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=1024)
    fields: list[SchemaField] = Field(default_factory=list, max_length=32)


class SchemaOut(BaseModel):
    id: str
    namespace: str
    name: str
    description: str | None
    fields: list[SchemaField]
    created_at: datetime


# ---- helpers -----------------------------------------------------------


def _to_out(row: EntitySchema) -> SchemaOut:
    return SchemaOut(
        id=row.id,
        namespace=row.namespace,
        name=row.name,
        description=row.description,
        fields=[SchemaField(**f) for f in (row.fields_json or [])],
        created_at=row.created_at,
    )


async def _resolve_ns(raw: str | None, user) -> str:  # type: ignore[no-untyped-def]
    try:
        return resolve(raw, user).raw
    except NamespaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---- routes ------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED, response_model=SchemaOut)
async def register_schema(
    payload: SchemaIn,
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
) -> SchemaOut:
    ns_raw = await _resolve_ns(namespace, user)
    ns = resolve(namespace, user)
    if not await can_write(user, ns, db):
        raise HTTPException(
            status_code=403,
            detail=f"Need write permission on namespace '{ns_raw}' to register schemas",
        )

    # Validate field definitions up front so callers get a clean 400
    # instead of a 500 later during build_pydantic_model.
    for f in payload.fields:
        try:
            schema_core.validate_field_def(f.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Upsert by (namespace, name).
    existing = (
        await db.execute(
            select(EntitySchema).where(
                EntitySchema.namespace == ns_raw, EntitySchema.name == payload.name
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.description = payload.description
        existing.fields_json = [f.model_dump() for f in payload.fields]
        await db.commit()
        await db.refresh(existing)
        return _to_out(existing)

    row = EntitySchema(
        namespace=ns_raw,
        name=payload.name,
        description=payload.description,
        fields_json=[f.model_dump() for f in payload.fields],
        created_by_user_id=user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return _to_out(row)


@router.get("", response_model=list[SchemaOut])
async def list_schemas(
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
) -> list[SchemaOut]:
    ns_raw = await _resolve_ns(namespace, user)
    ns = resolve(namespace, user)
    if not await can_read(user, ns, db):
        raise HTTPException(status_code=403, detail=f"Cannot read '{ns_raw}'")
    rows = list(
        (
            await db.execute(
                select(EntitySchema)
                .where(EntitySchema.namespace == ns_raw)
                .order_by(EntitySchema.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_out(r) for r in rows]


@router.get("/{name}", response_model=SchemaOut)
async def get_schema(
    name: str,
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
) -> SchemaOut:
    ns_raw = await _resolve_ns(namespace, user)
    ns = resolve(namespace, user)
    if not await can_read(user, ns, db):
        raise HTTPException(status_code=403, detail=f"Cannot read '{ns_raw}'")
    row = (
        await db.execute(
            select(EntitySchema).where(
                EntitySchema.namespace == ns_raw, EntitySchema.name == name
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Schema {name!r} not found")
    return _to_out(row)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schema(
    name: str,
    user: CurrentUser,
    db: DBDep,
    namespace: Annotated[str | None, Query()] = None,
) -> None:
    ns_raw = await _resolve_ns(namespace, user)
    ns = resolve(namespace, user)
    if not await can_write(user, ns, db):
        raise HTTPException(
            status_code=403,
            detail=f"Need write permission on '{ns_raw}' to delete schemas",
        )
    row = (
        await db.execute(
            select(EntitySchema).where(
                EntitySchema.namespace == ns_raw, EntitySchema.name == name
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Schema {name!r} not found")
    await db.delete(row)
    await db.commit()
