"""Workflow tools for agents to interact with the orchestration engine.

Allows agents to update task status, add subtasks, and log decisions
during workflow execution.
"""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import Tool


class WorkflowUpdateTool(Tool):
    """Tool for agents to report task progress and status updates."""

    @property
    def name(self) -> str:
        return "workflow_update"

    @property
    def description(self) -> str:
        return (
            "Report progress or status update for the current task. "
            "Use this to communicate intermediate results, blockers, "
            "or completion status back to the orchestrator."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "description": "Current status: 'progress', 'blocked', 'needs_approval'",
                    "enum": ["progress", "blocked", "needs_approval"],
                },
                "message": {
                    "type": "string",
                    "description": "Status message or progress description",
                },
            },
            "required": ["status", "message"],
        }

    async def execute(self, **kwargs: Any) -> str:
        status = kwargs.get("status", "progress")
        message = kwargs.get("message", "")
        # In the full implementation, this publishes to the MessageBus
        # and the supervisor picks it up. For now, it's a structured log.
        return f"[Workflow Update] status={status}: {message}"


class WorkflowAddSubtaskTool(Tool):
    """Tool for agents to request additional subtasks during execution."""

    @property
    def name(self) -> str:
        return "workflow_add_subtask"

    @property
    def description(self) -> str:
        return (
            "Request that a new subtask be added to the current workflow. "
            "The orchestrator will schedule it according to dependencies. "
            "Use this when you discover additional work is needed."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short title for the subtask",
                },
                "description": {
                    "type": "string",
                    "description": "Detailed description of what needs to be done",
                },
                "role": {
                    "type": "string",
                    "description": "Agent role to handle this: planner, researcher, coder, executor, reviewer",
                    "enum": ["planner", "researcher", "coder", "executor", "reviewer"],
                },
                "depends_on_current": {
                    "type": "boolean",
                    "description": "If true, this subtask will only run after the current task completes",
                },
            },
            "required": ["title", "description", "role"],
        }

    async def execute(self, **kwargs: Any) -> str:
        title = kwargs.get("title", "")
        description = kwargs.get("description", "")
        role = kwargs.get("role", "executor")
        depends = kwargs.get("depends_on_current", False)
        # In the full implementation, this sends a request to the supervisor
        # to add a new task to the workflow's task graph.
        return (
            f"[Subtask Requested] title='{title}' role={role} "
            f"depends_on_current={depends}\n"
            f"Description: {description}\n"
            f"The orchestrator will schedule this subtask."
        )
