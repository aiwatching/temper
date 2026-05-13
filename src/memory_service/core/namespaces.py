"""Namespace parsing + permission checks.

Per PRD §4.2 namespaces are flat strings of four shapes:

  user:{user_id}        owned by one user
  group:{group_slug}    owned by a group
  org:{org_slug}        owned by an organization
  public                everyone-authenticated readable

This file implements the **minimum** set of checks needed for Phase 1.5
(episode write/read). Phase 1.4 will replace it with the full matrix
once orgs/groups CRUD exists. Behaviour in this minimal version:

  user:self        rw  for the user, deny for others.
  user:other       deny for everyone except super_admin.
  group:*          deny except super_admin until membership exists.
  org:*            deny except super_admin until membership exists.
  public           read for any authenticated user; write for super_admin.

The point is to fail safely now and tighten later. We deliberately do
not silently grant access we'd later want to take away.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.models import Group, Organization, User, UserGroupMembership

NamespaceKind = Literal["user", "group", "org", "public"]


@dataclass(frozen=True)
class Namespace:
    raw: str
    kind: NamespaceKind
    # Identifier within the kind (user_id, group slug, org slug). Empty for public.
    value: str

    def as_graphiti_group_id(self) -> str:
        """The string Graphiti uses as `group_id`.

        FalkorDB rejects characters outside `[A-Za-z0-9_-]` in group_id, so
        we encode the `:` separator as `__` (double underscore — single
        underscores stay legal inside ids). The mapping is reversible if
        we ever need it: split on `__` for kind/value.
        """
        return self.raw.replace(":", "__")


class NamespaceError(ValueError):
    pass


def parse(raw: str) -> Namespace:
    raw = raw.strip()
    if raw == "public":
        return Namespace(raw=raw, kind="public", value="")
    if ":" not in raw:
        raise NamespaceError(
            f"namespace must be one of: 'public', 'user:<id>', 'group:<slug>', "
            f"'org:<slug>', or 'user:me' for self. Got: {raw!r}"
        )
    kind, _, value = raw.partition(":")
    if kind not in ("user", "group", "org"):
        raise NamespaceError(
            f"unknown namespace kind {kind!r}. Valid: user, group, org, public."
        )
    value = value.strip()
    if not value:
        raise NamespaceError(f"empty {kind} id in namespace: {raw!r}")
    return Namespace(raw=raw, kind=kind, value=value)  # type: ignore[arg-type]


def resolve(raw: str | None, caller: User) -> Namespace:
    """High-level namespace resolution for callers.

    - None / "" → caller's own namespace
    - "user:me"  → caller's own namespace (handy for agents that don't
                   want to look up their own UUID first)
    - anything else → parse() normally

    Use this from API layers; reserve plain `parse()` for cases where
    the caller has already been resolved.
    """
    if not raw or not raw.strip():
        return default_namespace_for(caller)
    cleaned = raw.strip()
    if cleaned == "user:me":
        return parse(f"user:{caller.id}")
    return parse(cleaned)


def default_namespace_for(user: User) -> Namespace:
    """The namespace a new episode lands in if the caller didn't specify."""
    return parse(f"user:{user.id}")


async def can_read(user: User, ns: Namespace, db: AsyncSession) -> bool:
    """Whether `user` should see episodes in `ns`. See module docstring."""
    if user.is_super_admin:
        return True
    if ns.kind == "user":
        return ns.value == user.id
    if ns.kind == "public":
        return True
    if ns.kind == "group":
        return await _is_group_member(user, ns.value, db)
    if ns.kind == "org":
        return await _is_in_org(user, ns.value, db)
    return False


async def can_write(user: User, ns: Namespace, db: AsyncSession) -> bool:
    """Whether `user` should be allowed to add an episode to `ns`."""
    if user.is_super_admin:
        return True
    if ns.kind == "user":
        return ns.value == user.id
    if ns.kind == "public":
        # Only super_admin writes public — locked down at MVP. PRD §4.3.
        return False
    if ns.kind == "group":
        return await _is_group_member(user, ns.value, db)
    if ns.kind == "org":
        # Writing to org-level needs org-admin role. Until Phase 1.3 ships
        # that, deny outright.
        return False
    return False


async def readable_namespaces_for(user: User, db: AsyncSession) -> list[Namespace]:
    """Every namespace this user is allowed to read from.

    Used by /v1/search when the caller doesn't pin namespaces explicitly.
    """
    out: list[Namespace] = [parse(f"user:{user.id}"), parse("public")]
    # Groups the user is a member of
    if user.id:
        stmt = (
            select(Group.slug)
            .join(UserGroupMembership, UserGroupMembership.group_id == Group.id)
            .where(UserGroupMembership.user_id == user.id)
        )
        for (slug,) in (await db.execute(stmt)).all():
            out.append(parse(f"group:{slug}"))
    # User's own org
    if user.org_id:
        org_slug = (
            await db.execute(select(Organization.slug).where(Organization.id == user.org_id))
        ).scalar_one_or_none()
        if org_slug:
            out.append(parse(f"org:{org_slug}"))
    return out


# ---- internal lookups ----


async def _is_group_member(user: User, group_slug: str, db: AsyncSession) -> bool:
    stmt = (
        select(UserGroupMembership.id)
        .join(Group, Group.id == UserGroupMembership.group_id)
        .where(UserGroupMembership.user_id == user.id, Group.slug == group_slug)
        .limit(1)
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None


async def _is_in_org(user: User, org_slug: str, db: AsyncSession) -> bool:
    if not user.org_id:
        return False
    stmt = select(Organization.id).where(
        Organization.slug == org_slug, Organization.id == user.org_id
    )
    return (await db.execute(stmt)).scalar_one_or_none() is not None
