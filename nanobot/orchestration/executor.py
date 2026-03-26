"""Agent executor — spawns parameterized AgentLoop instances per task.

Each task gets its own AgentLoop configured with the appropriate role,
model, tools, and budget.  Results are collected and returned to the
supervisor for task graph updates.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from loguru import logger

from nanobot.orchestration.models import Task, TaskStatus, _now_iso
from nanobot.roles.models import AgentRole
from nanobot.state.scratchpad import Scratchpad
from nanobot.state.store import StateStore


class TaskResult:
    """Result of executing a single task."""

    def __init__(
        self,
        task_id: str,
        success: bool,
        output: str = "",
        error: str = "",
    ) -> None:
        self.task_id = task_id
        self.success = success
        self.output = output
        self.error = error


class AgentExecutor:
    """Executes tasks by spawning configured agent loops.

    For the MVP, this runs a simplified agent loop inline.
    Full integration with AgentLoop will be done when we modify loop.py.
    """

    def __init__(
        self,
        state_store: StateStore,
        default_timeout: float = 600.0,
        max_concurrent: int = 3,
    ) -> None:
        self._store = state_store
        self._default_timeout = default_timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._running: dict[str, asyncio.Task] = {}

    async def execute_task(
        self,
        task: Task,
        role: AgentRole | None = None,
        *,
        provider=None,
        workspace=None,
    ) -> TaskResult:
        """Execute a task with the given role configuration.

        This is the main entry point called by the scheduler.
        It wraps the actual execution in a timeout and concurrency gate.
        """
        agent_id = uuid4().hex[:8]
        task.assigned_agent_id = agent_id
        task.status = TaskStatus.RUNNING
        task.started_at = _now_iso()

        logger.info(
            "Executing task {} [{}] with role={} agent={}",
            task.id,
            task.title,
            task.role,
            agent_id,
        )

        timeout = self._default_timeout
        if role and role.max_iterations:
            # Rough estimate: ~30s per iteration max
            timeout = min(role.max_iterations * 30, self._default_timeout)

        try:
            async with self._semaphore:
                result = await asyncio.wait_for(
                    self._run_agent(task, role, agent_id, provider, workspace),
                    timeout=timeout,
                )
                return result
        except asyncio.TimeoutError:
            error_msg = f"Task {task.id} timed out after {timeout}s"
            logger.warning(error_msg)
            return TaskResult(task_id=task.id, success=False, error=error_msg)
        except asyncio.CancelledError:
            logger.info("Task {} was cancelled", task.id)
            return TaskResult(task_id=task.id, success=False, error="Cancelled")
        except Exception as e:
            error_msg = f"Task {task.id} failed with exception: {e}"
            logger.error(error_msg)
            return TaskResult(task_id=task.id, success=False, error=error_msg)

    async def _run_agent(
        self,
        task: Task,
        role: AgentRole | None,
        agent_id: str,
        provider,
        workspace,
    ) -> TaskResult:
        """Run the agent loop for a task.

        Creates a parameterized AgentLoop with role-filtered tools and the
        role's system prompt.  Falls back to a stub if no provider is given.
        """
        scratchpad = Scratchpad(self._store, agent_id)

        try:
            if provider and workspace:
                output = await self._run_with_agent_loop(
                    task, role, agent_id, provider, workspace
                )
            else:
                # Lightweight fallback for testing / when no provider available
                output = f"[Task {task.id}] Executed: {task.title}\nDescription: {task.description}"

            await scratchpad.clear()
            return TaskResult(task_id=task.id, success=True, output=output)

        except Exception as e:
            await scratchpad.clear()
            return TaskResult(task_id=task.id, success=False, error=str(e))

    async def _run_with_agent_loop(
        self,
        task: Task,
        role: AgentRole | None,
        agent_id: str,
        provider,
        workspace,
    ) -> str:
        """Execute task using the full AgentLoop with role parameterization.

        This imports AgentLoop lazily to avoid circular imports and creates
        a parameterized instance that respects the role's tool permissions,
        system prompt, and iteration limits.
        """
        from nanobot.agent.loop import AgentLoop
        from nanobot.bus.queue import MessageBus

        model = provider.get_default_model()
        if role and role.model:
            model = role.model

        max_iter = role.max_iterations if role else task.max_iterations

        loop = AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=workspace,
            model=model,
            max_iterations=max_iter,
            restrict_to_workspace=True,
            role=role,
        )

        response = await loop.process_direct(
            content=task.description,
            session_key=f"wf:{task.workflow_id}:task:{task.id}",
        )

        return response.content if response else ""

    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        atask = self._running.pop(task_id, None)
        if atask and not atask.done():
            atask.cancel()
            logger.info("Cancelled task {}", task_id)
            return True
        return False

    @property
    def running_count(self) -> int:
        return len([t for t in self._running.values() if not t.done()])
