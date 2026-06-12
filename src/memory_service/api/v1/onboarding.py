"""POST /v1/onboarding/provision — one-shot user creation for external
onboarding systems (Forge, HRIS, etc).

The caller supplies four fields — username, email, company, dept —
and gets back a fully-wired TEMPER user: org + group both upserted by
slug, a fresh user row with a starter password, and an API key
scoped to the user's username. Returns plaintext password + plaintext
API key (single chance to capture).

Auth: super_admin only. Forge holds a super_admin API key and is
trusted to do the right thing with the returned secrets.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.config import get_settings
from memory_service.core.auth import (
    api_key_prefix,
    generate_api_key,
    hash_api_key,
    hash_password,
)
from memory_service.models import (
    APIKey,
    Group,
    Organization,
    User,
    UserGroupMembership,
)
from memory_service.schemas.onboarding import (
    ProvisionCreatedFlags,
    ProvisionRequest,
    ProvisionResponse,
)

router = APIRouter(prefix="/onboarding", tags=["onboarding"])

# Slugify rule mirrors the one used elsewhere (api/v1/users.py): lowercase,
# every non-alnum run collapses to a single '-', strip leading/trailing '-',
# truncate to 64 chars to fit the Org/Group slug column.
_SLUG_INVALID_RE = re.compile(r"[^a-z0-9]+")
_SLUG_MAX_LEN = 64


def _slugify(s: str) -> str:
    return _SLUG_INVALID_RE.sub("-", s.lower()).strip("-")[:_SLUG_MAX_LEN].strip("-")


@router.post(
    "/provision",
    response_model=ProvisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def provision(
    payload: ProvisionRequest,
    actor: CurrentUser,
    db: DBDep,
) -> ProvisionResponse:
    """Create org+group+user+API key in one call.

    Idempotency boundaries:
      - org / group are get-or-create by slug — re-running with the
        same company / dept reuses them.
      - user is NOT idempotent — same email → 409. Onboarding the
        same person twice is almost certainly a caller bug.

    The starter password comes from settings.default_new_user_password
    (same as POST /v1/users), and must_change_password is set so the
    user is force-routed to the change-password screen on first login.
    """
    if not actor.is_super_admin:
        raise HTTPException(
            status_code=403,
            detail="Only super_admin can provision users via /onboarding",
        )

    org_slug = _slugify(payload.company)
    dept_slug_raw = _slugify(payload.dept)
    if not org_slug:
        raise HTTPException(
            status_code=422,
            detail=f"company {payload.company!r} produced an empty slug; "
            "use a value with at least one ASCII alphanumeric character",
        )
    if not dept_slug_raw:
        raise HTTPException(
            status_code=422,
            detail=f"dept {payload.dept!r} produced an empty slug",
        )
    # Group slug is qualified by org to avoid global-uniqueness
    # collisions when two orgs both have e.g. "engineering".
    group_slug = f"{org_slug}-{dept_slug_raw}"[:_SLUG_MAX_LEN].strip("-")

    username = payload.username.strip().lower()
    if not username:
        raise HTTPException(status_code=422, detail="username cannot be blank")

    # ---- get-or-create org ----
    org = (
        await db.execute(select(Organization).where(Organization.slug == org_slug))
    ).scalar_one_or_none()
    org_created = False
    if org is None:
        org = Organization(slug=org_slug, name=payload.company.strip())
        db.add(org)
        await db.flush()
        org_created = True

    # ---- get-or-create group within that org ----
    group = (
        await db.execute(select(Group).where(Group.slug == group_slug))
    ).scalar_one_or_none()
    group_created = False
    if group is None:
        group = Group(slug=group_slug, name=payload.dept.strip(), org_id=org.id)
        db.add(group)
        await db.flush()
        group_created = True
    elif group.org_id != org.id:
        # Theoretically unreachable now that we prefix with org_slug,
        # but defended in case the prefix scheme ever changes.
        raise HTTPException(
            status_code=409,
            detail=f"group slug {group_slug!r} exists but belongs to a different org",
        )

    # ---- user must be new ----
    if (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"a user with email {payload.email!r} already exists",
        )
    if (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=409,
            detail=f"username {username!r} is already taken",
        )

    settings = get_settings()
    default_pw = settings.default_new_user_password

    u = User(
        email=payload.email,
        username=username,
        display_name=(payload.display_name or username).strip() or None,
        password_hash=hash_password(default_pw),
        org_id=org.id,
        is_super_admin=False,
        is_active=True,
        must_change_password=True,
        invited_by_user_id=actor.id,
    )
    db.add(u)
    await db.flush()
    db.add(UserGroupMembership(user_id=u.id, group_id=group.id, role="member"))

    # ---- API key, scoped to a slug derived from the username ----
    # agent_slug puts the key's default namespace at
    # agent:<user_id>/<slug>. The username is slugified first — a raw
    # username with '_' / '.' / uppercase would be an invalid slug the
    # namespace parser later rejects (the key couldn't even write), and
    # the scope-edit UI couldn't save it. _slugify keeps it valid.
    api_slug = _slugify(username) or "agent"
    plaintext = generate_api_key()
    api_key = APIKey(
        user_id=u.id,
        agent_name=(payload.display_name or username),
        agent_slug=api_slug,
        key_hash=hash_api_key(plaintext),
        prefix=api_key_prefix(plaintext),
    )
    db.add(api_key)

    await db.commit()
    await db.refresh(u)
    await db.refresh(api_key)

    return ProvisionResponse(
        user_id=u.id,
        username=u.username or username,
        email=u.email,
        display_name=u.display_name,
        org_slug=org.slug,
        group_slug=group.slug,
        default_password=default_pw,
        must_change_password=True,
        api_key=plaintext,
        api_key_prefix=api_key.prefix,
        api_key_agent_slug=api_key.agent_slug or api_slug,
        created=ProvisionCreatedFlags(
            org=org_created,
            group=group_created,
            user=True,
        ),
    )
