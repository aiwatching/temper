"""Per-namespace entity type schemas.

Lets callers register Pydantic-shaped descriptions of the entity types
they care about (e.g. Customer{email, signup_date}, Project{deadline}).
At write time these get materialized into actual Pydantic models that
Graphiti uses to constrain entity extraction.

We store the schema as JSON because Python classes don't survive a
process restart, and we need them rebuildable on demand.
"""
from __future__ import annotations

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from memory_service.models._base import Base, TimestampMixin, UUIDPKMixin


class EntitySchema(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "entity_schemas"
    __table_args__ = (
        # One schema per (namespace, type name) — re-registering with the
        # same name updates in place via PATCH/PUT.
        UniqueConstraint("namespace", "name", name="uq_entity_schema_ns_name"),
    )

    namespace: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(String(1024), default=None)
    # Field definitions stored as JSON list:
    # [{"name": "email", "type": "string", "description": "...", "required": true}, ...]
    fields_json: Mapped[list] = mapped_column(JSON, default_factory=list)
    created_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), default=None
    )
