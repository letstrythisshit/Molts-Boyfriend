"""Tool registry for dynamic tool management."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.roles.models import AgentRole


class ToolRegistry:
    """
    Registry for agent tools.

    Allows dynamic registration and execution of tools.
    Supports role-based filtering via get_definitions(role=...).
    """

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Unregister a tool by name."""
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        """Check if a tool is registered."""
        return name in self._tools

    def get_definitions(self, role: AgentRole | None = None) -> list[dict[str, Any]]:
        """Get tool definitions in OpenAI format, optionally filtered by role.

        When *role* is provided, only tools that the role is allowed to use
        are included.  Tools in the role's denied list are always excluded.
        If the role has an allowed list, only those tools are included.
        If both lists are empty, all tools are returned (backward compat).
        """
        tools = self._tools.values()
        if role is not None:
            allowed = set(role.allowed_tools) if role.allowed_tools else None
            denied = set(role.denied_tools)
            filtered = []
            for tool in tools:
                if tool.name in denied:
                    continue
                if allowed is not None and tool.name not in allowed:
                    continue
                filtered.append(tool)
            tools = filtered
        return [tool.to_schema() for tool in tools]

    async def execute(self, name: str, params: dict[str, Any]) -> Any:
        """Execute a tool by name with given parameters."""
        _HINT = "\n\n[Analyze the error above and try a different approach.]"

        tool = self._tools.get(name)
        if not tool:
            return f"Error: Tool '{name}' not found. Available: {', '.join(self.tool_names)}"

        try:
            # Attempt to cast parameters to match schema types
            params = tool.cast_params(params)
            
            # Validate parameters
            errors = tool.validate_params(params)
            if errors:
                return f"Error: Invalid parameters for tool '{name}': " + "; ".join(errors) + _HINT
            result = await tool.execute(**params)
            if isinstance(result, str) and result.startswith("Error"):
                return result + _HINT
            return result
        except Exception as e:
            return f"Error executing {name}: {str(e)}" + _HINT

    @property
    def tool_names(self) -> list[str]:
        """Get list of registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
