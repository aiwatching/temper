"""Pydantic schemas for the typed memory API.

These endpoints sit ABOVE the raw block / episode primitives. The point
is to give agents a name for each kind of memory ("a task", "current
focus", "a preference") so:

  - the agent picks intent at tool-call time (tool name = intent)
  - TEMPER decides where the data lands (block vs graphiti) and which
    key / prefix / scope to use
  - cross-storage atomicity (e.g. task_complete = block update +
    graphiti append) is handled here, not in agent code

Routing reference (lives in core/typed_memory.py as the canonical
source — duplicated below for skim-readability):

  state.active_tasks         block, own scope,    pinned, p=100
  state.current_focus        block, own scope,    pinned, p=100
  preferences.<key>          block, global scope, pinned, p=90
  events                     graphiti episode in caller's default namespace
  turn_context               read-bundle (pinned blocks + recall)
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

# Statuses an agent can SET on a task (input).
TaskInputStatus = Literal["todo", "doing", "blocked"]
# Statuses a task can HAVE in a response (output). "done" only appears
# transiently in the response from POST /tasks/{id}/complete — it's never
# stored in the active list.
TaskStatus = Literal["todo", "doing", "blocked", "done"]


# ----- Tasks -----


class TaskItem(BaseModel):
    """One row inside the state.active_tasks block."""

    id: str
    title: str
    status: TaskStatus = "todo"
    priority: int = 50
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class CreateTaskRequest(BaseModel):
    title: Annotated[str, Field(min_length=1, max_length=500)]
    status: TaskInputStatus = "todo"
    priority: Annotated[int, Field(ge=0, le=100)] = 50
    notes: str | None = None


class UpdateTaskRequest(BaseModel):
    title: str | None = None
    status: TaskInputStatus | None = None
    priority: Annotated[int | None, Field(ge=0, le=100)] = None
    notes: str | None = None


class CompleteTaskRequest(BaseModel):
    summary: str | None = Field(
        default=None,
        description=(
            "Optional one-liner kept in the graphiti episode written when "
            "the task closes. If absent, episode text falls back to the "
            "task title."
        ),
    )


class TaskListResponse(BaseModel):
    tasks: list[TaskItem]


class TaskCompleteResponse(BaseModel):
    completed: TaskItem
    episode_id: str


# ----- Focus -----


class SetFocusRequest(BaseModel):
    value: Annotated[str, Field(min_length=1, max_length=500)]
    note: str | None = Field(
        default=None,
        description="Free-form context recorded in the focus-change episode.",
    )


class FocusResponse(BaseModel):
    value: str | None
    updated_at: datetime | None
    episode_id: str | None = None  # only set by SET, omitted by GET


# ----- Preferences -----


class SetPreferenceRequest(BaseModel):
    value: Any
    description: str | None = None


class PreferenceItem(BaseModel):
    key: str           # full block_key minus the "preferences." prefix
    value: Any
    description: str | None
    updated_at: datetime


class PreferenceListResponse(BaseModel):
    preferences: list[PreferenceItem]


# ----- Events -----
# Intentionally thin pass-through to the existing episode write path —
# the win is that "note_event" is the canonical agent-tool name; TEMPER
# routes it to a graphiti episode under the hood.


class NoteEventRequest(BaseModel):
    content: Annotated[str, Field(min_length=1, max_length=10_000)]
    namespace: str | None = Field(
        default=None,
        description=(
            "Override target namespace. Defaults to the caller's "
            "default (agent:me/<slug> if API-key auth)."
        ),
    )
    reference_time: datetime | None = None
    tags: list[str] | None = None
    saga: str | None = None


class NoteEventResponse(BaseModel):
    episode_id: str
    namespace: str
    created_at: datetime


# ----- turn_context (read-bundle) -----


class PinnedBlockOut(BaseModel):
    key: str
    value: Any
    priority: int
    description: str | None
    scope: Literal["own", "global"]


class RecalledEpisodeOut(BaseModel):
    episode_id: str | None
    namespace: str
    fact: str
    score: float
    valid_at: datetime | None = None
    invalid_at: datetime | None = None


class TurnContextResponse(BaseModel):
    """Everything an agent needs to assemble the system prompt for one turn.

    The bundle is structured so the agent can render it deterministically
    instead of stitching multiple endpoint calls together.
    """

    # Structured shortcuts — resolved from the canonical block_keys so
    # the agent doesn't have to know about state.active_tasks et al.
    active_tasks: list[TaskItem]
    current_focus: str | None
    preferences: dict[str, Any]

    # All pinned blocks (including the three above; the agent can choose
    # to render structured shortcuts and skip the raw row, or use both).
    pinned_blocks: list[PinnedBlockOut]

    # Graphiti recall against the user's message. Empty when no query.
    recalled_episodes: list[RecalledEpisodeOut]

    namespaces_searched: list[str]
