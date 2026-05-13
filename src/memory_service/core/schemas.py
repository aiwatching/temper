"""Per-namespace entity type schemas.

Loads schemas stored in `entity_schemas` and materializes them into
Pydantic v2 BaseModel subclasses that Graphiti's `entity_types` param
accepts.

We deliberately support only a small vocabulary of field types — enough
to constrain extraction usefully, without dragging in JSON Schema's
full complexity:

  string    -> str
  integer   -> int
  number    -> float
  boolean   -> bool
  datetime  -> datetime
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, create_model
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.models import EntitySchema

_TYPE_MAP: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "datetime": datetime,
}


def supported_field_types() -> list[str]:
    return list(_TYPE_MAP.keys())


def validate_field_def(field: dict[str, Any]) -> None:
    """Sanity-check a single field definition before storing."""
    name = field.get("name")
    ftype = field.get("type")
    if not isinstance(name, str) or not name.isidentifier():
        raise ValueError(
            f"field 'name' must be a Python identifier; got {name!r}"
        )
    # Reserved names that would collide with Graphiti's EntityNode model.
    if name in {"uuid", "name", "summary", "group_id", "labels",
                "created_at", "name_embedding", "attributes"}:
        raise ValueError(
            f"field name {name!r} is reserved by Graphiti's EntityNode"
        )
    if ftype not in _TYPE_MAP:
        raise ValueError(
            f"field 'type' must be one of {supported_field_types()}; got {ftype!r}"
        )


def build_pydantic_model(
    type_name: str, description: str | None, fields: list[dict[str, Any]]
) -> type[BaseModel]:
    """Turn a stored schema record into a Pydantic v2 BaseModel class.

    `fields` are dicts shaped like:
      {"name": str, "type": one_of(_TYPE_MAP), "description": str?, "required": bool?}
    """
    field_defs: dict[str, Any] = {}
    for f in fields:
        validate_field_def(f)
        py_type = _TYPE_MAP[f["type"]]
        is_required = f.get("required", False)
        field_info = Field(
            default=... if is_required else None,
            description=f.get("description"),
        )
        if not is_required:
            py_type = py_type | None  # type: ignore[operator]
        field_defs[f["name"]] = (py_type, field_info)

    Model = create_model(type_name, __doc__=description, **field_defs)
    return Model


async def load_entity_types_for_namespace(
    namespace: str, db: AsyncSession
) -> dict[str, type[BaseModel]]:
    """Hydrate every schema registered for `namespace` into a dict that
    Graphiti's `entity_types` param consumes directly."""
    rows = list(
        (
            await db.execute(
                select(EntitySchema).where(EntitySchema.namespace == namespace)
            )
        )
        .scalars()
        .all()
    )
    return {
        row.name: build_pydantic_model(row.name, row.description, row.fields_json)
        for row in rows
    }
