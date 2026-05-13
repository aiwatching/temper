"""Group + UserGroupMembership.

Groups are flat (no nesting). They live under an organization. Membership
is many-to-many with a `role` per-membership ("member" or "admin").
"""
from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from memory_service.models._base import Base, TimestampMixin, UUIDPKMixin


class Group(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "groups"

    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    org_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )


class UserGroupMembership(Base, UUIDPKMixin, TimestampMixin):
    __tablename__ = "user_group_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "group_id", name="uq_user_group"),
    )

    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    group_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16), default="member")
    # role values: "member" | "admin"
