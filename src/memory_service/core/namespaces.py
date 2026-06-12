"""Namespace parsing + permission checks.

Namespaces are flat strings of five shapes:

  user:{user_id}                  owned by one user (flat — every agent of
                                  this user sees this)
  agent:{user_id}/{agent_slug}    a sub-namespace for one named agent under
                                  a user; two agents with the same slug
                                  share it (explicit memory sharing)
  group:{group_slug}              owned by a group within an org
  org:{org_slug}                  owned by an organization
  public                          everyone-authenticated readable

Permission matrix:

  user:self                rw  for the user.
  user:other               ro/wo only for super_admin.
  agent:<self_id>/<slug>   rw  for that user only (and super_admin).
  agent:<other_id>/<slug>  super_admin only.
  group:slug               rw  for any group member; super_admin always.
  org:slug                 ro  for any member; write for super_admin only.
  public                   read for any authenticated user; write super_admin.

Shortcut forms accepted by `resolve()`:
  user:me            → user:{caller.id}
  agent:me/<slug>    → agent:{caller.id}/<slug>

Group-membership writes flow through `UserGroupMembership`; org
membership is the single `User.org_id` column. There is no per-org
admin role — only the global super_admin manages org/group state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.models import Group, Organization, User, UserGroupMembership

NamespaceKind = Literal["user", "agent", "group", "org", "public"]

# Slug rules for the agent suffix. Lowercase letters, digits, single hyphens,
# 1–64 chars. Same conventions as org/group slugs to keep namespace strings
# readable + URL-safe.
_AGENT_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")


def slugify_agent_slug(text: str | None) -> str | None:
    """Coerce arbitrary text into a valid agent_slug, or None.

    Lowercase, collapse every run of non-alnum into a single hyphen,
    trim hyphens, cap at 64. Guarantees the result matches
    `_AGENT_SLUG_RE` (or is None when nothing usable remains). Used
    everywhere a slug enters the system — API-key create / scope edit,
    onboarding username → slug — so a slug is sanitized once at the
    edge and is never stored in a form the namespace parser would
    later reject.
    """
    if text is None:
        return None
    s = _SLUG_INVALID_RE.sub("-", text.lower()).strip("-")[:64].strip("-")
    return s or None


@dataclass(frozen=True)
class Namespace:
    raw: str
    kind: NamespaceKind
    # Identifier within the kind (user_id, group slug, org slug). Empty for public.
    value: str

    def as_graphiti_group_id(self) -> str:
        """The string Graphiti uses as `group_id`.

        FalkorDB itself accepts `[A-Za-z0-9_-]` in group_id, but Graphiti's
        internal fulltext-search builds RediSearch queries that include the
        group_id verbatim, and RediSearch treats `:` as a field operator
        and `-` as NOT (and `/` would also confuse the query parser). All
        non-alnum-or-underscore chars get folded to underscores. One-way
        mapping is fine — we keep the raw form in EpisodeMetadata.namespace
        for the API surface, and translate only when talking to Graphiti.
        """
        return (
            self.raw.replace(":", "__")
            .replace("/", "_")
            .replace("-", "_")
        )


class NamespaceError(ValueError):
    pass


def parse(raw: str) -> Namespace:
    raw = raw.strip()
    if raw == "public":
        return Namespace(raw=raw, kind="public", value="")
    if ":" not in raw:
        raise NamespaceError(
            "namespace must be one of: 'public', 'user:<id>', 'group:<slug>', "
            "'org:<slug>', 'agent:<id>/<slug>', or the shortcuts 'user:me' / "
            f"'agent:me/<slug>'. Got: {raw!r}"
        )
    kind, _, value = raw.partition(":")
    if kind not in ("user", "agent", "group", "org"):
        raise NamespaceError(
            f"unknown namespace kind {kind!r}. "
            "Valid: user, agent, group, org, public."
        )
    value = value.strip()
    if not value:
        raise NamespaceError(f"empty {kind} id in namespace: {raw!r}")
    if kind == "agent":
        # Shape: agent:<user_id>/<slug>. Both halves required.
        if "/" not in value:
            raise NamespaceError(
                f"agent namespace must be 'agent:<user_id>/<slug>'. Got: {raw!r}"
            )
        owner, _, slug = value.partition("/")
        owner = owner.strip()
        slug = slug.strip()
        if not owner or not slug:
            raise NamespaceError(
                f"agent namespace needs both user_id and slug. Got: {raw!r}"
            )
        if not _AGENT_SLUG_RE.match(slug):
            raise NamespaceError(
                f"agent slug must be lowercase alnum / hyphens, ≤64 chars. "
                f"Got: {slug!r}"
            )
    return Namespace(raw=raw, kind=kind, value=value)  # type: ignore[arg-type]


def resolve(raw: str | None, caller: User) -> Namespace:
    """High-level namespace resolution for callers.

    - None / "" → caller's own default namespace (scoped to their API key's
                  agent_slug if any, otherwise flat user:<id>)
    - "user:me"          → caller's own user namespace
    - "agent:me/<slug>"  → caller's own agent sub-namespace
    - anything else      → parse() normally

    Use this from API layers; reserve plain `parse()` for cases where
    the caller has already been resolved.
    """
    if not raw or not raw.strip():
        return default_namespace_for(caller)
    cleaned = raw.strip()
    if cleaned == "user:me":
        return parse(f"user:{caller.id}")
    if cleaned.startswith("agent:me/"):
        suffix = cleaned[len("agent:me/"):]
        return parse(f"agent:{caller.id}/{suffix}")
    return parse(cleaned)


def default_namespace_for(user: User) -> Namespace:
    """The namespace a new episode lands in if the caller didn't specify.

    When the caller was authenticated by an API key that has `agent_slug`
    set, `api/deps.py` stashes the slug on the User instance as the
    transient `_default_agent_slug` attribute. We pick it up here so the
    default scope follows the key.
    """
    slug = getattr(user, "_default_agent_slug", None)
    if slug:
        return parse(f"agent:{user.id}/{slug}")
    return parse(f"user:{user.id}")


def _agent_owner(ns: Namespace) -> str:
    """Extract the owning user_id from an agent: namespace value."""
    owner, _, _slug = ns.value.partition("/")
    return owner


async def can_read(user: User, ns: Namespace, db: AsyncSession) -> bool:
    """Whether `user` should see episodes in `ns`. See module docstring."""
    if user.is_super_admin:
        return True
    if ns.kind == "user":
        return ns.value == user.id
    if ns.kind == "agent":
        return _agent_owner(ns) == user.id
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
    if ns.kind == "agent":
        return _agent_owner(ns) == user.id
    if ns.kind == "public":
        # Only super_admin writes public — locked down at MVP. PRD §4.3.
        return False
    if ns.kind == "group":
        return await _is_group_member(user, ns.value, db)
    if ns.kind == "org":
        # Only super_admin (handled above) writes to org namespaces now
        # that org_admin is gone. Plain members read but don't write.
        return False
    return False


async def readable_namespaces_for(user: User, db: AsyncSession) -> list[Namespace]:
    """Every namespace this user is allowed to read from.

    Used by /v1/search when the caller doesn't pin namespaces explicitly.
    Includes the umbrella user:<id> AND any agent:<id>/<slug> the user
    has ever used (discovered from their API keys) so a default search
    sees everything the user wrote across all their agents.
    """
    out: list[Namespace] = [parse(f"user:{user.id}"), parse("public")]
    # Every agent_slug this user has ever attached to an API key — gives
    # them cross-agent recall by default. Revoked keys included so old
    # data stays reachable.
    from memory_service.models import APIKey

    agent_slugs = list(
        (
            await db.execute(
                select(APIKey.agent_slug)
                .where(APIKey.user_id == user.id, APIKey.agent_slug.is_not(None))
                .distinct()
            )
        ).scalars().all()
    )
    for slug in agent_slugs:
        if slug:
            out.append(parse(f"agent:{user.id}/{slug}"))
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
