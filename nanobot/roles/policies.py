"""Default tool permission policies per role.

These are the built-in defaults used when no custom role config is provided.
Each role gets a curated set of allowed tools that matches its responsibilities.
"""

from __future__ import annotations

# Tool names that correspond to nanobot's built-in tools
_FILESYSTEM_TOOLS = ["read_file", "write_file", "edit_file", "list_dir"]
_WEB_TOOLS = ["web_search", "web_fetch"]
_EXEC_TOOLS = ["exec"]
_COMMS_TOOLS = ["message", "spawn"]
_WORKFLOW_TOOLS = ["workflow_update", "workflow_add_subtask"]
_CRON_TOOLS = ["cron"]

# All known built-in tools
ALL_TOOLS = sorted(
    set(
        _FILESYSTEM_TOOLS
        + _WEB_TOOLS
        + _EXEC_TOOLS
        + _COMMS_TOOLS
        + _WORKFLOW_TOOLS
        + _CRON_TOOLS
    )
)

DEFAULT_ROLE_POLICIES: dict[str, dict] = {
    "planner": {
        "allowed_tools": _WEB_TOOLS + _WORKFLOW_TOOLS + ["read_file", "list_dir"],
        "denied_tools": ["exec", "write_file", "edit_file"],
        "can_spawn_subtasks": True,
        "memory_scope": "workflow",
        "max_iterations": 20,
    },
    "researcher": {
        "allowed_tools": _WEB_TOOLS + _FILESYSTEM_TOOLS + _WORKFLOW_TOOLS,
        "denied_tools": ["exec", "spawn"],
        "can_access_browser": True,
        "memory_scope": "workflow",
        "max_iterations": 30,
    },
    "coder": {
        "allowed_tools": _FILESYSTEM_TOOLS + _EXEC_TOOLS + _WORKFLOW_TOOLS + ["web_search"],
        "denied_tools": ["message", "spawn"],
        "can_access_sandbox": True,
        "memory_scope": "task",
        "max_iterations": 40,
    },
    "executor": {
        "allowed_tools": _FILESYSTEM_TOOLS + _EXEC_TOOLS + _WORKFLOW_TOOLS,
        "denied_tools": ["message", "spawn", "web_search", "web_fetch"],
        "memory_scope": "task",
        "max_iterations": 20,
    },
    "reviewer": {
        "allowed_tools": _FILESYSTEM_TOOLS + _WEB_TOOLS + _WORKFLOW_TOOLS,
        "denied_tools": ["exec", "write_file", "edit_file", "spawn"],
        "memory_scope": "workflow",
        "max_iterations": 15,
    },
    "manager": {
        "allowed_tools": ALL_TOOLS,
        "denied_tools": [],
        "can_spawn_subtasks": True,
        "memory_scope": "project",
        "max_iterations": 30,
    },
}


def get_allowed_tools(role_name: str, available_tools: list[str] | None = None) -> list[str]:
    """Return the list of tools a role is allowed to use.

    If *available_tools* is provided, the result is intersected with it so
    that we never grant access to tools that don't exist in the current
    tool registry.
    """
    policy = DEFAULT_ROLE_POLICIES.get(role_name)
    if policy is None:
        return available_tools or ALL_TOOLS

    allowed = set(policy.get("allowed_tools", ALL_TOOLS))
    denied = set(policy.get("denied_tools", []))
    result = allowed - denied

    if available_tools is not None:
        result = result & set(available_tools)

    return sorted(result)
