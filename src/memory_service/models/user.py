"""User account + role flag.

The `is_super_admin` flag is the only hardcoded role — finer-grained
permissions come from group membership + the permissions matrix in
Phase 1.4.
"""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from memory_service.models._base import Base, TimestampMixin, UUIDPKMixin


class User(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255), default=None)
    org_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="SET NULL"), default=None, index=True
    )
    is_super_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Per the 1-user-1-org constraint (org_id is a single FK), org_admin is a
    # bool, not a per-org membership row. Super_admin or another org_admin in
    # the same org promotes/demotes. Setting org_id=NULL is what "removes
    # someone from the org" — is_org_admin should be cleared at the same time.
    is_org_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
