"""Declarative base + small mixins shared by every model.

We use `MappedAsDataclass` so models are dataclass-y (helps with type
inference + lets us write `User(email=..., password_hash=...)` without
a custom __init__). UUID PKs as strings to keep portability between
Postgres (`UUID`) and SQLite (`TEXT`) — the application layer holds the
canonical type.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedAsDataclass, mapped_column


def _uuid_str() -> str:
    return str(uuid.uuid4())


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Base(MappedAsDataclass, DeclarativeBase):
    """Common base for all ORM models."""


class TimestampMixin(MappedAsDataclass):
    """Adds created_at / updated_at columns with UTC defaults."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=_utc_now,
        init=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default_factory=_utc_now,
        onupdate=_utc_now,
        init=False,
    )


class UUIDPKMixin(MappedAsDataclass):
    """Adds a stringified UUID primary key column named `id`."""

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default_factory=_uuid_str,
        init=False,
    )
