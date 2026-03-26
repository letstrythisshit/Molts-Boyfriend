"""DAG-based task dependency tracking.

Maintains a directed acyclic graph of tasks within a workflow,
supports dependency resolution, topological ordering, and
runnable-task queries.
"""

from __future__ import annotations

from nanobot.orchestration.models import Task, TaskStatus


class CycleError(Exception):
    """Raised when adding a dependency would create a cycle."""


class TaskGraph:
    """Manages task dependencies as a DAG.

    All tasks must belong to the same workflow.  The graph is built
    from the ``depends_on`` field of each task and supports efficient
    queries for runnable tasks (all deps satisfied).
    """

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._dependents: dict[str, set[str]] = {}  # task_id -> set of tasks that depend on it

    # -- mutation ------------------------------------------------------------

    def add_task(self, task: Task) -> None:
        self._tasks[task.id] = task
        for dep_id in task.depends_on:
            self._dependents.setdefault(dep_id, set()).add(task.id)

    def add_dependency(self, task_id: str, depends_on_id: str) -> None:
        """Add a dependency edge.  Raises CycleError if it would create a cycle."""
        if self._would_cycle(depends_on_id, task_id):
            raise CycleError(
                f"Adding dependency {task_id} -> {depends_on_id} would create a cycle"
            )
        task = self._tasks.get(task_id)
        if task and depends_on_id not in task.depends_on:
            task.depends_on.append(depends_on_id)
        self._dependents.setdefault(depends_on_id, set()).add(task_id)

    def remove_task(self, task_id: str) -> None:
        task = self._tasks.pop(task_id, None)
        if task:
            for dep_id in task.depends_on:
                deps = self._dependents.get(dep_id)
                if deps:
                    deps.discard(task_id)
        self._dependents.pop(task_id, None)

    # -- queries -------------------------------------------------------------

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def all_tasks(self) -> list[Task]:
        return list(self._tasks.values())

    def get_runnable(self) -> list[Task]:
        """Return tasks whose dependencies are all completed and that are still pending/blocked."""
        runnable = []
        for task in self._tasks.values():
            if task.status not in (TaskStatus.PENDING, TaskStatus.BLOCKED):
                continue
            if self._all_deps_met(task):
                runnable.append(task)
        return runnable

    def get_by_status(self, status: TaskStatus) -> list[Task]:
        return [t for t in self._tasks.values() if t.status == status]

    def is_complete(self) -> bool:
        """True when all tasks are in a terminal state."""
        return all(t.is_terminal for t in self._tasks.values())

    def has_failures(self) -> bool:
        return any(t.status == TaskStatus.FAILED for t in self._tasks.values())

    def mark_complete(self, task_id: str, result: str | None = None) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.COMPLETED
            task.result = result

    def mark_failed(self, task_id: str, error: str | None = None) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.FAILED
            task.error = error

    def mark_running(self, task_id: str, agent_id: str | None = None) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.RUNNING
            task.assigned_agent_id = agent_id

    def mark_cancelled(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task:
            task.status = TaskStatus.CANCELLED

    # -- topological ordering ------------------------------------------------

    def topological_order(self) -> list[Task]:
        """Return tasks in dependency order (Kahn's algorithm)."""
        in_degree: dict[str, int] = {tid: 0 for tid in self._tasks}
        for task in self._tasks.values():
            for dep_id in task.depends_on:
                if dep_id in in_degree:
                    in_degree[task.id] = in_degree.get(task.id, 0) + 1

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        order: list[Task] = []

        while queue:
            tid = queue.pop(0)
            task = self._tasks.get(tid)
            if task:
                order.append(task)
            for dependent_id in self._dependents.get(tid, set()):
                if dependent_id in in_degree:
                    in_degree[dependent_id] -= 1
                    if in_degree[dependent_id] == 0:
                        queue.append(dependent_id)

        return order

    # -- stats ---------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for task in self._tasks.values():
            key = task.status.value
            counts[key] = counts.get(key, 0) + 1
        counts["total"] = len(self._tasks)
        return counts

    # -- internal ------------------------------------------------------------

    def _all_deps_met(self, task: Task) -> bool:
        for dep_id in task.depends_on:
            dep = self._tasks.get(dep_id)
            if dep is None:
                continue  # missing dep treated as met (defensive)
            if dep.status != TaskStatus.COMPLETED:
                return False
        return True

    def _would_cycle(self, from_id: str, to_id: str) -> bool:
        """Check if adding from_id -> to_id would create a cycle."""
        if from_id == to_id:
            return True
        visited: set[str] = set()
        stack = [from_id]
        while stack:
            current = stack.pop()
            if current == to_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            task = self._tasks.get(current)
            if task:
                stack.extend(task.depends_on)
        return False

    @classmethod
    def from_tasks(cls, tasks: list[Task]) -> TaskGraph:
        """Build a TaskGraph from a list of tasks."""
        graph = cls()
        for task in tasks:
            graph.add_task(task)
        return graph
