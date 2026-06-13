"""In-app full backups — super_admin, web-driven.

A "full backup" = a pg_dump of Postgres (users, api keys, blocks,
documents, episode metadata, snapshots) + a copy of FalkorDB's RDB
(the graph: entities, facts, episode content). Written under
settings.backup_dir as one timestamped folder per backup.

This is the container-side counterpart to `./deploy.sh backup` (host
CLI / systemd). It works WITHOUT host docker access because:
  - Postgres: pg_dump connects over the network to the postgres
    service (the image ships postgresql-client).
  - FalkorDB: the service triggers BGSAVE over RESP, then copies the
    RDB from the falkordb data volume mounted read-only at
    settings.falkordb_data_dir — no `docker cp` needed.

Restore is deliberately NOT exposed here: restoring over a live
Postgres + bouncing FalkorDB is a high-risk, low-frequency op that
stays on the CLI (`./deploy.sh restore`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from memory_service.config import Settings, get_settings

_logger = logging.getLogger(__name__)


def _backup_root(settings: Settings) -> Path:
    p = Path(settings.backup_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pg_dump_url(database_url: str) -> str:
    """asyncpg URL → libpq URL that pg_dump accepts (strip the +driver)."""
    return database_url.replace("+asyncpg", "").replace("+psycopg", "").replace(
        "+psycopg2", ""
    )


def list_backups(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Newest-first list of backups, read from each folder's meta.json
    (falling back to stat for older/partial ones)."""
    settings = settings or get_settings()
    root = _backup_root(settings)
    out: list[dict[str, Any]] = []
    for d in sorted(root.iterdir(), reverse=True) if root.exists() else []:
        if not d.is_dir():
            continue
        meta_file = d / "meta.json"
        meta: dict[str, Any] = {}
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text())
            except Exception:
                meta = {}
        pg = d / "postgres.dump"
        rdb = d / "falkordb.rdb"
        out.append({
            "id": d.name,
            "created_at": meta.get("created_at"),
            "status": meta.get("status", "complete"),
            "postgres_bytes": pg.stat().st_size if pg.exists() else 0,
            "falkordb_bytes": rdb.stat().st_size if rdb.exists() else 0,
            "has_postgres": pg.exists(),
            "has_falkordb": rdb.exists(),
            "error": meta.get("error"),
        })
    return out


def backup_file_path(
    backup_id: str, which: str, settings: Settings | None = None,
) -> Path | None:
    """Resolve a downloadable artifact. `which` is 'postgres' | 'falkordb'.
    Returns None if missing or if the id tries to escape the root."""
    settings = settings or get_settings()
    root = _backup_root(settings).resolve()
    fname = {"postgres": "postgres.dump", "falkordb": "falkordb.rdb"}.get(which)
    if fname is None:
        return None
    target = (root / backup_id / fname).resolve()
    # Path-traversal guard: target must stay under root.
    if root not in target.parents:
        return None
    return target if target.exists() else None


def delete_backup(backup_id: str, settings: Settings | None = None) -> bool:
    settings = settings or get_settings()
    root = _backup_root(settings).resolve()
    target = (root / backup_id).resolve()
    if root not in target.parents or not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def _prune(settings: Settings) -> None:
    root = _backup_root(settings)
    dirs = sorted([d for d in root.iterdir() if d.is_dir()], reverse=True)
    for old in dirs[settings.backup_keep:]:
        try:
            shutil.rmtree(old)
        except Exception:
            _logger.warning("failed to prune old backup %s", old)


def _run_pg_dump(database_url: str, dest: Path) -> None:
    """Blocking pg_dump (custom format). Raises on failure."""
    url = _pg_dump_url(database_url)
    with dest.open("wb") as f:
        proc = subprocess.run(
            ["pg_dump", "-Fc", "--no-owner", "--no-privileges", url],
            stdout=f, stderr=subprocess.PIPE, timeout=600,
        )
    if proc.returncode != 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError(
            f"pg_dump exit {proc.returncode}: "
            f"{proc.stderr.decode('utf-8', 'replace')[:300]}"
        )


def _copy_falkordb_rdb(settings: Settings, dest: Path) -> str:
    """Copy the freshly-BGSAVE'd RDB from the read-only data mount.
    Returns a status string; non-fatal (Postgres dump is still valid)."""
    data_dir = settings.falkordb_data_dir
    if not data_dir:
        return "skipped (FALKORDB_DATA_DIR not set)"
    src_dir = Path(data_dir)
    # FalkorDB's default dump filename is dump.rdb; accept any *.rdb.
    candidates = [src_dir / "dump.rdb", *sorted(src_dir.glob("*.rdb"))]
    for src in candidates:
        if src.exists():
            shutil.copy2(src, dest)
            return f"copied {src.name} ({dest.stat().st_size} bytes)"
    return f"no .rdb found under {data_dir}"


async def run_backup(settings: Settings | None = None) -> dict[str, Any]:
    """Take a full backup. Offloads the blocking pg_dump + file copy to a
    worker thread so the event loop stays free (same discipline as the
    community build)."""
    settings = settings or get_settings()
    # Folder name is a UTC timestamp; the loop-unsafe Date calls are fine
    # here (real wall clock, not the workflow sandbox).
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    folder = _backup_root(settings) / stamp
    folder.mkdir(parents=True, exist_ok=True)

    meta: dict[str, Any] = {
        "created_at": datetime.now(UTC).isoformat(),
        "status": "running",
    }
    (folder / "meta.json").write_text(json.dumps(meta))

    # FalkorDB BGSAVE first (async RESP), so the RDB on the mount is fresh
    # before we copy it in the worker thread.
    from memory_service.adapters.falkordb import falkordb_bgsave

    bgsave_ok, bgsave_detail = await falkordb_bgsave(settings)

    def _work() -> dict[str, Any]:
        result: dict[str, Any] = {"postgres": None, "falkordb": None}
        _run_pg_dump(settings.database_url, folder / "postgres.dump")
        result["postgres"] = "ok"
        if bgsave_ok:
            result["falkordb"] = _copy_falkordb_rdb(settings, folder / "falkordb.rdb")
        else:
            result["falkordb"] = f"bgsave failed: {bgsave_detail}"
        return result

    try:
        result = await asyncio.to_thread(_work)
        meta.update({
            "status": "complete",
            "finished_at": datetime.now(UTC).isoformat(),
            "postgres": result["postgres"],
            "falkordb": result["falkordb"],
        })
    except Exception as exc:
        _logger.exception("backup failed")
        meta.update({
            "status": "failed",
            "finished_at": datetime.now(UTC).isoformat(),
            "error": str(exc)[:500],
        })
        (folder / "meta.json").write_text(json.dumps(meta))
        raise

    (folder / "meta.json").write_text(json.dumps(meta))
    _prune(settings)
    return {"id": stamp, **meta}
