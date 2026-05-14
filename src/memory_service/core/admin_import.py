"""Bulk import implementation for /v1/admin/import.

Two-pass: validate everything first (collecting row-level errors + the
plan), then apply if not dry_run. New users get a generated password
returned to the caller — they can't read this any other way.
"""
from __future__ import annotations

import secrets
import string

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory_service.core.auth import hash_password
from memory_service.models import Group, Organization, User, UserGroupMembership
from memory_service.schemas.admin_import import (
    BulkImportRequest,
    BulkImportResponse,
    CreatedUser,
    ImportResultRow,
)


def _gen_password() -> str:
    """24-char URL-safe random — caller copies it once, hands to user."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(24))


async def run_import(payload: BulkImportRequest, db: AsyncSession) -> BulkImportResponse:
    errors: list[str] = []
    rows: list[ImportResultRow] = []
    created_users: list[CreatedUser] = []

    declared_org_slugs = {o.slug for o in payload.orgs}
    declared_group_slugs = {g.slug for g in payload.groups}

    # ---- Pre-validate -------------------------------------------------

    for u in payload.users:
        if u.org_slug and u.org_slug not in declared_org_slugs:
            # Allow referencing an org that already exists in DB.
            existing = (
                await db.execute(select(Organization).where(Organization.slug == u.org_slug))
            ).scalar_one_or_none()
            if existing is None:
                errors.append(
                    f"user {u.email}: org_slug '{u.org_slug}' not declared in payload "
                    "and not present in DB"
                )
        for gm in u.groups:
            if gm.slug not in declared_group_slugs:
                existing = (
                    await db.execute(select(Group).where(Group.slug == gm.slug))
                ).scalar_one_or_none()
                if existing is None:
                    errors.append(
                        f"user {u.email}: group '{gm.slug}' not declared and not in DB"
                    )

    # If any group declares an org_slug, verify it; if a group is referenced
    # only by a user (no explicit org_slug in declaration), inherit from
    # the user's org_slug (must be unambiguous).
    group_org_resolved: dict[str, str] = {}
    for g in payload.groups:
        if g.org_slug:
            group_org_resolved[g.slug] = g.org_slug
    for u in payload.users:
        for gm in u.groups:
            if gm.slug in group_org_resolved:
                continue
            if u.org_slug:
                group_org_resolved[gm.slug] = u.org_slug
    for g in payload.groups:
        if g.slug not in group_org_resolved:
            errors.append(
                f"group {g.slug}: no org_slug supplied and no user references it "
                "with an org_slug — declare it explicitly"
            )

    if errors:
        return BulkImportResponse(
            dry_run=payload.dry_run, created_users=[], rows=[], errors=errors,
        )

    # ---- Apply (or plan) ---------------------------------------------

    async def _record(kind, target, action, detail=None):
        rows.append(ImportResultRow(kind=kind, target=target, action=action, detail=detail))

    # Orgs
    for o in payload.orgs:
        existing = (
            await db.execute(select(Organization).where(Organization.slug == o.slug))
        ).scalar_one_or_none()
        if existing:
            await _record("org", o.slug, "would-skip" if payload.dry_run else "skipped",
                          "already exists")
            continue
        if payload.dry_run:
            await _record("org", o.slug, "would-create")
        else:
            db.add(Organization(slug=o.slug, name=o.name))
            await _record("org", o.slug, "created")

    if not payload.dry_run:
        await db.commit()

    # Groups — need their org_id, so resolve from DB after orgs are committed.
    for g in payload.groups:
        existing = (
            await db.execute(select(Group).where(Group.slug == g.slug))
        ).scalar_one_or_none()
        if existing:
            await _record("group", g.slug, "would-skip" if payload.dry_run else "skipped",
                          "already exists")
            continue
        org_slug = group_org_resolved[g.slug]
        if payload.dry_run:
            await _record("group", g.slug, "would-create", f"in org={org_slug}")
        else:
            org = (
                await db.execute(select(Organization).where(Organization.slug == org_slug))
            ).scalar_one_or_none()
            if org is None:
                errors.append(f"group {g.slug}: org '{org_slug}' missing after apply")
                continue
            db.add(Group(slug=g.slug, name=g.name, org_id=org.id))
            await _record("group", g.slug, "created", f"in org={org_slug}")

    if not payload.dry_run:
        await db.commit()

    # Users (and their org/group assignments).
    for u in payload.users:
        existing = (
            await db.execute(select(User).where(User.email == u.email))
        ).scalar_one_or_none()
        user_action = "would-skip" if (existing and payload.dry_run) else (
            "skipped" if existing else (
                "would-create" if payload.dry_run else "created"
            )
        )
        if existing:
            user_obj = existing
        elif payload.dry_run:
            user_obj = None  # plan only
        else:
            # Default-password flow (matches POST /v1/users behavior):
            # use settings.default_new_user_password unless the caller
            # supplied an explicit one. Force change on first login.
            from memory_service.config import get_settings

            password = u.password or get_settings().default_new_user_password
            user_obj = User(
                email=u.email,
                display_name=u.display_name,
                password_hash=hash_password(password),
                is_super_admin=False,
                must_change_password=True,
            )
            db.add(user_obj)
            await db.flush()  # need user.id for memberships
            if not u.password:
                created_users.append(
                    CreatedUser(
                        email=u.email, user_id=user_obj.id, generated_password=password,
                    )
                )
            else:
                created_users.append(
                    CreatedUser(email=u.email, user_id=user_obj.id, generated_password=None)
                )
        await _record("user", u.email, user_action,
                      "password generated" if (user_obj and not existing and not u.password) else None)

        # Org assignment + role.
        if u.org_slug:
            org = (
                await db.execute(select(Organization).where(Organization.slug == u.org_slug))
            ).scalar_one_or_none()
            if user_obj and org:
                already = user_obj.org_id == org.id and user_obj.is_org_admin == u.is_org_admin
                action = "would-skip" if (already and payload.dry_run) else (
                    "skipped" if already else (
                        "would-update" if payload.dry_run else "updated"
                    )
                )
                if not payload.dry_run and not already:
                    user_obj.org_id = org.id
                    user_obj.is_org_admin = u.is_org_admin
                await _record(
                    "membership", f"{u.email}→org:{u.org_slug}", action,
                    f"is_org_admin={u.is_org_admin}",
                )

        # Group memberships.
        for gm in u.groups:
            group = (
                await db.execute(select(Group).where(Group.slug == gm.slug))
            ).scalar_one_or_none()
            if user_obj is None or group is None:
                if payload.dry_run:
                    await _record("membership", f"{u.email}→group:{gm.slug}",
                                  "would-create", f"role={gm.role}")
                continue
            existing_mem = (
                await db.execute(
                    select(UserGroupMembership).where(
                        UserGroupMembership.user_id == user_obj.id,
                        UserGroupMembership.group_id == group.id,
                    )
                )
            ).scalar_one_or_none()
            if existing_mem:
                if existing_mem.role == gm.role:
                    await _record("membership", f"{u.email}→group:{gm.slug}",
                                  "would-skip" if payload.dry_run else "skipped",
                                  f"role={gm.role}")
                else:
                    if not payload.dry_run:
                        existing_mem.role = gm.role
                    await _record("membership", f"{u.email}→group:{gm.slug}",
                                  "would-update" if payload.dry_run else "updated",
                                  f"role: {existing_mem.role}→{gm.role}")
            else:
                if not payload.dry_run:
                    db.add(UserGroupMembership(
                        user_id=user_obj.id, group_id=group.id, role=gm.role,
                    ))
                await _record("membership", f"{u.email}→group:{gm.slug}",
                              "would-create" if payload.dry_run else "created",
                              f"role={gm.role}")

    if not payload.dry_run:
        await db.commit()

    return BulkImportResponse(
        dry_run=payload.dry_run,
        created_users=created_users,
        rows=rows,
        errors=errors,
    )
