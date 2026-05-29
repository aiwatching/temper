"""/v1/me/export + /v1/me/import — per-user memory portability.

Lets a logged-in user dump their blocks + documents + episodes to a
JSON bundle and replay it into another TEMPER instance. Designed for
laptop-dev → server migration and "moving my memory between hosts"
workflows. See `core/memory_export.py` for the building blocks and
`schemas/memory_export.py` for the bundle shape.

Auth: any authenticated user (API key or session) can export/import
THEIR OWN data. There's no admin-on-behalf-of-X variant yet — that
would need an explicit /v1/admin/users/{id}/export.
"""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Query

from memory_service.api.deps import CurrentUser, DBDep
from memory_service.core import memory_export
from memory_service.schemas.memory_export import (
    ImportReport,
    MemoryBundleV1,
)

router = APIRouter(prefix="/me", tags=["memory-export"])


@router.get(
    "/export",
    response_model=MemoryBundleV1,
    summary="Export the caller's blocks + documents + episodes as a JSON bundle.",
)
async def export_my_memory(
    user: CurrentUser,
    db: DBDep,
) -> MemoryBundleV1:
    """Dump everything owned by the caller into a portable JSON bundle.

    What's included: every MemoryBlock (across all agent_slugs), every
    Document, every Episode (metadata + raw content fetched live from
    graphiti). What's NOT included: vector embeddings, FalkorDB graph
    structure, user/auth records.

    The response can be large — episodes carry their full text. For
    a user with thousands of episodes, expect MB-scale JSON. If you
    need stream-friendly export, build a CLI client that paginates
    via direct DB access instead.
    """
    return await memory_export.build_bundle(user, db)


@router.post(
    "/import",
    response_model=ImportReport,
    summary="Replay a previously-exported bundle into the caller's namespace.",
)
async def import_my_memory(
    bundle: MemoryBundleV1,
    user: CurrentUser,
    db: DBDep,
    mode: Annotated[
        Literal["merge", "replace"],
        Query(
            description=(
                "merge (default): upsert. Existing blocks / documents / "
                "episodes survive — same-key rows in the bundle overwrite "
                "them. replace: wipe the caller's existing data first, "
                "then load the bundle. replace does NOT clear the "
                "FalkorDB graph — for a clean slate run "
                "`drop_namespace_graph` first or `./deploy.sh reset` "
                "the whole stack."
            ),
        ),
    ] = "merge",
    background_extraction: Annotated[
        bool,
        Query(
            description=(
                "If true, episode writes return immediately and graphiti "
                "extraction runs in the background. Trades 'graph is "
                "complete when the import responds' for fast bulk-write. "
                "Default false: each episode synchronously re-extracts "
                "entities + facts via the configured LLM."
            ),
        ),
    ] = False,
) -> ImportReport:
    """Apply a bundle to the caller's namespace.

    Namespace remap: if the bundle was exported by user X and is being
    imported by user Y, references to `user:X` / `agent:X/<slug>` are
    rewritten to `user:Y` / `agent:Y/<slug>`. Group / org / public
    namespaces are NOT remapped — they're shared identifiers and the
    importing user either has access on the target host or doesn't
    (rows that fail the perm check go into `errors[]`, the import
    continues).

    Errors per row don't stop the import — see `errors[]` in the
    response for what failed. `blocks.errored + documents.errored +
    episodes.errored == 0` means clean."""
    return await memory_export.apply_bundle(
        user, bundle, db,
        mode=mode,
        background_extraction=background_extraction,
    )
