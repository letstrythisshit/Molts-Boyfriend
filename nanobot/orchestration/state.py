"""Orchestration state persistence.

Wraps StateStore to provide workflow/task specific persistence
with crash recovery support.
"""

from __future__ import annotations

from loguru import logger

from nanobot.orchestration.models import Task, Workflow, WorkflowStatus
from nanobot.state.store import StateStore


class OrchestrationStore:
    """Persists orchestration state (workflows + tasks) to SQLite."""

    def __init__(self, store: StateStore) -> None:
        self._store = store

    async def save_workflow(self, workflow: Workflow) -> None:
        await self._store.save_workflow(workflow.to_db_dict())
        for task in workflow.tasks:
            task.workflow_id = workflow.id
            await self._store.save_task(task.to_db_dict())
        logger.debug("Saved workflow {} with {} tasks", workflow.id, len(workflow.tasks))

    async def load_workflow(self, workflow_id: str) -> Workflow | None:
        wf_dict = await self._store.load_workflow(workflow_id)
        if not wf_dict:
            return None
        task_dicts = await self._store.load_tasks_for_workflow(workflow_id)
        tasks = [Task.from_db_dict(td) for td in task_dicts]
        return Workflow.from_db_dict(wf_dict, tasks)

    async def load_incomplete_workflows(self) -> list[Workflow]:
        wf_dicts = await self._store.load_incomplete_workflows()
        workflows = []
        for wf_dict in wf_dicts:
            task_dicts = await self._store.load_tasks_for_workflow(wf_dict["id"])
            tasks = [Task.from_db_dict(td) for td in task_dicts]
            workflows.append(Workflow.from_db_dict(wf_dict, tasks))
        logger.info("Loaded {} incomplete workflows for recovery", len(workflows))
        return workflows

    async def update_workflow_status(self, workflow_id: str, status: WorkflowStatus) -> None:
        from nanobot.orchestration.models import _now_iso
        await self._store.update_workflow_status(workflow_id, status.value, _now_iso())

    async def save_task(self, task: Task) -> None:
        await self._store.save_task(task.to_db_dict())

    async def update_task_status(
        self,
        task_id: str,
        status: str,
        *,
        result: str | None = None,
        error: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> None:
        await self._store.update_task_status(
            task_id,
            status,
            result=result,
            error=error,
            started_at=started_at,
            completed_at=completed_at,
        )

    async def increment_task_retry(self, task_id: str) -> None:
        await self._store.increment_task_retry(task_id)
