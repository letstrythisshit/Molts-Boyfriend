"""Artifact tracking for task outputs.

Tracks files, URLs, and data blobs produced by agent tasks so that
downstream tasks and the supervisor can reference them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pydantic import BaseModel, Field

from nanobot.state.store import StateStore


class Artifact(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    task_id: str
    workflow_id: str = ""
    type: str = "file"  # file | url | data
    path: str
    description: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_db_dict(self) -> dict:
        return self.model_dump()

    @classmethod
    def from_db_dict(cls, d: dict) -> Artifact:
        return cls(**d)


class ArtifactTracker:
    """Register and query artifacts produced during workflow execution."""

    def __init__(self, store: StateStore) -> None:
        self._store = store

    async def register(self, artifact: Artifact) -> None:
        await self._store.register_artifact(artifact.to_db_dict())

    async def list_for_task(self, task_id: str) -> list[Artifact]:
        rows = await self._store.list_artifacts_for_task(task_id)
        return [Artifact.from_db_dict(r) for r in rows]

    async def list_for_workflow(self, workflow_id: str) -> list[Artifact]:
        rows = await self._store.list_artifacts_for_workflow(workflow_id)
        return [Artifact.from_db_dict(r) for r in rows]
