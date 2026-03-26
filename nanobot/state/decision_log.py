"""Append-only structured decision log.

Records every significant decision an agent makes during task execution
so that reviewers (human or agent) can audit the reasoning chain.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

from nanobot.state.store import StateStore


class DecisionEntry(BaseModel):
    task_id: str
    workflow_id: str = ""
    agent_role: str = ""
    action: str
    reasoning: str = ""
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_db_dict(self) -> dict:
        return self.model_dump()


class DecisionLog:
    """Append-only decision log backed by SQLite."""

    def __init__(self, store: StateStore) -> None:
        self._store = store

    async def log(self, entry: DecisionEntry) -> None:
        await self._store.log_decision(entry.to_db_dict())

    async def get_for_task(self, task_id: str) -> list[DecisionEntry]:
        rows = await self._store.get_decisions_for_task(task_id)
        return [DecisionEntry(**r) for r in rows]

    async def get_for_workflow(self, workflow_id: str) -> list[DecisionEntry]:
        rows = await self._store.get_decisions_for_workflow(workflow_id)
        return [DecisionEntry(**r) for r in rows]
