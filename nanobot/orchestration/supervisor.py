"""Supervisor — the central brain of the orchestration engine.

Converts goals into task graphs using a Planner agent, then drives
execution by scheduling tasks, collecting results, handling retries,
and persisting state for crash recovery.

State machine:
    IDLE -> PLANNING -> EXECUTING -> AGGREGATING -> COMPLETE
                          |    ^
                          v    |
                        PAUSED (user-requested)
                          |
                          v
                       CANCELLED
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from loguru import logger

from nanobot.orchestration.executor import AgentExecutor, TaskResult
from nanobot.orchestration.models import (
    Task,
    TaskStatus,
    Workflow,
    WorkflowStatus,
    _new_id,
    _now_iso,
)
from nanobot.orchestration.retry import DEFAULT_RETRY_POLICY, RetryPolicy
from nanobot.orchestration.scheduler import Scheduler
from nanobot.orchestration.state import OrchestrationStore
from nanobot.orchestration.task_graph import TaskGraph
from nanobot.roles.models import AgentRole
from nanobot.roles.registry import RoleRegistry
from nanobot.state.decision_log import DecisionEntry, DecisionLog
from nanobot.state.store import StateStore


class Supervisor:
    """Central orchestration supervisor.

    Manages the full lifecycle of workflows: planning, scheduling,
    execution, retry, aggregation, and crash recovery.
    """

    def __init__(
        self,
        state_store: StateStore,
        role_registry: RoleRegistry,
        *,
        max_concurrent: int = 3,
        default_timeout: float = 600.0,
        circuit_breaker_threshold: int = 3,
        retry_policy: RetryPolicy | None = None,
        provider=None,
        workspace=None,
        config=None,
    ) -> None:
        self._state_store = state_store
        self._orch_store = OrchestrationStore(state_store)
        self._roles = role_registry
        self._executor = AgentExecutor(
            state_store,
            default_timeout=default_timeout,
            max_concurrent=max_concurrent,
        )
        self._scheduler = Scheduler(max_concurrent=max_concurrent)
        self._decision_log = DecisionLog(state_store)
        self._retry_policy = retry_policy or DEFAULT_RETRY_POLICY
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._provider = provider
        self._workspace = workspace
        self._config = config
        self._active_workflows: dict[str, Workflow] = {}

    # -- public API ----------------------------------------------------------

    async def handle_goal(
        self,
        goal: str,
        channel: str = "",
        chat_id: str = "",
    ) -> str:
        """Main entry point: take a user goal and execute it as a workflow.

        Returns the aggregated result string when the workflow completes.
        """
        workflow = Workflow(
            goal=goal,
            originator_channel=channel,
            originator_chat_id=chat_id,
        )
        self._active_workflows[workflow.id] = workflow

        try:
            # Phase 1: Planning
            await self._transition(workflow, WorkflowStatus.PLANNING)
            tasks = await self._plan_workflow(workflow)

            if not tasks:
                await self._transition(workflow, WorkflowStatus.FAILED)
                return "Failed to decompose goal into tasks."

            workflow.tasks = tasks
            await self._orch_store.save_workflow(workflow)

            # Phase 2: Execution
            await self._transition(workflow, WorkflowStatus.EXECUTING)
            await self._execute_workflow(workflow)

            # Phase 3: Aggregation
            if workflow.cancel_requested:
                await self._transition(workflow, WorkflowStatus.CANCELLED)
                return "Workflow was cancelled."

            await self._transition(workflow, WorkflowStatus.AGGREGATING)
            result = self._aggregate_results(workflow)

            await self._transition(workflow, WorkflowStatus.COMPLETED)
            return result

        except Exception as e:
            logger.error("Workflow {} failed: {}", workflow.id, e)
            await self._transition(workflow, WorkflowStatus.FAILED)
            return f"Workflow failed: {e}"
        finally:
            self._active_workflows.pop(workflow.id, None)

    async def recover_workflows(self) -> list[str]:
        """Recover incomplete workflows after crash/restart.

        Returns list of workflow IDs that were resumed.
        """
        workflows = await self._orch_store.load_incomplete_workflows()
        resumed = []
        for wf in workflows:
            logger.info("Recovering workflow {} (status={})", wf.id, wf.status)
            self._active_workflows[wf.id] = wf
            # Re-run execution for non-terminal workflows
            if wf.status in (WorkflowStatus.EXECUTING, WorkflowStatus.PLANNING):
                asyncio.create_task(self._resume_workflow(wf))
                resumed.append(wf.id)
        return resumed

    async def pause_workflow(self, workflow_id: str) -> bool:
        wf = self._active_workflows.get(workflow_id)
        if wf and not wf.is_terminal:
            wf.pause_requested = True
            await self._transition(wf, WorkflowStatus.PAUSED)
            return True
        return False

    async def cancel_workflow(self, workflow_id: str) -> bool:
        wf = self._active_workflows.get(workflow_id)
        if wf and not wf.is_terminal:
            wf.cancel_requested = True
            return True
        return False

    async def resume_workflow(self, workflow_id: str) -> bool:
        wf = self._active_workflows.get(workflow_id)
        if wf and wf.status == WorkflowStatus.PAUSED:
            wf.pause_requested = False
            asyncio.create_task(self._resume_workflow(wf))
            return True
        return False

    def get_workflow_status(self, workflow_id: str) -> dict | None:
        wf = self._active_workflows.get(workflow_id)
        if not wf:
            return None
        graph = TaskGraph.from_tasks(wf.tasks)
        return {
            "id": wf.id,
            "goal": wf.goal,
            "status": wf.status.value,
            "tasks": graph.stats(),
            "created_at": wf.created_at,
        }

    # -- planning ------------------------------------------------------------

    async def _plan_workflow(self, workflow: Workflow) -> list[Task]:
        """Use the Planner role to decompose the goal into tasks.

        Falls back to a single executor task if planning fails or
        if the planner role is not available.
        """
        planner_role = self._roles.get_role("planner")

        if planner_role and self._provider:
            try:
                result = await self._executor.execute_task(
                    Task(
                        workflow_id=workflow.id,
                        title="Plan workflow",
                        description=f"Decompose this goal into a task plan:\n\n{workflow.goal}",
                        role="planner",
                    ),
                    role=planner_role,
                    provider=self._provider,
                    workspace=self._workspace,
                    config=self._config,
                )
                if result.success and result.output:
                    tasks = self._parse_task_plan(result.output, workflow.id)
                    if tasks:
                        await self._decision_log.log(DecisionEntry(
                            task_id="planning",
                            workflow_id=workflow.id,
                            agent_role="planner",
                            action="decomposed_goal",
                            reasoning=f"Created {len(tasks)} tasks from goal",
                        ))
                        return tasks
            except Exception as e:
                logger.warning("Planner failed, falling back to single task: {}", e)

        # Fallback: single task
        return [
            Task(
                workflow_id=workflow.id,
                title="Execute goal",
                description=workflow.goal,
                role="executor",
            )
        ]

    def _parse_task_plan(self, planner_output: str, workflow_id: str) -> list[Task]:
        """Parse the planner's JSON output into Task objects."""
        # Try to extract JSON array from the output
        try:
            # Find JSON array in the output
            start = planner_output.find("[")
            end = planner_output.rfind("]") + 1
            if start >= 0 and end > start:
                raw = json.loads(planner_output[start:end])
                tasks = []
                id_map: dict[int, str] = {}  # index -> task_id for dependency resolution

                for i, entry in enumerate(raw):
                    task_id = _new_id()
                    id_map[i] = task_id

                    # Resolve depends_on from indices to task IDs
                    deps_raw = entry.get("depends_on", [])
                    deps = []
                    for dep in deps_raw:
                        if isinstance(dep, int) and dep in id_map:
                            deps.append(id_map[dep])
                        elif isinstance(dep, str):
                            deps.append(dep)

                    tasks.append(Task(
                        id=task_id,
                        workflow_id=workflow_id,
                        title=entry.get("title", f"Task {i + 1}"),
                        description=entry.get("description", ""),
                        role=entry.get("role", "executor"),
                        depends_on=deps,
                        max_iterations=entry.get("max_iterations", 40),
                    ))

                return tasks
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.warning("Failed to parse planner output as JSON: {}", e)

        return []

    # -- execution -----------------------------------------------------------

    async def _execute_workflow(self, workflow: Workflow) -> None:
        """Main execution loop: schedule, execute, collect, repeat."""
        graph = TaskGraph.from_tasks(workflow.tasks)
        consecutive_failures = 0

        while not graph.is_complete():
            # Check for pause/cancel
            if workflow.cancel_requested:
                for task in graph.get_by_status(TaskStatus.PENDING):
                    graph.mark_cancelled(task.id)
                break

            if workflow.pause_requested:
                logger.info("Workflow {} paused by user request", workflow.id)
                await self._save_workflow_state(workflow, graph)
                return

            # Schedule next batch
            running_count = self._executor.running_count
            next_tasks = self._scheduler.get_next_tasks(graph, running_count)

            if not next_tasks and running_count == 0:
                # Nothing to run and nothing running — we're stuck or done
                if graph.has_failures():
                    logger.warning("Workflow {} has unresolvable failures", workflow.id)
                break

            if not next_tasks:
                # Wait for running tasks to complete
                await asyncio.sleep(0.5)
                continue

            # Execute tasks concurrently
            coros = []
            for task in next_tasks:
                role = self._roles.get_role(task.role)
                graph.mark_running(task.id)
                await self._orch_store.update_task_status(
                    task.id, TaskStatus.RUNNING.value, started_at=_now_iso()
                )
                coros.append(self._executor.execute_task(
                    task,
                    role=role,
                    provider=self._provider,
                    workspace=self._workspace,
                    config=self._config,
                ))

            results = await asyncio.gather(*coros, return_exceptions=True)

            # Process results
            for result in results:
                if isinstance(result, Exception):
                    logger.error("Task execution raised: {}", result)
                    continue

                if not isinstance(result, TaskResult):
                    continue

                task = graph.get_task(result.task_id)
                if not task:
                    continue

                if result.success:
                    graph.mark_complete(task.id, result.output)
                    await self._orch_store.update_task_status(
                        task.id,
                        TaskStatus.COMPLETED.value,
                        result=result.output,
                        completed_at=_now_iso(),
                    )
                    consecutive_failures = 0
                    logger.info("Task {} completed: {}", task.id, task.title)
                else:
                    # Retry logic
                    if self._retry_policy.should_retry(task):
                        task.retry_count += 1
                        task.status = TaskStatus.PENDING
                        task.error = result.error
                        await self._orch_store.increment_task_retry(task.id)
                        await self._orch_store.update_task_status(
                            task.id, TaskStatus.PENDING.value, error=result.error
                        )
                        delay = self._retry_policy.next_delay(task)
                        logger.info(
                            "Task {} failed, retrying in {:.1f}s (attempt {}): {}",
                            task.id, delay, task.retry_count, result.error,
                        )
                        await asyncio.sleep(delay)
                    else:
                        graph.mark_failed(task.id, result.error)
                        await self._orch_store.update_task_status(
                            task.id,
                            TaskStatus.FAILED.value,
                            error=result.error,
                            completed_at=_now_iso(),
                        )
                        consecutive_failures += 1
                        logger.error(
                            "Task {} failed permanently: {}", task.id, result.error
                        )

                    # Circuit breaker
                    if consecutive_failures >= self._circuit_breaker_threshold:
                        logger.error(
                            "Circuit breaker: {} consecutive failures in workflow {}",
                            consecutive_failures,
                            workflow.id,
                        )
                        workflow.pause_requested = True
                        break

        # Save final state
        await self._save_workflow_state(workflow, graph)

    async def _resume_workflow(self, workflow: Workflow) -> None:
        """Resume a paused or recovering workflow."""
        try:
            workflow.pause_requested = False
            await self._transition(workflow, WorkflowStatus.EXECUTING)
            await self._execute_workflow(workflow)

            if not workflow.cancel_requested and not workflow.pause_requested:
                await self._transition(workflow, WorkflowStatus.AGGREGATING)
                await self._transition(workflow, WorkflowStatus.COMPLETED)
        except Exception as e:
            logger.error("Failed to resume workflow {}: {}", workflow.id, e)
            await self._transition(workflow, WorkflowStatus.FAILED)

    # -- aggregation ---------------------------------------------------------

    def _aggregate_results(self, workflow: Workflow) -> str:
        """Combine results from all completed tasks into a final output."""
        parts = []
        for task in workflow.tasks:
            if task.status == TaskStatus.COMPLETED and task.result:
                parts.append(f"## {task.title}\n{task.result}")
            elif task.status == TaskStatus.FAILED:
                parts.append(f"## {task.title} (FAILED)\nError: {task.error}")

        if not parts:
            return "No task results available."

        return "\n\n".join(parts)

    # -- state management ----------------------------------------------------

    async def _transition(self, workflow: Workflow, new_status: WorkflowStatus) -> None:
        """Transition workflow to a new status, persisting the change."""
        old = workflow.status
        workflow.status = new_status
        workflow.updated_at = _now_iso()
        await self._orch_store.update_workflow_status(workflow.id, new_status)
        logger.info("Workflow {} transitioned: {} -> {}", workflow.id, old.value, new_status.value)

    async def _save_workflow_state(self, workflow: Workflow, graph: TaskGraph) -> None:
        """Persist the current workflow and all task states."""
        workflow.tasks = graph.all_tasks()
        workflow.updated_at = _now_iso()
        await self._orch_store.save_workflow(workflow)
