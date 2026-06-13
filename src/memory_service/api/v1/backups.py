"""/v1/admin/backups — super_admin-only full backups, web-driven.

List + trigger + download + delete. Restore is intentionally not here
(it's a high-risk op that stays on `./deploy.sh restore`). See
core/backups.py for how a backup is taken without host docker access.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from memory_service.api.deps import CurrentUser
from memory_service.core import backups

router = APIRouter(prefix="/admin/backups", tags=["admin-backups"])


def _require_super_admin(user) -> None:  # type: ignore[no-untyped-def]
    if not user.is_super_admin:
        raise HTTPException(
            status_code=403, detail="Backups are super_admin-only.",
        )


class BackupMeta(BaseModel):
    id: str
    created_at: str | None = None
    status: str = "complete"
    postgres_bytes: int = 0
    falkordb_bytes: int = 0
    has_postgres: bool = False
    has_falkordb: bool = False
    error: str | None = None


class BackupListResponse(BaseModel):
    backups: list[BackupMeta]


@router.get("", response_model=BackupListResponse)
async def list_backups(user: CurrentUser) -> BackupListResponse:
    _require_super_admin(user)
    return BackupListResponse(backups=[BackupMeta(**b) for b in backups.list_backups()])


@router.post("", response_model=BackupMeta, status_code=status.HTTP_201_CREATED)
async def create_backup(user: CurrentUser) -> BackupMeta:
    """Take a full backup now (pg_dump + FalkorDB RDB). The heavy work
    runs in a worker thread, so the event loop stays responsive; the
    request returns when the backup is written."""
    _require_super_admin(user)
    try:
        result = await backups.run_backup()
    except Exception as exc:
        raise HTTPException(
            status_code=500, detail=f"backup failed: {exc}",
        ) from exc
    return BackupMeta(
        id=result["id"],
        created_at=result.get("created_at"),
        status=result.get("status", "complete"),
        error=result.get("error"),
        has_postgres=True,
    )


@router.get("/{backup_id}/download/{which}")
async def download_backup(
    backup_id: str,
    which: Literal["postgres", "falkordb"],
    user: CurrentUser,
):  # type: ignore[no-untyped-def]
    _require_super_admin(user)
    path = backups.backup_file_path(backup_id, which)
    if path is None:
        raise HTTPException(status_code=404, detail="Backup artifact not found")
    ext = "dump" if which == "postgres" else "rdb"
    return FileResponse(
        path,
        media_type="application/octet-stream",
        filename=f"temper-{backup_id}-{which}.{ext}",
    )


@router.delete("/{backup_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backup(backup_id: str, user: CurrentUser) -> None:
    _require_super_admin(user)
    if not backups.delete_backup(backup_id):
        raise HTTPException(status_code=404, detail="Backup not found")
