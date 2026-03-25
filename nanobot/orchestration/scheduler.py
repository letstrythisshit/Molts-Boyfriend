"""Task scheduler — picks runnable tasks and assigns them to agents.

The scheduler queries the task graph for tasks whose dependencies are
all met, respects concurrency limits, and prioritizes tasks that
unblock the most downstream work.
"""

from __future__ import annotations

from loguru import logger

from nanobot.orchestration.models import Task, TaskStatus
from nanobot.orchestration.task_graph import TaskGraph


class Scheduler:
    """Selects and prioritizes tasks for execution."""

    def __init__(self, max_concurrent: int = 3) -> None:
        self._max_concurrent = max_concurrent

    def get_next_tasks(self, graph: TaskGraph, running_count: int = 0) -> list[Task]:
        """Return the next batch of tasks to execute.

        Respects the concurrency limit and prioritizes tasks that
        unblock the most downstream work (most dependents first).
        """
        available_slots = self._max_concurrent - running_count
        if available_slots <= 0:
            return []

        runnable = graph.get_runnable()
        if not runnable:
            return []

        # Prioritize: tasks with more dependents first (unblock more work)
        scored = []
        for task in runnable:
            dependent_count = self._count_dependents(graph, task.id)
            scored.append((dependent_count, task))
        scored.sort(key=lambda x: x[0], reverse=True)

        selected = [task for _, task in scored[:available_slots]]
        if selected:
            logger.debug(
                "Scheduled {} tasks: {}",
                len(selected),
                [f"{t.id}({t.role})" for t in selected],
            )
        return selected

    def _count_dependents(self, graph: TaskGraph, task_id: str) -> int:
        """Count how many tasks (directly or transitively) depend on this task."""
        count = 0
        for task in graph.all_tasks():
            if task_id in task.depends_on:
                count += 1
        return count

    def check_capacity(self, running_count: int) -> bool:
        """Return True if there's capacity to run more tasks."""
        return running_count < self._max_concurrent
