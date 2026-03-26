"""Core data models for the orchestration engine."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "pending"
    BLOCKED = "blocked"
    ASSIGNED = "assigned"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    CANCELLED = "cancelled"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid4().hex[:12]


class Task(BaseModel):
    id: str = Field(default_factory=_new_id)
    workflow_id: str = ""
    parent_id: str | None = None
    title: str = ""
    description: str = ""
    role: str = "executor"
    status: TaskStatus = TaskStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)
    assigned_agent_id: str | None = None
    model_override: str | None = None
    max_iterations: int = 40
    token_budget: int | None = None
    retry_count: int = 0
    max_retries: int = 2
    result: str | None = None
    error: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now_iso)
    started_at: str | None = None
    completed_at: str | None = None
    metadata: dict = Field(default_factory=dict)

    def to_db_dict(self) -> dict:
        d = self.model_dump()
        d["depends_on_json"] = json.dumps(d.pop("depends_on"))
        d["artifacts_json"] = json.dumps(d.pop("artifacts"))
        d["metadata_json"] = json.dumps(d.pop("metadata"))
        d["status"] = d["status"].value if isinstance(d["status"], TaskStatus) else d["status"]
        return d

    @classmethod
    def from_db_dict(cls, d: dict) -> Task:
        d = dict(d)
        d["depends_on"] = json.loads(d.pop("depends_on_json", "[]"))
        d["artifacts"] = json.loads(d.pop("artifacts_json", "[]"))
        d["metadata"] = json.loads(d.pop("metadata_json", "{}"))
        return cls(**d)

    @property
    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)


class Workflow(BaseModel):
    id: str = Field(default_factory=_new_id)
    goal: str = ""
    status: WorkflowStatus = WorkflowStatus.PENDING
    tasks: list[Task] = Field(default_factory=list)
    originator_channel: str = ""
    originator_chat_id: str = ""
    pause_requested: bool = False
    cancel_requested: bool = False
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)
    metadata: dict = Field(default_factory=dict)

    def to_db_dict(self) -> dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status.value if isinstance(self.status, WorkflowStatus) else self.status,
            "originator_channel": self.originator_channel,
            "originator_chat_id": self.originator_chat_id,
            "pause_requested": int(self.pause_requested),
            "cancel_requested": int(self.cancel_requested),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata_json": json.dumps(self.metadata),
        }

    @classmethod
    def from_db_dict(cls, d: dict, tasks: list[Task] | None = None) -> Workflow:
        d = dict(d)
        d["pause_requested"] = bool(d.get("pause_requested", 0))
        d["cancel_requested"] = bool(d.get("cancel_requested", 0))
        d["metadata"] = json.loads(d.pop("metadata_json", "{}"))
        d["tasks"] = tasks or []
        return cls(**d)

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        )
